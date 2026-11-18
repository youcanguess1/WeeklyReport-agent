from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from langgraph.checkpoint.base import empty_checkpoint

from app.schemas.evidence import ArtifactKind, ArtifactRef
from app.schemas.report import ReportSection
from app.schemas.review import EvidenceRequest, ReviewIssue, ReviewResult, ReviewSeverity, WorkflowDecision
from app.schemas.task import TaskStatus
from app.workflows.checkpointer import build_sqlite_checkpointer
from app.workflows.graph_state import WorkflowNode, WorkflowWaitReason


def test_sqlite_checkpoint_restores_business_types_after_reopening(tmp_path: Path, caplog) -> None:
    created_at = datetime.now(UTC)
    artifact = ArtifactRef(
        artifact_id="image-001", kind=ArtifactKind.IMAGE_MATERIAL,
        path="images/demo.png", media_type="image/png", created_at=created_at,
    )
    review = ReviewResult(
        review_id="review-001", task_id="task-001", decision=WorkflowDecision.REQUEST_EVIDENCE,
        summary="A newer test board is required.", created_at=created_at,
        issues=[ReviewIssue(
            issue_id="issue-001", section_id=ReportSection.COMPLETED_THIS_WEEK,
            severity=ReviewSeverity.HIGH, category="missing_material",
            description="The test count changed.", requires_evidence=True,
        )],
        evidence_requests=[EvidenceRequest(
            request_id="request-001", section_id=ReportSection.COMPLETED_THIS_WEEK,
            reason="Confirm the updated count.", requested_inputs=["Latest test board"],
        )],
    )
    values = {
        "task_status": TaskStatus.WAITING,
        "waiting_reason": WorkflowWaitReason.EVIDENCE,
        "last_completed_node": WorkflowNode.WAIT_FOR_EVIDENCE,
        "workflow_decision": WorkflowDecision.REQUEST_EVIDENCE,
        "input_artifact_refs": [artifact],
        "latest_review_result": review,
    }
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = values
    checkpoint["channel_versions"] = {name: "1" for name in values}
    path = tmp_path / "checkpoints.db"
    with build_sqlite_checkpointer(path) as saver:
        saved_config = saver.put(
            {"configurable": {"thread_id": "task-001", "checkpoint_ns": ""}},
            checkpoint, {"source": "update", "step": 1, "parents": {}},
            checkpoint["channel_versions"],
        )
    with build_sqlite_checkpointer(path) as reopened:
        restored = reopened.get(saved_config)
    assert restored is not None
    actual = restored["channel_values"]
    assert isinstance(actual["input_artifact_refs"][0], ArtifactRef)
    assert actual["input_artifact_refs"][0].kind is ArtifactKind.IMAGE_MATERIAL
    assert isinstance(actual["latest_review_result"], ReviewResult)
    assert actual["latest_review_result"].decision is WorkflowDecision.REQUEST_EVIDENCE
    assert actual["latest_review_result"].issues[0].severity is ReviewSeverity.HIGH
    assert actual["latest_review_result"].issues[0].section_id is ReportSection.COMPLETED_THIS_WEEK
    assert actual["latest_review_result"].evidence_requests[0].section_id is ReportSection.COMPLETED_THIS_WEEK
    assert actual["task_status"] is TaskStatus.WAITING
    assert actual["last_completed_node"] is WorkflowNode.WAIT_FOR_EVIDENCE
    assert actual["waiting_reason"] is WorkflowWaitReason.EVIDENCE
    assert actual == values
    assert "Blocked deserialization" not in caplog.text
    assert "Could not reconstruct" not in caplog.text
