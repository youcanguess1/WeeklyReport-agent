import {
  FileZipOutlined,
  CloudUploadOutlined,
  DeleteOutlined,
  FileImageOutlined,
  FileMarkdownOutlined,
  FileOutlined,
  FolderOpenOutlined,
  InboxOutlined,
  LoadingOutlined,
  ReloadOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { listArtifacts, uploadProjectArtifact } from "../../api/artifacts";
import { listTasks } from "../../api/tasks";
import type { ArtifactLibrary, ArtifactListItem, TaskListItem } from "../../types/api";

type DirectoryId = ArtifactLibrary | `task:${string}`;
type FormatFilter = "ALL" | "MD" | "ZIP" | "JPG";

const projectDirectories: Array<{ id: ArtifactLibrary; name: string; color: string }> = [
  { id: "all", name: "所有产物与规范", color: "text-blue-500" },
  { id: "rules", name: "周报规则库", color: "text-orange-500" },
  { id: "templates", name: "周报模板库", color: "text-emerald-500" },
  { id: "tasks", name: "周报产物", color: "text-purple-500" },
];

const uploadTags = ["写作规则", "审核规则", "项目", "周报", "模板"];

function formatSize(size: number | null) {
  if (size === null) return "-";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function matchesFormat(item: ArtifactListItem, filter: FormatFilter) {
  if (filter === "ALL") return true;
  if (filter === "MD") return ["markdown", "weekly_report_markdown"].includes(item.kind);
  if (filter === "ZIP") return item.kind === "archive";
  return ["image", "image_material"].includes(item.kind);
}

function formatLabel(kind: string) {
  const labels: Record<string, string> = {
    markdown: "MD",
    weekly_report_markdown: "MD",
    archive: "ZIP",
    image: "JPG/PNG",
    image_material: "JPG/PNG",
    pdf: "PDF",
  };
  return labels[kind] ?? kind;
}

function displayArtifactFilename(filename: string) {
  return filename
    .replace(/^final_report(?=_|\.)/i, "final_weekly_report")
    .replace(/^report(?=_|\.)/i, "weekly_report");
}

function ArtifactIcon({ item }: { item: ArtifactListItem }) {
  if (item.kind === "archive") return <FileZipOutlined className="text-purple-500" />;
  if (["image", "image_material"].includes(item.kind)) return <FileImageOutlined className="text-pink-500" />;
  if (item.kind === "markdown" || item.kind === "weekly_report_markdown") return <FileMarkdownOutlined className="text-emerald-500" />;
  return <FileOutlined className="text-gray-500" />;
}

function DirectoryRow({
  active,
  icon,
  label,
  count,
  indent = false,
  onClick,
}: {
  active: boolean;
  icon?: React.ReactNode;
  label: string;
  count: number;
  indent?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`group flex w-full items-center justify-between rounded-xl border px-3 py-2.5 text-left transition ${indent ? "pl-9" : ""} ${active ? "border-gray-100 bg-white font-semibold text-gray-900 shadow-sm" : "border-transparent font-medium text-gray-600 hover:bg-white/50 hover:text-gray-900"}`}
    >
      <span className="flex min-w-0 items-center gap-3">{icon}<span className={`truncate ${indent ? "font-mono text-[12px] text-gray-500" : "text-[13px]"}`}>{label}</span></span>
      <span className={`ml-2 shrink-0 rounded-full px-2 py-0.5 text-[11px] ${active ? "bg-gray-100 text-gray-700" : "text-gray-400 group-hover:bg-black/5"}`}>{count}</span>
    </button>
  );
}

export function ArtifactCenterPage() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [selectedDirectory, setSelectedDirectory] = useState<DirectoryId>("all");
  const [items, setItems] = useState<ArtifactListItem[]>([]);
  const [tasks, setTasks] = useState<TaskListItem[]>([]);
  const [query, setQuery] = useState("");
  const [formatFilter, setFormatFilter] = useState<FormatFilter>("ALL");
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showUpload, setShowUpload] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [uploadLibrary, setUploadLibrary] = useState<"rules" | "templates">("rules");
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");
  const [selectedTags, setSelectedTags] = useState<string[]>([]);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [artifactResponse, taskResponse] = await Promise.all([
        listArtifacts({ library: "all", page: 1, page_size: 200 }),
        listTasks({ page: 1, page_size: 100 }),
      ]);
      setItems(artifactResponse.items);
      setTasks(taskResponse.items);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "文件数据加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void loadData(); }, [loadData]);

  const taskArtifactCount = useMemo(() => {
    const counts = new Map<string, number>();
    items.forEach((item) => { if (item.task_id) counts.set(item.task_id, (counts.get(item.task_id) ?? 0) + 1); });
    return counts;
  }, [items]);

  const directoryCount = (id: ArtifactLibrary) => {
    if (id === "all") return items.length;
    return items.filter((item) => item.library === id).length;
  };

  const currentItems = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return items.filter((item) => {
      const matchesDirectory = selectedDirectory.startsWith("task:")
        ? item.task_id === selectedDirectory.slice(5)
        : selectedDirectory === "all" || item.library === selectedDirectory;
      const searchable = `${item.filename} ${item.task_id ?? ""} ${item.description ?? ""}`.toLowerCase();
      return matchesDirectory && matchesFormat(item, formatFilter) && (!normalized || searchable.includes(normalized));
    });
  }, [formatFilter, items, query, selectedDirectory]);

  const currentDirectoryName = selectedDirectory.startsWith("task:")
    ? tasks.find((task) => task.task_id === selectedDirectory.slice(5))?.task_name ?? selectedDirectory.slice(5)
    : projectDirectories.find((directory) => directory.id === selectedDirectory)?.name ?? "所有产物与规范";
  const isReadOnlyDirectory = selectedDirectory === "tasks" || selectedDirectory.startsWith("task:");

  const selectFile = (nextFile: File | undefined) => {
    if (!nextFile) return;
    setFile(nextFile);
    if (!displayName) setDisplayName(nextFile.name);
  };

  const resetUpload = () => {
    setShowUpload(false);
    setFile(null);
    setDisplayName("");
    setDescription("");
    setSelectedTags([]);
    setDragging(false);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const handleUpload = async () => {
    if (!file) return;
    setUploading(true);
    setError(null);
    const formData = new FormData();
    formData.append("library", uploadLibrary);
    formData.append("file", file);
    formData.append("display_name", displayName.trim() || file.name);
    formData.append("description", description.trim());
    formData.append("tags", selectedTags.join(","));
    try {
      await uploadProjectArtifact(formData);
      resetUpload();
      await loadData();
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "文件上传失败");
    } finally {
      setUploading(false);
    }
  };

  return (
    <main className="flex min-h-full flex-1 flex-col overflow-hidden px-6 py-6 md:px-8">
      <header className="flex h-[70px] shrink-0 items-center justify-between border-b border-white/40 bg-white/10">
        <div className="flex items-center gap-2 text-[14px] text-gray-500"><span className="font-medium text-gray-600">周报助手</span><span>/</span><span className="font-medium text-gray-900">周报资料</span></div>
        <button type="button" aria-label="刷新文件列表" onClick={() => void loadData()} className="rounded-full bg-white/40 p-2 text-gray-400 shadow-sm hover:bg-white/60 hover:text-gray-800"><ReloadOutlined /></button>
      </header>

      <div className="flex min-h-0 flex-1 gap-5 overflow-hidden pt-6">
        <section className="flex w-[260px] shrink-0 flex-col overflow-hidden rounded-[28px] border border-white/60 bg-white/60 shadow-[0_4px_24px_-8px_rgba(0,0,0,0.06)] backdrop-blur-xl">
          <div className="flex shrink-0 items-center justify-between border-b border-white/50 bg-white/40 px-5 py-4"><h2 className="flex items-center gap-2 text-[14px] font-bold text-gray-900"><FolderOpenOutlined className="text-blue-500" />文件目录</h2><span className="text-[11px] text-gray-400">{items.length} 项</span></div>
          <div className="min-h-0 flex-1 space-y-1 overflow-y-auto p-3">
            {projectDirectories.map((directory) => <DirectoryRow key={directory.id} active={selectedDirectory === directory.id} icon={<FolderOpenOutlined className={`${directory.color} text-[17px]`} />} label={directory.name} count={directory.id === "tasks" ? items.filter((item) => item.library === "tasks").length : directoryCount(directory.id)} onClick={() => setSelectedDirectory(directory.id)} />)}
            {projectDirectories[3] && <div className="mt-1 space-y-1">{tasks.map((task) => <DirectoryRow key={task.task_id} active={selectedDirectory === `task:${task.task_id}`} label={task.task_name} count={taskArtifactCount.get(task.task_id) ?? 0} indent onClick={() => setSelectedDirectory(`task:${task.task_id}`)} />)}</div>}
          </div>
        </section>

        <section className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-[28px] border border-white/60 bg-white/60 shadow-[0_4px_24px_-8px_rgba(0,0,0,0.06)] backdrop-blur-xl">
          <div className="flex shrink-0 items-center justify-between border-b border-white/50 bg-white/40 px-6 py-4"><div className="flex items-center gap-2 text-[13.5px] font-medium"><span className="text-gray-400">资料中心</span><span className="text-gray-300">›</span><span className="flex items-center gap-1.5 text-gray-900"><FolderOpenOutlined className="text-amber-400" />{currentDirectoryName}</span></div>{!isReadOnlyDirectory && <button type="button" onClick={() => setShowUpload(true)} className="flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2 text-[13px] font-medium text-white shadow-sm transition hover:bg-blue-700"><CloudUploadOutlined />上传文件</button>}</div>
          <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-white/50 bg-white/30 px-6 py-3"><div className="relative min-w-[240px] flex-1"><SearchOutlined className="absolute left-3.5 top-1/2 -translate-y-1/2 text-gray-400" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="按文件名称、周报名称、周报 ID 或关键词搜索..." className="w-full rounded-xl border border-white/70 bg-white/50 py-2 pl-10 pr-4 text-[12.5px] text-gray-800 outline-none placeholder:text-gray-400 focus:bg-white/90" /></div><div className="flex items-center gap-2"><span className="text-[12.5px] font-medium text-gray-500">文件格式:</span><select value={formatFilter} onChange={(event) => setFormatFilter(event.target.value as FormatFilter)} className="rounded-lg border border-white/70 bg-white/60 px-3 py-1.5 text-[12.5px] text-gray-800 outline-none"><option value="ALL">全部格式 (All Types)</option><option value="MD">MD</option><option value="ZIP">ZIP</option><option value="JPG">JPG/PNG</option></select></div></div>
          {error && <div className="mx-6 mt-4 flex items-center justify-between rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700"><span>{error}</span><button type="button" onClick={() => void loadData()}><ReloadOutlined /></button></div>}
          <div className="min-h-0 flex-1 overflow-auto">
            {loading ? <div className="flex h-full min-h-48 items-center justify-center text-sm text-gray-500"><LoadingOutlined className="mr-2" />正在加载文件...</div> : currentItems.length === 0 ? <div className="flex h-full min-h-48 flex-col items-center justify-center text-[13px] text-gray-400"><InboxOutlined className="mb-3 text-3xl text-gray-300" />没有找到符合条件的文件</div> : <table className="w-full min-w-[700px] border-collapse text-left"><thead className="sticky top-0 z-10 border-b border-black/[0.04] bg-white/70 backdrop-blur-md"><tr><th className="w-[35%] px-6 py-3.5 text-[12px] font-semibold text-gray-500">文件名称 / 类别</th><th className="w-[30%] px-6 py-3.5 text-[12px] font-semibold text-gray-500">关联周报 / 描述</th><th className="w-[15%] px-6 py-3.5 text-[12px] font-semibold text-gray-500">类型</th><th className="w-[10%] px-6 py-3.5 text-[12px] font-semibold text-gray-500">大小</th><th className="w-[10%] px-6 py-3.5 text-[12px] font-semibold text-gray-500">上传时间</th></tr></thead><tbody className="divide-y divide-black/[0.03]">{currentItems.map((item) => <tr key={item.artifact_id} className="transition hover:bg-white/50"><td className="px-6 py-4"><div className="flex min-w-0 items-center gap-3"><div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-white shadow-sm"><ArtifactIcon item={item} /></div><div className="min-w-0"><div className="truncate text-[13px] font-semibold text-gray-900" title={displayArtifactFilename(item.filename)}>{displayArtifactFilename(item.filename)}</div><div className="mt-1 text-[11px] text-gray-400">{item.artifact_id}</div></div></div></td><td className="px-6 py-4"><div className="truncate text-[12.5px] text-gray-700">{item.task_id ?? (item.library === "rules" ? "内部共享知识库" : item.library === "templates" ? "内部共享知识库" : "项目文件")}</div><div className="mt-1 truncate text-[11px] text-gray-400">{item.description ?? "-"}</div></td><td className="px-6 py-4"><span className="rounded-md bg-gray-50 px-2 py-1 text-[11px] text-gray-600">{formatLabel(item.kind)}</span></td><td className="px-6 py-4 text-[12px] text-gray-500">{formatSize(item.size_bytes)}</td><td className="px-6 py-4 text-[12px] text-gray-500">{new Date(item.created_at).toLocaleDateString()}</td></tr>)}</tbody></table>}
          </div>
        </section>
      </div>

      {showUpload && <div className="fixed inset-0 z-40 flex items-center justify-center bg-gray-900/20 p-6 backdrop-blur-sm" onClick={() => !uploading && resetUpload()}><div className="flex max-h-[85vh] w-full max-w-[520px] flex-col overflow-hidden rounded-[24px] border border-white/60 bg-white/90 shadow-[0_20px_40px_-10px_rgba(0,0,0,0.1)] backdrop-blur-2xl" onClick={(event) => event.stopPropagation()}><div className="flex shrink-0 items-center justify-between border-b border-black/[0.05] px-6 py-4"><h3 className="flex items-center gap-2 text-[15px] font-bold text-gray-900"><CloudUploadOutlined className="text-blue-600" />上传文件</h3><button type="button" aria-label="关闭上传弹窗" onClick={resetUpload} className="rounded-full bg-black/5 p-2 text-gray-500 hover:bg-black/10"><span className="text-lg leading-none">×</span></button></div><div className="space-y-5 overflow-y-auto px-6 py-5"><div><label className="mb-1.5 block text-[12.5px] font-semibold text-gray-700">选择文件 <span className="text-red-500">*</span></label><div onClick={() => fileInputRef.current?.click()} onDragEnter={(event) => { event.preventDefault(); setDragging(true); }} onDragOver={(event) => event.preventDefault()} onDragLeave={() => setDragging(false)} onDrop={(event) => { event.preventDefault(); setDragging(false); selectFile(event.dataTransfer.files[0]); }} className={`cursor-pointer rounded-xl border-2 border-dashed p-6 text-center transition ${dragging ? "border-blue-500 bg-blue-50/70" : "border-blue-200/80 bg-blue-50/40 hover:bg-blue-50/60"}`}><input ref={fileInputRef} type="file" className="hidden" onChange={(event) => selectFile(event.target.files?.[0])} /><CloudUploadOutlined className="mb-3 text-2xl text-blue-600" /><div className="text-[13px] font-semibold text-gray-900"><span className="text-blue-600">点击上传文件</span> 或拖拽文件到此处</div><div className="mt-1 text-[11.5px] text-gray-400">单次上传一个文件</div>{file && <div className="mt-4 flex items-center justify-between rounded-xl bg-white px-3 py-2 text-left text-[12px] text-gray-700 shadow-sm"><span className="flex min-w-0 items-center gap-2"><FileOutlined className="text-blue-500" /><span className="truncate">{file.name}</span></span><button type="button" aria-label="移除待上传文件" onClick={(event) => { event.stopPropagation(); setFile(null); }} className="ml-2 shrink-0 text-gray-400 hover:text-red-500"><DeleteOutlined /></button></div>}</div></div><label className="block text-[12.5px] font-semibold text-gray-700">目标位置<select value={uploadLibrary} onChange={(event) => setUploadLibrary(event.target.value as "rules" | "templates")} className="mt-1.5 w-full rounded-xl border border-black/10 bg-white/60 px-3.5 py-2.5 text-[13px] font-normal text-gray-800 outline-none"><option value="rules">周报规则库</option><option value="templates">周报模板库</option></select></label><label className="block text-[12.5px] font-semibold text-gray-700">文件名 <span className="text-red-500">*</span><input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="例如：Prod_Security_Policy_2026.pdf" className="mt-1.5 w-full rounded-xl border border-black/10 bg-white/60 px-3.5 py-2.5 text-[13px] font-normal text-gray-800 outline-none" /></label><label className="block text-[12.5px] font-semibold text-gray-700">内容说明<textarea value={description} onChange={(event) => setDescription(event.target.value)} placeholder="简要说明该文件的用途、适用范围与合规条目..." className="mt-1.5 min-h-[80px] w-full resize-y rounded-xl border border-black/10 bg-white/60 px-3.5 py-2.5 text-[13px] font-normal text-gray-800 outline-none" /></label><div><div className="mb-1.5 text-[12.5px] font-semibold text-gray-700">标签</div><div className="flex flex-wrap gap-2">{uploadTags.map((tag) => <button key={tag} type="button" onClick={() => setSelectedTags((current) => current.includes(tag) ? current.filter((item) => item !== tag) : [...current, tag])} className={`rounded-lg border px-3 py-1.5 text-[12px] font-medium transition ${selectedTags.includes(tag) ? "border-blue-500 bg-blue-50 text-blue-700" : "border-black/10 bg-white/50 text-gray-600 hover:border-blue-300"}`}>{tag}</button>)}</div></div></div><div className="flex shrink-0 justify-end gap-3 border-t border-black/[0.05] bg-white/30 px-6 py-4"><button type="button" onClick={resetUpload} className="rounded-xl px-5 py-2 text-[13px] font-medium text-gray-500 hover:bg-black/5">取消</button><button type="button" disabled={uploading || !file || !displayName.trim()} onClick={() => void handleUpload()} className="flex items-center gap-2 rounded-xl bg-blue-600 px-6 py-2 text-[13px] font-medium text-white shadow-sm hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50">{uploading && <LoadingOutlined />}{uploading ? "上传中..." : "确认上传"}</button></div></div></div>}
    </main>
  );
}
