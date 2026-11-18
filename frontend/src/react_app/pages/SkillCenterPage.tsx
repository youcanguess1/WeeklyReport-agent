import {
  CheckOutlined,
  CloudDownloadOutlined,
  CloudUploadOutlined,
  DeleteOutlined,
  EditOutlined,
  CodeOutlined,
  FilterOutlined,
  InboxOutlined,
  LoadingOutlined,
  ReloadOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";

import { createSkill, deleteSkill, listSkills, updateSkill } from "../../api/skills";
import {
  agentStageLabels,
  agentStageOptions,
  skillKindLabels,
  skillStatusLabels,
  taskTypeLabels,
  taskTypeOptions,
} from "../../constants/workflow";
import type { SkillRecord, SkillUpdatePayload, TaskType } from "../../types/api";

type DialogMode = "import" | "edit" | null;
type ViewMode = "split" | "edit" | "preview";
type Tag = { value: string; label: string };

function skillTags(skill: SkillRecord): Tag[] {
  const fields: Array<[string, string | null]> = [
    ["kind", skill.kind],
    ["status", skill.status],
    ["task_type", skill.task_type],
    ["agent_stage", skill.agent_stage],
    ["report_section", skill.report_section],
    ["issue_type", skill.issue_type],
  ];
  return fields.filter(([, value]) => value).map(([field, value]) => ({
    value: value as string,
    label: field === "kind" ? skillKindLabels[value as string] ?? value as string
      : field === "status" ? skillStatusLabels[value as string] ?? value as string
        : field === "task_type" ? taskTypeLabels[value as string] ?? value as string
          : field === "agent_stage" ? agentStageLabels[value as string] ?? value as string
            : value as string,
  }));
}

function readTextFile(file: File, onRead: (content: string) => void) {
  const reader = new FileReader();
  reader.onload = () => onRead(String(reader.result ?? ""));
  reader.readAsText(file);
}

export function SkillCenterPage() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [skills, setSkills] = useState<SkillRecord[]>([]);
  const [query, setQuery] = useState("");
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [dialogMode, setDialogMode] = useState<DialogMode>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("split");
  const [activeSkill, setActiveSkill] = useState<SkillRecord | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [content, setContent] = useState("");
  const [taskType, setTaskType] = useState<TaskType>("weekly_report");
  const [agentStage, setAgentStage] = useState("writer");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadSkills = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setSkills(await listSkills());
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "规则数据加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void loadSkills(); }, [loadSkills]);

  const allTags = useMemo(() => {
    const values = new Map<string, string>();
    skills.forEach((skill) => skillTags(skill).forEach((tag) => values.set(tag.value, tag.label)));
    return Array.from(values, ([value, label]) => ({ value, label }));
  }, [skills]);

  const filteredSkills = useMemo(() => skills.filter((skill) => {
    const normalized = query.trim().toLowerCase();
    const matchesSearch = !normalized || `${skill.title} ${skill.description} ${skill.content}`.toLowerCase().includes(normalized);
    const tags = skillTags(skill).map((tag) => tag.value);
    return matchesSearch && selectedTags.every((tag) => tags.includes(tag));
  }), [query, selectedTags, skills]);

  const openImport = () => {
    setActiveSkill(null);
    setFile(null);
    setTitle("");
    setDescription("");
    setContent("");
    setTaskType("weekly_report");
    setAgentStage("writer");
    setViewMode("split");
    setDialogMode("import");
  };

  const openEdit = (skill: SkillRecord) => {
    setActiveSkill(skill);
    setFile(null);
    setTitle(skill.title);
    setDescription(skill.description);
    setContent(skill.content);
    setTaskType((skill.task_type as TaskType) || "weekly_report");
    setAgentStage(skill.agent_stage || "writer");
    setViewMode("split");
    setDialogMode("edit");
  };

  const closeDialog = (force = false) => {
    if (saving && !force) return;
    setDialogMode(null);
    setActiveSkill(null);
    setFile(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const handleFile = (nextFile: File | undefined) => {
    if (!nextFile) return;
    setFile(nextFile);
    if (!title.trim()) setTitle(nextFile.name);
    readTextFile(nextFile, setContent);
  };

  const submitImport = async () => {
    if (!file || !title.trim() || !content.trim() || !taskType || !agentStage) return;
    setSaving(true);
    setError(null);
    const formData = new FormData();
    formData.append("file", file);
    formData.append("title", title.trim());
    formData.append("description", description.trim());
    formData.append("kind", "writer_skill");
    formData.append("task_type", taskType);
    formData.append("agent_stage", agentStage);
    formData.append("content", content);
    try {
      await createSkill(formData);
      closeDialog(true);
      await loadSkills();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "规则导入失败");
    } finally {
      setSaving(false);
    }
  };

  const submitEdit = async () => {
    if (!activeSkill || !title.trim() || !content.trim() || !taskType || !agentStage) return;
    setSaving(true);
    setError(null);
    const payload: SkillUpdatePayload = { title: title.trim(), description: description.trim(), content, task_type: taskType, agent_stage: agentStage };
    try {
      await updateSkill(activeSkill.skill_id, payload);
      closeDialog(true);
      await loadSkills();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "规则保存失败");
    } finally {
      setSaving(false);
    }
  };

  const removeSkill = async (skill: SkillRecord) => {
    if (!window.confirm(`确认删除规则「${skill.title}」？`)) return;
    setError(null);
    try {
      await deleteSkill(skill.skill_id);
      await loadSkills();
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "规则删除失败");
    }
  };

  return (
    <main className="flex min-h-full flex-1 flex-col overflow-hidden">
      <header className="flex h-[70px] shrink-0 items-center justify-between border-b border-white/40 bg-white/10 px-8"><div className="flex items-center gap-2 text-[14px] text-gray-500"><span className="font-medium text-gray-600">周报助手</span><span>/</span><span className="font-medium text-gray-900">规则库</span></div><button type="button" aria-label="刷新规则列表" onClick={() => void loadSkills()} className="rounded-full bg-white/40 p-2 text-gray-400 shadow-sm hover:bg-white/60 hover:text-gray-800"><ReloadOutlined /></button></header>
      <div className="min-h-0 flex-1 overflow-y-auto p-8"><div className="relative z-10 mx-auto max-w-[1100px] space-y-6">
        <div className="flex items-center justify-between"><h1 className="flex items-center gap-2 text-[22px] font-bold text-gray-900"><CodeOutlined className="text-2xl text-[#2563eb]" />规则库</h1><button type="button" onClick={openImport} className="flex items-center gap-2 rounded-full bg-[#2563eb] px-5 py-2.5 text-[13.5px] font-medium text-white shadow-[0_4px_12px_rgba(37,99,235,0.2)] transition hover:bg-blue-700"><CloudDownloadOutlined />导入规则</button></div>
        <section className="rounded-[24px] border border-white/60 bg-white/70 p-6 shadow-[0_4px_20px_-8px_rgba(0,0,0,0.04)] backdrop-blur-md"><div className="mb-6 flex items-center gap-4"><div className="relative flex-1"><SearchOutlined className="absolute left-4 top-1/2 -translate-y-1/2 text-gray-400" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="按规则名称、描述内容或关键字进行搜索..." className="w-full rounded-[14px] border border-gray-100 bg-white/50 py-3 pl-11 pr-4 text-[13.5px] text-gray-800 outline-none placeholder:text-gray-400 focus:bg-white/90" /></div><div className="shrink-0 rounded-[14px] border border-gray-100 bg-gray-50/80 px-5 py-3 text-[13px] font-medium text-gray-600">全部规则共 <span className="mx-1 font-bold text-gray-900">{skills.length}</span> 项{selectedTags.length > 0 || query.trim() ? <span className="ml-1 font-bold text-[#2563eb]">(已筛选出 {filteredSkills.length} 项)</span> : null}</div></div><div className="flex items-start gap-4"><div className="mt-2 flex shrink-0 items-center gap-1.5 text-[13px] font-medium text-gray-500"><FilterOutlined />标签:</div><div className="flex flex-1 flex-wrap items-center gap-2.5"><button type="button" onClick={() => setSelectedTags([])} className={`rounded-lg px-4 py-2 text-[13px] font-medium transition ${selectedTags.length === 0 ? "bg-[#2563eb] text-white shadow-sm" : "border border-gray-200 bg-white text-gray-600 hover:bg-gray-50"}`}>全部</button>{allTags.map((tag) => <button key={tag.value} type="button" onClick={() => setSelectedTags((current) => current.includes(tag.value) ? current.filter((item) => item !== tag.value) : [...current, tag.value])} className={`rounded-lg px-4 py-2 text-[13px] font-medium transition ${selectedTags.includes(tag.value) ? "bg-[#2563eb] text-white shadow-sm" : "border border-gray-200 bg-white text-gray-600 hover:bg-gray-50"}`}>{tag.label}</button>)}</div></div></section>
        {error && <div className="flex items-center justify-between rounded-2xl bg-red-50 px-4 py-3 text-sm text-red-700"><span>{error}</span><button type="button" onClick={() => void loadSkills()}><ReloadOutlined /></button></div>}
        {loading ? <div className="flex min-h-48 items-center justify-center rounded-[24px] bg-white/40 text-sm text-gray-500"><LoadingOutlined className="mr-2" />正在加载规则...</div> : filteredSkills.length === 0 ? <div className="flex flex-col items-center justify-center rounded-[24px] border border-white/60 bg-white/40 py-16 text-gray-400"><InboxOutlined className="mb-3 text-4xl text-gray-300" /><p className="text-[14px] font-medium">没有找到符合条件的规则</p><button type="button" onClick={() => { setQuery(""); setSelectedTags([]); }} className="mt-4 text-[13px] text-[#2563eb] hover:underline">重置筛选</button></div> : <div className="space-y-5">{filteredSkills.map((skill) => <article key={skill.skill_id} className="w-full rounded-[24px] border border-white/60 bg-white/70 p-6 shadow-[0_4px_20px_-8px_rgba(0,0,0,0.04)] backdrop-blur-md transition hover:shadow-[0_8px_30px_-10px_rgba(0,0,0,0.08)]"><div className="mb-3 flex items-start justify-between gap-4"><div className="min-w-0"><h2 className="truncate text-[16px] font-bold text-gray-900">{skill.title}</h2><div className="mt-2 flex flex-wrap items-center gap-2"><span className={`rounded-md border px-2.5 py-1 text-[11px] font-bold ${skill.status === "active" ? "border-emerald-200/60 bg-emerald-50 text-emerald-600" : skill.status === "candidate" ? "border-amber-200/60 bg-amber-50 text-amber-600" : "border-gray-200 bg-gray-50 text-gray-500"}`}>状态: {skillStatusLabels[skill.status] ?? skill.status}</span><span className="rounded-md border border-gray-200 bg-gray-50 px-2.5 py-1 text-[11px] font-bold text-gray-500">击中次数: {skill.hit_count} 次</span></div></div><div className="flex shrink-0 gap-2"><button type="button" aria-label={`编辑 ${skill.title}`} onClick={() => openEdit(skill)} className="flex items-center gap-1.5 rounded-full border border-blue-200/80 px-4 py-1.5 text-[13px] font-medium text-[#2563eb] transition hover:bg-blue-50"><EditOutlined />修改</button><button type="button" aria-label={`删除 ${skill.title}`} onClick={() => void removeSkill(skill)} className="rounded-full p-2 text-gray-400 hover:bg-red-50 hover:text-red-600"><DeleteOutlined /></button></div></div><p className="mb-4 truncate text-[13.5px] text-gray-600" title={skill.description}>{skill.description || "暂无描述"}</p><div className="flex flex-wrap gap-2">{skillTags(skill).map((tag) => <span key={tag.value} className={`rounded-lg border px-3 py-1.5 text-[12px] font-medium tracking-wide ${selectedTags.includes(tag.value) ? "border-transparent bg-[#2563eb] font-bold text-white" : "border-gray-100 bg-gray-50 text-gray-500"}`}>{tag.label}</span>)}</div></article>)}</div>}
      </div></div>

      {dialogMode && <div className="fixed inset-0 z-40 flex items-center justify-center bg-gray-900/20 p-6 backdrop-blur-sm" onClick={() => closeDialog()}><div className="flex h-[85vh] w-full max-w-[1100px] flex-col overflow-hidden rounded-[24px] border border-white/60 bg-white/95 shadow-[0_20px_60px_-15px_rgba(0,0,0,0.1)] backdrop-blur-2xl" onClick={(event) => event.stopPropagation()}><div className="flex shrink-0 items-center justify-between border-b border-black/[0.05] px-6 py-4"><div className="flex items-center gap-3"><div className="flex h-8 w-8 items-center justify-center rounded-full bg-blue-100 text-blue-600"><CloudUploadOutlined /></div><div><h3 className="text-[15px] font-bold text-gray-900">{dialogMode === "import" ? "导入规则" : "修改 Skill"}</h3><p className="mt-0.5 text-[12px] text-gray-500">配置基本属性，编辑并预览 Markdown 内容。</p></div></div><button type="button" aria-label="关闭 Skill 弹窗" onClick={() => closeDialog()} className="rounded-full bg-black/5 p-2 text-gray-500 hover:bg-black/10">×</button></div><div className="flex min-h-0 flex-1 gap-6 overflow-hidden bg-gray-50/30 p-6"><div className="w-[360px] shrink-0 space-y-5 overflow-y-auto pr-2"><div onClick={() => dialogMode === "import" && fileInputRef.current?.click()} className={`rounded-xl border-2 border-dashed border-blue-200/80 bg-blue-50/40 p-5 text-center ${dialogMode === "import" ? "cursor-pointer hover:bg-blue-50/60" : "opacity-60"}`}><input ref={fileInputRef} type="file" accept=".md,.markdown,.txt,text/markdown,text/plain" className="hidden" onChange={(event) => handleFile(event.target.files?.[0])} /><CodeOutlined className="mb-2 text-2xl text-blue-600" /><div className="text-[13px] font-bold text-[#2563eb]">{file ? file.name : "点击选择 .md/.txt 文件"}</div><div className="mt-1 text-[11px] text-gray-400">{dialogMode === "import" ? "上传后可继续编辑内容" : "编辑已有 Skill 内容"}</div></div><label className="block text-[12.5px] font-bold text-gray-800">规则名称 <span className="text-red-500">*</span><input value={title} onChange={(event) => setTitle(event.target.value)} className="mt-2 w-full rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-[13px] font-normal text-gray-800 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100" /></label><label className="block text-[12.5px] font-bold text-gray-800">材料来源 <span className="text-red-500">*</span><select value={taskType} onChange={(event) => setTaskType(event.target.value as TaskType)} className="mt-2 w-full rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-[13px] font-normal text-gray-800 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100">{taskTypeOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label><label className="block text-[12.5px] font-bold text-gray-800">规则描述内容<textarea value={description} onChange={(event) => setDescription(event.target.value)} className="mt-2 min-h-[80px] w-full resize-y rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-[13px] font-normal text-gray-800 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100" /></label><fieldset><legend className="mb-2 text-[12.5px] font-bold text-gray-800">生效 Agent 节点 <span className="text-red-500">*</span></legend><div className="space-y-2">{agentStageOptions.map((option) => <label key={option.value} className={`flex cursor-pointer items-center gap-3 rounded-lg border p-2.5 transition ${agentStage === option.value ? "border-[#2563eb] bg-[#eff6ff]" : "border-gray-200 bg-white hover:bg-gray-50"}`}><input type="radio" name="skill-agent-stage" value={option.value} checked={agentStage === option.value} onChange={() => setAgentStage(option.value)} className="h-4 w-4 accent-[#2563eb]" /><span className={`text-[12.5px] font-medium ${agentStage === option.value ? "font-bold text-[#2563eb]" : "text-gray-700"}`}>{option.label}</span></label>)}</div></fieldset></div><div className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-2xl border border-gray-200/80 bg-white shadow-[0_4px_12px_rgba(0,0,0,0.02)]"><div className="flex shrink-0 items-center justify-between border-b border-gray-200 px-5 py-3"><div className="text-[13px] font-bold text-gray-800">规则内容实时编辑与渲染</div><div className="flex items-center rounded-md bg-gray-50 p-1">{(["split", "edit", "preview"] as ViewMode[]).map((mode) => <button key={mode} type="button" onClick={() => setViewMode(mode)} className={`rounded-md px-3 py-1.5 text-[12px] font-medium transition ${viewMode === mode ? "bg-white text-gray-900 shadow-sm" : "text-gray-500 hover:text-gray-900"}`}>{mode === "split" ? "分屏" : mode === "edit" ? "仅编辑" : "仅预览"}</button>)}</div></div><div className={`grid min-h-0 flex-1 gap-4 p-4 ${viewMode === "split" ? "grid-cols-2" : "grid-cols-1"}`}>{viewMode !== "preview" && <label className="flex min-h-0 flex-col text-[12px] font-semibold text-gray-500">Markdown 源码编辑区域 <span className="text-red-500">*</span><textarea value={content} onChange={(event) => setContent(event.target.value)} className="mt-2 min-h-0 flex-1 resize-none rounded-xl bg-[#1c1c1c] p-4 font-mono text-[13px] leading-6 text-gray-200 outline-none" placeholder="# 在此编写规则的 Markdown 条款与 Prompt 说明..." /></label>}{viewMode !== "edit" && <div className="flex min-h-0 flex-col text-[12px] font-semibold text-gray-500">Markdown 预览<div className="prose prose-sm mt-2 min-h-0 flex-1 max-w-none overflow-y-auto rounded-xl border border-gray-200 bg-white p-6 text-gray-700"><ReactMarkdown>{content || "暂无内容"}</ReactMarkdown></div></div>}</div></div></div><div className="flex shrink-0 justify-end gap-3 border-t border-black/[0.05] bg-white/30 px-6 py-4"><button type="button" onClick={() => closeDialog()} className="rounded-xl px-5 py-2 text-[13px] font-medium text-gray-500 hover:bg-black/5">取消</button><button type="button" disabled={saving || !title.trim() || !content.trim() || !taskType || !agentStage || (dialogMode === "import" && !file)} onClick={() => void (dialogMode === "import" ? submitImport() : submitEdit())} className="flex items-center gap-2 rounded-xl bg-[#2563eb] px-6 py-2 text-[13px] font-medium text-white shadow-sm hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50">{saving && <LoadingOutlined />}{saving ? "保存中..." : dialogMode === "import" ? "确认导入" : "保存修改"}</button></div></div></div>}
    </main>
  );
}
