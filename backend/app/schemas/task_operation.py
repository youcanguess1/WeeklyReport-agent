from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class TaskOperationType(StrEnum):
    FEEDBACK = "feedback"
    SUPPLEMENT = "supplement"


class TaskOperationStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskOperationResponse(BaseModel):
    operation_id: str
    task_id: str
    request_id: str
    operation_type: TaskOperationType
    status: TaskOperationStatus
    stage: str
    error_message: str | None = None
    result: dict[str, Any] | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    updated_at: datetime


class TaskOperationInput(BaseModel):
    request_id: str = Field(min_length=1, max_length=128)
    operation_type: TaskOperationType
    input: dict[str, Any] = Field(default_factory=dict)
    previous_state: dict[str, Any] | None = None
