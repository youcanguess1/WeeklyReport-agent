from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ReportSection(StrEnum):
    COMPLETED_THIS_WEEK = "completed_this_week"
    IN_PROGRESS = "in_progress"
    RISKS_AND_BLOCKERS = "risks_and_blockers"
    NEXT_WEEK_PLAN = "next_week_plan"
    PENDING_CONFIRMATIONS = "pending_confirmations"


class WeeklyReportDocumentDraft(BaseModel):
    project_name: str = Field(min_length=1)
    completed_this_week: list[str] = Field(default_factory=list)
    in_progress: list[str] = Field(default_factory=list)
    risks_and_blockers: list[str] = Field(default_factory=list)
    next_week_plan: list[str] = Field(default_factory=list)
    pending_confirmations: list[str] = Field(default_factory=list)


class WeeklyReportDocument(WeeklyReportDocumentDraft):
    version: str = Field(default="V0", min_length=1)
    report_period: str | None = None
    material_refs: list[str] = Field(default_factory=list)
    raw_markdown: str = ""
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
