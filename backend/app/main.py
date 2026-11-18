from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any

from fastapi import FastAPI

from app.agents.memory_evolution_agent import MemoryEvolutionAgent
from app.agents.report_review_agent import WeeklyReportReviewAgent
from app.agents.report_writer_agent import WeeklyReportWriterAgent
from app.agents.visual_understanding_agent import VisualUnderstandingAgent
from app.api.routes_tasks import router as tasks_router
from app.api.routes_trace import router as trace_router
from app.api.routes_artifacts import router as artifacts_router
from app.api.routes_skills import router as skills_router
from app.logging_config import configure_system_logging
from app.memory.skill_memory import SkillMemory
from app.memory.skill_selector import SkillSelector
from app.observability import ObservabilityProvider, ObservedToolExecutor, create_observability_provider
from app.services.artifact_service import ArtifactService
from app.services.project_artifact_service import ProjectArtifactService
from app.services.config_service import ConfigService
from app.services.chat_model_factory import ChatModelFactory
from app.services.structured_trace_service import StructuredTraceService
from app.services.trace_service import TraceService
from app.services.task_runner import LocalTaskRunner, TaskRunner
from app.services.task_operation_service import TaskOperationService
from app.schemas.task import TaskStatus
from app.services.task_execution_service import TaskExecutionService
from app.services.task_query_service import TaskQueryService
from app.services.workbench_query_service import WorkbenchQueryService
from app.tools.report_render_tool import WeeklyReportRenderTool
from app.tools.material_preprocess_tool import MaterialPreprocessTool
from app.tools.langchain_tools import (
    GetSkillContentTool,
)
from app.workflows.graph_io import (
    build_current_report_loader,
    build_material_pack_loader,
    build_material_pack_saver,
)
from app.workflows.graph_nodes import WeeklyReportGraphNodes
from app.workflows.checkpointer import build_sqlite_checkpointer


logger = logging.getLogger(__name__)


