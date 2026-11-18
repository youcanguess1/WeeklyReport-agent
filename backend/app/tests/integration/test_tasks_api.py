from __future__ import annotations

import base64
import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.observability import ObservabilityProvider
from app.schemas.evidence import ArtifactKind, ArtifactRef, MaterialUnderstanding, ImageMaterial, MaterialPack
from app.schemas.review import ReviewIssue, ReviewResult, ReviewSeverity, WorkflowDecision
from app.schemas.report import WeeklyReportDocument
from app.schemas.task import TaskStatus, TaskType
from app.services.artifact_service import ArtifactService, StorageGroup
from app.services.config_service import ConfigService, ObservabilitySettings
from app.services.trace_service import TraceService
from app.tools.report_render_tool import WeeklyReportRenderTool
from app.tools.material_preprocess_tool import MaterialPreprocessTool
from app.workflows.graph_io import (
    build_current_report_loader,
    build_material_pack_loader,
    build_material_pack_saver,
    load_material_pack_from_manifest_ref,
)
from app.workflows.graph_nodes import WeeklyReportGraphNodes
from app.workflows.graph_state import TaskState, WorkflowNode, WorkflowWaitReason
from app.api.routes_tasks import _run_workflow


PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC"
)


class _IncrementalVisualUnderstandingAgent:
    def understand_material_pack(self, material_pack: MaterialPack) -> MaterialPack:
        understanding_by_clip = {
            item.material_id: item
            for item in material_pack.understandings
        }
        for clip in material_pack.materials:
            if clip.material_id not in understanding_by_clip:
                understanding_by_clip[clip.material_id] = MaterialUnderstanding(
                    material_id=clip.material_id,
                    content_summary=f"Understanding for {clip.material_id}.",
                )
        return material_pack.model_copy(
            update={
                "understandings": [
                    understanding_by_clip[clip.material_id]
                    for clip in material_pack.materials
                ]
            }
        )


class _WriterAgent:
    def __init__(self) -> None:
        self.write_calls = 0
        self.revise_calls = 0
        self.revision_instructions: list[str | None] = []

    def write_report(
        self,
        material_pack: MaterialPack,
        *,
        version: str = "V0",
        project_name: str | None = None,
        previous_report: WeeklyReportDocument,
        review_result: ReviewResult | None = None,
        revision_instructions: str | None = None,
    ) -> WeeklyReportDocument:
        self.write_calls += 1
        if review_result is None:
            return WeeklyReportDocument(project_name=project_name or "Printer reload", version=version, completed_this_week=["Completed project milestone"])
        self.revise_calls += 1
        self.revision_instructions.append(revision_instructions)
        return WeeklyReportDocument(project_name=project_name or previous_report.project_name, version=version, in_progress=[
                'Detail page is 90% complete and has not been submitted for QA.',
                'Registration verification code depends on the SMS account.',
                'Abnormal-login testing is tracked separately from the SMS account.',
            ], pending_confirmations=['Confirm the database issue status; no operations intervention is supported by the materials.'], completed_this_week=["Completed project milestone"])


class _ReviewAgent:
    def __init__(self) -> None:
        self.review_calls = 0
        self.feedback_calls = 0
        self.feedback_contents: list[list[dict[str, object]] | None] = []

    def review_report(
        self,
        *,
        task_id: str,
        report_document: WeeklyReportDocument,
        material_pack: MaterialPack | None = None,
    ) -> ReviewResult:
        self.review_calls += 1
        return ReviewResult(
            review_id=f"review-{self.review_calls:03d}",
            task_id=task_id,
            decision=WorkflowDecision.APPROVED,
            summary="Approved after resume.",
            issues=[],
            created_at=datetime(2026, 7, 25, 10, self.review_calls, tzinfo=UTC),
        )

    def structure_human_feedback(
        self,
        *,
        task_id: str,
        feedback_text: str,
        feedback_content=None,
        report_document: WeeklyReportDocument | None = None,
        material_pack: MaterialPack | None = None,
        runtime=None,
    ) -> ReviewResult:
        self.feedback_calls += 1
        self.feedback_contents.append(feedback_content)
        return ReviewResult(
            review_id="feedback-review-001",
            task_id=task_id,
            decision=WorkflowDecision.REVISE_REPORT,
            summary="Human feedback requires a revision.",
            issues=[
                ReviewIssue(
                    issue_id="issue-feedback-001",
                    section_id="completed_this_week",
                    severity=ReviewSeverity.HIGH,
                    category="missing_expected_result",
                    description=feedback_text,
                    suggestion="Add a final-state confirmation step.",
                    requires_evidence=False,
                )
            ],
            created_at=datetime(2026, 7, 25, 11, 0, tzinfo=UTC),
        )


