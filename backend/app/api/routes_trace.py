from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.schemas.structured_trace import StructuredTraceTimeline, TraceTimelineResponse
from app.schemas.trace import TraceLogEntry, TraceTimeline
from app.services.structured_trace_service import StructuredTraceService
from app.services.trace_service import TraceService

router = APIRouter(prefix="/api/traces", tags=["traces"])


def _trace_service_from(request: Request) -> TraceService:
    return request.app.state.trace_service  # type: ignore[return-value]


def _structured_trace_service_from(request: Request) -> StructuredTraceService:
    return request.app.state.structured_trace_service  # type: ignore[return-value]


@router.get("/tasks/{task_id}", response_model=TraceTimelineResponse)
async def get_trace_timeline(task_id: str, request: Request) -> TraceTimelineResponse:
    trace_service = _trace_service_from(request)
    structured_trace_service = _structured_trace_service_from(request)
    try:
        timeline = trace_service.get_trace_timeline(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    structured_trace_summary = None
    try:
        structured_trace_summary = structured_trace_service.get_structured_trace_summary(
            timeline.trace.trace_id
        )
    except Exception:
        structured_trace_summary = None
    return TraceTimelineResponse(
        trace=timeline.trace,
        logs=timeline.logs,
        structured_trace_summary=structured_trace_summary,
    )


@router.get("/{trace_id}/logs", response_model=list[TraceLogEntry])
async def list_trace_logs(trace_id: str, request: Request) -> list[TraceLogEntry]:
    trace_service = _trace_service_from(request)
    try:
        return trace_service.list_logs(trace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{trace_id}/structured", response_model=StructuredTraceTimeline)
async def get_structured_trace(trace_id: str, request: Request) -> StructuredTraceTimeline:
    structured_trace_service = _structured_trace_service_from(request)
    try:
        return structured_trace_service.get_structured_trace(trace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{trace_id}/sync", response_model=StructuredTraceTimeline)
async def sync_structured_trace(trace_id: str, request: Request) -> StructuredTraceTimeline:
    structured_trace_service = _structured_trace_service_from(request)
    try:
        return structured_trace_service.sync_trace(trace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