def create_app(
    config_service: ConfigService | None = None,
    task_runner: TaskRunner | None = None,
    trace_service: TraceService | None = None,
    artifact_service: ArtifactService | None = None,
    material_preprocess_tool: MaterialPreprocessTool | None = None,
    graph_nodes: WeeklyReportGraphNodes | None = None,
    memory_evolution_agent: MemoryEvolutionAgent | None = None,
    observability_provider: ObservabilityProvider | None = None,
    structured_trace_service: StructuredTraceService | None = None,
) -> FastAPI:
    config_service = config_service or ConfigService()
    settings = config_service.load()
    configure_system_logging(settings.logging)
    runner = task_runner or LocalTaskRunner(max_workers=settings.task_runner.max_workers)
    artifact_service = artifact_service or ArtifactService(
        settings.storage.artifact_root,
        settings.storage.sqlite_path,
    )
    project_artifact_service = ProjectArtifactService(
        settings.storage.artifact_root,
        settings.storage.sqlite_path,
    )
    trace_service = trace_service or TraceService(artifact_service.sqlite_path)
    task_operation_service = TaskOperationService(artifact_service.session_factory)
    interrupted_operations = task_operation_service.mark_interrupted()
    for operation in interrupted_operations:
        previous_state = task_operation_service.get_previous_state(operation.operation_id)
        if previous_state is not None:
            artifact_service.save_workflow_state(
                operation.task_id,
                previous_state,
                status=TaskStatus(previous_state.get("task_status", TaskStatus.WAITING.value)),
            )
    task_query_service = TaskQueryService(artifact_service)
    workbench_query_service = WorkbenchQueryService(
        artifact_service,
        task_query_service,
        task_operation_service,
        trace_service,
    )
    observability_provider = observability_provider or create_observability_provider(
        settings.observability
    )
    structured_trace_service = structured_trace_service or StructuredTraceService(
        trace_service=trace_service,
        artifact_service=artifact_service,
        observability_provider=observability_provider,
    )
    checkpoint_path = settings.storage.sqlite_path.with_name("langgraph_checkpoints.db")
    checkpoint_context = build_sqlite_checkpointer(checkpoint_path)
    graph_checkpointer = checkpoint_context.__enter__()
    model_factory = ChatModelFactory(settings.model)
    chat_model = model_factory.chat()
    vision_model = model_factory.vision() if settings.model.vision.model else None
    tool_executor = ObservedToolExecutor(observability_provider)
    material_preprocess_tool = material_preprocess_tool or MaterialPreprocessTool(artifact_service)
    skill_memory = SkillMemory(artifact_service.sqlite_path)
    skill_selector = SkillSelector(skill_memory)
    skill_content_tool = GetSkillContentTool(skill_selector)
    graph_nodes = graph_nodes or WeeklyReportGraphNodes(
        material_pack_loader=build_material_pack_loader(),
        material_pack_saver=build_material_pack_saver(artifact_service),
        current_report_loader=build_current_report_loader(artifact_service),
        visual_understanding_agent=(
            VisualUnderstandingAgent(vision_model, tool_executor=tool_executor)
            if vision_model is not None
            else None
        ),
        writer_agent=WeeklyReportWriterAgent(chat_model, tools=[skill_content_tool], tool_executor=tool_executor),
        review_agent=WeeklyReportReviewAgent(chat_model, tools=[skill_content_tool], tool_executor=tool_executor),
        report_render_tool=WeeklyReportRenderTool(artifact_service),
        memory_tool=artifact_service.memory_tool,
        skill_selector=skill_selector,
        task_type_loader=lambda task_id: str(artifact_service.get_task_index(task_id)["task_type"]),
    )
    memory_evolution_agent = memory_evolution_agent or MemoryEvolutionAgent(
        chat_model,
        artifact_service,
        tool_executor=tool_executor,
    )
    replace_kwargs: dict[str, Any] = {}
    if graph_nodes.memory_tool is None:
        replace_kwargs["memory_tool"] = artifact_service.memory_tool
    if graph_nodes.skill_selector is None:
        replace_kwargs["skill_selector"] = skill_selector
    if graph_nodes.task_type_loader is None:
        replace_kwargs["task_type_loader"] = lambda task_id: str(artifact_service.get_task_index(task_id)["task_type"])
    if graph_nodes.observability_provider is None:
        replace_kwargs["observability_provider"] = observability_provider
    if graph_nodes.trace_service is None:
        replace_kwargs["trace_service"] = trace_service
    if replace_kwargs:
        graph_nodes = replace(graph_nodes, **replace_kwargs)
    task_execution_service = TaskExecutionService(
        artifact_service=artifact_service,
        task_runner=runner,
        material_preprocess_tool=material_preprocess_tool,
        graph_nodes=graph_nodes,
        checkpointer=graph_checkpointer,
        trace_service=trace_service,
        observability_provider=observability_provider,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info(
            "WeeklyReport API startup.",
            extra={"event": "app_startup", "component": "api"},
        )
        app.state.config_service = config_service
        app.state.settings = settings
        app.state.task_runner = runner
        app.state.artifact_service = artifact_service
        app.state.project_artifact_service = project_artifact_service
        app.state.task_query_service = task_query_service
        app.state.workbench_query_service = workbench_query_service
        app.state.task_execution_service = task_execution_service
        app.state.task_operation_service = task_operation_service
        app.state.task_memory = artifact_service.task_memory
        app.state.memory_tool = artifact_service.memory_tool
        app.state.material_preprocess_tool = material_preprocess_tool
        app.state.graph_nodes = graph_nodes
        app.state.memory_evolution_agent = memory_evolution_agent
        app.state.trace_service = trace_service
        app.state.structured_trace_service = structured_trace_service
        app.state.observability_provider = observability_provider
        app.state.skill_memory = skill_memory
        app.state.graph_checkpointer = graph_checkpointer
        try:
            yield
        finally:
            logger.info(
                "WeeklyReport API shutdown.",
                extra={"event": "app_shutdown", "component": "api"},
            )
            try:
                observability_provider.flush()
            except Exception:
                logger.exception(
                    "Failed to flush observability provider during shutdown.",
                    extra={"event": "observability_flush_failed", "component": "observability"},
                )
            runner.shutdown(wait=False)
            checkpoint_context.__exit__(None, None, None)

    app = FastAPI(
        title=settings.app.name,
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(tasks_router)
    app.include_router(trace_router)
    app.include_router(artifacts_router)
    app.include_router(skills_router)

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        query_service_reachable = observability_provider.is_query_service_available()
        return {
            "status": "ok",
            "app_name": settings.app.name,
            "environment": settings.app.environment,
            "model_provider": settings.model.provider,
            "artifact_root": str(settings.storage.artifact_root),
            "task_runner_backend": settings.task_runner.backend,
            "observability_provider": observability_provider.provider_name,
            "observability_enabled": observability_provider.enabled,
            "observability_query_service_reachable": query_service_reachable,
            "observability_query_client_available": query_service_reachable,
        }

    return app


app = create_app()