class _FakeMemoryEvolutionAgent:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def evolve_task_memory(self, *, task_id: str, final_state: TaskState) -> str:
        self.calls.append({"task_id": task_id, "status": final_state.task_status.value})
        return "memory evolution completed"


class _FakeSdkClient:
    def __init__(self) -> None:
        self.update_calls: list[dict[str, object]] = []
        self.span_update_calls: list[dict[str, object]] = []
        self.flush_calls = 0

    def update_current_trace(self, **kwargs) -> None:
        self.update_calls.append(kwargs)

    def update_current_span(self, **kwargs) -> None:
        self.span_update_calls.append(kwargs)

    def flush(self) -> None:
        self.flush_calls += 1


class _FakeObservabilityProvider(ObservabilityProvider):
    provider_name = "fake-langfuse"
    enabled = True

    def __init__(self) -> None:
        super().__init__(
            ObservabilitySettings(
                provider="langfuse",
                project_name="weekly_report-test",
                environment="test",
            )
        )
        self.sdk_client = _FakeSdkClient()
        self.decorated_calls: list[dict[str, object]] = []

    def observe(
        self,
        *,
        name: str | None = None,
        as_type: str = "span",
        capture_input: bool | None = None,
        capture_output: bool | None = None,
    ):
        def decorator(func):
            def wrapped(*args, **kwargs):
                self.decorated_calls.append(
                    {
                        "name": name,
                        "as_type": as_type,
                        "capture_input": capture_input,
                        "capture_output": capture_output,
                        "kwargs": dict(kwargs),
                    }
                )
                kwargs.pop("langfuse_trace_id", None)
                kwargs.pop("langfuse_public_key", None)
                return func(*args, **kwargs)

            return wrapped

        return decorator

    def get_sdk_client(self):
        return self.sdk_client

    def flush(self) -> None:
        self.sdk_client.flush()


def test_upload_supplement_image_auto_resumes_workflow(tmp_path: Path) -> None:
    app, artifact_service, _writer_agent, _review_agent, memory_evolution_agent = _build_test_app(tmp_path)
    task_id, manifest_ref, report_ref = _seed_waiting_task(artifact_service)
    state = TaskState(
        task_id=task_id,
        thread_id="thread-001",
        run_id="run-001",
        task_status=TaskStatus.WAITING,
        waiting_reason=WorkflowWaitReason.EVIDENCE,
        last_completed_node=WorkflowNode.WAIT_FOR_EVIDENCE,
        material_pack_ref=manifest_ref,
        current_report_version_ref=report_ref,
        report_version_history=[report_ref],
        writer_round=1,
        review_round=1,
        latest_review_result=ReviewResult(
            review_id="review-wait-001",
            task_id=task_id,
            decision=WorkflowDecision.REQUEST_EVIDENCE,
            summary="Need one more image.",
            created_at=datetime(2026, 7, 25, 9, 0, tzinfo=UTC),
        ),
    )
    artifact_service.save_workflow_state(task_id, state.model_dump(mode="json"), status=TaskStatus.WAITING)

    with TestClient(app) as client:
        response = client.post(
            f"/api/tasks/{task_id}/supplements",
            files={"file": ("supplement.png", base64.b64decode(PNG_1X1_BASE64), "image/png")},
            data={"request_id": "req-001", "section_id": "completed_this_week", "note": "Tray fully inserted."},
        )
        _wait_for_operation(app, response.json()["operation_id"])

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "queued"

    workflow_state = artifact_service.load_workflow_state(task_id)
    assert workflow_state is not None
    assert workflow_state["task_status"] == TaskStatus.WAITING.value
    assert workflow_state["waiting_reason"] == WorkflowWaitReason.HUMAN.value
    assert workflow_state["supplement_round"] == 1
    merged_pack = load_material_pack_from_manifest_ref(
        artifact_service.get_artifact_ref(workflow_state["material_pack_ref"]["artifact_id"])
    )
    assert len(merged_pack.materials) == 2
    assert len(merged_pack.understandings) == 2
    supplement_links = artifact_service.get_task_metadata(task_id)["supplement_links"]
    assert len(supplement_links) == 1
    assert supplement_links[0]["request_id"] == "req-001"
    assert supplement_links[0]["section_id"] == "completed_this_week"
    assert supplement_links[0]["evidence_refs"] == [merged_pack.materials[1].material_id]
    assert memory_evolution_agent.calls == []


