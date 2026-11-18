from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from langchain_core.messages import AIMessage, HumanMessage

from app.observability import (
    NoOpObservabilityProvider,
    ObservabilityProvider,
    push_trace_context,
)
from app.schemas.evidence import ArtifactKind, ArtifactRef, MaterialPack
from app.schemas.report import ReportSection
from app.schemas.review import WorkflowDecision
from app.schemas.task import TaskStatus, TaskType
from app.schemas.task_api import (
    TaskBoardSummary,
    TaskCreatePayload,
    TaskCreateResponse,
    TaskListResponse,
)
from app.schemas.trace import TraceLogLevel, TraceStatus
from app.schemas.task_operation import (
    TaskOperationResponse,
    TaskOperationType,
)
from app.schemas.workbench import (
    WeeklyReportVersionResponse,
    TaskWorkbenchResponse,
    WorkbenchEventsResponse,
)
from app.services.artifact_service import ArtifactService, StorageGroup
from app.services.config_service import ObservabilitySettings
from app.services.llm_image_content import build_chat_image_content
from app.services.task_execution_service import (
    TaskControlError,
    TaskExecutionService,
    TaskNotFoundError,
)
from app.services.task_query_service import TaskQueryService
from app.services.task_operation_service import (
    ActiveTaskOperationError,
    TaskOperationService,
)
from app.services.trace_service import TraceService
from app.services.workbench_query_service import WorkbenchQueryService
from app.tools.material_preprocess_tool import MaterialPreprocessTool
from app.workflows.graph_io import (
    build_current_report_loader,
    build_material_pack_saver,
    load_material_pack_from_manifest_ref,
    merge_material_packs,
)
from app.workflows.graph_state import TaskState, WorkflowNode, WorkflowWaitReason
from app.workflows.weekly_report_graph import build_weekly_report_graph

router = APIRouter(prefix="/api/tasks", tags=["tasks"])
MAX_SUPPLEMENT_ROUNDS = 4


def _artifact_service_from(request: Request) -> ArtifactService:
    return request.app.state.artifact_service  # type: ignore[return-value]


def _task_query_service_from(request: Request) -> TaskQueryService:
    return request.app.state.task_query_service  # type: ignore[return-value]


def _workbench_query_service_from(request: Request) -> WorkbenchQueryService:
    return request.app.state.workbench_query_service  # type: ignore[return-value]


def _task_execution_service_from(request: Request) -> TaskExecutionService:
    return request.app.state.task_execution_service  # type: ignore[return-value]


def _task_operation_service_from(request: Request) -> TaskOperationService:
    return request.app.state.task_operation_service  # type: ignore[return-value]


def _task_runner_from(request: Request):
    return request.app.state.task_runner


def _material_preprocess_tool_from(request: Request) -> MaterialPreprocessTool:
    return request.app.state.material_preprocess_tool  # type: ignore[return-value]


def _graph_nodes_from(request: Request):
    return request.app.state.graph_nodes


def _memory_evolution_agent_from(request: Request):
    return getattr(request.app.state, "memory_evolution_agent", None)


def _trace_service_from(request: Request) -> TraceService:
    return request.app.state.trace_service  # type: ignore[return-value]


def _observability_provider_from(request: Request) -> ObservabilityProvider:
    return request.app.state.observability_provider  # type: ignore[return-value]


def _graph_checkpointer_from(request: Request):
    return getattr(request.app.state, "graph_checkpointer", None)


def _select_skill_briefs(graph_nodes, task_id: str, agent_stage: str):
    if getattr(graph_nodes, "skill_selector", None) is None or getattr(graph_nodes, "task_type_loader", None) is None:
        return []
    task_type = graph_nodes.task_type_loader(task_id)
    if task_type is None:
        return []
    return graph_nodes.skill_selector.select_skills(task_type=task_type, agent_stage=agent_stage)


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    request: Request,
    q: str | None = Query(default=None),
    status: TaskStatus | None = Query(default=None),
    task_type: TaskType | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> TaskListResponse:
    return _task_query_service_from(request).list_tasks(
        query=q,
        status=status,
        task_type=task_type,
        page=page,
        page_size=page_size,
    )


@router.get("/summary", response_model=TaskBoardSummary)
async def get_task_summary(request: Request) -> TaskBoardSummary:
    return _task_query_service_from(request).summary()


