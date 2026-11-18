import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  CloudUploadOutlined,
  DownloadOutlined,
  LoadingOutlined,
  PauseOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  StopOutlined,
  SendOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRef } from "react";
import { useNavigate, useParams } from "react-router-dom";

import {
  controlTask,
  confirmTask,
  listTaskEvents,
  listTaskWeeklyReportVersions,
  listTasks,
  submitHumanFeedback,
  uploadTaskSupplement,
  type TaskControlAction,
} from "../../api/tasks";
import type {
  WeeklyReportVersion,
  TaskListItem,
  TaskOperation,
  TaskStatus,
  TaskWorkbench,
  WorkbenchEvent,
} from "../../types/api";
import { WeeklyReportMarkdownPreview } from "../WeeklyReportMarkdownPreview";

const statusMeta: Record<TaskStatus, { label: string; className: string }> = {
  created: { label: "待开始", className: "bg-gray-100 text-gray-600" },
  queued: { label: "排队中", className: "bg-blue-100 text-blue-700" },
  running: { label: "运行中", className: "bg-blue-100 text-blue-700" },
  waiting: { label: "待处理", className: "bg-amber-100 text-amber-700" },
  completed: { label: "已完成", className: "bg-emerald-100 text-emerald-700" },
  failed: { label: "失败", className: "bg-red-100 text-red-700" },
  canceled: { label: "已取消", className: "bg-gray-100 text-gray-600" },
};

type WorkflowTimelineEntry = {
  start: WorkbenchEvent;
  end: WorkbenchEvent | null;
  actions: WorkbenchEvent[];
};

function buildWorkflowEntries(events: WorkbenchEvent[]): WorkflowTimelineEntry[] {
  const groups: WorkflowTimelineEntry[] = [];
  let active: WorkflowTimelineEntry | null = null;

  for (const event of events) {
    if (event.event_type === "node_started") {
      active = { start: event, end: null, actions: [] };
      groups.push(active);
      continue;
    }
    if (event.event_type === "action") {
      if (active && event.agent === active.start.agent) active.actions.push(event);
      continue;
    }
    if (
      (event.event_type === "node_completed" || event.event_type === "node_failed") &&
      active &&
      event.agent === active.start.agent
    ) {
      active.end = event;
      active = null;
      continue;
    }
    if (event.event_type === "message" || event.role === "user") {
      active = null;
      groups.push({ start: event, end: null, actions: [] });
      continue;
    }
    if (event.event_type === "node_completed" || event.event_type === "node_failed") {
      groups.push({ start: event, end: null, actions: [] });
    }
  }
  return groups;
}