def test_supplement_restarts_exhausted_review_cycle(tmp_path: Path, monkeypatch) -> None:
    app, artifact_service, writer_agent, review_agent, _memory_agent = _build_test_app(tmp_path)
    task_id, manifest_ref, report_ref = _seed_waiting_task(artifact_service)
    state = TaskState(
        task_id=task_id, thread_id="thread-001", run_id="run-001",
        task_status=TaskStatus.WAITING, waiting_reason=WorkflowWaitReason.EVIDENCE,
        last_completed_node=WorkflowNode.WAIT_FOR_EVIDENCE,
        material_pack_ref=manifest_ref, current_report_version_ref=report_ref,
        report_version_history=[report_ref], writer_round=1, review_round=3,
        max_review_rounds=3,
        latest_review_result=ReviewResult(
            review_id="review-wait-001", task_id=task_id,
            decision=WorkflowDecision.REQUEST_EVIDENCE,
            summary="The updated test board is required.", created_at=datetime.now(UTC),
        ),
    )
    artifact_service.save_workflow_state(task_id, state.model_dump(mode="json"), status=TaskStatus.WAITING)
    original_review = review_agent.review_report

    def review_updated_materials(*, task_id, report_document, material_pack=None):
        result = original_review(
            task_id=task_id, report_document=report_document, material_pack=material_pack
        )
        if review_agent.review_calls == 1:
            return result.model_copy(update={
                "decision": WorkflowDecision.REVISE_REPORT,
                "issues": [ReviewIssue(
                    issue_id="updated-count", section_id="completed_this_week",
                    severity=ReviewSeverity.HIGH, category="outdated_fact",
                    description="Use the updated test count from the new board.",
                )],
            })
        return result

    monkeypatch.setattr(review_agent, "review_report", review_updated_materials)
    with TestClient(app) as client:
        initial_versions = client.get(f"/api/tasks/{task_id}/weekly-report-versions").json()
        response = client.post(
            f"/api/tasks/{task_id}/supplements",
            files={"file": ("updated-board.png", base64.b64decode(PNG_1X1_BASE64), "image/png")},
            data={"request_id": "updated-board", "section_id": "completed_this_week", "note": "Latest test count."},
        )
        assert response.status_code == 202
        _wait_for_operation(app, response.json()["operation_id"])
        updated_versions = client.get(f"/api/tasks/{task_id}/weekly-report-versions").json()
    restored = TaskState.model_validate(artifact_service.load_workflow_state(task_id))
    assert writer_agent.revise_calls == 1
    assert restored.review_round == 2
    assert restored.supplement_round == 1
    assert restored.task_status is TaskStatus.WAITING
    assert restored.waiting_reason is WorkflowWaitReason.HUMAN
    assert len(updated_versions) == len(initial_versions) + 1
    assert updated_versions[0] == initial_versions[0]
    assert len(updated_versions[-1]["evidence_images"]) == 2


def test_submit_human_feedback_structures_review_and_resumes_writer(tmp_path: Path) -> None:
    app, artifact_service, writer_agent, review_agent, memory_evolution_agent = _build_test_app(tmp_path)
    task_id, manifest_ref, report_ref = _seed_waiting_task(artifact_service)
    feedback_text = (
        'Change the detail-page progress from 80% to 90%; it has not been submitted for QA. '
        'Registration verification code depends on the SMS account, but abnormal-login testing is separate. '
        'Remove the unsupported claim that database investigation is ongoing and operations has intervened.'
    )
    state = TaskState(
        task_id=task_id,
        thread_id="thread-001",
        run_id="run-001",
        task_status=TaskStatus.WAITING,
        waiting_reason=WorkflowWaitReason.HUMAN,
        last_completed_node=WorkflowNode.WAIT_FOR_HUMAN,
        material_pack_ref=manifest_ref,
        current_report_version_ref=report_ref,
        report_version_history=[report_ref],
        writer_round=1,
        review_round=3,
        max_review_rounds=3,
    )
    artifact_service.save_workflow_state(task_id, state.model_dump(mode="json"), status=TaskStatus.WAITING)

    with TestClient(app) as client:
        response = client.post(
            f"/api/tasks/{task_id}/feedback",
            data={'feedback_text': feedback_text, 'request_id': 'feedback-001'},
        )
        _wait_for_operation(app, response.json()["operation_id"])

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "queued"
    workflow_state = artifact_service.load_workflow_state(task_id)
    assert workflow_state is not None
    assert workflow_state["task_status"] == TaskStatus.WAITING.value
    assert workflow_state["waiting_reason"] == WorkflowWaitReason.HUMAN.value
    assert workflow_state["writer_round"] == 2
    assert workflow_state["review_round"] == 1
    assert workflow_state["revision_instructions"] is None
    assert any('Change the detail-page progress' in str(message) for message in workflow_state['report_review_messages'])
    assert any("feedback-review-001" in str(message) for message in workflow_state["report_review_messages"])
    assert writer_agent.revise_calls == 1
    assert writer_agent.revision_instructions == [feedback_text]
    assert review_agent.feedback_calls == 1
    assert memory_evolution_agent.calls == []
    sessions = artifact_service.memory_tool.list_agent_sessions(task_id)
    feedback_session = next(item for item in sessions if item["workflow_node"] == "human_feedback")
    assert feedback_session['final_result_json']['feedback_text'] == feedback_text
    assert feedback_session["final_result_json"]["review_result"]["decision"] == WorkflowDecision.REVISE_REPORT.value
    versions = artifact_service.list_report_versions(task_id)
    assert [item['version'] for item in versions] == ['V0', 'V1']
    assert versions[-1]['structured_snapshot']['in_progress'] == [
        'Detail page is 90% complete and has not been submitted for QA.',
        'Registration verification code depends on the SMS account.',
        'Abnormal-login testing is tracked separately from the SMS account.',
    ]
    assert 'operations has intervened' not in str(versions[-1]['structured_snapshot'])


