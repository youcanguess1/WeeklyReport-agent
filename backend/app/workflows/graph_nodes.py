from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
import inspect
from typing import Any, TypeVar

from langgraph.graph import END
from langgraph.types import Command
from langchain_core.messages import AIMessage

from app.agents.report_review_agent import WeeklyReportReviewAgent
from app.agents.report_writer_agent import WeeklyReportWriterAgent
from app.agents.visual_understanding_agent import VisualUnderstandingAgent
from app.memory.memory_tool import AgentSessionRuntime, MemoryTool
from app.memory.skill_selector import SkillSelector
from app.observability import (
    NoOpObservabilityProvider,
    ObservabilityProvider,
    get_current_trace_context,
    push_trace_context,
)
from app.schemas.evidence import ArtifactKind, ArtifactRef
from app.schemas.memory import SkillBrief
from app.schemas.review import ReviewResult, WorkflowDecision
from app.schemas.report import WeeklyReportDocument
from app.schemas.task import TaskStatus
from app.schemas.trace import TraceLogLevel
from app.services.config_service import ObservabilitySettings
from app.services.trace_service import TraceService
from app.tools.report_render_tool import WeeklyReportRenderTool
from app.workflows.graph_io import MaterialPackLoader, MaterialPackSaver, WeeklyReportDocumentLoader
from app.workflows.graph_state import TaskState, WorkflowNode, WorkflowWaitReason, utc_now

NodeResult = TypeVar("NodeResult")


class MockDecisionProvider:
    def __init__(self, decisions: Sequence[WorkflowDecision]) -> None:
        self._decisions = list(decisions)
        self._index = 0

    def __call__(self, state: TaskState) -> WorkflowDecision:
        if not self._decisions:
            return WorkflowDecision.APPROVED
        decision = self._decisions[min(self._index, len(self._decisions) - 1)]
        self._index += 1
        return decision


TaskTypeLoader = Callable[[str], str | None]


