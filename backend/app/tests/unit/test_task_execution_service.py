from datetime import UTC, datetime
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver

from app.observability import ObservabilityProvider
from app.schemas.evidence import ArtifactKind, MaterialPack
from app.schemas.task import TaskStatus, TaskType
from app.services.config_service import ObservabilitySettings
from app.services.artifact_service import ArtifactService, StorageGroup
from app.services.task_execution_service import TaskExecutionService
from app.services.task_runner import TaskRunner, TaskSubmission
from app.services.trace_service import TraceService
from app.workflows.graph_io import build_material_pack_loader, build_material_pack_saver
from app.workflows.graph_nodes import WeeklyReportGraphNodes
from app.workflows.graph_state import TaskState, WorkflowWaitReason


class FakeRunner(TaskRunner):
    def __init__(self) -> None:
        self.submissions: list[tuple[str, object, tuple[object, ...]]] = []

    def submit(self, task_name, func, *args, **kwargs):
        self.submissions.append((task_name, func, args))
        return TaskSubmission(task_name, task_name, "fake", datetime.now(UTC))

    def get_status(self, task_id: str) -> str:
        return "pending"

    def get_result(self, task_id: str, timeout: float | None = None):
        raise KeyError(task_id)

    def shutdown(self, wait: bool = True) -> None:
        return None


class FakePreprocessTool:
    def __init__(self, artifact_service: ArtifactService) -> None:
        self.artifact_service = artifact_service
        self.calls: list[str] = []

    def preprocess_image(self, *, task_id, source_image, task_type, note):
        self.calls.append(source_image.artifact_id)
        return MaterialPack(
            material_pack_id="evidence-fake-image",
            task_id=task_id,
            source_artifact_id=source_image.artifact_id,
        )


class FakeVisualUnderstandingAgent:
    def understand_material_pack(self, material_pack: MaterialPack) -> MaterialPack:
        return material_pack


class FakeObservabilityProvider(ObservabilityProvider):
    provider_name = "fake-langfuse"
    enabled = True

    def __init__(self) -> None:
        super().__init__(ObservabilitySettings(provider="langfuse", environment="test"))
        self.calls: list[dict[str, object]] = []
        self.flush_count = 0

    def observe(self, *, name=None, as_type="span", capture_input=None, capture_output=None):
        def decorator(func):
            def wrapped(*args, **kwargs):
                self.calls.append(
                    {
                        "name": name,
                        "as_type": as_type,
                        "capture_input": capture_input,
                        "capture_output": capture_output,
                        "kwargs": dict(kwargs),
                    }
                )
                kwargs.pop("langfuse_trace_id", None)
                return func(*args, **kwargs)

            return wrapped

        return decorator

    def flush(self) -> None:
        self.flush_count += 1


def _service(tmp_path: Path, observability_provider: ObservabilityProvider | None = None):
    artifact_service = ArtifactService(tmp_path / "artifacts", tmp_path / "sqlite" / "weekly_report.db")
    task_id = artifact_service.create_task("Run task", TaskType.WEEKLY_REPORT)
    source = artifact_service.save_bytes(
        task_id,
        ArtifactKind.IMAGE_MATERIAL,
        "input.png",
        b"image",
        StorageGroup.UPLOAD,
    )
    artifact_service.save_workflow_state(
        task_id,
        TaskState(
            task_id=task_id,
            thread_id=f"thread_{task_id}",
            run_id="run-1",
            input_artifact_refs=[source],
        ),
        status=TaskStatus.CREATED,
    )
    runner = FakeRunner()
    preprocess = FakePreprocessTool(artifact_service)
    service = TaskExecutionService(
        artifact_service=artifact_service,
        task_runner=runner,
        material_preprocess_tool=preprocess,
        graph_nodes=WeeklyReportGraphNodes(
            visual_understanding_agent=FakeVisualUnderstandingAgent(),
            material_pack_loader=build_material_pack_loader(),
            material_pack_saver=build_material_pack_saver(artifact_service),
            observability_provider=observability_provider,
        ),
        checkpointer=MemorySaver(),
        observability_provider=observability_provider,
    )
    return service, artifact_service, runner, preprocess, task_id


def test_start_queues_and_prepares_initial_evidence(tmp_path: Path) -> None:
    service, artifact_service, runner, preprocess, task_id = _service(tmp_path)

    queued = service.start(task_id)

    assert queued.task_status == TaskStatus.QUEUED
    trace = TraceService(artifact_service.sqlite_path).get_trace_by_task_id(task_id)
    assert trace.current_stage == "start_workflow"
    assert trace.status.value == "running"
    assert len(runner.submissions) == 1
    runner.submissions[0][1](*runner.submissions[0][2])
    state = TaskState.model_validate(artifact_service.load_workflow_state(task_id))
    assert preprocess.calls == [state.input_artifact_refs[0].artifact_id]
    assert state.material_pack_ref is not None
    assert state.task_status == TaskStatus.WAITING
    assert state.waiting_reason == WorkflowWaitReason.HUMAN
    trace = TraceService(artifact_service.sqlite_path).get_trace_by_task_id(task_id)
    assert trace.status.value == "waiting_for_human"
    assert any(
        log.event_type == "workflow_started"
        for log in TraceService(artifact_service.sqlite_path).list_logs(trace.trace_id)
    )