def test_human_feedback_sends_one_image_as_multimodal_input_without_evidence_link(tmp_path: Path) -> None:
    app, artifact_service, _writer_agent, _review_agent, _memory_evolution_agent = _build_test_app(tmp_path)
    task_id, manifest_ref, report_ref = _seed_waiting_task(artifact_service)
    state = TaskState(
        task_id=task_id,
        thread_id="thread-001",
        run_id="run-001",
        task_status=TaskStatus.WAITING,
        waiting_reason=WorkflowWaitReason.HUMAN,
        last_completed_node=WorkflowNode.WAIT_FOR_HUMAN,
        material_pack_ref=manifest_ref,
        current_report_version_ref=report_ref,
        report_version_history=[report_ref],
        writer_round=1,
        review_round=1,
    )
    artifact_service.save_workflow_state(task_id, state.model_dump(mode="json"), status=TaskStatus.WAITING)

    with TestClient(app) as client:
        response = client.post(
            f"/api/tasks/{task_id}/feedback",
            data={"feedback_text": "Please clarify the final confirmation.", "request_id": "feedback-image-001"},
            files={"file": ("feedback.png", base64.b64decode(PNG_1X1_BASE64), "image/png")},
        )
        _wait_for_operation(app, response.json()["operation_id"])

    assert response.status_code == 202
    artifacts = artifact_service.list_task_artifacts(task_id)
    assert not any(item["metadata"].get("note") == "human_feedback" for item in artifacts)
    assert _review_agent.feedback_contents == [[
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64," + PNG_1X1_BASE64},
        }
    ]]


def test_human_feedback_requires_text_and_accepts_evidence_waiting_state(tmp_path: Path) -> None:
    app, artifact_service, writer_agent, review_agent, _memory_evolution_agent = _build_test_app(tmp_path)
    task_id, manifest_ref, report_ref = _seed_waiting_task(artifact_service)
    state = TaskState(
        task_id=task_id,
        thread_id="thread-001",
        run_id="run-001",
        task_status=TaskStatus.WAITING,
        waiting_reason=WorkflowWaitReason.EVIDENCE,
        last_completed_node=WorkflowNode.WAIT_FOR_EVIDENCE,
        material_pack_ref=manifest_ref,
        current_report_version_ref=report_ref,
        report_version_history=[report_ref],
        writer_round=1,
        review_round=1,
    )
    artifact_service.save_workflow_state(task_id, state.model_dump(mode="json"), status=TaskStatus.WAITING)

    with TestClient(app) as client:
        missing_text = client.post(f"/api/tasks/{task_id}/feedback", data={})
        accepted = client.post(f"/api/tasks/{task_id}/feedback", data={"feedback_text": "feedback", "request_id": "feedback-evidence-001"})
        _wait_for_operation(app, accepted.json()["operation_id"])

    assert missing_text.status_code == 422
    assert accepted.status_code == 202
    assert writer_agent.revise_calls == 1
    assert review_agent.feedback_calls == 1


