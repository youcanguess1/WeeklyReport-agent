from __future__ import annotations

from enum import StrEnum


class TaskType(StrEnum):
    WEEKLY_REPORT = "weekly_report"


class TaskStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
