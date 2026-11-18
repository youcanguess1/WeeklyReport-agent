import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  ExclamationCircleOutlined,
  FilterOutlined,
  InboxOutlined,
  LoadingOutlined,
  PlusOutlined,
  ReloadOutlined,
  SearchOutlined,
  ArrowRightOutlined,
} from "@ant-design/icons";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { getTaskSummary, listTasks } from "../../api/tasks";
import { taskTypeLabels } from "../../constants/workflow";
import type { TaskBoardSummary, TaskListItem, TaskStatus } from "../../types/api";

const PAGE_SIZE = 50;

const statusMeta: Record<TaskStatus, { label: string; className: string }> = {
  created: { label: "待开始", className: "text-gray-600 bg-gray-100" },
  queued: { label: "运行中", className: "text-blue-700 bg-blue-100" },
  running: { label: "运行中", className: "text-blue-700 bg-blue-100" },
  waiting: { label: "待处理", className: "text-amber-700 bg-amber-100" },
  completed: { label: "已通过", className: "text-emerald-700 bg-emerald-100" },
  failed: { label: "失败", className: "text-red-700 bg-red-100" },
  canceled: { label: "已取消", className: "text-gray-600 bg-gray-100" },
};

function matchesFilter(task: TaskListItem, filter: string, query: string) {
  const text = `${task.task_id} ${task.task_name} ${task.description}`.toLowerCase();
  const matchesQuery = !query || text.includes(query.toLowerCase());
  const matchesStatus =
    filter === "全部" ||
    (filter === "运行中" && ["queued", "running"].includes(task.status)) ||
    (filter === "待处理" && ["created", "waiting"].includes(task.status)) ||
    (filter === "已通过" && task.status === "completed");
  return matchesQuery && matchesStatus;
}

function StatCard({
  label,
  value,
  icon,
  iconClass,
}: {
  label: string;
  value: number;
  icon: React.ReactNode;
  iconClass: string;
}) {
  return (
    <div className="flex items-center justify-between rounded-[24px] border border-white/60 bg-white/70 p-5 shadow-[0_4px_20px_-8px_rgba(0,0,0,0.04)] backdrop-blur-md">
      <div>
        <div className="mb-1.5 text-[13px] font-medium text-gray-500">{label}</div>
        <div className="text-[26px] font-bold leading-none text-gray-900">{value}</div>
      </div>
      <div className={`flex h-11 w-11 items-center justify-center rounded-full bg-white shadow-sm ${iconClass}`}>
        {icon}
      </div>
    </div>
  );
}

function TaskCard({ task, onOpen }: { task: TaskListItem; onOpen: () => void }) {
  const meta = statusMeta[task.status];
  return (
    <article className="flex h-full flex-col rounded-[28px] border border-white/60 bg-white/70 p-6 shadow-[0_8px_32px_-12px_rgba(0,0,0,0.06)] transition-all hover:shadow-[0_12px_40px_-12px_rgba(0,0,0,0.1)]">
      <div className="mb-4 flex items-start justify-between gap-3">
        <span className="rounded-full bg-black/5 px-2.5 py-1 text-[11px] font-bold tracking-wider text-gray-500">
          {task.task_id}
        </span>
        <span className={`rounded-full px-3 py-1.5 text-[11.5px] font-semibold ${meta.className}`}>
          {meta.label}
        </span>
      </div>
      <div className="mb-5 flex-1">
        <h3 className="mb-2 text-[17px] font-bold leading-snug text-gray-900">{task.task_name}</h3>
        <p className="line-clamp-2 text-[13.5px] leading-relaxed text-gray-500">
          {task.description || "暂无周报材料说明"}
        </p>
      </div>
      <div className="mb-5 grid grid-cols-3 gap-3 text-[12px]">
        <div>
          <div className="text-[10px] font-semibold uppercase tracking-wider text-gray-400">version</div>
          <div className="font-bold text-gray-800">{task.current_report_version ?? "-"}</div>
        </div>
        <div>
          <div className="text-[10px] font-semibold uppercase tracking-wider text-gray-400">node</div>
          <div className="truncate font-bold text-gray-800">{task.current_node ?? "-"}</div>
        </div>
        <div>
          <div className="text-[10px] font-semibold uppercase tracking-wider text-gray-400">artifacts</div>
          <div className="font-bold text-gray-800">{task.artifact_count}</div>
        </div>
      </div>
      <div className="flex items-center justify-between border-t border-black/[0.04] pt-4">
        <span className="text-[12px] text-gray-400">{taskTypeLabels[task.task_type]}</span>
        <button
          type="button"
          aria-label={`打开 ${task.task_name}`}
          onClick={onOpen}
          className="flex h-[34px] w-[34px] items-center justify-center rounded-full bg-black/5 text-gray-500 transition-all hover:bg-black/10 hover:text-gray-900"
        >
          <ArrowRightOutlined />
        </button>
      </div>
    </article>
  );
}