def test_confirm_final_review_persists_confirmation_and_completes_task(tmp_path: Path) -> None:
    app, artifact_service, _writer_agent, _review_agent, _memory_evolution_agent = _build_test_app(tmp_path)
    task_id, manifest_ref, report_ref = _seed_waiting_task(artifact_service)
    state = TaskState(
        task_id=task_id,
        thread_id="thread-001",
        run_id="run-001",
        task_status=TaskStatus.WAITING,
        waiting_reason=WorkflowWaitReason.HUMAN,
        last_completed_node=WorkflowNode.WAIT_FOR_HUMAN,
        material_pack_ref=manifest_ref,
        current_report_version_ref=report_ref,
        report_version_history=[report_ref],
        writer_round=1,
        review_round=1,
        latest_review_result=ReviewResult(
            review_id="review-approved-001",
            task_id=task_id,
            decision=WorkflowDecision.APPROVED,
            summary="Approved.",
            created_at=datetime(2026, 7, 25, 12, 0, tzinfo=UTC),
        ),
    )
    artifact_service.save_workflow_state(task_id, state.model_dump(mode="json"), status=TaskStatus.WAITING)

    with TestClient(app) as client:
        response = client.post(f"/api/tasks/{task_id}/confirm")
        assert response.status_code == 202
        for _ in range(200):
            workflow_state = artifact_service.load_workflow_state(task_id)
            if workflow_state and workflow_state["task_status"] == TaskStatus.COMPLETED.value:
                break
            time.sleep(0.01)
        else:
            raise AssertionError("Confirmed workflow did not complete in time.")

    feedback_sessions = [
        session
        for session in artifact_service.memory_tool.list_agent_sessions(task_id)
        if session["workflow_node"] == "human_feedback"
    ]
    records = [record for session in feedback_sessions for record in artifact_service.memory_tool.load_records(session["agent_session_id"])]
    assert any(
        record.get("record_type") == "message"
            and "已确认最终周报" in str(record.get("payload"))
        for record in records
    )


def test_supplement_evidence_rejects_fifth_round(tmp_path: Path) -> None:
    app, artifact_service, _writer_agent, _review_agent, _memory_evolution_agent = _build_test_app(tmp_path)
    task_id, manifest_ref, report_ref = _seed_waiting_task(artifact_service)
    state = TaskState(
        task_id=task_id,
        thread_id="thread-001",
        run_id="run-001",
        task_status=TaskStatus.WAITING,
        waiting_reason=WorkflowWaitReason.EVIDENCE,
        last_completed_node=WorkflowNode.WAIT_FOR_EVIDENCE,
        material_pack_ref=manifest_ref,
        current_report_version_ref=report_ref,
        supplement_round=4,
    )
    artifact_service.save_workflow_state(task_id, state.model_dump(mode="json"), status=TaskStatus.WAITING)

    with TestClient(app) as client:
        response = client.post(
            f"/api/tasks/{task_id}/supplements",
            files={"file": ("supplement.png", base64.b64decode(PNG_1X1_BASE64), "image/png")},
            data={"request_id": "req-fifth"},
        )

    assert response.status_code == 409
    assert "four rounds" in response.json()["detail"]


def test_get_task_returns_safe_workbench_projection(tmp_path: Path) -> None:
    app, artifact_service, _writer_agent, _review_agent, _memory_evolution_agent = _build_test_app(tmp_path)
    task_id, manifest_ref, report_ref = _seed_waiting_task(artifact_service)
    state = TaskState(
        task_id=task_id,
        thread_id="thread-001",
        run_id="run-001",
        task_status=TaskStatus.WAITING,
        waiting_reason=WorkflowWaitReason.EVIDENCE,
        last_completed_node=WorkflowNode.WAIT_FOR_EVIDENCE,
        material_pack_ref=manifest_ref,
        current_report_version_ref=report_ref,
        report_version_history=[report_ref],
        writer_round=1,
        review_round=1,
    )
    artifact_service.save_workflow_state(task_id, state.model_dump(mode="json"), status=TaskStatus.WAITING)
    artifact_service.update_task_metadata(
        task_id,
        {"workflow_state": {"task_id": task_id, "thread_id": "stale-thread", "run_id": "stale-run"}},
    )

    with TestClient(app) as client:
        response = client.get(f"/api/tasks/{task_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "task_id": task_id,
        "task_name": "Printer reload",
        "task_type": TaskType.WEEKLY_REPORT.value,
        "description": "",
        "status": TaskStatus.WAITING.value,
        "current_node": WorkflowNode.WAIT_FOR_EVIDENCE.value,
        "waiting_reason": WorkflowWaitReason.EVIDENCE.value,
        "current_report_version": "V0",
        "created_at": payload["created_at"],
        "updated_at": payload["updated_at"],
    }
    assert "workflow_state" not in payload
    assert "thread_id" not in payload


