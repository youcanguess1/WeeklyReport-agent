from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.memory import MemoryKind, SkillStatus


class SkillResponse(BaseModel):
    skill_id: str
    title: str
    description: str
    kind: MemoryKind
    status: SkillStatus
    content: str
    task_type: str | None = None
    agent_stage: str | None = None
    report_section: str | None = None
    issue_type: str | None = None
    confidence: float
    hit_count: int
    disabled: bool
    created_at: datetime
    updated_at: datetime


class SkillImportRequest(BaseModel):
    title: str = Field(min_length=1)
    description: str = ""
    content: str = Field(min_length=1)
    task_type: str = Field(min_length=1)
    agent_stage: str = Field(min_length=1)


class SkillUpdateRequest(BaseModel):
    title: str = Field(min_length=1)
    description: str = ""
    content: str = Field(min_length=1)
    task_type: str | None = None
    agent_stage: str | None = None
    report_section: str | None = None
    issue_type: str | None = None
