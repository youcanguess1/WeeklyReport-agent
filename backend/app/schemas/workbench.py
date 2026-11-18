from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.task import TaskStatus, TaskType
from app.schemas.task_operation import TaskOperationResponse


class TaskWorkbenchResponse(BaseModel):
    task_id: str
    task_name: str
    task_type: TaskType
    description: str = ""
    status: TaskStatus
    current_node: str | None = None
    waiting_reason: str | None = None
    current_report_version: str | None = None
    created_at: datetime
    updated_at: datetime


class WorkbenchEvent(BaseModel):
    event_id: str
    event_type: Literal["node_started", "node_completed", "node_failed", "action", "message"]
    timestamp: datetime
    agent: str
    role: Literal["user", "assistant"]
    kind: Literal["message", "action"] = "message"
    content: str | None = None
    action_label: str | None = None
    session_id: str
    sequence: int
    request_id: str | None = None


class WorkbenchEventsResponse(BaseModel):
    task: TaskWorkbenchResponse
    operations: list[TaskOperationResponse]
    events: list[WorkbenchEvent]
    cursor: str


class WeeklyReportEvidenceImageResponse(BaseModel):
    material_id: str
    url: str


class WeeklyReportVersionResponse(BaseModel):
    report_version_id: str
    version: str
    created_at: datetime
    change_note: str | None = None
    markdown: str
    evidence_images: list[WeeklyReportEvidenceImageResponse] = Field(default_factory=list)
