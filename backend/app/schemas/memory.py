from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class MemoryKind(StrEnum):
    WRITER_SKILL = "writer_skill"
    REVIEW_RULE = "review_rule"
    FAILURE_PATTERN = "failure_pattern"


class SkillStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    DISABLED = "disabled"


class MemoryUpdateAction(StrEnum):
    CREATED = "created"
    PROMOTED = "promoted"
    MERGED = "merged"
    DOWNGRADED = "downgraded"
    DISABLED = "disabled"


class SkillTrigger(BaseModel):
    task_type: str | None = None
    agent_stage: str | None = None
    report_section: str | None = None
    issue_type: str | None = None


class SkillBrief(BaseModel):
    skill_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    kind: MemoryKind
    trigger: SkillTrigger = Field(default_factory=SkillTrigger)
    confidence: float = Field(ge=0, le=1)
    hit_count: int = Field(default=0, ge=0)


class SkillRecord(BaseModel):
    skill_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    kind: MemoryKind
    status: SkillStatus
    content: str = Field(min_length=1)
    trigger: SkillTrigger = Field(default_factory=SkillTrigger)
    confidence: float = Field(ge=0, le=1)
    hit_count: int = Field(default=0, ge=0)
    disabled: bool = False
    created_at: datetime
    updated_at: datetime


class FailurePattern(BaseModel):
    pattern_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    symptom: str = Field(min_length=1)
    cause: str = Field(min_length=1)
    fix: str = Field(min_length=1)
    related_issue_types: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class MemoryUpdate(BaseModel):
    update_id: str = Field(min_length=1)
    kind: MemoryKind
    action: MemoryUpdateAction
    target_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    source_task_id: str | None = None
    created_at: datetime
