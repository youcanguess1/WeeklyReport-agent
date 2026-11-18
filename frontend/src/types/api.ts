export type TaskType = "weekly_report";
export type TaskStatus =
  | "created"
  | "queued"
  | "running"
  | "waiting"
  | "completed"
  | "failed"
  | "canceled";

export type TaskListItem = {
  task_id: string;
  task_name: string;
  task_type: TaskType;
  description: string;
  status: TaskStatus;
  current_node: string | null;
  waiting_reason: string | null;
  current_report_version: string | null;
  artifact_count: number;
  created_at: string;
  updated_at: string;
};

export type TaskListResponse = {
  items: TaskListItem[];
  total: number;
  page: number;
  page_size: number;
};

export type TaskBoardSummary = {
  total_count: number;
  running_count: number;
  pending_count: number;
  completed_count: number;
};

export type TaskCreateResponse = {
  task: TaskListItem;
};

export type TaskWorkbench = {
  task_id: string;
  task_name: string;
  task_type: TaskType;
  description: string;
  status: TaskStatus;
  current_node: string | null;
  waiting_reason: string | null;
  current_report_version: string | null;
  created_at: string;
  updated_at: string;
};

export type TaskOperationType = "feedback" | "supplement";
export type TaskOperationStatus = "queued" | "processing" | "completed" | "failed";

export type TaskOperation = {
  operation_id: string;
  task_id: string;
  request_id: string;
  operation_type: TaskOperationType;
  status: TaskOperationStatus;
  stage: string;
  error_message: string | null;
  result: Record<string, unknown> | null;
  events: Record<string, unknown>[];
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
  updated_at: string;
};

export type WorkbenchEvent = {
  event_id: string;
  event_type: "node_started" | "node_completed" | "node_failed" | "action" | "message";
  timestamp: string;
  agent: string;
  role: "user" | "assistant";
  kind: "message" | "action";
  content?: string | null;
  action_label?: string | null;
  session_id: string;
  sequence: number;
  request_id: string | null;
};

export type WorkbenchEventsResponse = {
  task: TaskWorkbench;
  operations: TaskOperation[];
  events: WorkbenchEvent[];
  cursor: string;
};

export type WeeklyReportVersion = {
  report_version_id: string;
  version: string;
  created_at: string;
  change_note: string | null;
  markdown: string;
  evidence_images?: { material_id: string; url: string }[];
};

export type ArtifactLibrary = "all" | "rules" | "templates" | "tasks";

export type ArtifactListItem = {
  artifact_id: string;
  task_id: string | null;
  library: string;
  filename: string;
  kind: string;
  description: string | null;
  size_bytes: number | null;
  created_at: string;
  media_type: string;
};

export type ArtifactListResponse = {
  items: ArtifactListItem[];
  total: number;
  page: number;
  page_size: number;
};

export type SkillStatus = "candidate" | "active" | "disabled";
export type SkillKind = "writer_skill" | "review_rule" | "failure_pattern";

export type SkillRecord = {
  skill_id: string;
  title: string;
  description: string;
  kind: SkillKind;
  status: SkillStatus;
  content: string;
  task_type: string | null;
  agent_stage: string | null;
  report_section: string | null;
  issue_type: string | null;
  confidence: number;
  hit_count: number;
  disabled: boolean;
  created_at: string;
  updated_at: string;
};

export type SkillUpdatePayload = {
  title: string;
  description: string;
  content: string;
  task_type?: string | null;
  agent_stage?: string | null;
  report_section?: string | null;
  issue_type?: string | null;
};

export type TraceStatus =
  | "running"
  | "waiting_for_evidence"
  | "waiting_for_human"
  | "completed"
  | "failed"
  | "canceled";

export type TraceLogEntry = {
  log_id: string;
  trace_id: string;
  task_id: string;
  timestamp: string;
  level: "info" | "warning" | "error";
  stage: string;
  event_type: string;
  message: string;
  artifact_id: string | null;
  report_version_id: string | null;
  issue_id: string | null;
  extra: Record<string, unknown>;
};

export type TraceObservationNode = {
  observation_id: string;
  parent_observation_id: string | null;
  name: string;
  type: string;
  level: string | null;
  status_message: string | null;
  started_at: string | null;
  ended_at: string | null;
  duration_ms: number | null;
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  total_cost: number | null;
  input: unknown;
  output: unknown;
  metadata: Record<string, unknown>;
  children: TraceObservationNode[];
};

export type StructuredTraceTimeline = {
  trace_id: string;
  task_id: string;
  provider: "langfuse" | "local_fallback";
  source: string;
  trace_url: string | null;
  status: TraceStatus;
  synced_at: string | null;
  cache_artifact_id: string | null;
  sync_error: string | null;
  root: TraceObservationNode | null;
  observations: TraceObservationNode[];
  metrics: {
    observation_count: number;
    error_count: number;
    warning_count: number;
    duration_ms: number | null;
    input_tokens: number | null;
    output_tokens: number | null;
    total_tokens: number | null;
    total_cost: number | null;
  };
  fallback_logs: TraceLogEntry[];
};

export type TraceTimelineResponse = {
  trace: {
    trace_id: string;
    task_id: string;
    task_name: string;
    task_type: string;
    status: TraceStatus;
    current_stage: string;
    started_at: string;
    ended_at: string | null;
    updated_at: string;
    summary: string | null;
    error_message: string | null;
  };
  logs: TraceLogEntry[];
  structured_trace_summary: {
    trace_id: string;
    task_id: string;
    provider: string;
    source: string;
    trace_url: string | null;
    status: TraceStatus;
    synced_at: string | null;
    cache_artifact_id: string | null;
    observation_count: number;
    error_count: number;
    warning_count: number;
  } | null;
};
