from __future__ import annotations

from pathlib import Path

from app.schemas.evidence import ArtifactKind
from app.schemas.task import TaskStatus, TaskType
from app.schemas.trace import TraceLogLevel, TraceStatus
from app.services.artifact_service import ArtifactService, StorageGroup
from app.services.trace_service import TraceService


def test_trace_service_records_task_timeline(tmp_path: Path) -> None:
    sqlite_path = tmp_path / "sqlite" / "weekly_report.db"
    artifact_service = ArtifactService(
        artifact_root=tmp_path / "artifacts",
        sqlite_path=sqlite_path,
    )
    task_id = artifact_service.create_task(
        task_name="Printer reload",
        task_type=TaskType.WEEKLY_REPORT,
        status=TaskStatus.RUNNING,
    )

    trace_service = TraceService(sqlite_path)
    trace = trace_service.start_task_trace(
        task_id=task_id,
        current_stage="writer",
        summary="Writer is generating the first WeeklyReport draft.",
    )
    log = trace_service.append_log(
        trace_id=trace.trace_id,
        level=TraceLogLevel.INFO,
        stage="writer",
        event_type="writer_started",
        message="WeeklyReportWriterAgent started writing.",
        extra={"node": "WeeklyReportWriterAgent"},
    )
    updated = trace_service.update_task_trace(
        trace_id=trace.trace_id,
        status=TraceStatus.COMPLETED,
        current_stage="review",
        summary="Writer completed and handed off to review.",
    )
    timeline = trace_service.get_trace_timeline(task_id)

    assert trace.task_id == task_id
    assert log.trace_id == trace.trace_id
    assert log.extra == {"node": "WeeklyReportWriterAgent"}
    assert updated.status == TraceStatus.COMPLETED
    assert timeline.trace.summary == "Writer completed and handed off to review."
    assert [item.event_type for item in timeline.logs] == ["writer_started"]


def test_trace_service_preserves_artifact_and_issue_links(tmp_path: Path) -> None:
    sqlite_path = tmp_path / "sqlite" / "weekly_report.db"
    artifact_service = ArtifactService(
        artifact_root=tmp_path / "artifacts",
        sqlite_path=sqlite_path,
    )
    task_id = artifact_service.create_task(
        task_name="Computer flow",
        task_type=TaskType.WEEKLY_REPORT,
    )
    artifact = artifact_service.save_text(
        task_id=task_id,
        kind=ArtifactKind.WEEKLY_REPORT_MARKDOWN,
        filename="draft.md",
        text="# Draft",
        storage_group=StorageGroup.DERIVED,
    )
    report_version_id = artifact_service.register_report_version(
        task_id=task_id,
        version="V0",
        markdown_artifact_id=artifact.artifact_id,
        structured_snapshot={"completed_this_week": []},
        change_note="Initial draft",
    )

    trace_service = TraceService(sqlite_path)
    trace = trace_service.start_task_trace(
        task_id=task_id,
        current_stage="review",
        summary="Review is waiting for more evidence.",
    )
    trace_service.append_log(
        trace_id=trace.trace_id,
        level=TraceLogLevel.WARNING,
        stage="review",
        event_type="review_issue_created",
        message="Review found an ambiguous save button.",
        artifact_id=artifact.artifact_id,
        report_version_id=report_version_id,
        issue_id="issue-001",
        extra={"severity": "medium"},
    )

    logs = trace_service.list_logs(trace.trace_id)

    assert len(logs) == 1
    assert logs[0].artifact_id == artifact.artifact_id
    assert logs[0].report_version_id == report_version_id
    assert logs[0].issue_id == "issue-001"
    assert logs[0].level == TraceLogLevel.WARNING


def test_trace_service_generates_langfuse_compatible_trace_ids(tmp_path: Path) -> None:
    sqlite_path = tmp_path / "sqlite" / "weekly_report.db"
    artifact_service = ArtifactService(
        artifact_root=tmp_path / "artifacts",
        sqlite_path=sqlite_path,
    )
    task_id = artifact_service.create_task(
        task_name="Trace id migration",
        task_type=TaskType.WEEKLY_REPORT,
    )
    trace_service = TraceService(sqlite_path)

    trace = trace_service.start_task_trace(
        task_id=task_id,
        current_stage="start_workflow",
        summary="Workflow is starting.",
    )

    assert len(trace.trace_id) == 32
    assert trace_service.is_valid_trace_id(trace.trace_id) is True


def test_trace_service_migrates_legacy_trace_ids_on_restart(tmp_path: Path) -> None:
    sqlite_path = tmp_path / "sqlite" / "weekly_report.db"
    artifact_service = ArtifactService(
        artifact_root=tmp_path / "artifacts",
        sqlite_path=sqlite_path,
    )
    task_id = artifact_service.create_task(
        task_name="Legacy trace id",
        task_type=TaskType.WEEKLY_REPORT,
    )
    trace_service = TraceService(sqlite_path)
    legacy_trace = trace_service.start_task_trace(
        task_id=task_id,
        current_stage="review",
        summary="Legacy trace.",
        trace_id="trace_legacy001",
    )
    trace_service.append_log(
        trace_id=legacy_trace.trace_id,
        level=TraceLogLevel.INFO,
        stage="review",
        event_type="legacy_log",
        message="Legacy trace log.",
    )

    restarted_trace = trace_service.start_task_trace(
        task_id=task_id,
        current_stage="write_report",
        summary="Workflow resumed.",
    )
    timeline = trace_service.get_trace_timeline(task_id)

    assert restarted_trace.trace_id != "trace_legacy001"
    assert len(restarted_trace.trace_id) == 32
    assert trace_service.is_valid_trace_id(restarted_trace.trace_id) is True
    assert timeline.trace.trace_id == restarted_trace.trace_id
    assert timeline.logs[0].trace_id == restarted_trace.trace_id
