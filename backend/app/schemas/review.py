from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from app.schemas.report import ReportSection

from pydantic import BaseModel, Field


class ReviewSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class WorkflowDecision(StrEnum):
    APPROVED = "approved"
    REVISE_REPORT = "revise_report"
    REQUEST_EVIDENCE = "request_evidence"
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    FAIL = "fail"


class EvidenceRequestStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


class EvidenceRequest(BaseModel):
    request_id: str = Field(min_length=1)
    section_id: ReportSection | None = None
    reason: str = Field(min_length=1)
    requested_inputs: list[str] = Field(default_factory=list)


class ReviewIssue(BaseModel):
    issue_id: str = Field(min_length=1)
    section_id: ReportSection | None = None
    severity: ReviewSeverity
    category: str = Field(min_length=1)
    description: str = Field(min_length=1)
    suggestion: str | None = None
    requires_evidence: bool = False


class ReviewResult(BaseModel):
    review_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    decision: WorkflowDecision
    summary: str = Field(min_length=1)
    issues: list[ReviewIssue] = Field(default_factory=list)
    evidence_requests: list[EvidenceRequest] = Field(default_factory=list)
    created_at: datetime


class ReviewDraft(BaseModel):
    decision: WorkflowDecision
    summary: str = Field(min_length=1)
    issues: list[ReviewIssue] = Field(default_factory=list)
    evidence_requests: list[EvidenceRequest] = Field(default_factory=list)