def test_run_workflow_triggers_memory_evolution_for_failed_terminal_states(tmp_path: Path) -> None:
    artifact_service = ArtifactService(
        artifact_root=tmp_path / "artifacts",
        sqlite_path=tmp_path / "sqlite" / "weekly_report.db",
    )
    task_id = artifact_service.create_task("Failing workflow", TaskType.WEEKLY_REPORT)
    state = TaskState(task_id=task_id, thread_id="thread-001", run_id="run-001")
    memory_evolution_agent = _FakeMemoryEvolutionAgent()

    final_state = _run_workflow(
        artifact_service,
        WeeklyReportGraphNodes(review_decision_provider=lambda _state: WorkflowDecision.FAIL),
        state,
        memory_evolution_agent=memory_evolution_agent,
    )

    assert final_state.task_status == TaskStatus.FAILED
    assert memory_evolution_agent.calls == [{"task_id": task_id, "status": TaskStatus.FAILED.value}]


def test_run_workflow_skips_memory_evolution_for_waiting_and_canceled(tmp_path: Path) -> None:
    artifact_service = ArtifactService(
        artifact_root=tmp_path / "artifacts",
        sqlite_path=tmp_path / "sqlite" / "weekly_report.db",
    )
    waiting_task_id = artifact_service.create_task("Waiting workflow", TaskType.WEEKLY_REPORT)
    canceled_task_id = artifact_service.create_task("Canceled workflow", TaskType.WEEKLY_REPORT)
    memory_evolution_agent = _FakeMemoryEvolutionAgent()

    waiting_state = TaskState(task_id=waiting_task_id, thread_id="thread-001", run_id="run-001")
    waiting_result = _run_workflow(
        artifact_service,
        WeeklyReportGraphNodes(review_decision_provider=lambda _state: WorkflowDecision.REQUEST_EVIDENCE),
        waiting_state,
        memory_evolution_agent=memory_evolution_agent,
    )
    canceled_state = TaskState(
        task_id=canceled_task_id,
        thread_id="thread-002",
        run_id="run-002",
        cancel_requested=True,
    )
    canceled_result = _run_workflow(
        artifact_service,
        WeeklyReportGraphNodes(),
        canceled_state,
        memory_evolution_agent=memory_evolution_agent,
    )

    assert waiting_result.task_status == TaskStatus.WAITING
    assert canceled_result.task_status == TaskStatus.CANCELED
    assert memory_evolution_agent.calls == []


def test_run_workflow_records_completed_trace_and_observability_metadata(tmp_path: Path) -> None:
    artifact_service = ArtifactService(
        artifact_root=tmp_path / "artifacts",
        sqlite_path=tmp_path / "sqlite" / "weekly_report.db",
    )
    trace_service = TraceService(artifact_service.sqlite_path)
    task_id = artifact_service.create_task("Observed workflow", TaskType.WEEKLY_REPORT)
    state = TaskState(task_id=task_id, thread_id="thread-123", run_id="run-123")
    provider = _FakeObservabilityProvider()

    final_state = _run_workflow(
        artifact_service,
        WeeklyReportGraphNodes(),
        state,
        trace_service=trace_service,
        observability_provider=provider,
    )
    timeline = trace_service.get_trace_timeline(task_id)

    assert final_state.task_status == TaskStatus.WAITING
    assert final_state.waiting_reason == WorkflowWaitReason.HUMAN
    assert len(timeline.trace.trace_id) == 32
    assert timeline.trace.status == "waiting_for_human"
    assert timeline.logs[0].event_type == "workflow_started"
    assert timeline.logs[-1].event_type == "workflow_waiting_for_human"
    assert [item.stage for item in timeline.logs if item.event_type == "node_started"] == [
        "start_workflow",
        "write_report",
        "review_report",
        "wait_for_human",
    ]
    decorated_names = [str(item["name"]) for item in provider.decorated_calls]
    assert decorated_names[0] == "weekly_report.workflow"
    assert provider.decorated_calls[0]["as_type"] == "chain"
    assert provider.decorated_calls[0]["kwargs"]["langfuse_trace_id"] == timeline.trace.trace_id
    assert decorated_names[1:] == [
        "workflow.start_workflow",
        "workflow.write_report",
        "workflow.review_report",
        "workflow.wait_for_human",
    ]
    assert provider.sdk_client.update_calls[0]["metadata"]["task_id"] == task_id
    assert provider.sdk_client.update_calls[0]["session_id"] == "thread-123"
    assert provider.sdk_client.span_update_calls[0]["metadata"]["workflow_node"] == "start_workflow"
    assert provider.sdk_client.flush_calls == 1


