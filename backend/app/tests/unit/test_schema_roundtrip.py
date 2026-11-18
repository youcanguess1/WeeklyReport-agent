from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.evidence import ArtifactKind, MaterialUnderstanding, ImageMaterial, MaterialPack
from app.schemas.memory import (
    FailurePattern,
    MemoryKind,
    MemoryUpdate,
    MemoryUpdateAction,
    SkillBrief,
    SkillRecord,
    SkillStatus,
    SkillTrigger,
)

from app.schemas.review import (
    EvidenceRequest,
    ReviewIssue,
    ReviewResult,
    ReviewSeverity,
    WorkflowDecision,
)
from app.schemas.report import WeeklyReportDocument
from app.schemas.task import TaskStatus, TaskType
from app.schemas.task_api import TaskCreatePayload, TaskListItem
from app.schemas.trace import (
    TaskTrace,
    TraceLogEntry,
    TraceLogLevel,
    TraceStatus,
    TraceTimeline,
)


def _now() -> datetime:
    return datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


def test_core_schemas_round_trip() -> None:
    now = _now()
    task = TaskCreatePayload(
        task_name="Project delivery report",
        task_type=TaskType.WEEKLY_REPORT,
        template_name="default",
        notes="Completed the API milestone; validation remains in progress.",
    )
    task_summary = TaskListItem(
        task_id="task-001",
        task_name="Project delivery report",
        task_type=TaskType.WEEKLY_REPORT,
        description="Completed the API milestone; validation remains in progress.",
        status=TaskStatus.RUNNING,
        current_node="write_report",
        waiting_reason=None,
        current_report_version="V1",
        artifact_count=4,
        created_at=now,
        updated_at=now,
    )

    evidence = MaterialPack(
        material_pack_id="evp-001",
        task_id="task-001",
        source_artifact_id="image-001",
        materials=[
            ImageMaterial(material_id="material-001", source_artifact_id="image-001", image_path="data/artifacts/task-001/uploads/images/project-board.png", note="The project board confirms the completed API milestone.")
        ],
        understandings=[
            MaterialUnderstanding(
                material_id="material-001",
                content_summary="The API milestone is complete; validation remains in progress.",
            )
        ],


        metadata={"project": "delivery-dashboard"},
    )




    report = WeeklyReportDocument(project_name="Project delivery", version="V1.0", report_period='2026-10-19 \u81f3 2026-10-23', completed_this_week=["The API milestone is complete."], in_progress=["Validation remains in progress."], risks_and_blockers=["Validation is blocked by an unresolved dependency."], next_week_plan=["Complete the validation next week."], pending_confirmations=["Confirm the validation owner."], raw_markdown="# Project delivery\n\n## 本周完成\n- The API milestone is complete.\n\n## 进行中\n- Validation remains in progress.\n\n## 风险与阻塞\n- Validation is blocked by an unresolved dependency.\n\n## 下周计划\n- Complete the validation next week.\n\n## 待确认事项\n- Confirm the validation owner.\n", generated_at=now, material_refs=["material-001"])

    review = ReviewResult(
        review_id="review-001",
        task_id="task-001",
        decision=WorkflowDecision.REQUEST_EVIDENCE,
        summary="Need an updated project board confirming the validation status.",
        issues=[
            ReviewIssue(
                issue_id="issue-001",
                section_id="completed_this_week",
                severity=ReviewSeverity.MEDIUM,
                category="missing_expected_result",
                description="The latest validation status is not shown.",
                suggestion="Add the latest project board screenshot.",
                requires_evidence=True,
            )
        ],
        evidence_requests=[
            EvidenceRequest(
                request_id="req-001",
                section_id="completed_this_week",
                reason="Need evidence of the validation status.",
                requested_inputs=["latest project board screenshot"],
            )
        ],
        created_at=now,
    )


    skill = SkillRecord(
        skill_id="skill-001",
        title="Disambiguate duplicate buttons",
        description="Tell the agent how to distinguish duplicate button labels in UI flows.",
        kind=MemoryKind.WRITER_SKILL,
        status=SkillStatus.ACTIVE,
        content="If multiple buttons share the same label, mention the dialog position or color.",
        trigger=SkillTrigger(
            task_type="weekly_report",
            agent_stage="writer",
            issue_type="ambiguous_target",
        ),
        confidence=0.88,
        hit_count=4,
        created_at=now,
        updated_at=now,
    )
    skill_brief = SkillBrief(
        skill_id="skill-001",
        title="Disambiguate duplicate buttons",
        description="Tell the agent how to distinguish duplicate button labels in UI flows.",
        kind=MemoryKind.WRITER_SKILL,
        trigger=SkillTrigger(
            task_type="weekly_report",
            agent_stage="writer",
            issue_type="ambiguous_target",
        ),
        confidence=0.88,
        hit_count=4,
    )
    failure_pattern = FailurePattern(
        pattern_id="fp-001",
        title="Missing final state evidence",
        symptom="Review asks for proof of the end state.",
        cause="The final frame was not captured.",
        fix="Capture one additional post-action frame.",
        related_issue_types=["missing_expected_result"],
        confidence=0.8,
    )
    memory_update = MemoryUpdate(
        update_id="memu-001",
        kind=MemoryKind.FAILURE_PATTERN,
        action=MemoryUpdateAction.CREATED,
        target_id="fp-001",
        summary="Added a reusable pattern for missing final-state evidence.",
        source_task_id="task-001",
        created_at=now,
    )

    trace = TraceTimeline(
        trace=TaskTrace(
            trace_id="trace-001",
            task_id="task-001",
            task_name="Printer reload demo",
            task_type="weekly_report",
            status=TraceStatus.FAILED,
            started_at=now,
            ended_at=now,
            updated_at=now,
            current_stage="review",
            summary="Review requested more evidence.",
            error_message="Need one more final-state frame.",
        ),
        logs=[
            TraceLogEntry(
                log_id="log-001",
                trace_id="trace-001",
                task_id="task-001",
                timestamp=now,
                level=TraceLogLevel.INFO,
                stage="preprocess",
                event_type="artifact_saved",
                message="Manifest saved.",
                artifact_id="manifest-001",
                extra={"kind": ArtifactKind.MANIFEST.value},
            ),
            TraceLogEntry(
                log_id="log-002",
                trace_id="trace-001",
                task_id="task-001",
                timestamp=now,
                level=TraceLogLevel.WARNING,
                stage="review",
                event_type="review_issue_created",
                message="Need one more final-state frame.",
                issue_id="issue-001",
                extra={"severity": "medium"},
            ),
        ],
    )

    round_trip_models = [
        task,
        task_summary,
        evidence,
        report,
        review,
        skill,
        skill_brief,
        failure_pattern,
        memory_update,
        trace,
    ]

    for model in round_trip_models:
        restored = model.__class__.model_validate_json(model.model_dump_json())
        assert restored == model


def test_schema_constraints_reject_invalid_values() -> None:
    with pytest.raises(ValidationError):
        WeeklyReportDocument(project_name="")
    with pytest.raises(ValidationError):
        ReviewIssue(issue_id="issue-1", section_id="unknown", severity=ReviewSeverity.HIGH, category="facts", description="Unknown section")
