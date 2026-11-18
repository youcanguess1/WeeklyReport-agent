from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.task import TaskStatus, TaskType


class TaskCreatePayload(BaseModel):
    task_name: str = Field(min_length=1, max_length=200)
    task_type: TaskType
    template_name: str | None = Field(default=None, max_length=120)
    notes: str | None = None


class TaskListItem(BaseModel):
    task_id: str
    task_name: str
    task_type: TaskType
    description: str = ""
    status: TaskStatus
    current_node: str | None = None
    waiting_reason: str | None = None
    current_report_version: str | None = None
    artifact_count: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime


class TaskListResponse(BaseModel):
    items: list[TaskListItem]
    total: int
    page: int
    page_size: int


class TaskBoardSummary(BaseModel):
    total_count: int
    running_count: int
    pending_count: int
    completed_count: int


class TaskCreateResponse(BaseModel):
    task: TaskListItem
