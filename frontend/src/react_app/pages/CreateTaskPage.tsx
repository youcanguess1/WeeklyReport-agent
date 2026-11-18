import {
  ArrowRightOutlined,
  CloudUploadOutlined,
  CloseOutlined,
  PictureOutlined,
} from "@ant-design/icons";
import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { createTask } from "../../api/tasks";
import { defaultWeeklyReportTemplateName } from "../../constants/workflow";

const acceptedFiles = "image/png,image/jpeg,image/webp";
const allowedImageExtensions = new Set([".png", ".jpg", ".jpeg", ".webp"]);

export function CreateTaskPage() {
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [taskName, setTaskName] = useState("");
  const [notes, setNotes] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const appendFiles = (selectedFiles: FileList | File[]) => {
    const images = Array.from(selectedFiles).filter((file) => {
      const extension = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
      return file.type.startsWith("image/") && allowedImageExtensions.has(extension);
    });
    if (images.length !== selectedFiles.length) {
      setError("仅支持 PNG、JPG、JPEG 或 WebP 图片");
    }
    setFiles((current) => {
      const next = [...current];
      images.forEach((file) => {
        const exists = next.some(
          (item) => item.name === file.name && item.size === file.size && item.lastModified === file.lastModified,
        );
        if (!exists) next.push(file);
      });
      return next;
    });
  };

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!taskName.trim() || !notes.trim()) return;

    setSubmitting(true);
    setError(null);
    const formData = new FormData();
    formData.append("task_name", taskName.trim());
    formData.append("task_type", "weekly_report");
    formData.append("template_name", defaultWeeklyReportTemplateName);
    formData.append("notes", notes.trim());
    files.forEach((file) => formData.append("files", file));

    try {
      await createTask(formData);
      navigate("/tasks");
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "周报创建失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="min-h-full flex-1 overflow-y-auto px-6 py-8 md:px-10">
      <div className="mx-auto max-w-[900px]">
        <div className="mb-8 flex items-end justify-between gap-4">
          <div>
            <div className="mb-2 text-[12px] font-semibold uppercase tracking-[0.16em] text-gray-400">周报助手 / 新建周报</div>
            <h1 className="text-[30px] font-bold tracking-tight text-gray-900">新建周报</h1>
          </div>
          <button type="button" onClick={() => navigate("/tasks")} className="rounded-full px-4 py-2 text-sm text-gray-500 hover:bg-white hover:text-gray-900">
            返回周报看板
          </button>
        </div>

        <form onSubmit={handleSubmit} className="rounded-[28px] border border-white/70 bg-white/75 p-6 shadow-[0_8px_32px_-12px_rgba(0,0,0,0.08)] backdrop-blur-md md:p-10">
          <section className="mb-10">
            <div className="mb-6 text-[15px] font-bold text-gray-700">周报基本信息</div>
            <div className="space-y-6">
              <label className="block">
                <span className="mb-2 block text-[13px] font-semibold text-gray-700">项目名称 / 周报名称 <span className="text-red-500">*</span></span>
                <input
                  required
                  value={taskName}
                  onChange={(event) => setTaskName(event.target.value)}
                  placeholder="例如：企业官网改版二期｜2026-10-19 至 2026-10-23"
                  className="w-full rounded-2xl border border-white/70 bg-white/50 px-4 py-3 text-[13.5px] text-gray-800 outline-none transition focus:border-blue-300 focus:ring-2 focus:ring-blue-100"
                />
              </label>

              <label className="block">
                <span className="mb-2 block text-[13px] font-semibold text-gray-700">项目背景与碎片化工作记录 <span className="text-red-500">*</span></span>
                <textarea
                  required
                  value={notes}
                  onChange={(event) => setNotes(event.target.value)}
                  placeholder="写下项目背景、本周零散进展、问题、下周安排或待确认事项。可直接使用自然语言，不必套用固定格式。"
                  className="min-h-[120px] w-full resize-y rounded-2xl border border-white/70 bg-white/50 px-4 py-3 text-[13.5px] text-gray-800 outline-none transition focus:border-blue-300 focus:ring-2 focus:ring-blue-100"
                />
              </label>
            </div>
          </section>

          <section className="mb-10">
            <div className="mb-6 flex items-center gap-2 text-[15px] font-bold text-gray-700"><CloudUploadOutlined className="text-gray-400" /> 图片补充材料（可选）</div>
            <div
              onClick={() => fileInputRef.current?.click()}
              onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
              onDragOver={(event) => event.preventDefault()}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                appendFiles(event.dataTransfer.files);
              }}
              className={`cursor-pointer rounded-[24px] border-2 border-dashed p-8 transition ${dragging ? "border-blue-500 bg-blue-100/50" : "border-blue-200/80 bg-blue-50/20 hover:bg-blue-50/40"}`}
            >
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept={acceptedFiles}
                className="hidden"
                onChange={(event) => {
                  if (event.target.files) appendFiles(event.target.files);
                  event.target.value = "";
                }}
              />
              <div className="flex flex-col items-center text-center">
                <div className="mb-4 flex gap-3 text-2xl">
                  <PictureOutlined className="rounded-2xl bg-blue-100/70 p-3 text-blue-600" />
                </div>
                <div className="text-[14px] font-semibold text-gray-900"><span className="text-blue-600">点击选择图片</span> 或拖拽图片到此处</div>
                <div className="mt-1.5 max-w-[400px] text-[12px] text-gray-400">支持 PNG、JPG、JPEG、WebP；不上传图片也可以创建周报</div>
              </div>
              {files.length > 0 && (
                <div className="mt-6 grid grid-cols-1 gap-3 sm:grid-cols-2">
                  {files.map((file) => (
                    <div key={`${file.name}-${file.lastModified}`} onClick={(event) => event.stopPropagation()} className="flex items-center justify-between rounded-2xl border border-gray-200/80 bg-white/90 px-3 py-3 text-[12px] text-gray-700 shadow-sm">
                      <span className="flex min-w-0 items-center gap-2">
                        <PictureOutlined className="text-blue-600" />
                        <span className="truncate">{file.name}</span>
                      </span>
                      <button
                        type="button"
                        aria-label={`移除 ${file.name}`}
                        onClick={() => setFiles((current) => current.filter((item) => item !== file))}
                        className="ml-2 rounded-full p-1 text-gray-400 hover:bg-red-50 hover:text-red-500"
                      >
                        <CloseOutlined />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </section>

          {error && <div className="mb-6 rounded-2xl bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

          <div className="flex items-center justify-end gap-3 border-t border-black/[0.04] pt-6">
            <button type="button" onClick={() => navigate("/tasks")} className="rounded-full px-5 py-2.5 text-[13.5px] font-medium text-gray-500 transition hover:bg-black/5">取消</button>
            <button disabled={submitting || !taskName.trim() || !notes.trim()} type="submit" className="flex items-center gap-2 rounded-full bg-[#1c1c1c] px-6 py-2.5 text-[13.5px] font-medium text-white shadow-md transition hover:bg-black disabled:cursor-not-allowed disabled:opacity-40">
              {submitting ? "创建中..." : "创建周报"}
              {!submitting && <ArrowRightOutlined />}
            </button>
          </div>
        </form>
      </div>
    </main>
  );
}