def test_run_workflow_records_waiting_for_human_trace_status(tmp_path: Path) -> None:
    artifact_service = ArtifactService(
        artifact_root=tmp_path / "artifacts",
        sqlite_path=tmp_path / "sqlite" / "weekly_report.db",
    )
    trace_service = TraceService(artifact_service.sqlite_path)
    task_id = artifact_service.create_task("Human wait workflow", TaskType.WEEKLY_REPORT)
    state = TaskState(task_id=task_id, thread_id="thread-001", run_id="run-001")

    final_state = _run_workflow(
        artifact_service,
        WeeklyReportGraphNodes(review_decision_provider=lambda _state: WorkflowDecision.HUMAN_REVIEW_REQUIRED),
        state,
        trace_service=trace_service,
    )
    timeline = trace_service.get_trace_timeline(task_id)

    assert final_state.task_status == TaskStatus.WAITING
    assert final_state.waiting_reason == WorkflowWaitReason.HUMAN
    assert timeline.trace.status == "waiting_for_human"
    assert timeline.logs[-1].event_type == "workflow_waiting_for_human"


def test_text_only_task_reaches_writer_and_review_without_vision(tmp_path: Path) -> None:
    app, artifact_service, writer, review, _memory = _build_test_app(tmp_path, with_vision=False)
    with TestClient(app) as client:
        assert app.state.graph_nodes.visual_understanding_agent is None
        created = client.post('/api/tasks', data={'task_name': 'Weekly progress', 'task_type': TaskType.WEEKLY_REPORT.value, 'notes': 'Completed the dashboard.'})
        assert created.status_code == 200
        task_id = created.json()['task']['task_id']
        started = client.post(f'/api/tasks/{task_id}/start')
        assert started.status_code == 200
        for _ in range(200):
            state = TaskState.model_validate(artifact_service.load_workflow_state(task_id))
            if state.task_status in {TaskStatus.WAITING, TaskStatus.FAILED}:
                break
            time.sleep(0.01)
        assert state.task_status == TaskStatus.WAITING
        assert state.waiting_reason == WorkflowWaitReason.HUMAN
        assert state.writer_round == state.review_round == 1
        assert state.material_understanding_messages == []
        assert state.current_report_version_ref is not None
        assert load_material_pack_from_manifest_ref(state.material_pack_ref).materials == []
        assert writer.write_calls == review.review_calls == 1


def test_supplement_rejects_video_upload(tmp_path: Path) -> None:
    app, artifact_service, _writer, _review, _memory = _build_test_app(tmp_path)
    task_id, manifest, report = _seed_waiting_task(artifact_service)
    state = TaskState(
        task_id=task_id, thread_id='thread-video-rejected', run_id='run-video-rejected',
        task_status=TaskStatus.WAITING, waiting_reason=WorkflowWaitReason.EVIDENCE,
        last_completed_node=WorkflowNode.WAIT_FOR_EVIDENCE,
        material_pack_ref=manifest, current_report_version_ref=report,
    )
    artifact_service.save_workflow_state(task_id, state, status=TaskStatus.WAITING)
    with TestClient(app) as client:
        response = client.post(
            f'/api/tasks/{task_id}/supplements',
            files={'file': ('recording.mp4', b'video', 'video/mp4')},
            data={'request_id': 'rejected-video'},
        )
    assert response.status_code == 400
    assert 'PNG' in response.json()['detail']
    assert TaskState.model_validate(artifact_service.load_workflow_state(task_id)).supplement_round == 0