@router.post("", response_model=TaskCreateResponse)
async def create_task(
    request: Request,
    task_name: str = Form(..., min_length=1, max_length=200),
    task_type: TaskType = Form(...),
    template_name: str | None = Form(default=None, max_length=120),
    notes: str | None = Form(default=None),
    files: list[UploadFile] = File(default=[]),
) -> TaskCreateResponse:
    if notes is None or not notes.strip():
        raise HTTPException(status_code=422, detail="Project notes are required.")
    payload = TaskCreatePayload(
        task_name=task_name,
        task_type=task_type,
        template_name=template_name,
        notes=notes,
    )
    prepared_uploads: list[tuple[str, ArtifactKind, bytes]] = []
    for upload in files:
        filename = upload.filename or "input.bin"
        kind = _artifact_kind_for_task_upload(filename, upload.content_type)
        content = await upload.read()
        if not content:
            raise HTTPException(status_code=422, detail="Uploaded input file is empty.")
        prepared_uploads.append((filename, kind, content))

    artifact_service = _artifact_service_from(request)
    task_id = artifact_service.create_task(
        task_name=payload.task_name,
        task_type=payload.task_type,
        metadata={
            key: value
            for key, value in {
                "notes": payload.notes,
                "template_name": payload.template_name,
            }.items()
            if value is not None
        },
    )

    input_artifact_refs: list[ArtifactRef] = []
    for filename, kind, content in prepared_uploads:
        input_artifact_refs.append(
            artifact_service.save_bytes(
                task_id=task_id,
                kind=kind,
                filename=filename,
                content=content,
                storage_group=StorageGroup.UPLOAD,
            )
        )

    initial_state = TaskState(
        task_id=task_id,
        thread_id=f"thread_{task_id}",
        run_id=f"run_{uuid4().hex[:16]}",
        task_status=TaskStatus.CREATED,
        input_artifact_refs=input_artifact_refs,
    )
    artifact_service.save_workflow_state(
        task_id,
        initial_state,
        status=TaskStatus.CREATED,
    )
    return TaskCreateResponse(task=_task_query_service_from(request).get_task(task_id))


@router.get("/{task_id}", response_model=TaskWorkbenchResponse)
async def get_task(task_id: str, request: Request) -> TaskWorkbenchResponse:
    try:
        return _workbench_query_service_from(request).get_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{task_id}/events", response_model=WorkbenchEventsResponse)
async def get_task_events(
    task_id: str,
    request: Request,
    cursor: str | None = Query(default=None),
) -> WorkbenchEventsResponse:
    try:
        return _workbench_query_service_from(request).list_events(task_id, cursor)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{task_id}/weekly-report-versions", response_model=list[WeeklyReportVersionResponse])
