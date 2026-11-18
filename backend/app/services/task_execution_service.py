from __future__ import annotations

from threading import RLock
from typing import Any

from app.observability import (
    NoOpObservabilityProvider,
    ObservabilityProvider,
    push_trace_context,
)
from app.schemas.evidence import ArtifactRef, MaterialPack
from app.schemas.task import TaskStatus, TaskType
from app.schemas.trace import TraceLogLevel, TraceStatus
from app.services.artifact_service import ArtifactService
from app.services.config_service import ObservabilitySettings
from app.services.task_runner import TaskRunner
from app.services.trace_service import TraceService
from app.tools.material_preprocess_tool import MaterialPreprocessTool
from app.workflows.graph_io import build_material_pack_saver, merge_material_packs
from app.workflows.graph_nodes import WeeklyReportGraphNodes
from app.workflows.graph_state import TaskState, WorkflowNode, WorkflowWaitReason
from app.workflows.weekly_report_graph import build_weekly_report_graph


class TaskControlError(Exception):
    pass


class TaskNotFoundError(TaskControlError):
    pass


class TaskExecutionService:
    def __init__(
        self,
        *,
        artifact_service: ArtifactService,
        task_runner: TaskRunner,
        material_preprocess_tool: MaterialPreprocessTool,
        graph_nodes: WeeklyReportGraphNodes,
        checkpointer: Any,
        trace_service: TraceService | None = None,
        observability_provider: ObservabilityProvider | None = None,
    ) -> None:
        self.artifact_service = artifact_service
        self.task_runner = task_runner
        self.material_preprocess_tool = material_preprocess_tool
        self.graph_nodes = graph_nodes
        self.checkpointer = checkpointer
        self.trace_service = trace_service or TraceService(artifact_service.sqlite_path)
        self.observability_provider = (
            observability_provider
            or graph_nodes.observability_provider
            or NoOpObservabilityProvider(ObservabilitySettings(provider="none"))
        )
        self._locks: dict[str, RLock] = {}

    def start(self, task_id: str) -> TaskState:
        with self._lock_for(task_id):
            state = self._load_state(task_id)
            if state.task_status != TaskStatus.CREATED:
                raise TaskControlError("Only created tasks can be started.")
            queued = state.model_copy(update={"task_status": TaskStatus.QUEUED})
            self.artifact_service.save_workflow_state(task_id, queued, status=TaskStatus.QUEUED)
            self.trace_service.start_task_trace(
                task_id=task_id,
                current_stage=WorkflowNode.START_WORKFLOW.value,
                summary="Workflow queued for execution.",
            )
            self.task_runner.submit(task_id, self._run, task_id)
            return queued

    def pause(self, task_id: str) -> TaskState:
        with self._lock_for(task_id):
            state = self._load_state(task_id)
            if state.task_status not in {TaskStatus.QUEUED, TaskStatus.RUNNING}:
                raise TaskControlError("Only queued or running tasks can be paused.")
            if state.task_status == TaskStatus.QUEUED:
                paused = state.model_copy(
                    update={
                        "task_status": TaskStatus.WAITING,
                        "waiting_reason": WorkflowWaitReason.MANUAL_PAUSE,
                        "pause_requested": False,
                    }
                )
            else:
                paused = state.model_copy(update={"pause_requested": True})
            self.artifact_service.save_workflow_state(task_id, paused, status=paused.task_status)
            if paused.task_status == TaskStatus.WAITING:
                self._record_trace_for_task(task_id, paused)
            return paused

    def resume(self, task_id: str) -> TaskState:
        with self._lock_for(task_id):
            state = self._load_state(task_id)
            if state.task_status != TaskStatus.WAITING or state.waiting_reason != WorkflowWaitReason.MANUAL_PAUSE:
                raise TaskControlError("Only manually paused tasks can be resumed.")
            queued = state.model_copy(
                update={
                    "task_status": TaskStatus.QUEUED,
                    "waiting_reason": None,
                    "pause_requested": False,
                    "cancel_requested": False,
                }
            )
            self.artifact_service.save_workflow_state(task_id, queued, status=TaskStatus.QUEUED)
            self.task_runner.submit(task_id, self._run, task_id)
            return queued

    def cancel(self, task_id: str) -> TaskState:
        with self._lock_for(task_id):
            state = self._load_state(task_id)
            if state.task_status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED}:
                raise TaskControlError("Terminal tasks cannot be canceled.")
            if state.task_status in {TaskStatus.CREATED, TaskStatus.QUEUED, TaskStatus.WAITING}:
                canceled = state.model_copy(
                    update={
                        "task_status": TaskStatus.CANCELED,
                        "waiting_reason": None,
                        "cancel_requested": False,
                        "pause_requested": False,
                    }
                )
            else:
                # Expose cancellation immediately; the running node may still be unwinding.
                canceled = state.model_copy(
                    update={
                        "task_status": TaskStatus.CANCELED,
                        "waiting_reason": None,
                        "cancel_requested": False,
                        "pause_requested": False,
                    }
                )
            self.artifact_service.save_workflow_state(task_id, canceled, status=canceled.task_status)
            self._record_trace_for_task(task_id, canceled)
            return canceled

    def _run(self, task_id: str) -> None:
        trace_id: str | None = None
        try:
            state = self._load_state(task_id)
            if self._is_paused_or_canceled(state):
                return
            trace = self.trace_service.start_task_trace(
                task_id=task_id,
                current_stage=self._trace_stage_for_state(state),
                summary=self._trace_summary_for_state(state),
            )
            trace_id = trace.trace_id
            self.trace_service.append_log(
                trace_id=trace_id,
                level=TraceLogLevel.INFO,
                stage=self._trace_stage_for_state(state),
                event_type="workflow_started",
                message="Workflow execution started.",
                extra={
                    "thread_id": state.thread_id,
                    "run_id": state.run_id,
                    "task_status": state.task_status.value,
                },
            )
            def execute_workflow() -> None:
                nonlocal state
                if state.material_pack_ref is None:
                    state = self._prepare_initial_evidence(state)
                    if self._is_paused_or_canceled(state):
                        return

                graph = build_weekly_report_graph(
                    nodes=self.graph_nodes,
                    checkpointer=self.checkpointer,
                )
                config = {"configurable": {"thread_id": state.thread_id}}
                snapshot = graph.get_state(config)
                if snapshot.next:
                    graph.update_state(
                        config,
                        {
                            "task_status": TaskStatus.RUNNING,
                            "waiting_reason": None,
                            "pause_requested": False,
                            "cancel_requested": False,
                        },
                    )
                graph_input: dict[str, Any] | None = (
                    state.model_dump(mode="json") if not snapshot.next else None
                )

                while True:
                    with self._lock_for(task_id):
                        latest = self._load_state(task_id)
                        if self._is_paused_or_canceled(latest):
                            return
                        if latest.pause_requested:
                            paused = latest.model_copy(
                                update={
                                    "task_status": TaskStatus.WAITING,
                                    "waiting_reason": WorkflowWaitReason.MANUAL_PAUSE,
                                    "pause_requested": False,
                                }
                            )
                            graph.update_state(config, paused.model_dump(mode="json"))
                            self.artifact_service.save_workflow_state(task_id, paused, status=TaskStatus.WAITING)
                            return
                        if latest.cancel_requested:
                            graph.update_state(config, {"cancel_requested": True})

                    with push_trace_context(
                        task_id=task_id,
                        trace_id=trace_id,
                        workflow_node=self._trace_stage_for_state(state),
                    ):
                        result = graph.invoke(graph_input, config=config, interrupt_after="*")
                    graph_input = None
                    state = TaskState.model_validate(result)
                    with self._lock_for(task_id):
                        latest = self._load_state(task_id)
                        if latest.task_status == TaskStatus.CANCELED:
                            return
                        if graph.get_state(config).next and latest.pause_requested:
                            state = state.model_copy(
                                update={
                                    "task_status": TaskStatus.WAITING,
                                    "waiting_reason": WorkflowWaitReason.MANUAL_PAUSE,
                                    "pause_requested": False,
                                }
                            )
                            graph.update_state(config, state.model_dump(mode="json"))
                        elif latest.cancel_requested:
                            state = state.model_copy(update={"cancel_requested": True})
                            graph.update_state(config, {"cancel_requested": True})
                        self.artifact_service.save_workflow_state(task_id, state, status=state.task_status)
                        self._record_trace_state(trace_id, state)
                        if not graph.get_state(config).next or state.task_status == TaskStatus.WAITING:
                            return

            observed_workflow = self.observability_provider.observe(
                name="weekly_report.workflow",
                as_type="chain",
                capture_input=False,
                capture_output=False,
            )(execute_workflow)
            try:
                sdk_client = self.observability_provider.get_sdk_client()
                if callable(getattr(sdk_client, "start_as_current_observation", None)):
                    with self.observability_provider.observation(
                        name="weekly_report.workflow",
                        as_type="chain",
                        trace_id=trace_id,
                        input=self._root_input_summary(state),
                        metadata={
                            "task_id": state.task_id,
                            "thread_id": state.thread_id,
                            "run_id": state.run_id,
                            "environment": self.observability_provider.environment,
                        },
                    ) as observation:
                        execute_workflow()
                        if observation is not None:
                            observation.update(output=self._root_output_summary(state))
                elif self.observability_provider.enabled:
                    observed_workflow(langfuse_trace_id=trace_id)
                else:
                    observed_workflow()
            finally:
                self.observability_provider.flush()
        except Exception:
            with self._lock_for(task_id):
                latest = self._load_state(task_id)
                if latest.task_status not in {
                    TaskStatus.COMPLETED,
                    TaskStatus.FAILED,
                    TaskStatus.CANCELED,
                    TaskStatus.WAITING,
                }:
                    failed = latest.model_copy(
                        update={
                            "task_status": TaskStatus.FAILED,
                            "waiting_reason": None,
                            "pause_requested": False,
                            "cancel_requested": False,
                        }
                    )
                    self.artifact_service.save_workflow_state(task_id, failed, status=TaskStatus.FAILED)
                    if trace_id is not None:
                        self._record_trace_state(
                            trace_id,
                            failed,
                            error_message="Workflow execution failed.",
                        )
            raise

    def _record_trace_state(
        self,
        trace_id: str,
        state: TaskState,
        *,
        error_message: str | None = None,
    ) -> None:
        status = self._trace_status_for_state(state)
        stage = self._trace_stage_for_state(state)
        self.trace_service.append_log(
            trace_id=trace_id,
            level=self._trace_log_level_for_status(status),
            stage=stage,
            event_type=self._trace_event_type_for_status(status),
            message=self._trace_message_for_state(state),
            extra={
                "task_status": state.task_status.value,
                "waiting_reason": state.waiting_reason.value if state.waiting_reason else None,
                "last_completed_node": (
                    state.last_completed_node.value if state.last_completed_node else None
                ),
            },
        )
        self.trace_service.update_task_trace(
            trace_id=trace_id,
            status=status,
            current_stage=stage,
            summary=self._trace_summary_for_state(state),
            error_message=error_message,
        )

    @staticmethod
    def _root_input_summary(state: TaskState) -> dict[str, Any]:
        return {
            "task_id": state.task_id,
            "task_status": state.task_status.value,
            "input_artifact_ids": [item.artifact_id for item in state.input_artifact_refs],
            "thread_id": state.thread_id,
            "run_id": state.run_id,
        }

    @staticmethod
    def _root_output_summary(state: TaskState) -> dict[str, Any]:
        return {
            "task_status": state.task_status.value,
            "waiting_reason": state.waiting_reason.value if state.waiting_reason is not None else None,
            "last_completed_node": (
                state.last_completed_node.value if state.last_completed_node is not None else None
            ),
            "review_round": state.review_round,
            "writer_round": state.writer_round,
        }

    def _record_trace_for_task(self, task_id: str, state: TaskState) -> None:
        try:
            trace = self.trace_service.get_trace_by_task_id(task_id)
        except KeyError:
            return
        self._record_trace_state(trace.trace_id, state)

    @staticmethod
    def _trace_stage_for_state(state: TaskState) -> str:
        return (
            state.last_completed_node.value
            if state.last_completed_node is not None
            else WorkflowNode.START_WORKFLOW.value
        )

    @staticmethod
    def _trace_status_for_state(state: TaskState) -> TraceStatus:
        if state.task_status == TaskStatus.COMPLETED:
            return TraceStatus.COMPLETED
        if state.task_status == TaskStatus.FAILED:
            return TraceStatus.FAILED
        if state.task_status == TaskStatus.CANCELED:
            return TraceStatus.CANCELED
        if state.task_status == TaskStatus.WAITING:
            return (
                TraceStatus.WAITING_FOR_HUMAN
                if state.waiting_reason in {WorkflowWaitReason.HUMAN, WorkflowWaitReason.MANUAL_PAUSE}
                else TraceStatus.WAITING_FOR_EVIDENCE
            )
        return TraceStatus.RUNNING

    @staticmethod
    def _trace_log_level_for_status(status: TraceStatus) -> TraceLogLevel:
        if status == TraceStatus.FAILED:
            return TraceLogLevel.ERROR
        if status in {
            TraceStatus.CANCELED,
            TraceStatus.WAITING_FOR_EVIDENCE,
            TraceStatus.WAITING_FOR_HUMAN,
        }:
            return TraceLogLevel.WARNING
        return TraceLogLevel.INFO

    @staticmethod
    def _trace_event_type_for_status(status: TraceStatus) -> str:
        return {
            TraceStatus.RUNNING: "workflow_running",
            TraceStatus.WAITING_FOR_EVIDENCE: "workflow_waiting_for_evidence",
            TraceStatus.WAITING_FOR_HUMAN: "workflow_waiting_for_human",
            TraceStatus.COMPLETED: "workflow_completed",
            TraceStatus.FAILED: "workflow_failed",
            TraceStatus.CANCELED: "workflow_canceled",
        }[status]

    @staticmethod
    def _trace_message_for_state(state: TaskState) -> str:
        if state.waiting_reason == WorkflowWaitReason.MANUAL_PAUSE:
            return "Workflow manually paused."
        return {
            TraceStatus.RUNNING: "Workflow is running.",
            TraceStatus.WAITING_FOR_EVIDENCE: "Workflow is waiting for supplementary evidence.",
            TraceStatus.WAITING_FOR_HUMAN: "Workflow is waiting for human feedback.",
            TraceStatus.COMPLETED: "Workflow completed successfully.",
            TraceStatus.FAILED: "Workflow reached the failed terminal state.",
            TraceStatus.CANCELED: "Workflow was canceled.",
        }[TaskExecutionService._trace_status_for_state(state)]

    @staticmethod
    def _trace_summary_for_state(state: TaskState) -> str:
        stage = TaskExecutionService._trace_stage_for_state(state)
        status = TaskExecutionService._trace_status_for_state(state)
        if status == TraceStatus.WAITING_FOR_HUMAN:
            if state.waiting_reason == WorkflowWaitReason.MANUAL_PAUSE:
                return f"Workflow was manually paused at {stage}."
            return f"Workflow paused at {stage} and is waiting for human feedback."
        if status == TraceStatus.WAITING_FOR_EVIDENCE:
            return f"Workflow paused at {stage} and is waiting for supplementary evidence."
        if status == TraceStatus.COMPLETED:
            return f"Workflow completed at {stage}."
        if status == TraceStatus.FAILED:
            return f"Workflow failed at {stage}."
        if status == TraceStatus.CANCELED:
            return f"Workflow was canceled at {stage}."
        return f"Workflow is running at {stage}."

    def _prepare_initial_evidence(self, state: TaskState) -> TaskState:
        task_type = TaskType(self.artifact_service.get_task_index(state.task_id)["task_type"])
        material_pack: MaterialPack | None = None
        for artifact in state.input_artifact_refs:
            current = self._preprocess_input(state.task_id, task_type, artifact)
            material_pack = current if material_pack is None else merge_material_packs(material_pack, current)
        if material_pack is None:
            material_pack = MaterialPack(
                material_pack_id=f"evidence_text_{state.task_id}",
                task_id=state.task_id,
                source_artifact_id=f"text_{state.task_id}",
                metadata={"schema": "text_only_evidence_v1", "source_type": "task_notes"},
            )
        manifest = build_material_pack_saver(self.artifact_service)(state, material_pack)
        prepared = state.model_copy(update={"material_pack_ref": manifest})
        with self._lock_for(state.task_id):
            latest = self._load_state(state.task_id)
            prepared = prepared.model_copy(
                update={
                    "task_status": latest.task_status,
                    "waiting_reason": latest.waiting_reason,
                    "pause_requested": latest.pause_requested,
                    "cancel_requested": latest.cancel_requested,
                }
            )
            self.artifact_service.save_workflow_state(
                state.task_id,
                prepared,
                status=prepared.task_status,
            )
        return prepared

    def _preprocess_input(
        self,
        task_id: str,
        task_type: TaskType,
        artifact: ArtifactRef,
    ) -> MaterialPack:
        return self.material_preprocess_tool.preprocess_image(
            task_id=task_id,
            source_image=artifact,
            task_type=task_type,
            note=None,
        )

    def _load_state(self, task_id: str) -> TaskState:
        try:
            payload = self.artifact_service.load_workflow_state(task_id)
        except KeyError as exc:
            raise TaskNotFoundError(str(exc)) from exc
        if payload is None:
            raise TaskNotFoundError(f"No workflow state found for task id: {task_id}")
        return TaskState.model_validate(payload)

    @staticmethod
    def _is_paused_or_canceled(state: TaskState) -> bool:
        return state.task_status == TaskStatus.CANCELED or (
            state.task_status == TaskStatus.WAITING
            and state.waiting_reason == WorkflowWaitReason.MANUAL_PAUSE
        )

    def _lock_for(self, task_id: str) -> RLock:
        return self._locks.setdefault(task_id, RLock())