export function TaskBoardPage() {
  const navigate = useNavigate();
  const [tasks, setTasks] = useState<TaskListItem[]>([]);
  const [summary, setSummary] = useState<TaskBoardSummary>({
    total_count: 0,
    running_count: 0,
    pending_count: 0,
    completed_count: 0,
  });
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("全部");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadTasks = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [taskResponse, summaryResponse] = await Promise.all([
        listTasks({ page: 1, page_size: PAGE_SIZE }),
        getTaskSummary(),
      ]);
      setTasks(taskResponse.items);
      setSummary(summaryResponse);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "周报数据加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadTasks();
  }, [loadTasks]);

  const visibleTasks = useMemo(
    () => tasks.filter((task) => matchesFilter(task, filter, query.trim())),
    [filter, query, tasks],
  );

  return (
    <main className="min-h-full flex-1 overflow-y-auto px-6 py-8 md:px-10">
      <div className="mx-auto max-w-[1100px]">
        <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="mb-2 text-[12px] font-semibold uppercase tracking-[0.16em] text-gray-400">周报助手 / 周报看板</div>
            <h1 className="text-[30px] font-bold tracking-tight text-gray-900">周报看板</h1>
          </div>
          <button
            type="button"
            onClick={() => navigate("/tasks/create")}
            className="flex items-center gap-2 rounded-full bg-[#1c1c1c] px-5 py-3 text-[13px] font-medium text-white shadow-md transition hover:bg-black"
          >
            <PlusOutlined />
            新建周报
          </button>
        </div>

        <div className="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard label="周报总数" value={summary.total_count} icon={<InboxOutlined />} iconClass="text-gray-700" />
          <StatCard label="运行中" value={summary.running_count} icon={<LoadingOutlined />} iconClass="text-blue-500" />
          <StatCard label="待处理" value={summary.pending_count} icon={<ClockCircleOutlined />} iconClass="text-amber-500" />
          <StatCard label="已通过" value={summary.completed_count} icon={<CheckCircleOutlined />} iconClass="text-emerald-500" />
        </div>

        <div className="mb-6 flex flex-wrap items-center gap-3 rounded-full border border-white/50 bg-white/60 p-2 shadow-[0_2px_12px_-4px_rgba(0,0,0,0.03)] backdrop-blur-md">
          <div className="flex min-w-[220px] flex-1 items-center gap-2 pl-4 text-gray-400">
            <SearchOutlined />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索周报 ID、项目名称或材料说明..."
              className="w-full bg-transparent text-[13.5px] text-gray-800 outline-none placeholder:text-gray-400/80"
            />
          </div>
          <div className="flex items-center gap-2 pr-1 text-[13px] text-gray-600">
            <FilterOutlined />
            <select
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              className="rounded-full bg-white/60 px-3 py-2 text-gray-800 outline-none"
            >
              <option>全部</option>
              <option>运行中</option>
              <option>待处理</option>
              <option>已通过</option>
            </select>
          </div>
        </div>

        {loading && (
          <div className="flex min-h-48 items-center justify-center rounded-[28px] bg-white/70 text-sm text-gray-500">
            <LoadingOutlined className="mr-2" /> 正在加载周报...
          </div>
        )}
        {!loading && error && (
          <div className="flex min-h-48 flex-col items-center justify-center rounded-[28px] bg-white/70 text-sm text-red-600">
            <ExclamationCircleOutlined className="mb-3 text-xl" />
            <p>{error}</p>
            <button type="button" onClick={() => void loadTasks()} className="mt-4 flex items-center gap-2 rounded-full bg-gray-900 px-4 py-2 text-white">
              <ReloadOutlined /> 重试
            </button>
          </div>
        )}
        {!loading && !error && visibleTasks.length === 0 && (
          <div className="flex min-h-48 flex-col items-center justify-center rounded-[28px] bg-white/70 text-sm text-gray-500">
            <InboxOutlined className="mb-3 text-2xl" />
            {tasks.length === 0 ? "暂无周报" : "没有匹配的周报"}
          </div>
        )}
        {!loading && !error && visibleTasks.length > 0 && (
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
            {visibleTasks.map((task) => (
              <TaskCard key={task.task_id} task={task} onOpen={() => navigate(`/tasks/${task.task_id}`)} />
            ))}
          </div>
        )}
      </div>
    </main>
  );
}
