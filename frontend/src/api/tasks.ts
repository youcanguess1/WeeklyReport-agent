import { requestJson } from "./client";
import type {
  TaskBoardSummary,
  TaskCreateResponse,
  WeeklyReportVersion,
  TaskOperation,
  TaskListResponse,
  TaskStatus,
  TaskType,
  TaskWorkbench,
  WorkbenchEventsResponse,
} from "../types/api";

export function listTasks(params: {
  q?: string;
  status?: TaskStatus;
  task_type?: TaskType;
  page?: number;
  page_size?: number;
} = {}) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== "") query.set(key, String(value));
  });
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return requestJson<TaskListResponse>(`/api/tasks${suffix}`);
}

export function getTaskSummary() {
  return requestJson<TaskBoardSummary>("/api/tasks/summary");
}

export function createTask(formData: FormData) {
  return requestJson<TaskCreateResponse>("/api/tasks", {
    method: "POST",
    body: formData,
  });
}

export function getTask(taskId: string) {
  return requestJson<TaskWorkbench>(`/api/tasks/${encodeURIComponent(taskId)}`);
}

export function listTaskEvents(taskId: string, cursor?: string) {
  const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
  return requestJson<WorkbenchEventsResponse>(
    `/api/tasks/${encodeURIComponent(taskId)}/events${query}`,
  );
}

export function listTaskWeeklyReportVersions(taskId: string) {
  return requestJson<WeeklyReportVersion[]>(
    `/api/tasks/${encodeURIComponent(taskId)}/weekly-report-versions`,
  );
}

export type TaskControlAction = "start" | "pause" | "resume" | "cancel";

export function controlTask(taskId: string, action: TaskControlAction) {
  return requestJson<TaskWorkbench>(
    `/api/tasks/${encodeURIComponent(taskId)}/${action}`,
    { method: "POST" },
  );
}

export function confirmTask(taskId: string) {
  return requestJson<TaskWorkbench>(
    `/api/tasks/${encodeURIComponent(taskId)}/confirm`,
    { method: "POST" },
  );
}

export function submitHumanFeedback(taskId: string, formData: FormData) {
  return requestJson<TaskOperation>(
    `/api/tasks/${encodeURIComponent(taskId)}/feedback`,
    { method: "POST", body: formData },
  );
}

export function uploadTaskSupplement(taskId: string, formData: FormData) {
  return requestJson<TaskOperation>(
    `/api/tasks/${encodeURIComponent(taskId)}/supplements`,
    { method: "POST", body: formData },
  );
}
