from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class TraceStatus(StrEnum):
    RUNNING = "running"
    WAITING_FOR_EVIDENCE = "waiting_for_evidence"
    WAITING_FOR_HUMAN = "waiting_for_human"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class TraceLogLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class TaskTrace(BaseModel):
    trace_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    task_name: str = Field(min_length=1)
    task_type: str = Field(min_length=1)
    status: TraceStatus
    current_stage: str = Field(min_length=1)
    started_at: datetime
    ended_at: datetime | None = None
    updated_at: datetime
    summary: str | None = None
    error_message: str | None = None


class TraceLogEntry(BaseModel):
    log_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    timestamp: datetime
    level: TraceLogLevel
    stage: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    message: str = Field(min_length=1)
    artifact_id: str | None = None
    report_version_id: str | None = None
    issue_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class TraceTimeline(BaseModel):
    trace: TaskTrace
    logs: list[TraceLogEntry] = Field(default_factory=list)