async def get_task_report_versions(task_id: str, request: Request) -> list[WeeklyReportVersionResponse]:
    try:
        _workbench_query_service_from(request).get_task(task_id)
        return _workbench_query_service_from(request).list_versions(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{task_id}/artifacts/{artifact_id}/content")
async def get_task_artifact_content(task_id: str, artifact_id: str, request: Request) -> FileResponse:
    """Serve task-owned media without exposing filesystem paths."""
    try:
        artifact = next(
            item
            for item in _artifact_service_from(request).list_task_artifacts(task_id)
            if item["artifact_id"] == artifact_id
        )
    except StopIteration as exc:
        raise HTTPException(status_code=404, detail="Task artifact not found.") from exc

    path = Path(str(artifact["absolute_path"]))
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Task artifact content is unavailable.")
    if not str(artifact["media_type"]).startswith("image/"):
        raise HTTPException(status_code=404, detail="Task artifact is not an image.")
    return FileResponse(path, media_type=str(artifact["media_type"]), filename=path.name)


def _run_task_control(request: Request, task_id: str, action: str) -> TaskWorkbenchResponse:
    service = _task_execution_service_from(request)
    try:
        getattr(service, action)(task_id)
        return _workbench_query_service_from(request).get_task(task_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TaskControlError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{task_id}/start", response_model=TaskWorkbenchResponse)
async def start_task(task_id: str, request: Request) -> TaskWorkbenchResponse:
    return _run_task_control(request, task_id, "start")


@router.post("/{task_id}/pause", response_model=TaskWorkbenchResponse)
async def pause_task(task_id: str, request: Request) -> TaskWorkbenchResponse:
    return _run_task_control(request, task_id, "pause")


@router.post("/{task_id}/resume", response_model=TaskWorkbenchResponse)
async def resume_task(task_id: str, request: Request) -> TaskWorkbenchResponse:
    return _run_task_control(request, task_id, "resume")


@router.post("/{task_id}/cancel", response_model=TaskWorkbenchResponse)
async def cancel_task(task_id: str, request: Request) -> TaskWorkbenchResponse:
    return _run_task_control(request, task_id, "cancel")


@router.post("/{task_id}/confirm", response_model=TaskWorkbenchResponse, status_code=202)
async def confirm_final_review(task_id: str, request: Request) -> TaskWorkbenchResponse:
    artifact_service = _artifact_service_from(request)
    state = _load_workflow_state_or_404(artifact_service, task_id)
    if (
        state.task_status != TaskStatus.WAITING
        or state.waiting_reason != WorkflowWaitReason.HUMAN
        or state.latest_review_result is None
        or (
            state.latest_review_result.decision.value != "approved"
            and not (
                state.latest_review_result.decision.value == "revise_report"
                and state.review_round >= state.max_review_rounds
            )
        )
    ):
        raise HTTPException(status_code=409, detail="Task is not waiting for final review confirmation.")

    runtime = artifact_service.memory_tool.start_agent_runtime(
        task_id=task_id,
        workflow_node="human_feedback",
        agent_name="User",
        round_index=state.review_round + 1,
    )
    artifact_service.memory_tool.append_messages(
        runtime,
        [
            HumanMessage(
                content="已确认最终周报",
                additional_kwargs={"request_id": f"confirm_{uuid4().hex[:16]}"},
            )
        ],
    )
    artifact_service.memory_tool.complete_agent_session(
        runtime,
        {"confirmation": "approved", "review_round": state.review_round},
    )
    queued = state.model_copy(
        update={
            "task_status": TaskStatus.QUEUED,
            "waiting_reason": None,
            "last_completed_node": WorkflowNode.WAIT_FOR_HUMAN,
            "updated_at": datetime.now(UTC),
        }
    )
    artifact_service.save_workflow_state(task_id, queued.model_dump(mode="json"), status=TaskStatus.QUEUED)
    _task_runner_from(request).submit(
        f"confirm:{task_id}",
        _run_workflow,
        artifact_service,
        _graph_nodes_from(request),
        queued,
        memory_evolution_agent=_memory_evolution_agent_from(request),
        trace_service=_trace_service_from(request),
        observability_provider=_observability_provider_from(request),
        checkpointer=_graph_checkpointer_from(request),
    )
    return _workbench_query_service_from(request).get_task(task_id)


@router.post("/{task_id}/supplements", response_model=TaskOperationResponse, status_code=202)
async def upload_supplement(
    task_id: str,
    request: Request,
    file: UploadFile = File(...),
    request_id: str = Form(..., min_length=1, max_length=128),
    section_id: ReportSection | None = Form(default=None),
    note: str | None = Form(default=None),
) -> TaskOperationResponse:
    form = await request.form()
    if len(form.getlist("file")) > 1:
        raise HTTPException(status_code=400, detail="Only one supplement file is allowed.")
    return await _queue_supplement_operation(
        task_id=task_id,
        request=request,
        file=file,
        request_id=request_id,
        section_id=section_id,
        note=note,
    )


@router.post("/{task_id}/feedback", response_model=TaskOperationResponse, status_code=202)
async def submit_human_feedback(
    task_id: str,
    request: Request,
    feedback_text: str = Form(..., min_length=1),
    request_id: str = Form(..., min_length=1, max_length=128),
    file: UploadFile | None = File(default=None),
) -> TaskOperationResponse:
    form = await request.form()
    if len(form.getlist("file")) > 1:
        raise HTTPException(status_code=400, detail="Only one feedback file is allowed.")
    return await _queue_feedback_operation(
        task_id=task_id,
        request=request,
        feedback_text=feedback_text,
        request_id=request_id,
        file=file,
    )


async def _queue_supplement_operation(
    *,
    task_id: str,
    request: Request,
    file: UploadFile,
    request_id: str,
    section_id: str | None,
    note: str | None,
) -> TaskOperationResponse:
    artifact_service = _artifact_service_from(request)
    operation_service = _task_operation_service_from(request)
    existing = operation_service.get_by_request(task_id, request_id)
    if existing is not None:
        return existing
    state = _load_workflow_state_or_404(artifact_service, task_id)
    if state.task_status != TaskStatus.WAITING or state.waiting_reason != WorkflowWaitReason.EVIDENCE:
        raise HTTPException(status_code=409, detail="Task is not waiting for supplementary evidence.")
    if state.supplement_round >= MAX_SUPPLEMENT_ROUNDS:
        raise HTTPException(status_code=409, detail="Supplement evidence limit of four rounds has been reached.")
    try:
        operation, created = operation_service.create_or_get(
            task_id=task_id,
            request_id=request_id,
            operation_type=TaskOperationType.SUPPLEMENT,
            input_data={"section_id": section_id, "note": note},
            previous_state=state.model_dump(mode="json"),
        )
    except ActiveTaskOperationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not created:
        return operation
    try:
        upload_artifact = await _save_upload_artifact(
            artifact_service=artifact_service,
            task_id=task_id,
            upload=file,
            request_id=request_id,
            section_id=section_id,
            note=note,
        )
        operation_service.update_input(
            operation.operation_id,
            {
                "section_id": section_id,
                "note": note,
                "artifact": upload_artifact.model_dump(mode="json"),
            },
        )
        supplement_runtime = artifact_service.memory_tool.start_agent_runtime(
            task_id=task_id,
            workflow_node="human_feedback",
            agent_name="User",
            round_index=state.review_round + 1,
        )
        supplement_filename = file.filename or "supplement.bin"
        supplement_message = note.strip() if note and note.strip() else "提交补充证据"
        supplement_message = f"{supplement_message}\n[补充证据：{supplement_filename}]"
        artifact_service.memory_tool.append_messages(
            supplement_runtime,
            [HumanMessage(content=supplement_message, additional_kwargs={"request_id": request_id})],
        )
        artifact_service.memory_tool.complete_agent_session(
            supplement_runtime,
            {"note": note or "", "artifact": upload_artifact.model_dump(mode="json")},
        )
        _task_runner_from(request).submit(
            operation.operation_id,
            _run_supplement_operation,
            operation.operation_id,
            artifact_service,
            _graph_nodes_from(request),
            _material_preprocess_tool_from(request),
            _memory_evolution_agent_from(request),
            _trace_service_from(request),
            _observability_provider_from(request),
            _graph_checkpointer_from(request),
            operation_service,
        )
    except HTTPException as exc:
        _fail_operation(operation_service, artifact_service, operation.operation_id, exc)
        raise
    except Exception as exc:
        _fail_operation(operation_service, artifact_service, operation.operation_id, exc)
        raise HTTPException(status_code=500, detail="Supplement job could not be queued.") from exc
    return operation


async def _queue_feedback_operation(
    *,
    task_id: str,
    request: Request,
    feedback_text: str,
    request_id: str,
    file: UploadFile | None,
) -> TaskOperationResponse:
    artifact_service = _artifact_service_from(request)
    operation_service = _task_operation_service_from(request)
    graph_nodes = _graph_nodes_from(request)
    existing = operation_service.get_by_request(task_id, request_id)
    if existing is not None:
        return existing
    state = _load_workflow_state_or_404(artifact_service, task_id)
    if state.task_status != TaskStatus.WAITING or state.waiting_reason not in {
        WorkflowWaitReason.HUMAN,
        WorkflowWaitReason.EVIDENCE,
    }:
        raise HTTPException(status_code=409, detail="Task is not waiting for human feedback or evidence.")
    if graph_nodes.review_agent is None:
        raise HTTPException(status_code=500, detail="Review agent is not configured.")
    feedback_content: list[dict[str, object]] = []
    if file is not None:
        media_type = (file.content_type or "").lower()
        if not media_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="Chat feedback attachments must be images.")
        content = await file.read()
        if not content:
            raise HTTPException(status_code=422, detail="Chat feedback image is empty.")
        feedback_content.append(build_chat_image_content(content, media_type))
    try:
        operation, created = operation_service.create_or_get(
            task_id=task_id,
            request_id=request_id,
            operation_type=TaskOperationType.FEEDBACK,
            input_data={
                "request_id": request_id,
                "feedback_text": feedback_text,
                "feedback_content": feedback_content,
            },
            previous_state=state.model_dump(mode="json"),
        )
    except ActiveTaskOperationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not created:
        return operation
    feedback_runtime = None
    try:
        feedback_runtime = artifact_service.memory_tool.start_agent_runtime(
            task_id=task_id,
            workflow_node="human_feedback",
            agent_name=graph_nodes.review_agent.__class__.__name__,
            round_index=state.review_round + 1,
        )
        message_content: str | list[dict[str, object]] = feedback_text
        if feedback_content:
            message_content = [{"type": "text", "text": feedback_text}, *feedback_content]
        artifact_service.memory_tool.append_messages(
            feedback_runtime,
            [HumanMessage(content=message_content, additional_kwargs={"request_id": request_id})],
        )
        _task_runner_from(request).submit(
            operation.operation_id,
            _run_feedback_operation,
            operation.operation_id,
            feedback_runtime,
            artifact_service,
            graph_nodes,
            _memory_evolution_agent_from(request),
            _trace_service_from(request),
            _observability_provider_from(request),
            _graph_checkpointer_from(request),
            operation_service,
        )
    except Exception as exc:
        if feedback_runtime is not None:
            artifact_service.memory_tool.fail_agent_session(feedback_runtime, exc)
        _fail_operation(operation_service, artifact_service, operation.operation_id, exc)
        raise HTTPException(status_code=500, detail="Feedback job could not be queued.") from exc
    return operation


def _run_feedback_operation(
    operation_id: str,
    feedback_runtime,
    artifact_service: ArtifactService,
    graph_nodes,
    memory_evolution_agent,
    trace_service: TraceService,
    observability_provider: ObservabilityProvider,
    checkpointer,
    operation_service: TaskOperationService,
) -> None:
    operation_service.mark_processing(operation_id, stage="review_feedback")
    completed_session = False
    try:
        state = _load_workflow_state_or_404(artifact_service, feedback_runtime.task_id)
        input_data = operation_service.get_input(operation_id)
        feedback_text = str(input_data.get("feedback_text") or "")
        current_report_loader = build_current_report_loader(artifact_service)
        report_document = current_report_loader(state) if state.current_report_version_ref is not None else None
        material_pack = (
            load_material_pack_from_manifest_ref(state.material_pack_ref)
            if state.material_pack_ref is not None
            else None
        )
        task_index = artifact_service.get_task_index(state.task_id)
        task_metadata = task_index.get("metadata")
        task_notes = task_metadata.get("notes") if isinstance(task_metadata, dict) else None
        feedback_review = graph_nodes._call_agent_method(
            graph_nodes.review_agent.structure_human_feedback,
            task_id=state.task_id,
            feedback_text=feedback_text,
            feedback_content=input_data.get("feedback_content") or None,
            report_document=report_document,
            task_name=str(task_index.get("task_name") or "") or None,
            task_notes=str(task_notes) if task_notes else None,
            material_pack=material_pack,
            skill_briefs=_select_skill_briefs(graph_nodes, state.task_id, "review"),
            runtime=feedback_runtime,
            messages=state.report_review_messages,
            supplement_round=state.supplement_round,
            max_supplement_rounds=MAX_SUPPLEMENT_ROUNDS,
        )
        restart_review_cycle = feedback_review.decision == WorkflowDecision.REVISE_REPORT
        agent_messages = getattr(graph_nodes.review_agent, "last_turn_messages", None) or []
        if agent_messages:
            artifact_service.memory_tool.append_messages(feedback_runtime, agent_messages)
        review_result_message = AIMessage(content=feedback_review.model_dump_json())
        artifact_service.memory_tool.append_messages(feedback_runtime, [review_result_message])
        artifact_service.memory_tool.complete_agent_session(
            feedback_runtime,
            {
                "feedback_text": feedback_text,
                "review_result": feedback_review.model_dump(mode="json"),
                "feedback_has_image": bool(input_data.get("feedback_content")),
            },
        )
        completed_session = True
        resumed_state = state.model_copy(
            update={
                "task_status": TaskStatus.QUEUED,
                "waiting_reason": None,
                "workflow_decision": feedback_review.decision,
                "latest_review_result": feedback_review,
                "review_round": 0 if restart_review_cycle else state.review_round,
                "supplement_round": state.supplement_round,
                "revision_instructions": feedback_text if restart_review_cycle else None,
                "report_review_messages": [
                    *state.report_review_messages,
                    HumanMessage(
                        content=(
                            [{"type": "text", "text": feedback_text}, *(
                                input_data.get("feedback_content") or []
                            )]
                            if input_data.get("feedback_content")
                            else feedback_text
                        ),
                        additional_kwargs={"request_id": str(input_data.get("request_id") or "")},
                    ),
                    review_result_message,
                ],
                "last_completed_node": WorkflowNode.REVIEW_REPORT,
                "updated_at": datetime.now(UTC),
            }
        )
        artifact_service.save_workflow_state(
            state.task_id,
            resumed_state.model_dump(mode="json"),
            status=resumed_state.task_status,
        )
        operation_service.update_stage(operation_id, stage="workflow")
        final_state = _run_workflow(
            artifact_service,
            graph_nodes,
            resumed_state,
            memory_evolution_agent=memory_evolution_agent,
            trace_service=trace_service,
            observability_provider=observability_provider,
            checkpointer=checkpointer,
        )
        operation_service.mark_completed(
            operation_id,
            stage="completed",
            result=final_state.model_dump(mode="json"),
        )
    except Exception as exc:
        if not completed_session:
            try:
                artifact_service.memory_tool.fail_agent_session(feedback_runtime, exc)
            except Exception:
                pass
        _fail_operation(operation_service, artifact_service, operation_id, exc)


def _run_supplement_operation(
    operation_id: str,
    artifact_service: ArtifactService,
    graph_nodes,
    preprocess_tool: MaterialPreprocessTool,
    memory_evolution_agent,
    trace_service: TraceService,
    observability_provider: ObservabilityProvider,
    checkpointer,
    operation_service: TaskOperationService,
) -> None:
    operation_service.mark_processing(operation_id, stage="preprocess_supplement")
    try:
        operation = operation_service.get(operation_id)
        input_data = operation_service.get_input(operation_id)
        state = _load_workflow_state_or_404(artifact_service, operation.task_id)
        artifact = ArtifactRef.model_validate(input_data["artifact"])
        supplement_pack = _preprocess_supplement(
            preprocess_tool=preprocess_tool,
            task_id=state.task_id,
            task_type=_load_task_type(artifact_service, state.task_id),
            artifact=artifact,
            note=input_data.get("note"),
        )
        operation_service.update_stage(operation_id, stage="merge_evidence")
        merged_pack = merge_material_packs(
            load_material_pack_from_manifest_ref(_require_material_pack_ref(state)),
            supplement_pack,
        )
        if graph_nodes.visual_understanding_agent is None:
            raise RuntimeError("A vision model is required to analyze attached images.")
        updated_pack = graph_nodes.visual_understanding_agent.understand_material_pack(merged_pack)
        merged_manifest = build_material_pack_saver(artifact_service)(state, updated_pack)
        resumed_state = state.model_copy(
            update={
                "task_status": TaskStatus.QUEUED,
                "waiting_reason": None,
                "material_pack_ref": merged_manifest,
                "supplement_artifact_refs": [*state.supplement_artifact_refs, artifact],
                "supplement_round": state.supplement_round + 1,
                "review_round": 0,
                "updated_at": datetime.now(UTC),
            }
        )
        artifact_service.append_supplement_link(
            state.task_id,
            artifact_id=artifact.artifact_id,
            request_id=operation.request_id,
            section_id=input_data.get("section_id"),
            evidence_refs=_evidence_refs_from_pack(supplement_pack),
        )
        artifact_service.save_workflow_state(
            state.task_id,
            resumed_state.model_dump(mode="json"),
            status=resumed_state.task_status,
        )
        operation_service.update_stage(operation_id, stage="workflow")
        final_state = _run_workflow(
            artifact_service,
            graph_nodes,
            resumed_state,
            memory_evolution_agent=memory_evolution_agent,
            trace_service=trace_service,
            observability_provider=observability_provider,
            checkpointer=checkpointer,
        )
        operation_service.mark_completed(
            operation_id,
            stage="completed",
            result=final_state.model_dump(mode="json"),
        )
    except Exception as exc:
        _fail_operation(operation_service, artifact_service, operation_id, exc)


def _fail_operation(
    operation_service: TaskOperationService,
    artifact_service: ArtifactService,
    operation_id: str,
    error: Exception,
) -> None:
    try:
        operation = operation_service.get(operation_id)
        previous_state = operation_service.get_previous_state(operation_id)
        current_state = artifact_service.load_workflow_state(operation.task_id)
        if previous_state is not None and current_state is not None:
            current_status = TaskStatus(current_state.get("task_status"))
            if current_status not in {
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELED,
            }:
                artifact_service.save_workflow_state(
                    operation.task_id,
                    previous_state,
                    status=TaskStatus(previous_state.get("task_status", TaskStatus.WAITING.value)),
                )
        operation_service.mark_failed(operation_id, stage="failed", error_message=str(error))
    except Exception:
        return


def _load_workflow_state_or_404(artifact_service: ArtifactService, task_id: str) -> TaskState:
    try:
        task_snapshot = artifact_service.load_workflow_state(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if task_snapshot is None:
        raise HTTPException(status_code=404, detail=f"No workflow state found for task id: {task_id}")
    return TaskState.model_validate(task_snapshot)


def _load_task_type(artifact_service: ArtifactService, task_id: str) -> TaskType:
    task_index = artifact_service.get_task_index(task_id)
    return TaskType(task_index["task_type"])


async def _save_upload_artifact(
    *,
    artifact_service: ArtifactService,
    task_id: str,
    upload: UploadFile,
    request_id: str | None,
    section_id: str | None,
    note: str | None,
) -> ArtifactRef:
    content = await upload.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded supplement file is empty.")

    filename = upload.filename or "supplement.bin"
    kind = _artifact_kind_for_media_upload(filename, upload.content_type)
    return artifact_service.save_bytes(
        task_id=task_id,
        kind=kind,
        filename=filename,
        content=content,
        storage_group=StorageGroup.UPLOAD,
        metadata={
            "request_id": request_id or "",
            "section_id": section_id or "",
            "note": note or "",
            "is_supplement": "true",
        },
    )


def _preprocess_supplement(
    *,
    preprocess_tool: MaterialPreprocessTool,
    task_id: str,
    task_type: TaskType,
    artifact: ArtifactRef,
    note: str | None,
) -> MaterialPack:
    if artifact.kind == ArtifactKind.IMAGE_MATERIAL:
        return preprocess_tool.preprocess_image(
            task_id=task_id,
            source_image=artifact,
            task_type=task_type,
            note=note,
        )
    raise HTTPException(status_code=400, detail="Unsupported supplement file type.")


def _artifact_kind_for_media_upload(filename: str, content_type: str | None) -> ArtifactKind:
    return _artifact_kind_for_task_upload(filename, content_type)


def _artifact_kind_for_task_upload(filename: str, content_type: str | None) -> ArtifactKind:
    suffix = Path(filename).suffix.lower()
    media_type = (content_type or "").lower()
    allowed_types = {"image/png", "image/jpeg", "image/webp"}
    allowed_suffixes = {".png", ".jpg", ".jpeg", ".webp"}
    if suffix not in allowed_suffixes or (media_type and media_type not in allowed_types):
        raise HTTPException(status_code=400, detail="Task attachments must be PNG, JPG, JPEG, or WebP images.")
    return ArtifactKind.IMAGE_MATERIAL


def _require_material_pack_ref(state: TaskState) -> ArtifactRef:
    if state.material_pack_ref is None:
        raise HTTPException(status_code=400, detail="Task does not have a base evidence pack.")
    return state.material_pack_ref


def _evidence_refs_from_pack(material_pack: MaterialPack) -> list[str]:
    return [material.material_id for material in material_pack.materials]


def _run_workflow(
    artifact_service: ArtifactService,
    graph_nodes,
    initial_state: TaskState,
    *,
    memory_evolution_agent=None,
    trace_service: TraceService | None = None,
    observability_provider: ObservabilityProvider | None = None,
    checkpointer=None,
) -> TaskState:
    trace_service = trace_service or TraceService(artifact_service.sqlite_path)
    observability_provider = observability_provider or NoOpObservabilityProvider(
        ObservabilitySettings(provider="none")
    )
    replace_kwargs: dict[str, object] = {}
    if getattr(graph_nodes, "trace_service", None) is None:
        replace_kwargs["trace_service"] = trace_service
    if getattr(graph_nodes, "observability_provider", None) is None:
        replace_kwargs["observability_provider"] = observability_provider
    if replace_kwargs:
        graph_nodes = replace(graph_nodes, **replace_kwargs)
    task_index = artifact_service.get_task_index(initial_state.task_id)
    initial_stage = _trace_stage_for_state(initial_state)
    trace = trace_service.start_task_trace(
        task_id=initial_state.task_id,
        current_stage=initial_stage,
        summary=_trace_summary_for_state(initial_state, stage=initial_stage),
    )
    trace_service.append_log(
        trace_id=trace.trace_id,
        level=TraceLogLevel.INFO,
        stage=initial_stage,
        event_type="workflow_started",
        message="Workflow execution started.",
        extra={
            "thread_id": initial_state.thread_id,
            "run_id": initial_state.run_id,
            "task_status": initial_state.task_status.value,
            "last_completed_node": (
                initial_state.last_completed_node.value
                if initial_state.last_completed_node is not None
                else None
            ),
        },
    )
    graph = build_weekly_report_graph(
        nodes=graph_nodes,
        checkpointer=checkpointer,
    )

    def invoke_graph() -> dict:
        with push_trace_context(
            task_id=initial_state.task_id,
            trace_id=trace.trace_id,
            workflow_node=initial_stage,
        ):
            sdk_client = observability_provider.get_sdk_client()
            if sdk_client is not None:
                sdk_client.update_current_trace(
                    name="weekly_report.workflow",
                    session_id=initial_state.thread_id,
                    metadata={
                        "task_id": initial_state.task_id,
                        "task_name": task_index["task_name"],
                        "task_type": task_index["task_type"],
                        "thread_id": initial_state.thread_id,
                        "run_id": initial_state.run_id,
                        "environment": observability_provider.environment,
                        "project_name": observability_provider.project_name,
                    },
                    input={
                        "task_id": initial_state.task_id,
                        "task_status": initial_state.task_status.value,
                        "waiting_reason": (
                            initial_state.waiting_reason.value
                            if initial_state.waiting_reason is not None
                            else None
                        ),
                        "last_completed_node": (
                            initial_state.last_completed_node.value
                            if initial_state.last_completed_node is not None
                            else None
                        ),
                    }
                    if observability_provider.capture_input
                    else None,
                )
            config = (
                {"configurable": {"thread_id": initial_state.thread_id}}
                if checkpointer is not None
                else None
            )
            result = graph.invoke(initial_state.model_dump(), config=config)
            if sdk_client is not None:
                final_state = TaskState.model_validate(result)
                sdk_client.update_current_trace(
                    output={
                        "task_status": final_state.task_status.value,
                        "waiting_reason": (
                            final_state.waiting_reason.value
                            if final_state.waiting_reason is not None
                            else None
                        ),
                        "last_completed_node": (
                            final_state.last_completed_node.value
                            if final_state.last_completed_node is not None
                            else None
                        ),
                    }
                    if observability_provider.capture_output
                    else None,
                )
            return result

    observed_invoke = observability_provider.observe(
        name="weekly_report.workflow",
        as_type="chain",
        capture_input=False,
        capture_output=False,
    )(invoke_graph)
    try:
        if observability_provider.enabled:
            result = observed_invoke(langfuse_trace_id=trace.trace_id)
        else:
            result = observed_invoke()
    except Exception as exc:
        failed_state = initial_state.model_copy(
            update={
                "task_status": TaskStatus.FAILED,
                "last_completed_node": WorkflowNode.MARK_FAILED,
                "updated_at": datetime.now(UTC),
            }
        )
        artifact_service.save_workflow_state(
            initial_state.task_id,
            failed_state.model_dump(mode="json"),
            status=TaskStatus.FAILED,
        )
        trace_service.append_log(
            trace_id=trace.trace_id,
            level=TraceLogLevel.ERROR,
            stage=WorkflowNode.MARK_FAILED.value,
            event_type="workflow_failed",
            message=f"Workflow execution failed: {exc}",
            extra={"error_type": type(exc).__name__},
        )
        trace_service.update_task_trace(
            trace_id=trace.trace_id,
            status=TraceStatus.FAILED,
            current_stage=WorkflowNode.MARK_FAILED.value,
            summary="Workflow execution failed before reaching a terminal review state.",
            error_message=str(exc),
        )
        raise HTTPException(status_code=500, detail=f"Workflow execution failed: {exc}") from exc

    final_state = TaskState.model_validate(result)
    artifact_service.save_workflow_state(
        final_state.task_id,
        final_state.model_dump(mode="json"),
        status=final_state.task_status,
    )
    final_trace_status = _trace_status_for_state(final_state)
    final_stage = _trace_stage_for_state(final_state)
    trace_service.append_log(
        trace_id=trace.trace_id,
        level=_trace_log_level_for_status(final_trace_status),
        stage=final_stage,
        event_type=_trace_event_type_for_status(final_trace_status),
        message=_trace_message_for_state(final_state),
        extra={
            "task_status": final_state.task_status.value,
            "waiting_reason": (
                final_state.waiting_reason.value
                if final_state.waiting_reason is not None
                else None
            ),
            "last_completed_node": (
                final_state.last_completed_node.value
                if final_state.last_completed_node is not None
                else None
            ),
        },
    )
    trace_service.update_task_trace(
        trace_id=trace.trace_id,
        status=final_trace_status,
        current_stage=final_stage,
        summary=_trace_summary_for_state(final_state, stage=final_stage),
        error_message=(
            "Workflow reached the failed terminal state."
            if final_trace_status == TraceStatus.FAILED
            else None
        ),
    )
    if (
        memory_evolution_agent is not None
        and final_state.task_status in {TaskStatus.COMPLETED, TaskStatus.FAILED}
    ):
        try:
            memory_evolution_agent.evolve_task_memory(
                task_id=final_state.task_id,
                final_state=final_state,
            )
        except Exception:
            pass
    observability_provider.flush()
    return final_state


def _trace_stage_for_state(state: TaskState) -> str:
    if state.last_completed_node is not None:
        return state.last_completed_node.value
    return WorkflowNode.START_WORKFLOW.value


def _trace_status_for_state(state: TaskState) -> TraceStatus:
    if state.task_status == TaskStatus.COMPLETED:
        return TraceStatus.COMPLETED
    if state.task_status == TaskStatus.FAILED:
        return TraceStatus.FAILED
    if state.task_status == TaskStatus.CANCELED:
        return TraceStatus.CANCELED
    if state.task_status == TaskStatus.WAITING:
        if state.waiting_reason == WorkflowWaitReason.HUMAN:
            return TraceStatus.WAITING_FOR_HUMAN
        return TraceStatus.WAITING_FOR_EVIDENCE
    return TraceStatus.RUNNING


def _trace_log_level_for_status(status: TraceStatus) -> TraceLogLevel:
    if status == TraceStatus.FAILED:
        return TraceLogLevel.ERROR
    if status in {TraceStatus.CANCELED, TraceStatus.WAITING_FOR_EVIDENCE, TraceStatus.WAITING_FOR_HUMAN}:
        return TraceLogLevel.WARNING
    return TraceLogLevel.INFO


def _trace_event_type_for_status(status: TraceStatus) -> str:
    mapping = {
        TraceStatus.RUNNING: "workflow_running",
        TraceStatus.WAITING_FOR_EVIDENCE: "workflow_waiting_for_evidence",
        TraceStatus.WAITING_FOR_HUMAN: "workflow_waiting_for_human",
        TraceStatus.COMPLETED: "workflow_completed",
        TraceStatus.FAILED: "workflow_failed",
        TraceStatus.CANCELED: "workflow_canceled",
    }
    return mapping[status]


def _trace_message_for_state(state: TaskState) -> str:
    status = _trace_status_for_state(state)
    messages = {
        TraceStatus.RUNNING: "Workflow is running.",
        TraceStatus.WAITING_FOR_EVIDENCE: "Workflow is waiting for supplementary evidence.",
        TraceStatus.WAITING_FOR_HUMAN: "Workflow is waiting for human feedback.",
        TraceStatus.COMPLETED: "Workflow completed successfully.",
        TraceStatus.FAILED: "Workflow reached the failed terminal state.",
        TraceStatus.CANCELED: "Workflow was canceled.",
    }
    return messages[status]


def _trace_summary_for_state(state: TaskState, *, stage: str) -> str:
    if state.task_status == TaskStatus.WAITING and state.waiting_reason == WorkflowWaitReason.HUMAN:
        return f"Workflow paused at {stage} and is waiting for human feedback."
    if state.task_status == TaskStatus.WAITING:
        return f"Workflow paused at {stage} and is waiting for supplementary evidence."
    if state.task_status == TaskStatus.COMPLETED:
        return f"Workflow completed at {stage}."
    if state.task_status == TaskStatus.FAILED:
        return f"Workflow failed at {stage}."
    if state.task_status == TaskStatus.CANCELED:
        return f"Workflow was canceled at {stage}."
    return f"Workflow is running at {stage}."
