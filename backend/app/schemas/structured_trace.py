from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.trace import TaskTrace, TraceLogEntry, TraceStatus


class StructuredTraceSource(StrEnum):
    LANGFUSE_LIVE = "langfuse_live"
    LANGFUSE_CACHE = "langfuse_cache"
    LOCAL_FALLBACK = "local_fallback"


class TraceObservationNode(BaseModel):
    observation_id: str = Field(min_length=1)
    parent_observation_id: str | None = None
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    level: str | None = None
    status_message: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: float | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    total_cost: float | None = None
    input: Any | None = None
    output: Any | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    children: list["TraceObservationNode"] = Field(default_factory=list)


class TraceMetricsSummary(BaseModel):
    observation_count: int = 0
    error_count: int = 0
    warning_count: int = 0
    duration_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    total_cost: float | None = None


class StructuredTraceSummary(BaseModel):
    trace_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    provider: Literal["langfuse", "local_fallback"]
    source: StructuredTraceSource
    trace_url: str | None = None
    status: TraceStatus
    synced_at: datetime | None = None
    cache_artifact_id: str | None = None
    observation_count: int = 0
    error_count: int = 0
    warning_count: int = 0


class StructuredTraceTimeline(BaseModel):
    trace_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    provider: Literal["langfuse", "local_fallback"]
    source: StructuredTraceSource
    trace_url: str | None = None
    status: TraceStatus
    synced_at: datetime | None = None
    cache_artifact_id: str | None = None
    sync_error: str | None = None
    root: TraceObservationNode | None = None
    observations: list[TraceObservationNode] = Field(default_factory=list)
    metrics: TraceMetricsSummary = Field(default_factory=TraceMetricsSummary)
    fallback_logs: list[TraceLogEntry] = Field(default_factory=list)


class TraceTimelineResponse(BaseModel):
    trace: TaskTrace
    logs: list[TraceLogEntry] = Field(default_factory=list)
    structured_trace_summary: StructuredTraceSummary | None = None


TraceObservationNode.model_rebuild()