function sanitizeDownloadFileName(value: string) {
  return value.replace(/[<>:"/\\|?*\u0000-\u001F]/g, "_").replace(/\s+/g, " ").trim() || "weekly-report";
}

function buildMarkdownDownloadFileName(taskName: string, version: string) {
  return `${sanitizeDownloadFileName(taskName)}_${sanitizeDownloadFileName(version)}.md`;
}

function displayAgentName(value: string) {
  const aliases: Record<string, string> = {
    "WeeklyReport Writer": "周报 Writer",
    "WeeklyReport Review": "周报 Review",
  };
  return aliases[value] ?? value.replace(/WeeklyReport/gi, "周报");
}

function displayWorkflowText(value: string) {
  return value
    .replace(/保存位置：[^\r\n]+/g, "周报 Markdown 已保存至任务产物目录")
    .replace(/WeeklyReport/gi, "周报");
}

function displayChangeNote(value: string | null) {
  const note = value?.trim();
  if (!note) return "版本预览";
  const aliases: Record<string, string> = {
    "Initial WeeklyReport draft": "初始周报",
    Initial: "初始版本",
    "Revision from review issues": "根据审核意见修订",
    "Review revision": "审核修订",
  };
  return aliases[note] ?? displayWorkflowText(note);
}

function WorkflowEventBubbleV2({
  entry,
  canConfirm,
  onConfirm,
}: {
  entry: WorkflowTimelineEntry;
  canConfirm: boolean;
  onConfirm: () => void;
}) {
  const event = entry.end ?? entry.start;
  const isUser = event.event_type === "message" || event.role === "user";
  if (isUser) {
    return (
      <div className="flex flex-row-reverse gap-3">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[#111827] text-[10px] font-bold text-white">U</div>
        <div className="min-w-0 max-w-[88%] text-right">
          <div className="mb-1 flex items-center justify-end gap-2 text-[11px] text-gray-400"><span>{new Date(event.timestamp).toLocaleTimeString()}</span><span>用户</span></div>
          <div className="whitespace-pre-line rounded-[18px] rounded-tr-sm bg-[#111827] px-4 py-3 text-left text-[13px] leading-relaxed text-white shadow-[0_8px_20px_-14px_rgba(15,23,42,0.7)]">{event.content ? displayWorkflowText(event.content) : "已提交反馈"}</div>
        </div>
      </div>
    );
  }

  const failed = event.event_type === "node_failed";
  const completed = event.event_type === "node_completed";
  const statusLabel = failed ? "失败" : completed ? "已完成" : "处理中";
  const statusClass = failed ? "text-red-600" : completed ? "text-emerald-600" : "text-blue-600";
  const startContent = displayWorkflowText(entry.start.content || "正在处理当前节点");
  const rawEndContent = entry.end?.content || null;
  const endContent = rawEndContent && rawEndContent !== entry.start.content ? displayWorkflowText(rawEndContent) : null;

  const renderSummary = (content: string, extraClass = "") => {
    const lines = content.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    if (lines.length <= 1) return <span className={`whitespace-pre-line ${extraClass}`}>{content}</span>;
    return (
      <span className={`block space-y-1 whitespace-pre-line ${extraClass}`}>
        {lines.map((line, index) => <span key={`${line}-${index}`} className="block">{line}</span>)}
      </span>
    );
  };

  return (
    <div className="max-w-[94%]">
      <div className="mb-1.5 flex items-center justify-between gap-3 px-1 text-[12px] font-semibold text-[#1f3b5b]">
        <span>{displayAgentName(event.agent)}</span>
        <span className={`flex items-center gap-1 text-[11px] font-medium ${statusClass}`}>
          {failed ? <CloseCircleOutlined /> : completed ? <CheckCircleOutlined /> : <LoadingOutlined />}
          <span>{statusLabel}</span>
        </span>
      </div>
      <div className="rounded-[18px] rounded-tl-sm border border-[#e5e9ef] bg-white px-4 py-3.5 text-gray-700 shadow-[0_5px_18px_-16px_rgba(15,23,42,0.55)]">
        <div className="flex items-start gap-2 text-[13px] leading-6 text-[#24364b]">
          {completed ? <CheckCircleOutlined className="mt-1 shrink-0 text-emerald-500" /> : failed ? <CloseCircleOutlined className="mt-1 shrink-0 text-red-500" /> : <LoadingOutlined className="mt-1 shrink-0 text-blue-500" />}
          {renderSummary(startContent)}
        </div>
        {entry.actions.length > 0 && <div className="mt-2.5 space-y-1.5 border-t border-[#edf0f4] pt-2.5">{entry.actions.map((action) => <div key={action.event_id} className="flex items-center gap-2 text-[12px] text-gray-500"><CheckCircleOutlined className="text-emerald-500" /><span>{displayAgentName(action.action_label || "执行工具")}</span><span className="ml-auto text-[11px] text-gray-400">已完成</span></div>)}</div>}
        {endContent && <div className={`mt-2.5 border-t border-[#edf0f4] pt-2.5 text-[13px] leading-6 ${failed ? "text-red-700" : "text-[#24364b]"}`}>{renderSummary(endContent)}</div>}
        {canConfirm && <button type="button" onClick={onConfirm} className="mt-3 rounded-lg bg-[#1c1c1c] px-3 py-2 text-[12px] font-semibold text-white hover:bg-black">确认最终周报</button>}
      </div>
    </div>
  );
}

function WorkflowTimeline({
  events,
  confirmationEventId,
  onConfirm,
}: {
  events: WorkbenchEvent[];
  confirmationEventId?: string;
  onConfirm: () => void;
}) {
  return <>{buildWorkflowEntries(events).map((group) => {
    const reviewEvent = group.end ?? group.start;
    const isFinalConfirmationReview = reviewEvent.agent === "周报 Review"
      && reviewEvent.event_type === "node_completed"
      && reviewEvent.content?.includes("是否选择当前周报作为最终版") === true;
    return <WorkflowEventBubbleV2 key={group.start.event_id} entry={group} canConfirm={isFinalConfirmationReview && reviewEvent.event_id === confirmationEventId} onConfirm={onConfirm} />;
  })}</>;
}

function ActionButton({
  action,
  label,
  icon,
  onClick,
  disabled,
}: {
  action: TaskControlAction;
  label: string;
  icon: React.ReactNode;
  onClick: (action: TaskControlAction) => void;
  disabled: boolean;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => onClick(action)}
      className="flex items-center gap-1.5 rounded-full border border-gray-200 bg-white px-3.5 py-2 text-[12px] font-medium text-gray-700 shadow-sm transition hover:border-gray-300 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
    >
      {icon}
      {label}
    </button>
  );
}

export function WorkbenchPage() {
  const { taskId = "" } = useParams();
  const navigate = useNavigate();
  const feedbackInputRef = useRef<HTMLInputElement>(null);
  const supplementInputRef = useRef<HTMLInputElement>(null);
  const [task, setTask] = useState<TaskWorkbench | null>(null);
  const [tasks, setTasks] = useState<TaskListItem[]>([]);
  const [messages, setMessages] = useState<WorkbenchEvent[]>([]);
  const [operations, setOperations] = useState<TaskOperation[]>([]);
  const cursorRef = useRef("");
  const [versions, setVersions] = useState<WeeklyReportVersion[]>([]);
  const [selectedVersionId, setSelectedVersionId] = useState("");
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [submitLoading, setSubmitLoading] = useState(false);
  const [confirmLoading, setConfirmLoading] = useState(false);
  const [feedbackText, setFeedbackText] = useState("");
  const [feedbackFile, setFeedbackFile] = useState<File | null>(null);
  const [supplementFile, setSupplementFile] = useState<File | null>(null);
  const [supplementNote, setSupplementNote] = useState("");
  const [supplementOpen, setSupplementOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mergeEvents = useCallback((incoming: WorkbenchEvent[]) => {
    setMessages((current) => {
      const byId = new Map(current.map((item) => [item.event_id, item]));
      for (const event of incoming) {
        const optimistic = [...byId.values()].find(
          (item) => item.request_id && item.request_id === event.request_id,
        );
        if (optimistic) byId.delete(optimistic.event_id);
        byId.set(event.event_id, event);
      }
      return [...byId.values()].sort(
        (left, right) => new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime(),
      );
    });
  }, []);

  const loadWorkbench = useCallback(async ({ silent = false }: { silent?: boolean } = {}) => {
    if (!silent) setLoading(true);
    setError(null);
    try {
      const [eventsResponse, versionResponse, taskListResponse] = await Promise.all([
        listTaskEvents(taskId),
        listTaskWeeklyReportVersions(taskId),
        listTasks({ page: 1, page_size: 100 }),
      ]);
      setTask(eventsResponse.task);
      setOperations(eventsResponse.operations);
      mergeEvents(eventsResponse.events);
      cursorRef.current = eventsResponse.cursor;
      setVersions(versionResponse);
      setTasks(taskListResponse.items);
      setSelectedVersionId((current) => current || versionResponse.at(-1)?.report_version_id || "");
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "周报工作台数据加载失败");
    } finally {
      setLoading(false);
    }
  }, [mergeEvents, taskId]);

  const pollEvents = useCallback(async () => {
    try {
      const response = await listTaskEvents(taskId, cursorRef.current || undefined);
      setTask(response.task);
      setOperations(response.operations);
      mergeEvents(response.events);
      cursorRef.current = response.cursor;
    } catch (pollError) {
      setError(pollError instanceof Error ? pollError.message : "Failed to refresh workbench events.");
    }
  }, [mergeEvents, taskId]);

  useEffect(() => {
    setTask(null);
    setOperations([]);
    setMessages([]);
    cursorRef.current = "";
    setSelectedVersionId("");
    setFeedbackText("");
    setFeedbackFile(null);
    void loadWorkbench();
  }, [loadWorkbench]);

  useEffect(() => {
    const hasActiveOperation = operations.some((operation) => ["queued", "processing"].includes(operation.status));
    if (!task || (!hasActiveOperation && !["queued", "running"].includes(task.status))) return;
    const timer = window.setInterval(() => {
      void pollEvents();
    }, 1500);
    return () => window.clearInterval(timer);
  }, [operations, pollEvents, task?.status]);

  const selectedVersion = useMemo(
    () => versions.find((version) => version.report_version_id === selectedVersionId) ?? versions.at(-1),
    [selectedVersionId, versions],
  );

  const runAction = async (action: TaskControlAction) => {
    setActionLoading(true);
    setError(null);
    try {
      await controlTask(taskId, action);
      await loadWorkbench();
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "周报操作失败");
    } finally {
      setActionLoading(false);
    }
  };

  const sendFeedback = async () => {
    if (!feedbackText.trim()) return;
    const requestId = crypto.randomUUID();
    setSubmitLoading(true);
    setError(null);
    try {
      const formData = new FormData();
      formData.set("feedback_text", feedbackText.trim());
      formData.set("request_id", requestId);
      if (feedbackFile) formData.set("file", feedbackFile);
      mergeEvents([{
        event_id: `optimistic:${requestId}`,
        event_type: "message",
        timestamp: new Date().toISOString(),
        agent: "Human Feedback",
        role: "user",
        kind: "message",
        content: feedbackText.trim(),
        session_id: "",
        sequence: 0,
        request_id: requestId,
      }]);
      const operation = await submitHumanFeedback(taskId, formData);
      setOperations((current) => [...current.filter((item) => item.operation_id !== operation.operation_id), operation]);
      setFeedbackText("");
      setFeedbackFile(null);
      await pollEvents();
    } catch (submitError) {
      setMessages((current) => current.filter((item) => item.event_id !== `optimistic:${requestId}`));
      setError(submitError instanceof Error ? submitError.message : "反馈提交失败");
    } finally {
      setSubmitLoading(false);
    }
  };

  const sendSupplement = async () => {
    if (!supplementFile || !["image/png", "image/jpeg", "image/webp"].includes(supplementFile.type)) return;
    setSubmitLoading(true);
    setError(null);
    try {
      const formData = new FormData();
      formData.set("request_id", crypto.randomUUID());
      formData.set("file", supplementFile);
      if (supplementNote.trim()) formData.set("note", supplementNote.trim());
      const operation = await uploadTaskSupplement(taskId, formData);
      setOperations((current) => [...current.filter((item) => item.operation_id !== operation.operation_id), operation]);
      setSupplementFile(null);
      setSupplementNote("");
      setSupplementOpen(false);
      await pollEvents();
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "补充证据上传失败");
    } finally {
      setSubmitLoading(false);
    }
  };

  const confirmFinalReview = async () => {
    setConfirmLoading(true);
    setError(null);
    try {
      await confirmTask(taskId);
      await loadWorkbench();
    } catch (confirmError) {
      setError(confirmError instanceof Error ? confirmError.message : "确认最终周报失败");
    } finally {
      setConfirmLoading(false);
    }
  };

  if (loading) {
    return <div className="flex min-h-full flex-1 items-center justify-center text-sm text-gray-500"><LoadingOutlined className="mr-2" /> 正在加载周报工作台...</div>;
  }

  if (error && !task) {
    return (
      <main className="flex min-h-full flex-1 flex-col items-center justify-center text-sm text-red-600">
        <CloseCircleOutlined className="mb-3 text-2xl" />
        <p>{error}</p>
        <button type="button" onClick={() => void loadWorkbench()} className="mt-4 flex items-center gap-2 rounded-full bg-gray-900 px-4 py-2 text-white"><ReloadOutlined /> 重试</button>
      </main>
    );
  }

  if (!task) return null;
  const meta = statusMeta[task.status];
  const activeOperation = operations.find((operation) => ["queued", "processing"].includes(operation.status));
  const canPause = task.status === "queued" || task.status === "running";
  const canResume = task.status === "waiting" && task.waiting_reason === "manual_pause";
  const canCancel = ["created", "queued", "running", "waiting"].includes(task.status);
  const isEvidenceWait = task.status === "waiting" && task.waiting_reason === "evidence";
  const canSubmitFeedback = task.status === "waiting" && ["human", "evidence"].includes(task.waiting_reason || "") && !activeOperation;
  const failedOperation = operations.find((operation) => operation.status === "failed");
  const canConfirmFinalReview = task.status === "waiting" && task.waiting_reason === "human" && !activeOperation && !confirmLoading;
  const latestReviewCompletion = [...messages].reverse().find(
    (event) => event.agent === "周报 Review" && event.event_type === "node_completed",
  );
  const confirmationEventId = canConfirmFinalReview
    && latestReviewCompletion?.content?.includes("是否选择当前周报作为最终版")
    ? latestReviewCompletion.event_id
    : undefined;
  const downloadSelectedMarkdown = () => {
    if (!selectedVersion) return;
    const blob = new Blob([selectedVersion.markdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = buildMarkdownDownloadFileName(task.task_name, selectedVersion.version);
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  };

  return (
    <main className="flex min-h-full flex-1 flex-col overflow-hidden bg-[#f7f4ef] px-4 py-3 text-gray-800 xl:px-6 xl:py-4">
      <div className="mx-auto flex min-h-0 w-full max-w-[1520px] flex-1 flex-col">
        <header className="mb-3 flex min-h-[58px] flex-wrap items-center justify-between gap-3 rounded-[20px] border border-[#ebe7e0] bg-white px-4 py-3 shadow-[0_6px_24px_-20px_rgba(15,23,42,0.35)]">
          <div className="flex min-w-0 items-center gap-3"><span className="text-[13px] font-medium text-gray-500">当前周报:</span><select aria-label="选择周报" value={taskId} onChange={(event) => navigate(`/tasks/${event.target.value}`)} className="w-[min(360px,55vw)] rounded-lg border border-white/50 bg-white/60 px-3 py-1.5 text-[13px] font-medium text-gray-800 shadow-sm outline-none"><option value={taskId}>{task.task_id} - {task.task_name}</option>{tasks.filter((item) => item.task_id !== taskId).map((item) => <option key={item.task_id} value={item.task_id}>{item.task_id} - {item.task_name}</option>)}</select><span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold ${meta.className}`}>{meta.label}</span></div>
          <div className="hidden min-w-0">
            <div className="mb-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-gray-400">周报助手 / 周报工作台</div>
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="truncate text-[21px] font-bold text-gray-900">{task.task_name}</h1>
              <span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold ${meta.className}`}>{meta.label}</span>
            </div>
            <p className="mt-1 text-[12px] text-gray-500">{task.task_id} · 当前节点：{task.current_node || "尚未开始"}</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {task.status === "created" && <ActionButton action="start" label="开始生成" icon={<PlayCircleOutlined />} onClick={runAction} disabled={actionLoading} />}
            {canPause && <ActionButton action="pause" label="暂停" icon={<PauseOutlined />} onClick={runAction} disabled={actionLoading} />}
            {canResume && <ActionButton action="resume" label="恢复" icon={<PlayCircleOutlined />} onClick={runAction} disabled={actionLoading} />}
            {canCancel && <ActionButton action="cancel" label="取消" icon={<StopOutlined />} onClick={runAction} disabled={actionLoading} />}
            <button type="button" onClick={() => navigate("/tasks")} className="rounded-full px-3 py-2 text-[12px] text-gray-500 hover:bg-white hover:text-gray-900">返回看板</button>
          </div>
        </header>

        {error && <div className="mb-4 flex items-center justify-between rounded-2xl bg-red-50 px-4 py-3 text-sm text-red-700"><span>{error}</span><button type="button" onClick={() => void loadWorkbench()}><ReloadOutlined /></button></div>}
        {activeOperation && <div className="mb-4 flex items-center gap-2 rounded-2xl bg-blue-50 px-4 py-3 text-sm text-blue-700"><LoadingOutlined /> {activeOperation.operation_type === "feedback" ? "正在处理反馈" : "正在处理补充证据"} · {activeOperation.stage}</div>}
        {failedOperation && <div className="mb-4 flex items-center justify-between gap-3 rounded-2xl bg-amber-50 px-4 py-3 text-sm text-amber-800"><span>{failedOperation.operation_type === "feedback" ? "反馈处理失败" : "补充证据处理失败"}：{failedOperation.error_message || "可重新提交"}</span><ReloadOutlined /></div>}

        <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 overflow-hidden xl:grid-cols-[minmax(320px,38fr)_minmax(0,62fr)]">
          <section className="flex min-h-0 min-w-0 flex-col overflow-hidden rounded-[24px] border border-[#ebe7e0] bg-white shadow-[0_10px_32px_-24px_rgba(15,23,42,0.45)]">
            <div className="flex items-center justify-between border-b border-black/[0.05] px-5 py-4">
              <div><h2 className="text-[14px] font-bold text-gray-900">工作流消息</h2><p className="mt-1 text-[11px] text-gray-400">按 Agent 事件时间排列</p></div>
              <span className="rounded-full bg-gray-100 px-2.5 py-1 text-[11px] text-gray-500">{messages.length} 条</span>
            </div>
            <div className="min-h-0 flex-1 space-y-5 overflow-y-auto bg-[#fcfbf9] px-5 py-5">
              {messages.length === 0 ? <div className="flex h-full min-h-48 items-center justify-center text-sm text-gray-400">暂无工作流事件</div> : <WorkflowTimeline events={messages} confirmationEventId={confirmationEventId} onConfirm={() => void confirmFinalReview()} />}
            </div>
            {canSubmitFeedback ? <form onSubmit={(event) => { event.preventDefault(); void sendFeedback(); }} className="shrink-0 border-t border-white/50 bg-white/30 p-4"><div className="rounded-[20px] border border-white/80 bg-white/70 shadow-sm"><textarea value={feedbackText} disabled={submitLoading} onChange={(event) => setFeedbackText(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void sendFeedback(); } }} placeholder="请输入修改建议或反馈..." className="min-h-[80px] w-full resize-none bg-transparent px-4 pt-4 text-[13px] text-gray-700 outline-none disabled:cursor-not-allowed disabled:opacity-50" /><div className="flex items-center justify-between px-3 pb-3"><div className="flex items-center gap-2">{feedbackFile && <span className="max-w-[220px] truncate rounded-lg bg-blue-50 px-3 py-2 text-[12px] text-blue-700">{feedbackFile.name}<button type="button" aria-label="移除反馈附件" onClick={() => setFeedbackFile(null)} className="ml-2">×</button></span>}<input ref={feedbackInputRef} type="file" accept="image/*" disabled={submitLoading} className="hidden" onChange={(event) => { const file = event.target.files?.[0] ?? null; setFeedbackFile(file?.type.startsWith("image/") ? file : null); event.currentTarget.value = ""; }} /><button type="button" aria-label="添加反馈图片" disabled={submitLoading} onClick={() => feedbackInputRef.current?.click()} className="rounded-lg p-2 text-gray-400 hover:bg-white hover:text-gray-800 disabled:cursor-not-allowed disabled:opacity-40"><UploadOutlined /></button></div><button type="submit" disabled={submitLoading || !feedbackText.trim()} className="flex items-center gap-1.5 rounded-lg bg-[#1c1c1c] px-4 py-2 text-[12px] font-semibold text-white disabled:opacity-40"><SendOutlined />发送</button></div></div><p className="mt-2 text-[11px] text-gray-400">反馈文本必填，可附加一张图片。</p></form> : task.status === "completed" ? <div className="flex shrink-0 items-center gap-2 border-t border-white/50 bg-white/30 px-5 py-3 text-[12px] text-emerald-700"><CheckCircleOutlined /><span>最终周报已确认，可继续查看历史版本或下载当前报告。</span></div> : <div className="shrink-0 border-t border-white/50 bg-white/30 px-5 py-3 text-[12px] text-gray-400">工作流到达人工处理节点后可提交反馈。</div>}
            {isEvidenceWait && <div className="shrink-0 border-t border-white/50 bg-white/30 p-4"><button type="button" disabled={Boolean(activeOperation) || submitLoading} onClick={() => setSupplementOpen(true)} className="flex w-full items-center justify-center gap-2 rounded-xl bg-amber-500 px-4 py-3 text-[13px] font-semibold text-white shadow-sm hover:bg-amber-600 disabled:cursor-not-allowed disabled:opacity-40"><CloudUploadOutlined />上传补充证据</button><p className="mt-2 text-center text-[11px] text-gray-400">当前流程等待一份图片材料。</p></div>}
          </section>

          <section className="flex min-h-0 min-w-0 flex-col overflow-hidden rounded-xl border border-[#ebe7e0] bg-white shadow-[0_10px_32px_-24px_rgba(15,23,42,0.45)]">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-black/[0.05] px-5 py-4">
              <div><h2 className="text-[14px] font-bold text-gray-900">周报版本</h2><p className="mt-1 text-[11px] text-gray-400">只读版本预览</p></div>
              <div className="flex flex-wrap items-center gap-2">
                <select aria-label="选择周报版本" value={selectedVersionId} onChange={(event) => setSelectedVersionId(event.target.value)} disabled={versions.length === 0} className="max-w-[220px] rounded-lg border border-gray-200 bg-white px-3 py-2 text-[12px] text-gray-700 outline-none">
                  {versions.length === 0 ? <option value="">暂无版本</option> : versions.map((version) => <option key={version.report_version_id} value={version.report_version_id}>{version.version}</option>)}
                </select>
                <button type="button" aria-label="下载 Markdown" onClick={downloadSelectedMarkdown} disabled={!selectedVersion} className="flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-2 text-[12px] font-medium text-gray-700 shadow-sm hover:border-gray-300 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"><DownloadOutlined />下载 Markdown</button>
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto bg-[#fcfbf9] p-4 sm:p-5 xl:p-7">
              {selectedVersion ? <div className="mx-auto min-h-[680px] w-full max-w-[920px] rounded bg-white px-6 py-7 shadow-[0_12px_32px_-8px_rgba(0,0,0,0.08)] sm:px-10 sm:py-9 lg:min-h-[1000px]"><div className="mb-7 flex min-w-0 items-center gap-2 border-b border-[#edf0f3] pb-3 text-[11px] text-gray-400"><CheckCircleOutlined className="shrink-0 text-emerald-500" /><span className="truncate">{displayChangeNote(selectedVersion.change_note)}</span><span className="ml-auto shrink-0 font-medium text-gray-500">{selectedVersion.version}</span></div><WeeklyReportMarkdownPreview markdown={selectedVersion.markdown} evidenceImages={selectedVersion.evidence_images} /></div> : <div className="flex h-full min-h-48 items-center justify-center text-sm text-gray-400">暂无周报内容</div>}
            </div>
          </section>
        </div>
      </div>
      {supplementOpen && <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-900/20 p-6 backdrop-blur-sm" onClick={() => !submitLoading && setSupplementOpen(false)}><div className="w-full max-w-[520px] rounded-[24px] border border-white/70 bg-white/95 p-6 shadow-xl" onClick={(event) => event.stopPropagation()}><div className="mb-5 flex items-center justify-between"><div><h3 className="text-[16px] font-bold text-gray-900">补充周报材料</h3><p className="mt-1 text-[12px] text-gray-500">选择一张 PNG、JPEG 或 WebP 图片作为补充材料。</p></div><button type="button" aria-label="关闭补充材料弹窗" disabled={submitLoading} onClick={() => setSupplementOpen(false)} className="rounded-full px-2 text-xl text-gray-400">×</button></div><input ref={supplementInputRef} type="file" accept="image/png,image/jpeg,image/webp" className="hidden" onChange={(event) => { const file = event.target.files?.[0] ?? null; setSupplementFile(file && ["image/png", "image/jpeg", "image/webp"].includes(file.type) ? file : null); event.currentTarget.value = ""; }} /><button type="button" onClick={() => supplementInputRef.current?.click()} className="flex w-full items-center justify-center gap-2 rounded-xl border-2 border-dashed border-blue-200 bg-blue-50/40 px-4 py-8 text-[13px] font-semibold text-blue-600">{supplementFile ? supplementFile.name : <><UploadOutlined />选择图片</>}</button>{supplementFile && <div className="mt-3 flex items-center justify-between rounded-lg bg-gray-50 px-3 py-2 text-[12px] text-gray-600"><span className="max-w-[390px] truncate">{supplementFile.name}</span><button type="button" onClick={() => setSupplementFile(null)}>移除</button></div>}<label className="mt-5 block text-[12.5px] font-semibold text-gray-700">补充说明<textarea value={supplementNote} onChange={(event) => setSupplementNote(event.target.value)} className="mt-2 min-h-[80px] w-full resize-y rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-[13px] font-normal outline-none" /></label><div className="mt-6 flex justify-end gap-3"><button type="button" disabled={submitLoading} onClick={() => setSupplementOpen(false)} className="rounded-xl px-4 py-2 text-[13px] text-gray-500">取消</button><button type="button" disabled={!supplementFile || submitLoading} onClick={() => void sendSupplement()} className="flex items-center gap-2 rounded-xl bg-amber-500 px-5 py-2 text-[13px] font-semibold text-white disabled:opacity-40">{submitLoading ? <LoadingOutlined /> : <UploadOutlined />}上传材料</button></div></div></div>}
    </main>
  );
}
