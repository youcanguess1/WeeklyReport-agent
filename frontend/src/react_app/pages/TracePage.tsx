import {
  ApiOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  CopyOutlined,
  DownOutlined,
  InfoCircleOutlined,
  LoadingOutlined,
  ReloadOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { listTasks } from "../../api/tasks";
import { getStructuredTrace, getTaskTrace } from "../../api/traces";
import type {
  StructuredTraceTimeline,
  TaskListItem,
  TraceObservationNode,
  TraceTimelineResponse,
} from "../../types/api";

type TraceNodeKind = "trace" | "agent" | "tool" | "generation" | "chain" | "span" | "log";

function jsonText(value: unknown) {
  if (value === null || value === undefined) return "{}";
  return JSON.stringify(value, null, 2);
}

function copyText(value: unknown) {
  void navigator.clipboard?.writeText(jsonText(value));
}

function formatDuration(value: number | null) {
  return value === null ? "-" : `${Math.round(value)} ms`;
}

function formatCount(value: number | null) {
  return value === null ? "-" : value.toLocaleString();
}

function aggregateTokenCount(node: TraceObservationNode, field: "total_tokens" | "input_tokens" | "output_tokens"): number | null {
  const ownValue = node[field];
  if (ownValue !== null && ownValue !== undefined) return ownValue;
  const childValues = node.children
    .map((child) => aggregateTokenCount(child, field))
    .filter((value): value is number => value !== null);
  return childValues.length > 0 ? childValues.reduce((sum, value) => sum + value, 0) : null;
}

function isRootNode(node: TraceObservationNode) {
  return node.name === "weekly_report.workflow" || node.type === "root" || node.type === "trace";
}

function nodeKind(node: TraceObservationNode): TraceNodeKind {
  if (isRootNode(node)) return "trace";
  const value = `${node.type} ${node.name}`.toLowerCase();
  if (value.includes("generation")) return "generation";
  if (value.includes("tool")) return "tool";
  if (value.includes("agent")) return "agent";
  if (value.includes("workflow") || node.type === "span") return "chain";
  if (node.type.toLowerCase() === "log") return "log";
  return "span";
}

function nodeKindLabel(node: TraceObservationNode) {
  const kind = nodeKind(node);
  if (kind === "trace") return "ROOT";
  if (kind === "generation") return "LLM";
  if (kind === "chain") return "AGENT";
  return kind.toUpperCase();
}

function nodeName(node: TraceObservationNode) {
  if (isRootNode(node)) return "LangGraph Task Execution";
  if (nodeKind(node) === "tool") return node.name.replace(/^tool\./, "");
  if (nodeKind(node) === "generation" && node.model) return node.model;
  const rawName = node.name.replace(/^workflow\./, "");
  const displayNames: Record<string, string> = {
    "WeeklyReport Writer": "周报 Writer",
    "WeeklyReport Review": "周报 Review",
  };
  return displayNames[rawName] ?? rawName;
}

function levelLabel(level: string | null) {
  if (level === "error") return "error";
  if (level === "warning") return "warning";
  if (!level || level === "ok" || level === "observationlevel.default") return "ok";
  return level;
}

function levelClass(level: string | null) {
  if (level === "error") return "text-red-600";
  if (level === "warning") return "text-amber-600";
  return "text-emerald-600";
}

function kindClass(node: TraceObservationNode) {
  switch (nodeKind(node)) {
    case "trace": return "bg-slate-900 text-white";
    case "generation": return "bg-violet-100 text-violet-700";
    case "tool": return "bg-emerald-100 text-emerald-700";
    case "agent":
    case "chain": return "bg-blue-100 text-blue-700";
    case "log": return "bg-amber-100 text-amber-700";
    default: return "bg-slate-100 text-slate-600";
  }
}

function nodeStatusIcon(node: TraceObservationNode) {
  if (node.level === "error") return <CloseCircleOutlined className="text-red-500" />;
  if (node.level === "warning") return <WarningOutlined className="text-amber-500" />;
  return <CheckCircleOutlined className="text-emerald-500" />;
}

function findNode(root: TraceObservationNode | null, observationId: string | null): TraceObservationNode | null {
  if (!root || !observationId) return null;
  if (root.observation_id === observationId) return root;
  for (const child of root.children) {
    const match = findNode(child, observationId);
    if (match) return match;
  }
  return null;
}

function defaultNode(root: TraceObservationNode | null): TraceObservationNode | null {
  return root?.children.at(-1) ?? root;
}

function buildClientFallbackTimeline(response: TraceTimelineResponse): StructuredTraceTimeline {
  const { trace, logs } = response;
  const children: TraceObservationNode[] = logs.map((log) => ({
    observation_id: `log_${log.log_id}`,
    parent_observation_id: trace.trace_id,
    name: `${log.stage} · ${log.event_type}`,
    type: "log",
    level: log.level,
    status_message: log.message,
    started_at: log.timestamp,
    ended_at: log.timestamp,
    duration_ms: 0,
    model: null,
    input_tokens: null,
    output_tokens: null,
    total_tokens: null,
    total_cost: null,
    input: log.event_type.endsWith("_started") ? log.extra : null,
    output: log.event_type.endsWith("_completed") || log.event_type.endsWith("_failed") ? log.extra : null,
    metadata: {
      stage: log.stage,
      artifact_id: log.artifact_id,
      report_version_id: log.report_version_id,
      issue_id: log.issue_id,
      ...log.extra,
    },
    children: [],
  }));

  return {
    trace_id: trace.trace_id,
    task_id: trace.task_id,
    provider: "local_fallback",
    source: "local_fallback",
    trace_url: null,
    status: trace.status,
    synced_at: null,
    cache_artifact_id: null,
    sync_error: "Langfuse structured trace unavailable; showing local trace logs.",
    root: {
      observation_id: trace.trace_id,
      parent_observation_id: null,
      name: "weekly_report.workflow",
      type: "trace",
      level: trace.status === "failed" ? "error" : null,
      status_message: trace.error_message,
      started_at: trace.started_at,
      ended_at: trace.ended_at,
      duration_ms: trace.ended_at ? Math.max(Date.parse(trace.ended_at) - Date.parse(trace.started_at), 0) : null,
      model: null,
      input_tokens: null,
      output_tokens: null,
      total_tokens: null,
      total_cost: null,
      input: null,
      output: null,
      metadata: {
        task_id: trace.task_id,
        current_stage: trace.current_stage,
        summary: trace.summary,
      },
      children,
    },
    observations: children,
    metrics: {
      observation_count: children.length,
      error_count: children.filter((node) => node.level === "error").length,
      warning_count: children.filter((node) => node.level === "warning").length,
      duration_ms: null,
      input_tokens: null,
      output_tokens: null,
      total_tokens: null,
      total_cost: null,
    },
    fallback_logs: logs,
  };
}

function TraceTreeNode({
  node,
  selectedId,
  onSelect,
}: {
  node: TraceObservationNode;
  selectedId: string;
  onSelect: (node: TraceObservationNode) => void;
}) {
  const [expanded, setExpanded] = useState(true);
  const selected = selectedId === node.observation_id;
  const root = isRootNode(node);

  return (
    <div className={`relative ${root ? "" : "ml-4 border-l border-slate-200 pl-4"}`}>
      <div
        role="button"
        tabIndex={0}
        onClick={() => onSelect(node)}
        onKeyDown={(event) => event.key === "Enter" && onSelect(node)}
        className={`group grid min-h-11 cursor-pointer grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-3 rounded-[10px] px-3 py-2 transition-colors hover:bg-slate-50 ${selected ? "bg-white shadow-[0_3px_12px_rgba(15,23,42,0.08)] ring-1 ring-slate-200" : ""}`}
      >
        <div className="flex min-w-0 items-center gap-2.5">
          {node.children.length > 0 ? (
            <button
              type="button"
              aria-label={expanded ? "折叠节点" : "展开节点"}
              onClick={(event) => { event.stopPropagation(); setExpanded((value) => !value); }}
              className="flex h-5 w-5 shrink-0 items-center justify-center text-slate-400 hover:text-slate-700"
            >
              <DownOutlined className={`text-[10px] transition-transform ${expanded ? "" : "-rotate-90"}`} />
            </button>
          ) : <span className="w-5 shrink-0" />}
          <span className={`rounded-md px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide ${kindClass(node)}`}>{nodeKindLabel(node)}</span>
          <span className="truncate font-medium text-slate-800" title={node.name}>{nodeName(node)}</span>
        </div>
        <div className={`flex shrink-0 items-center gap-1 text-[12px] font-semibold ${levelClass(node.level)}`}>
          {nodeStatusIcon(node)}
          {formatDuration(node.duration_ms)}
        </div>
        <div className="w-14 shrink-0 text-right text-[11px] font-medium text-slate-400">{aggregateTokenCount(node, "total_tokens") === null ? "-" : `${formatCount(aggregateTokenCount(node, "total_tokens"))} tks`}</div>
      </div>
      {expanded && node.children.length > 0 && (
        <div className="mt-1 space-y-1">
          {node.children.map((child) => <TraceTreeNode key={child.observation_id} node={child} selectedId={selectedId} onSelect={onSelect} />)}
        </div>
      )}
    </div>
  );
}

export function TracePage() {
  const { taskId = "" } = useParams();
  const navigate = useNavigate();
  const [tasks, setTasks] = useState<TaskListItem[]>([]);
  const [timeline, setTimeline] = useState<StructuredTraceTimeline | null>(null);
  const [selectedObservationId, setSelectedObservationId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tracePending, setTracePending] = useState(false);

  const loadTrace = useCallback(async ({ silent = false }: { silent?: boolean } = {}) => {
    if (!silent) setLoading(true);
    setError(null);
    setTracePending(false);
    try {
      const taskResponse = await listTasks({ page: 1, page_size: 50 });
      setTasks(taskResponse.items);
      let traceResponse: TraceTimelineResponse;
      try {
        traceResponse = await getTaskTrace(taskId);
      } catch (traceError) {
        if (traceError instanceof Error && traceError.message.toLowerCase().includes("no trace")) {
          setTimeline(null);
          setSelectedObservationId(null);
          setTracePending(true);
          return;
        }
        throw traceError;
      }

      let structured: StructuredTraceTimeline;
      try {
        structured = await getStructuredTrace(traceResponse.trace.trace_id);
      } catch (structuredError) {
        structured = buildClientFallbackTimeline(traceResponse);
        structured.sync_error = structuredError instanceof Error ? structuredError.message : structured.sync_error;
      }
      setTimeline(structured);
      setSelectedObservationId((currentId) => {
        if (currentId && findNode(structured.root, currentId)) return currentId;
        return defaultNode(structured.root)?.observation_id ?? null;
      });
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "运行追踪数据加载失败");
    } finally {
      setLoading(false);
    }
  }, [taskId]);

  useEffect(() => {
    setTimeline(null);
    setSelectedObservationId(null);
    void loadTrace();
  }, [loadTrace]);

  useEffect(() => {
    if (!tracePending && timeline?.status !== "running") return;
    const timer = window.setInterval(() => { void loadTrace({ silent: true }); }, 1500);
    return () => window.clearInterval(timer);
  }, [loadTrace, timeline?.status, tracePending]);

  const currentTask = useMemo(() => tasks.find((item) => item.task_id === taskId), [taskId, tasks]);
  const selectedNode = useMemo(
    () => findNode(timeline?.root ?? null, selectedObservationId) ?? defaultNode(timeline?.root ?? null),
    [selectedObservationId, timeline],
  );

  if (loading) return <div className="flex min-h-full flex-1 items-center justify-center text-sm text-gray-500"><LoadingOutlined className="mr-2" />正在加载运行追踪...</div>;
  if (tracePending) return <main className="flex min-h-full flex-1 flex-col items-center justify-center text-sm text-gray-500"><LoadingOutlined className="mb-3 text-2xl" /><p>周报运行记录尚未产生，正在等待工作流启动...</p><button type="button" onClick={() => void loadTrace()} className="mt-4 flex items-center gap-2 rounded-full bg-gray-900 px-4 py-2 text-white"><ReloadOutlined />重试</button></main>;
  if (error) return <main className="flex min-h-full flex-1 flex-col items-center justify-center text-sm text-red-600"><CloseCircleOutlined className="mb-3 text-2xl" /><p>{error}</p><button type="button" onClick={() => void loadTrace()} className="mt-4 flex items-center gap-2 rounded-full bg-gray-900 px-4 py-2 text-white"><ReloadOutlined />重试</button></main>;

  const root = timeline?.root;
  const isLive = timeline?.source === "langfuse_live";
  return (
    <main className="flex min-h-full flex-1 flex-col overflow-hidden bg-[#f7f4ef] text-slate-800">
      <header className="flex shrink-0 items-center justify-between border-b border-[#e9e4dc] bg-[#fbfaf7] px-6 py-4 xl:px-8">
        <div className="flex items-center gap-2 text-[14px]"><span className="font-semibold text-slate-500">周报助手</span><span className="text-slate-300">/</span><span className="font-semibold text-slate-900">运行追踪</span></div>
        <button type="button" aria-label="刷新运行追踪" onClick={() => void loadTrace()} className="flex h-9 w-9 items-center justify-center rounded-full border border-[#e4dfd7] bg-white text-slate-400 shadow-sm transition hover:border-slate-300 hover:text-slate-900"><ReloadOutlined /></button>
      </header>
      <div className="flex min-h-[72px] shrink-0 flex-wrap items-center gap-3 border-b border-[#e9e4dc] px-6 py-3 xl:px-8">
        <ApiOutlined className="text-[17px] text-blue-500" />
        <span className="text-[13px] font-medium text-slate-500">当前周报:</span>
        <select value={taskId} onChange={(event) => navigate(`/tasks/${event.target.value}/trace`)} className="min-w-0 max-w-full rounded-[12px] border border-[#e4dfd7] bg-white px-4 py-2.5 text-[13px] font-medium text-slate-800 shadow-sm outline-none focus:border-blue-400">
          {tasks.map((task) => <option key={task.task_id} value={task.task_id}>{task.task_id} - {task.task_name}</option>)}
        </select>
        <span className="min-w-0 truncate text-[12px] text-slate-500">{currentTask?.task_name || ""}</span>
        <div className="ml-auto flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide">
          <span className={`rounded-full px-2.5 py-1 ${isLive ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-700"}`}>{isLive ? "Langfuse Live" : "Local Fallback"}</span>
          <span className="text-slate-400">{timeline?.metrics.observation_count ?? 0} observations</span>
        </div>
      </div>
      {!isLive && <div className="mx-6 mt-4 flex shrink-0 items-start gap-2 rounded-[12px] border border-amber-200 bg-amber-50 px-4 py-3 text-[12px] leading-5 text-amber-800 xl:mx-8"><WarningOutlined className="mt-0.5 shrink-0" /><span>当前周报没有 Langfuse observation，页面展示的是本地运行日志。重新执行后才会生成结构化节点和模型/工具详情。</span></div>}
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-5 overflow-hidden p-5 pt-4 xl:grid-cols-[minmax(0,1.08fr)_minmax(390px,0.92fr)] xl:px-8">
        <section className="flex min-h-0 flex-col overflow-hidden rounded-[22px] border border-[#ebe7e1] bg-[#fffdfa] shadow-[0_12px_32px_-24px_rgba(15,23,42,0.38)]">
          <div className="flex items-center justify-between border-b border-[#eeeae4] px-6 py-5"><div className="flex items-center gap-3"><ApiOutlined className="text-[22px] text-slate-500" /><div><h2 className="text-[17px] font-bold text-slate-900">Workflow Nodes</h2><p className="mt-1 text-[11px] text-slate-400">LangGraph execution tree</p></div></div><div className="grid grid-cols-[auto_56px] gap-3 pr-1 text-[10px] font-bold uppercase tracking-wide text-slate-400"><span>Duration</span><span className="text-right">Tokens</span></div></div>
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5 text-[13px]">{root ? <TraceTreeNode node={root} selectedId={selectedNode?.observation_id || ""} onSelect={(node) => setSelectedObservationId(node.observation_id)} /> : <div className="flex h-full items-center justify-center text-sm text-slate-400">暂无运行追踪节点</div>}</div>
        </section>
        <section className="flex min-h-0 flex-col overflow-hidden rounded-[22px] border border-[#ebe7e1] bg-[#fffdfa] shadow-[0_12px_32px_-24px_rgba(15,23,42,0.38)]">
          <div className="border-b border-[#eeeae4] px-6 py-5"><div className="flex items-center gap-3"><InfoCircleOutlined className="text-[22px] text-blue-500" /><div className="min-w-0"><p className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Inspector</p><h2 className="mt-1 truncate text-[17px] font-bold text-slate-900">Inspector: {selectedNode ? nodeName(selectedNode) : "-"}</h2>{selectedNode && !isRootNode(selectedNode) && selectedNode.name !== nodeName(selectedNode) && <p className="mt-1 truncate text-[11px] text-slate-400">{selectedNode.name}</p>}</div></div></div>
          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
            <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-4"><Metric label="Status" value={selectedNode ? levelLabel(selectedNode.level) : "-"} /><Metric label="Duration" value={formatDuration(selectedNode?.duration_ms ?? null)} icon={<ClockCircleOutlined />} /><Metric label="Tokens" value={formatCount(selectedNode ? aggregateTokenCount(selectedNode, "total_tokens") : null)} /><Metric label="Model" value={selectedNode?.model || "-"} /></div>
            <div className="mb-6 flex flex-wrap gap-2 text-[11px] text-slate-500"><span className={`rounded-full px-2.5 py-1 font-semibold ${selectedNode ? kindClass(selectedNode) : "bg-slate-100"}`}>{selectedNode ? nodeKindLabel(selectedNode) : "NODE"}</span>{selectedNode && aggregateTokenCount(selectedNode, "input_tokens") !== null && <span className="rounded-full bg-slate-100 px-2.5 py-1">in {formatCount(aggregateTokenCount(selectedNode, "input_tokens"))}</span>}{selectedNode && aggregateTokenCount(selectedNode, "output_tokens") !== null && <span className="rounded-full bg-slate-100 px-2.5 py-1">out {formatCount(aggregateTokenCount(selectedNode, "output_tokens"))}</span>}</div>
            <Payload label="Input Payload" value={selectedNode?.input} />
            <Payload label="Output Payload" value={selectedNode?.output} />
            <Payload label="Metadata" value={selectedNode?.metadata} />
            {selectedNode?.status_message && <div className="mt-1 flex gap-2 rounded-[12px] border border-slate-200 bg-slate-50 p-3 text-[12px] leading-5 text-slate-600"><InfoCircleOutlined className="mt-0.5 shrink-0 text-slate-400" /><span>{selectedNode.status_message}</span></div>}
          </div>
        </section>
      </div>
    </main>
  );
}

function Metric({ label, value, icon }: { label: string; value: string; icon?: ReactNode }) {
  return <div className="min-w-0 rounded-[12px] border border-[#eeeae4] bg-[#fbfaf7] p-3"><div className="mb-1 flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide text-slate-400">{icon}{label}</div><div className="truncate text-[12px] font-bold text-slate-800" title={value}>{value}</div></div>;
}

function Payload({ label, value }: { label: string; value: unknown }) {
  return <div className="mb-5"><div className="mb-2 flex items-center justify-between pl-1 text-[12.5px] font-bold text-blue-600"><span>{label}</span><button type="button" aria-label={`Copy ${label}`} onClick={() => copyText(value)} className="rounded-md p-1 text-slate-400 transition hover:bg-slate-100 hover:text-slate-800"><CopyOutlined /></button></div><pre className="max-h-[220px] overflow-auto rounded-[12px] bg-[#1d1d1d] p-4 font-mono text-[12px] leading-6 text-slate-300">{jsonText(value)}</pre></div>;
}
