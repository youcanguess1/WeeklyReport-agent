from __future__ import annotations

from typing import Any

from app.schemas.trace import TaskTrace, TraceLogEntry, TraceLogLevel, TraceStatus
from app.services.trace_service import TraceService


class TraceLogTool:
    def __init__(self, trace_service: TraceService) -> None:
        self.trace_service = trace_service

    def start_task_trace(
        self,
        task_id: str,
        current_stage: str,
        summary: str | None = None,
    ) -> TaskTrace:
        return self.trace_service.start_task_trace(
            task_id=task_id,
            current_stage=current_stage,
            summary=summary,
        )

    def append_log(
        self,
        trace_id: str,
        stage: str,
        event_type: str,
        message: str,
        level: TraceLogLevel = TraceLogLevel.INFO,
        artifact_id: str | None = None,
        report_version_id: str | None = None,
        issue_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> TraceLogEntry:
        return self.trace_service.append_log(
            trace_id=trace_id,
            level=level,
            stage=stage,
            event_type=event_type,
            message=message,
            artifact_id=artifact_id,
            report_version_id=report_version_id,
            issue_id=issue_id,
            extra=extra,
        )

    def complete_task_trace(
        self,
        trace_id: str,
        current_stage: str,
        summary: str | None = None,
    ) -> TaskTrace:
        return self.trace_service.update_task_trace(
            trace_id=trace_id,
            status=TraceStatus.COMPLETED,
            current_stage=current_stage,
            summary=summary,
        )

    def fail_task_trace(
        self,
        trace_id: str,
        current_stage: str,
        error_message: str,
        summary: str | None = None,
    ) -> TaskTrace:
        return self.trace_service.update_task_trace(
            trace_id=trace_id,
            status=TraceStatus.FAILED,
            current_stage=current_stage,
            summary=summary,
            error_message=error_message,
        )

    def cancel_task_trace(
        self,
        trace_id: str,
        current_stage: str,
        message: str,
        summary: str | None = None,
    ) -> TaskTrace:
        self.trace_service.append_log(
            trace_id=trace_id,
            level=TraceLogLevel.WARNING,
            stage=current_stage,
            event_type="task_canceled",
            message=message,
        )
        return self.trace_service.update_task_trace(
            trace_id=trace_id,
            status=TraceStatus.CANCELED,
            current_stage=current_stage,
            summary=summary,
        )

    def wait_for_evidence(
        self,
        trace_id: str,
        summary: str | None = None,
    ) -> TaskTrace:
        return self.trace_service.update_task_trace(
            trace_id=trace_id,
            status=TraceStatus.WAITING_FOR_EVIDENCE,
            current_stage="review",
            summary=summary,
        )

    def wait_for_human(
        self,
        trace_id: str,
        current_stage: str = "review",
        summary: str | None = None,
    ) -> TaskTrace:
        return self.trace_service.update_task_trace(
            trace_id=trace_id,
            status=TraceStatus.WAITING_FOR_HUMAN,
            current_stage=current_stage,
            summary=summary,
        )