def test_start_prepares_empty_material_pack_for_text_only_task(tmp_path: Path) -> None:
    artifact_service = ArtifactService(tmp_path / "artifacts", tmp_path / "sqlite" / "weekly_report.db")
    task_id = artifact_service.create_task("Text report", TaskType.WEEKLY_REPORT, metadata={"notes": "Progress notes."})
    artifact_service.save_workflow_state(
        task_id,
        TaskState(task_id=task_id, thread_id=f"thread_{task_id}", run_id="run-text"),
        status=TaskStatus.CREATED,
    )
    runner = FakeRunner()
    preprocess = FakePreprocessTool(artifact_service)
    service = TaskExecutionService(
        artifact_service=artifact_service,
        task_runner=runner,
        material_preprocess_tool=preprocess,
        graph_nodes=WeeklyReportGraphNodes(),
        checkpointer=MemorySaver(),
    )

    service.start(task_id)
    runner.submissions[0][1](*runner.submissions[0][2])

    state = TaskState.model_validate(artifact_service.load_workflow_state(task_id))
    assert preprocess.calls == []
    assert state.material_pack_ref is not None
    from app.workflows.graph_io import load_material_pack_from_manifest_ref

    material_pack = load_material_pack_from_manifest_ref(state.material_pack_ref)
    assert material_pack.materials == []
    assert material_pack.metadata["source_type"] == "task_notes"
    assert state.waiting_reason == WorkflowWaitReason.HUMAN


def test_start_wraps_real_execution_in_root_trace(tmp_path: Path) -> None:
    provider = FakeObservabilityProvider()
    service, artifact_service, runner, _preprocess, task_id = _service(
        tmp_path,
        observability_provider=provider,
    )

    service.start(task_id)
    trace = TraceService(artifact_service.sqlite_path).get_trace_by_task_id(task_id)
    runner.submissions[0][1](*runner.submissions[0][2])

    assert [call for call in provider.calls if call["name"] == "weekly_report.workflow"] == [
        {
            "name": "weekly_report.workflow",
            "as_type": "chain",
            "capture_input": False,
            "capture_output": False,
            "kwargs": {"langfuse_trace_id": trace.trace_id},
        }
    ]
    assert provider.flush_count == 1


def test_pause_resume_and_cancel_use_expected_states(tmp_path: Path) -> None:
    service, artifact_service, runner, _preprocess, task_id = _service(tmp_path)

    service.start(task_id)
    paused = service.pause(task_id)
    assert paused.task_status == TaskStatus.WAITING
    assert paused.waiting_reason == WorkflowWaitReason.MANUAL_PAUSE
    resumed = service.resume(task_id)
    assert resumed.task_status == TaskStatus.QUEUED
    assert len(runner.submissions) == 2
    runner.submissions[1][1](*runner.submissions[1][2])
    resumed_state = TaskState.model_validate(artifact_service.load_workflow_state(task_id))
    assert resumed_state.task_status == TaskStatus.WAITING
    assert resumed_state.waiting_reason == WorkflowWaitReason.HUMAN

    second_service, second_artifact_service, second_runner, _second_preprocess, second_task_id = _service(tmp_path / "second")
    second_service.start(second_task_id)
    second_service.pause(second_task_id)
    second_service.cancel(second_task_id)
    canceled = TaskState.model_validate(second_artifact_service.load_workflow_state(second_task_id))
    assert canceled.task_status == TaskStatus.CANCELED
    assert canceled.cancel_requested is False
    assert second_runner.submissions
    assert TraceService(second_artifact_service.sqlite_path).get_trace_by_task_id(second_task_id).status.value == "canceled"


def test_cancel_running_task_is_immediately_visible_and_terminal(tmp_path: Path) -> None:
    service, artifact_service, _runner, _preprocess, task_id = _service(tmp_path)

    service.start(task_id)
    running = TaskState.model_validate(artifact_service.load_workflow_state(task_id)).model_copy(
        update={"task_status": TaskStatus.RUNNING}
    )
    artifact_service.save_workflow_state(task_id, running, status=TaskStatus.RUNNING)

    canceled = service.cancel(task_id)

    assert canceled.task_status == TaskStatus.CANCELED
    assert canceled.cancel_requested is False
    loaded = TaskState.model_validate(artifact_service.load_workflow_state(task_id))
    assert loaded.task_status == TaskStatus.CANCELED
    assert TraceService(artifact_service.sqlite_path).get_trace_by_task_id(task_id).status.value == "canceled"
