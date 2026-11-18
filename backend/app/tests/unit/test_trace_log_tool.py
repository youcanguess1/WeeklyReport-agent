from __future__ import annotations

from pathlib import Path

from app.schemas.task import TaskType
from app.schemas.trace import TraceLogLevel, TraceStatus
from app.services.artifact_service import ArtifactService
from app.services.trace_service import TraceService
from app.tools.trace_log_tool import TraceLogTool


def test_trace_log_tool_records_cancellation_log_and_status(tmp_path: Path) -> None:
    sqlite_path = tmp_path / "sqlite" / "weekly_report.db"
    artifact_service = ArtifactService(
        artifact_root=tmp_path / "artifacts",
        sqlite_path=sqlite_path,
    )
    task_id = artifact_service.create_task(
        task_name="Cancellation demo",
        task_type=TaskType.WEEKLY_REPORT,
    )

    trace_service = TraceService(sqlite_path)
    trace_tool = TraceLogTool(trace_service)
    trace = trace_tool.start_task_trace(
        task_id=task_id,
        current_stage="review",
        summary="Waiting for operator confirmation.",
    )

    canceled = trace_tool.cancel_task_trace(
        trace_id=trace.trace_id,
        current_stage="review",
        message="Task canceled by user.",
        summary="User canceled the task before review completed.",
    )
    logs = trace_service.list_logs(trace.trace_id)

    assert canceled.status == TraceStatus.CANCELED
    assert canceled.current_stage == "review"
    assert canceled.summary == "User canceled the task before review completed."
    assert len(logs) == 1
    assert logs[0].event_type == "task_canceled"
    assert logs[0].message == "Task canceled by user."
    assert logs[0].level == TraceLogLevel.WARNING


def test_trace_log_tool_marks_waiting_for_human(tmp_path: Path) -> None:
    sqlite_path = tmp_path / "sqlite" / "weekly_report.db"
    artifact_service = ArtifactService(
        artifact_root=tmp_path / "artifacts",
        sqlite_path=sqlite_path,
    )
    task_id = artifact_service.create_task(
        task_name="Human wait demo",
        task_type=TaskType.WEEKLY_REPORT,
    )

    trace_service = TraceService(sqlite_path)
    trace_tool = TraceLogTool(trace_service)
    trace = trace_tool.start_task_trace(
        task_id=task_id,
        current_stage="review",
        summary="Review is in progress.",
    )

    waiting = trace_tool.wait_for_human(
        trace_id=trace.trace_id,
        current_stage="wait_for_human",
        summary="Waiting for human confirmation.",
    )

    assert waiting.status == TraceStatus.WAITING_FOR_HUMAN
    assert waiting.current_stage == "wait_for_human"
    assert waiting.summary == "Waiting for human confirmation."