@dataclass(frozen=True)
class WeeklyReportGraphNodes:
    review_decision_provider: Callable[[TaskState], WorkflowDecision] = field(
        default_factory=lambda: MockDecisionProvider([WorkflowDecision.APPROVED])
    )
    material_pack_loader: MaterialPackLoader | None = None
    material_pack_saver: MaterialPackSaver | None = None
    current_report_loader: WeeklyReportDocumentLoader | None = None
    visual_understanding_agent: VisualUnderstandingAgent | None = None
    writer_agent: WeeklyReportWriterAgent | None = None
    review_agent: WeeklyReportReviewAgent | None = None
    report_render_tool: WeeklyReportRenderTool | None = None
    memory_tool: MemoryTool | None = None
    skill_selector: SkillSelector | None = None
    task_type_loader: TaskTypeLoader | None = None
    observability_provider: ObservabilityProvider | None = None
    trace_service: TraceService | None = None

    def start_workflow(self, state: TaskState) -> Command:
        return self._observe_node(
            state,
            WorkflowNode.START_WORKFLOW,
            lambda: Command(
                update={
                    "task_status": TaskStatus.RUNNING,
                    "waiting_reason": None,
                    "last_completed_node": WorkflowNode.START_WORKFLOW,
                    "updated_at": utc_now(),
                },
                goto=self._start_target_for(state),
            ),
        )

    def material_understanding(self, state: TaskState) -> Command:
        def execute() -> Command:
            if state.cancel_requested:
                return self._mark_canceled_command()
            if self.visual_understanding_agent is None:
                raise RuntimeError("A vision model is required to analyze attached images.")
            if self.visual_understanding_agent is not None and self.material_pack_loader is not None:
                return self._run_visual_understanding_agent(state)
            return Command(
                update={
                    "last_completed_node": WorkflowNode.MATERIAL_UNDERSTANDING,
                    "updated_at": utc_now(),
                },
                goto=WorkflowNode.WRITE_REPORT.value,
            )

        return self._observe_node(
            state,
            WorkflowNode.MATERIAL_UNDERSTANDING,
            execute,
        )

    def write_report(self, state: TaskState) -> Command:
        def execute() -> Command:
            if state.cancel_requested:
                return self._mark_canceled_command()
            if (
                self.writer_agent is not None
                and self.material_pack_loader is not None
                and self.report_render_tool is not None
            ):
                return self._write_report_with_agents(state)

            writer_round = state.writer_round + 1
            report_ref = _mock_artifact_ref(
                artifact_id=f"mock_report_{writer_round}",
                kind=ArtifactKind.WEEKLY_REPORT_MARKDOWN,
                path=f"mock://{state.task_id}/report_v{writer_round}.md",
                media_type="text/markdown",
            )
            return Command(
                update={
                    "writer_round": writer_round,
                    "current_report_version_ref": report_ref,
                    "report_version_history": [*state.report_version_history, report_ref],
                    "last_completed_node": WorkflowNode.WRITE_REPORT,
                    "updated_at": utc_now(),
                },
                goto=WorkflowNode.REVIEW_REPORT.value,
            )

        return self._observe_node(
            state,
            WorkflowNode.WRITE_REPORT,
            execute,
        )

    def review_report(self, state: TaskState) -> Command:
        def execute() -> Command:
            if state.cancel_requested:
                return self._mark_canceled_command()
            if self.review_agent is not None and self.current_report_loader is not None:
                return self._review_report_with_agent(state)

            review_round = state.review_round + 1
            decision = self.review_decision_provider(state)
            review_result = ReviewResult(
                review_id=f"mock_review_{review_round}",
                task_id=state.task_id,
                decision=decision,
                summary=f"Mock review decision: {decision.value}",
                created_at=utc_now(),
            )
            return Command(
                update={
                    "review_round": review_round,
                    "workflow_decision": decision,
                    "latest_review_result": review_result,
                    "last_completed_node": WorkflowNode.REVIEW_REPORT,
                    "updated_at": utc_now(),
                },
                goto=self._review_next_node(state, decision, review_round),
            )

        return self._observe_node(
            state,
            WorkflowNode.REVIEW_REPORT,
            execute,
        )


    def wait_for_evidence(self, state: TaskState) -> dict:
        return self._observe_node(
            state,
            WorkflowNode.WAIT_FOR_EVIDENCE,
            lambda: {
                "task_status": TaskStatus.WAITING,
                "waiting_reason": WorkflowWaitReason.EVIDENCE,
                "last_completed_node": WorkflowNode.WAIT_FOR_EVIDENCE,
                "updated_at": utc_now(),
            },
        )

    def wait_for_human(self, state: TaskState) -> dict:
        return self._observe_node(
            state,
            WorkflowNode.WAIT_FOR_HUMAN,
            lambda: {
                "task_status": TaskStatus.WAITING,
                "waiting_reason": WorkflowWaitReason.HUMAN,
                "last_completed_node": WorkflowNode.WAIT_FOR_HUMAN,
                "updated_at": utc_now(),
            },
        )

    def mark_completed(self, state: TaskState) -> dict:
        def execute() -> dict:
            self._persist_final_report_report(state)
            return {
                "task_status": TaskStatus.COMPLETED,
                "waiting_reason": None,
                "last_completed_node": WorkflowNode.MARK_COMPLETED,
                "updated_at": utc_now(),
            }

        return self._observe_node(
            state,
            WorkflowNode.MARK_COMPLETED,
            execute,
        )

    def mark_failed(self, state: TaskState) -> dict:
        return self._observe_node(
            state,
            WorkflowNode.MARK_FAILED,
            lambda: {
                "task_status": TaskStatus.FAILED,
                "waiting_reason": None,
                "last_completed_node": WorkflowNode.MARK_FAILED,
                "updated_at": utc_now(),
            },
        )

    def mark_canceled(self, state: TaskState) -> dict:
        return self._observe_node(
            state,
            WorkflowNode.MARK_CANCELED,
            lambda: {
                "task_status": TaskStatus.CANCELED,
                "waiting_reason": None,
                "last_completed_node": WorkflowNode.MARK_CANCELED,
                "updated_at": utc_now(),
            },
        )

    def _write_report_with_agents(self, state: TaskState) -> Command:
        if self.writer_agent is None or self.material_pack_loader is None or self.report_render_tool is None:
            raise RuntimeError("Writer agent workflow dependencies are not configured.")

        writer_round = state.writer_round + 1
        material_pack = self.material_pack_loader(state)
        runtime = self._start_agent_runtime(
            state=state,
            workflow_node=WorkflowNode.WRITE_REPORT,
            agent_name=self.writer_agent.__class__.__name__,
            round_index=writer_round,
        )
        is_revision = (
            state.latest_review_result is not None
            and state.latest_review_result.decision == WorkflowDecision.REVISE_REPORT
            and state.current_report_version_ref is not None
        )
        version = _version_for_writer_round(writer_round)
        previous_report = self._require_current_report(state) if is_revision else None
        skill_briefs = self._select_skill_briefs(state.task_id, agent_stage="writer")
        task_name, task_notes = self._task_report_context(state.task_id)

        try:
            report_document = self._call_agent_method(
                self.writer_agent.write_report,
                material_pack,
                version=version,
                project_name=task_name,
                task_notes=task_notes,
                previous_report=previous_report,
                skill_briefs=skill_briefs,
                review_result=state.latest_review_result if is_revision else None,
                revision_instructions=state.revision_instructions if is_revision else None,
                runtime=runtime,
                messages=state.report_writer_messages,
            )
        except Exception as exc:
            self._fail_agent_session(runtime, exc)
            raise

        parent_artifact_id = None
        if is_revision and state.current_report_version_ref is not None:
            parent_artifact_id = state.current_report_version_ref.artifact_id
        elif state.material_pack_ref is not None:
            parent_artifact_id = state.material_pack_ref.artifact_id

        rendered = self.report_render_tool.render_and_save(
            task_id=state.task_id,
            report_document=report_document,
            material_pack=material_pack,
            parent_artifact_id=parent_artifact_id,
            change_note="Revision from review issues" if is_revision else "Initial WeeklyReport draft",
        )
        self._record_agent_messages(runtime, self.writer_agent)
        self._complete_agent_session(
            runtime,
            {
                "artifact_id": rendered.artifact.artifact_id,
                "version": report_document.version,
                "project_name": report_document.project_name,
            },
        )
        return Command(
            update={
                **self._agent_message_update(self.writer_agent, "report_writer_messages"),
                "writer_round": writer_round,
                "current_report_version_ref": rendered.artifact,
                "report_version_history": [*state.report_version_history, rendered.artifact],
                "workflow_decision": None,
                "revision_instructions": None,
                "last_completed_node": WorkflowNode.WRITE_REPORT,
                "updated_at": utc_now(),
            },
            goto=WorkflowNode.REVIEW_REPORT.value,
        )

    def _persist_final_report_report(self, state: TaskState) -> None:
        if (
            self.report_render_tool is None
            or self.current_report_loader is None
            or self.material_pack_loader is None
        ):
            return
        metadata = self.report_render_tool.artifact_service.get_task_metadata(state.task_id)
        if metadata.get("final_report_artifact_id"):
            return
        document = self.current_report_loader(state)
        material_pack = self.material_pack_loader(state)
        artifact = self.report_render_tool.render_final_report(
            task_id=state.task_id,
            report_document=document,
            material_pack=material_pack,
        )
        self.report_render_tool.artifact_service.update_task_metadata(
            state.task_id,
            {
                "final_report_artifact_id": artifact.artifact_id,
                "final_report_version": document.version,
            },
        )

    def _review_report_with_agent(self, state: TaskState) -> Command:
        if self.review_agent is None or self.current_report_loader is None:
            raise RuntimeError("Review agent workflow dependencies are not configured.")

        review_round = state.review_round + 1
        previous_result = state.latest_review_result
        skill_briefs = self._select_skill_briefs(state.task_id, agent_stage="review")
        task_name, task_notes = self._task_report_context(state.task_id)
        runtime = self._start_agent_runtime(
            state=state,
            workflow_node=WorkflowNode.REVIEW_REPORT,
            agent_name=self.review_agent.__class__.__name__,
            round_index=review_round,
        )
        try:
            review_result = self._call_agent_method(
                self.review_agent.review_report,
                task_id=state.task_id,
                task_name=task_name,
                task_notes=task_notes,
                report_document=self.current_report_loader(state),
                material_pack=self.material_pack_loader(state) if self.material_pack_loader else None,
                skill_briefs=skill_briefs,
                runtime=runtime,
                messages=state.report_review_messages,
                supplement_round=state.supplement_round,
                max_supplement_rounds=4,
            )
        except Exception as exc:
            self._fail_agent_session(runtime, exc)
            raise
        self._record_agent_messages(runtime, self.review_agent)
        review_result_message = AIMessage(content=review_result.model_dump_json())
        if self.memory_tool is not None and runtime is not None:
            self.memory_tool.append_messages(runtime, [review_result_message])
        self._complete_agent_session(
            runtime,
            review_result.model_dump(mode="json"),
        )
        return Command(
            update={
                **self._review_message_update(
                    self.review_agent,
                    "report_review_messages",
                    review_result_message,
                ),
                "review_round": review_round,
                "workflow_decision": review_result.decision,
                "latest_review_result": review_result,
                "resolved_issue_ids": _merge_resolved_issue_ids(state, previous_result, review_result),
                "last_completed_node": WorkflowNode.REVIEW_REPORT,
                "updated_at": utc_now(),
            },
            goto=self._review_next_node(state, review_result.decision, review_round),
        )

    def _review_next_node(self, state: TaskState, decision: WorkflowDecision, review_round: int) -> str:
        if decision == WorkflowDecision.APPROVED:
            return WorkflowNode.WAIT_FOR_HUMAN.value
        if decision == WorkflowDecision.REVISE_REPORT:
            return (
                WorkflowNode.WAIT_FOR_HUMAN.value
                if review_round >= state.max_review_rounds
                else WorkflowNode.WRITE_REPORT.value
            )
        if decision == WorkflowDecision.REQUEST_EVIDENCE:
            return WorkflowNode.WAIT_FOR_EVIDENCE.value
        if decision == WorkflowDecision.HUMAN_REVIEW_REQUIRED:
            return WorkflowNode.WAIT_FOR_HUMAN.value
        if decision == WorkflowDecision.FAIL:
            return WorkflowNode.MARK_FAILED.value
        return WorkflowNode.MARK_FAILED.value


    def _require_current_report(self, state: TaskState) -> WeeklyReportDocument:
        if self.current_report_loader is None:
            raise RuntimeError("Current WeeklyReport loader is not configured.")
        return self.current_report_loader(state)

    def _run_visual_understanding_agent(self, state: TaskState) -> Command:
        if self.visual_understanding_agent is None or self.material_pack_loader is None:
            raise RuntimeError("Material understanding workflow dependencies are not configured.")

        material_pack = self.material_pack_loader(state)
        runtime = self._start_agent_runtime(
            state=state,
            workflow_node=WorkflowNode.MATERIAL_UNDERSTANDING,
            agent_name=self.visual_understanding_agent.__class__.__name__,
            round_index=1,
        )
        try:
            updated_material_pack = self._call_agent_method(
                self.visual_understanding_agent.understand_material_pack,
                material_pack,
                runtime=runtime,
            )
        except Exception as exc:
            self._fail_agent_session(runtime, exc)
            raise
        material_pack_ref = state.material_pack_ref
        if self.material_pack_saver is not None:
            material_pack_ref = self.material_pack_saver(state, updated_material_pack)
        self._record_agent_messages(runtime, self.visual_understanding_agent)
        self._complete_agent_session(
            runtime,
            {
                "material_count": len(updated_material_pack.materials),
                "understanding_count": len(updated_material_pack.understandings),
            },
        )

        return Command(
            update={
                **self._agent_message_update(self.visual_understanding_agent, "material_understanding_messages"),
                "material_pack_ref": material_pack_ref,
                "last_completed_node": WorkflowNode.MATERIAL_UNDERSTANDING,
                "updated_at": utc_now(),
            },
            goto=WorkflowNode.WRITE_REPORT.value,
        )

    def _mark_canceled_command(self) -> Command:
        return Command(
            update={
                "task_status": TaskStatus.CANCELED,
                "waiting_reason": None,
                "last_completed_node": WorkflowNode.MARK_CANCELED,
                "updated_at": utc_now(),
            },
            goto=END,
        )

    def _start_target_for(self, state: TaskState) -> str:
        if state.last_completed_node == WorkflowNode.WAIT_FOR_EVIDENCE:
            return WorkflowNode.REVIEW_REPORT.value
        if state.last_completed_node == WorkflowNode.REVIEW_REPORT and state.latest_review_result is not None:
            decision = state.latest_review_result.decision
            if state.task_status == TaskStatus.QUEUED:
                if decision == WorkflowDecision.REVISE_REPORT:
                    return WorkflowNode.WRITE_REPORT.value
                return self._review_next_node(state, decision, state.review_round)
            if decision == WorkflowDecision.APPROVED:
                return WorkflowNode.MARK_COMPLETED.value
        if state.last_completed_node == WorkflowNode.WAIT_FOR_HUMAN and state.latest_review_result is not None:
            decision = state.latest_review_result.decision
            if state.task_status == TaskStatus.QUEUED:
                if decision == WorkflowDecision.APPROVED:
                    return WorkflowNode.MARK_COMPLETED.value
                if decision == WorkflowDecision.REVISE_REPORT and state.review_round >= state.max_review_rounds:
                    return WorkflowNode.MARK_COMPLETED.value
            return self._review_next_node(state, decision, state.review_round)
        if not state.input_artifact_refs:
            return WorkflowNode.WRITE_REPORT.value
        return WorkflowNode.MATERIAL_UNDERSTANDING.value

    def _select_skill_briefs(self, task_id: str, *, agent_stage: str) -> list[SkillBrief]:
        if self.skill_selector is None or self.task_type_loader is None:
            return []
        task_type = self.task_type_loader(task_id)
        if task_type is None:
            return []
        return self.skill_selector.select_skills(task_type=task_type, agent_stage=agent_stage)

    def _start_agent_runtime(
        self,
        *,
        state: TaskState,
        workflow_node: WorkflowNode,
        agent_name: str,
        round_index: int,
    ) -> AgentSessionRuntime | None:
        if self.memory_tool is None:
            return None
        return self.memory_tool.start_agent_runtime(
            task_id=state.task_id,
            workflow_node=workflow_node.value,
            agent_name=agent_name,
            round_index=round_index,
        )

    def _complete_agent_session(
        self,
        runtime: AgentSessionRuntime | None,
        final_result: dict[str, Any],
    ) -> None:
        if self.memory_tool is None or runtime is None:
            return None
        self.memory_tool.complete_agent_session(runtime, final_result)

    def _record_agent_messages(self, runtime: AgentSessionRuntime | None, agent: Any) -> None:
        if self.memory_tool is None or runtime is None:
            return
        messages = getattr(agent, "last_turn_messages", None) or getattr(agent, "last_messages", None)
        if messages:
            self.memory_tool.append_messages(runtime, list(messages))

    def _fail_agent_session(
        self,
        runtime: AgentSessionRuntime | None,
        error: Exception | str,
    ) -> None:
        if self.memory_tool is None or runtime is None:
            return None
        self.memory_tool.fail_agent_session(runtime, error)

    @staticmethod
    def _agent_message_update(agent: Any, key: str) -> dict[str, Any]:
        # LangGraph needs the complete conversation for the next model turn.
        messages = getattr(agent, "last_messages", None)
        return {key: list(messages)} if messages else {}

    @staticmethod
    def _review_message_update(
        agent: Any,
        key: str,
        result_message: AIMessage,
    ) -> dict[str, Any]:
        messages = list(getattr(agent, "last_messages", None) or [])
        messages.append(result_message)
        return {key: messages}

    @staticmethod
    def _call_agent_method(method: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        allowed = inspect.signature(method).parameters
        filtered_kwargs = {key: value for key, value in kwargs.items() if key in allowed}
        return method(*args, **filtered_kwargs)

    def _task_report_context(self, task_id: str) -> tuple[str | None, str | None]:
        if self.report_render_tool is None:
            return None, None
        task_index = self.report_render_tool.artifact_service.get_task_index(task_id)
        metadata = task_index.get("metadata")
        notes = metadata.get("notes") if isinstance(metadata, dict) else None
        return str(task_index.get("task_name") or "") or None, str(notes) if notes else None

    def _observe_node(
        self,
        state: TaskState,
        workflow_node: WorkflowNode,
        execute: Callable[[], NodeResult],
    ) -> NodeResult:
        trace_context = get_current_trace_context()
        trace_id = trace_context.trace_id if trace_context is not None else None
        provider = self.observability_provider or NoOpObservabilityProvider(
            ObservabilitySettings(provider="none")
        )
        observed_execute = provider.observe_span(
            name=f"workflow.{workflow_node.value}",
            capture_input=False,
            capture_output=False,
        )(execute)
        self._append_node_log(
            trace_id=trace_id,
            workflow_node=workflow_node,
            level=TraceLogLevel.INFO,
            event_type="node_started",
            message=f"Workflow node {workflow_node.value} started.",
            extra=self._node_metadata(state, workflow_node),
        )
        with push_trace_context(
            task_id=state.task_id,
            trace_id=trace_id,
            workflow_node=workflow_node.value,
        ):
            sdk_client = provider.get_sdk_client()
            metadata = self._node_metadata(state, workflow_node)

            def run_node(observation: Any | None) -> NodeResult:
                try:
                    result = execute() if observation is not None else observed_execute()
                except Exception as exc:
                    error_metadata = {**metadata, "error_type": type(exc).__name__}
                    if observation is not None:
                        observation.update(
                            level="ERROR",
                            status_message=str(exc),
                            metadata=error_metadata,
                        )
                    elif sdk_client is not None and hasattr(sdk_client, "update_current_span"):
                        sdk_client.update_current_span(
                            level="ERROR",
                            status_message=str(exc),
                            metadata=error_metadata,
                        )
                    self._append_node_log(
                        trace_id=trace_id,
                        workflow_node=workflow_node,
                        level=TraceLogLevel.ERROR,
                        event_type="node_failed",
                        message=f"Workflow node {workflow_node.value} failed: {exc}",
                        extra=error_metadata,
                    )
                    raise

                output_summary = self._node_output_summary(result)
                if observation is not None:
                    observation.update(output=output_summary)
                elif sdk_client is not None and hasattr(sdk_client, "update_current_span"):
                    sdk_client.update_current_span(output=output_summary)
                self._append_node_log(
                    trace_id=trace_id,
                    workflow_node=workflow_node,
                    level=TraceLogLevel.INFO,
                    event_type="node_completed",
                    message=f"Workflow node {workflow_node.value} completed.",
                    extra=output_summary,
                )
                return result

            if callable(getattr(sdk_client, "start_as_current_observation", None)):
                with provider.observation(
                    name=f"workflow.{workflow_node.value}",
                    as_type="span",
                    input=self._node_input_summary(state, workflow_node),
                    metadata=metadata,
                ) as observation:
                    return run_node(observation)
            if sdk_client is not None and hasattr(sdk_client, "update_current_span"):
                sdk_client.update_current_span(metadata=metadata)
            return run_node(None)

    def _append_node_log(
        self,
        *,
        trace_id: str | None,
        workflow_node: WorkflowNode,
        level: TraceLogLevel,
        event_type: str,
        message: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if self.trace_service is None or trace_id is None:
            return
        self.trace_service.append_log(
            trace_id=trace_id,
            level=level,
            stage=workflow_node.value,
            event_type=event_type,
            message=message,
            extra=extra,
        )

    @staticmethod
    def _node_metadata(state: TaskState, workflow_node: WorkflowNode) -> dict[str, Any]:
        return {
            "task_id": state.task_id,
            "thread_id": state.thread_id,
            "run_id": state.run_id,
            "workflow_node": workflow_node.value,
            "task_status": state.task_status.value,
            "waiting_reason": state.waiting_reason.value if state.waiting_reason is not None else None,
            "last_completed_node": (
                state.last_completed_node.value if state.last_completed_node is not None else None
            ),
            "review_round": state.review_round,
            "writer_round": state.writer_round,
        }

    @staticmethod
    def _node_input_summary(state: TaskState, workflow_node: WorkflowNode) -> dict[str, Any]:
        return {
            "task_id": state.task_id,
            "workflow_node": workflow_node.value,
            "task_status": state.task_status.value,
            "waiting_reason": state.waiting_reason.value if state.waiting_reason is not None else None,
            "last_completed_node": (
                state.last_completed_node.value if state.last_completed_node is not None else None
            ),
            "input_artifact_ids": [item.artifact_id for item in state.input_artifact_refs],
            "material_pack_artifact_id": (
                state.material_pack_ref.artifact_id if state.material_pack_ref is not None else None
            ),
            "current_report_artifact_id": (
                state.current_report_version_ref.artifact_id
                if state.current_report_version_ref is not None
                else None
            ),
            "review_round": state.review_round,
            "writer_round": state.writer_round,
        }

    @staticmethod
    def _node_output_summary(result: Any) -> dict[str, Any]:
        summary: dict[str, Any] = {"result_type": type(result).__name__}
        update = getattr(result, "update", None)
        goto = getattr(result, "goto", None)
        if isinstance(update, dict):
            summary["updated_keys"] = sorted(update.keys())
            task_status = update.get("task_status")
            if isinstance(task_status, StrEnum):
                summary["task_status"] = task_status.value
            elif task_status is not None:
                summary["task_status"] = str(task_status)
            waiting_reason = update.get("waiting_reason")
            if isinstance(waiting_reason, StrEnum):
                summary["waiting_reason"] = waiting_reason.value
            elif waiting_reason is not None:
                summary["waiting_reason"] = str(waiting_reason)
            last_completed_node = update.get("last_completed_node")
            if isinstance(last_completed_node, StrEnum):
                summary["last_completed_node"] = last_completed_node.value
            elif last_completed_node is not None:
                summary["last_completed_node"] = str(last_completed_node)
        elif isinstance(result, dict):
            summary["updated_keys"] = sorted(result.keys())
            task_status = result.get("task_status")
            if isinstance(task_status, StrEnum):
                summary["task_status"] = task_status.value
            elif task_status is not None:
                summary["task_status"] = str(task_status)
            waiting_reason = result.get("waiting_reason")
            if isinstance(waiting_reason, StrEnum):
                summary["waiting_reason"] = waiting_reason.value
            elif waiting_reason is not None:
                summary["waiting_reason"] = str(waiting_reason)
            last_completed_node = result.get("last_completed_node")
            if isinstance(last_completed_node, StrEnum):
                summary["last_completed_node"] = last_completed_node.value
            elif last_completed_node is not None:
                summary["last_completed_node"] = str(last_completed_node)
        if goto is not None:
            summary["goto"] = str(goto)
        return summary


def _mock_artifact_ref(
    artifact_id: str,
    kind: ArtifactKind,
    path: str,
    media_type: str,
) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=artifact_id,
        kind=kind,
        path=path,
        media_type=media_type,
        created_at=utc_now(),
    )


def _merge_resolved_issue_ids(
    state: TaskState,
    previous_result: ReviewResult | None,
    current_result: ReviewResult,
) -> list[str]:
    resolved_issue_ids = list(state.resolved_issue_ids)
    if previous_result is None:
        return resolved_issue_ids

    previous_issue_ids = {item.issue_id for item in previous_result.issues}
    current_issue_ids = {item.issue_id for item in current_result.issues}
    for issue_id in sorted(previous_issue_ids - current_issue_ids):
        if issue_id not in resolved_issue_ids:
            resolved_issue_ids.append(issue_id)
    return resolved_issue_ids


def _version_for_writer_round(writer_round: int) -> str:
    return f"V{max(writer_round - 1, 0)}"