def _build_test_app(tmp_path: Path, *, with_vision: bool = True) -> tuple[object, ArtifactService, _WriterAgent, _ReviewAgent, _FakeMemoryEvolutionAgent]:
    artifact_service = ArtifactService(
        artifact_root=tmp_path / "artifacts",
        sqlite_path=tmp_path / "sqlite" / "weekly_report.db",
    )
    writer_agent = _WriterAgent()
    review_agent = _ReviewAgent()
    memory_evolution_agent = _FakeMemoryEvolutionAgent()
    graph_nodes = WeeklyReportGraphNodes(

        material_pack_loader=build_material_pack_loader(),
        material_pack_saver=build_material_pack_saver(artifact_service),
        current_report_loader=build_current_report_loader(artifact_service),
        visual_understanding_agent=_IncrementalVisualUnderstandingAgent() if with_vision else None,
        writer_agent=writer_agent,
        review_agent=review_agent,
        report_render_tool=WeeklyReportRenderTool(artifact_service),
    )
    app = create_app(
        config_service=_build_config_service(tmp_path, with_vision=with_vision),
        artifact_service=artifact_service,
        memory_evolution_agent=memory_evolution_agent,
        material_preprocess_tool=MaterialPreprocessTool(
            artifact_service=artifact_service,
        ),
        graph_nodes=graph_nodes,
    )
    return app, artifact_service, writer_agent, review_agent, memory_evolution_agent


def _wait_for_operation(app, operation_id: str) -> None:
    for _ in range(200):
        operation = app.state.task_operation_service.get(operation_id)
        if operation.status.value in {"completed", "failed"}:
            assert operation.status.value == "completed", operation.error_message
            return
        time.sleep(0.01)
    raise AssertionError(f"Operation {operation_id} did not finish in time.")


def _seed_waiting_task(artifact_service: ArtifactService) -> tuple[str, ArtifactRef, ArtifactRef]:
    task_id = artifact_service.create_task(
        task_name="Printer reload",
        task_type=TaskType.WEEKLY_REPORT,
    )
    source_image = artifact_service.save_bytes(
        task_id=task_id,
        kind=ArtifactKind.IMAGE_MATERIAL,
        filename="printer-demo.png",
        content=b"image",
        storage_group=StorageGroup.UPLOAD,
    )
    key_frame = artifact_service.save_bytes(
        task_id=task_id,
        kind=ArtifactKind.IMAGE_MATERIAL,
        filename="frame-001.png",
        content=base64.b64decode(PNG_1X1_BASE64),
        storage_group=StorageGroup.DERIVED,
        parent_artifact_id=source_image.artifact_id,
    )
    material_pack = MaterialPack(
        material_pack_id="evidence-001",
        task_id=task_id,
        source_artifact_id=source_image.artifact_id,
        materials=[
            ImageMaterial(material_id="material-001", source_artifact_id=key_frame.artifact_id, image_path=key_frame.path, note="Open the tray.")
        ],
        understandings=[
            MaterialUnderstanding(
                material_id="material-001",
                content_summary="Operator opens the tray.",
            )
        ],
    )
    manifest_ref = artifact_service.save_json(
        task_id=task_id,
        kind=ArtifactKind.MANIFEST,
        filename="material_manifest.json",
        payload=material_pack.model_dump(mode="json"),
        storage_group=StorageGroup.DERIVED,
        parent_artifact_id=source_image.artifact_id,
    )
    report_document = WeeklyReportDocument(project_name="Printer reload", version="V0", completed_this_week=["Completed project milestone"])
    report_ref = artifact_service.save_text(
        task_id=task_id,
        kind=ArtifactKind.WEEKLY_REPORT_MARKDOWN,
        filename="report_v0.md",
        text="# WeeklyReport V0",
        storage_group=StorageGroup.DERIVED,
        parent_artifact_id=source_image.artifact_id,
    )
    artifact_service.register_report_version(
        task_id=task_id,
        version="V0",
        markdown_artifact_id=report_ref.artifact_id,
        structured_snapshot=report_document.model_dump(mode="json"),
        change_note="Seeded WeeklyReport version",
    )
    return task_id, manifest_ref, report_ref


def _build_config_service(tmp_path: Path, *, with_vision: bool = True) -> ConfigService:
    config_dir = tmp_path / "config"
    repo_root = tmp_path / "repo"
    config_dir.mkdir(exist_ok=True)
    repo_root.mkdir(exist_ok=True)
    (config_dir / "settings.example.yaml").write_text(
        "\n".join(
            [
                "app:",
                "  name: WeeklyReport Test API",
                "  environment: test",
                "model:",
                "  provider: openai-compatible",
                "  chat:",
                "    model: gpt-4.1-mini",
                "  vision:",
                f"    model: {'gpt-4.1-mini' if with_vision else 'null'}",
                "storage:",
                "  artifact_root: data/artifacts",
                "  sqlite_path: data/sqlite/weekly_report.db",
                "task_runner:",
                "  backend: local",
                "  max_workers: 1",
            ]
        ),
        encoding="utf-8",
    )
    return ConfigService(config_dir=config_dir, repo_root=repo_root)
