import { requestJson } from "./client";
import type { StructuredTraceTimeline, TraceTimelineResponse } from "../types/api";

export function getTaskTrace(taskId: string) {
  return requestJson<TraceTimelineResponse>(`/api/traces/tasks/${encodeURIComponent(taskId)}`);
}

export function getStructuredTrace(traceId: string) {
  return requestJson<StructuredTraceTimeline>(`/api/traces/${encodeURIComponent(traceId)}/structured`);
}

export function syncStructuredTrace(traceId: string) {
  return requestJson<StructuredTraceTimeline>(`/api/traces/${encodeURIComponent(traceId)}/sync`, {
    method: "POST",
  });
}
