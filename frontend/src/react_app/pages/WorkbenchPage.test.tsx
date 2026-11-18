import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, it } from "node:test";
import { expect, apiMock, spies } from "../../test/helpers";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { confirmTask, controlTask, listTaskEvents, listTaskWeeklyReportVersions, listTasks, submitHumanFeedback, uploadTaskSupplement } from "../../api/tasks";
import { WorkbenchPage } from "./WorkbenchPage";


const mockedControlTask = apiMock("controlTask");
const mockedConfirmTask = apiMock("confirmTask");
const mockedListEvents = apiMock("listTaskEvents");
const mockedListVersions = apiMock("listTaskWeeklyReportVersions");
const mockedListTasks = apiMock("listTasks");
const mockedSubmitFeedback = apiMock("submitHumanFeedback");
const mockedUploadSupplement = apiMock("uploadTaskSupplement");

const task = {
  task_id: "task_001",
  task_name: "Printer reload",
  task_type: "weekly_report" as const,
  description: "",
  status: "waiting" as const,
  current_node: "wait_for_human",
  waiting_reason: "manual_pause",
  current_report_version: "V1",
  artifact_count: 0,
  created_at: "2026-08-08T00:00:00+00:00",
  updated_at: "2026-08-08T00:00:00+00:00",
};

describe("WorkbenchPage", () => {
  beforeEach(() => {
    mockedListEvents.mockResolvedValue({
      task,
      operations: [],
      events: [
      {
        event_id: "trace-1",
        event_type: "node_started",
        timestamp: "2026-08-08T00:00:00+00:00",
        agent: "周报 Writer",
        role: "assistant",
        kind: "action",
        content: "正在生成项目周报",
        action_label: "周报 Writer",
        session_id: "trace-1",
        sequence: 1,
        request_id: null,
      },
      {
        event_id: "session-1:1",
        event_type: "action",
        timestamp: "2026-08-08T00:00:00.500+00:00",
        agent: "周报 Writer",
        role: "assistant",
        kind: "action",
        content: "已完成",
        action_label: "读取技能内容",
        session_id: "session-1",
        sequence: 1,
        request_id: null,
      },
      {
        event_id: "trace-2",
        event_type: "node_completed",
        timestamp: "2026-08-08T00:00:01+00:00",
        agent: "周报 Writer",
        role: "assistant",
        kind: "action",
        content: "已生成项目周报初稿（V1）",
        action_label: "周报 Writer",
        session_id: "trace-1",
        sequence: 2,
        request_id: null,
      },
      ],
      cursor: "cursor-1",
    });
    mockedListVersions.mockResolvedValue([
      {
        report_version_id: "report_v0",
        version: "V0",
        created_at: "2026-08-08T00:00:00+00:00",
        change_note: "Initial WeeklyReport draft",
        markdown: "# Printer reload 周报 V0\n\n## 一、本周完成\n- Reload complete.\n\n## 二、进行中\n- Validation in progress.\n\n## 三、风险与阻塞\n- （暂无可据资料）\n\n## 四、下周计划\n- （暂无可据资料）\n\n## 五、待确认事项\n- Confirm validation date.",
      },
      {
        report_version_id: "report_v1",
        version: "V1",
        created_at: "2026-08-08T00:00:01+00:00",
        change_note: "Revision from review issues",
        markdown: "# Printer reload 周报 V1\n\n## 一、本周完成\n- Reload complete.\n\n## 二、进行中\n- Validation in progress.\n\n## 三、风险与阻塞\n- （暂无可据资料）\n\n## 四、下周计划\n- （暂无可据资料）\n\n## 五、待确认事项\n- Confirm validation date.",
      },
    ]);
    mockedListTasks.mockResolvedValue({ items: [task], total: 1, page: 1, page_size: 200 });
    mockedControlTask.mockResolvedValue({ ...task, status: "queued", waiting_reason: null });
    mockedConfirmTask.mockResolvedValue({ ...task, status: "queued", waiting_reason: null });
    mockedSubmitFeedback.mockResolvedValue({} as never);
    mockedUploadSupplement.mockResolvedValue({} as never);
  });

  it("renders weekly report messages, switches report versions, and resumes a paused task", async () => {
    render(
      <MemoryRouter initialEntries={["/tasks/task_001"]}>
        <Routes>
          <Route path="/tasks/:taskId" element={<WorkbenchPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("Printer reload")).toBeInTheDocument();
    await waitFor(() => expect(mockedListEvents).toHaveBeenCalledWith("task_001"));
    expect(screen.getByText("读取技能内容")).toBeInTheDocument();
    expect(screen.getByText("已生成项目周报初稿（V1）")).toBeInTheDocument();
    expect(screen.getAllByText("周报 Writer")).toHaveLength(1);
    expect(screen.getAllByText("已完成").length).toBeGreaterThan(0);
    expect(screen.queryByText("处理中")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Printer reload 周报 V1" })).toBeInTheDocument();
    expect(screen.getByText("根据审核意见修订")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "一、本周完成" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "五、待确认事项" })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("选择周报版本"), { target: { value: "report_v0" } });
    expect(screen.getByRole("heading", { name: "Printer reload 周报 V0" })).toBeInTheDocument();
    expect(screen.getByText("初始周报")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /恢复/ }));

    await waitFor(() => expect(mockedControlTask).toHaveBeenCalledWith("task_001", "resume"));
  });

  it("hides legacy WeeklyReport wording from historical workflow events", async () => {
    mockedListEvents.mockResolvedValue({
      task,
      operations: [],
      events: [
        {
          event_id: "review-start",
          event_type: "node_started",
          timestamp: "2026-08-08T00:00:00+00:00",
          agent: "WeeklyReport Review",
          role: "assistant",
          kind: "action",
          content: "正在审核项目周报",
          action_label: "WeeklyReport Review",
          session_id: "trace-review",
          sequence: 1,
          request_id: null,
        },
        {
          event_id: "review-complete",
          event_type: "node_completed",
          timestamp: "2026-08-08T00:00:01+00:00",
          agent: "WeeklyReport Review",
          role: "assistant",
          kind: "action",
          content: "项目周报已生成\n保存位置：D:\\data\\derived\\reports\\artifact__final_report_v2.md",
          action_label: "WeeklyReport Review",
          session_id: "trace-review",
          sequence: 2,
          request_id: null,
        },
      ],
      cursor: "cursor-1",
    });

    render(
      <MemoryRouter initialEntries={["/tasks/task_001"]}>
        <Routes><Route path="/tasks/:taskId" element={<WorkbenchPage />} /></Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("周报 Review")).toBeInTheDocument();
    expect(screen.getByText("正在审核项目周报")).toBeInTheDocument();
    expect(screen.getByText("周报 Markdown 已保存至任务产物目录")).toBeInTheDocument();
    expect(screen.queryByText(/WeeklyReport/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/final_report/i)).not.toBeInTheDocument();
  });

  it("downloads the selected weekly report version as UTF-8 Markdown", async () => {
    const downloadedBlobs: Blob[] = [];
    const downloadedNames: string[] = [];
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: spies.fn((blob: Blob) => {
        downloadedBlobs.push(blob);
        return `blob:weekly-report-${downloadedBlobs.length}`;
      }),
    });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: spies.fn() });
    const clickSpy = spies.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      downloadedNames.push(this.download);
    });

    render(
      <MemoryRouter initialEntries={["/tasks/task_001"]}>
        <Routes><Route path="/tasks/:taskId" element={<WorkbenchPage />} /></Routes>
      </MemoryRouter>,
    );

    await screen.findByRole("heading", { name: "Printer reload 周报 V1" });
    fireEvent.click(screen.getByRole("button", { name: "下载 Markdown" }));
    fireEvent.change(screen.getByLabelText("选择周报版本"), { target: { value: "report_v0" } });
    await screen.findByRole("heading", { name: "Printer reload 周报 V0" });
    fireEvent.click(screen.getByRole("button", { name: "下载 Markdown" }));

    expect(downloadedNames).toEqual(["Printer reload_V1.md", "Printer reload_V0.md"]);
    expect(downloadedBlobs.map((blob) => blob.type)).toEqual(["text/markdown;charset=utf-8", "text/markdown;charset=utf-8"]);
    const readBlob = (blob: Blob) => new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(reader.error);
      reader.readAsText(blob);
    });
    await expect(readBlob(downloadedBlobs[0])).resolves.toContain("# Printer reload 周报 V1");
    await expect(readBlob(downloadedBlobs[1])).resolves.toContain("# Printer reload 周报 V0");
    expect(clickSpy).toHaveBeenCalledTimes(2);
  });

  it("replaces the disabled feedback composer with a compact final-report notice", async () => {
    mockedListEvents.mockResolvedValue({
      task: { ...task, status: "completed", waiting_reason: null },
      operations: [],
      events: [],
      cursor: "cursor-1",
    });
    render(
      <MemoryRouter initialEntries={["/tasks/task_001"]}>
        <Routes><Route path="/tasks/:taskId" element={<WorkbenchPage />} /></Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("最终周报已确认，可继续查看历史版本或下载当前报告。")).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("请输入修改建议或反馈...")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下载 Markdown" })).toBeEnabled();
  });

  it("shows a retry action when the workbench request fails", async () => {
    mockedListEvents.mockRejectedValueOnce(new Error("request failed"));
    render(
      <MemoryRouter initialEntries={["/tasks/task_001"]}>
        <Routes>
          <Route path="/tasks/:taskId" element={<WorkbenchPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("request failed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /重试/ })).toBeInTheDocument();
  });

  it("submits required human feedback as multipart form data", async () => {
    mockedListEvents.mockResolvedValue({ task: { ...task, waiting_reason: "human" }, operations: [], events: [], cursor: "cursor-1" });
    render(
      <MemoryRouter initialEntries={["/tasks/task_001"]}>
        <Routes><Route path="/tasks/:taskId" element={<WorkbenchPage />} /></Routes>
      </MemoryRouter>,
    );
    const input = await screen.findByPlaceholderText("请输入修改建议或反馈...");
    fireEvent.change(input, { target: { value: "Please clarify the final step." } });
    fireEvent.click(screen.getByRole("button", { name: /发送/ }));
    await waitFor(() => expect(mockedSubmitFeedback).toHaveBeenCalled());
    expect(mockedSubmitFeedback.mock.calls[0][0]).toBe("task_001");
    expect(mockedSubmitFeedback.mock.calls[0][1].get("feedback_text")).toBe("Please clarify the final step.");
  });

  it("opens the single-file supplement dialog for evidence waiting", async () => {
    mockedListEvents.mockResolvedValue({ task: { ...task, waiting_reason: "evidence" }, operations: [], events: [], cursor: "cursor-1" });
    render(
      <MemoryRouter initialEntries={["/tasks/task_001"]}>
        <Routes><Route path="/tasks/:taskId" element={<WorkbenchPage />} /></Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByRole("button", { name: /上传补充证据/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /上传补充证据/ }));
    expect(screen.getByRole("button", { name: /选择图片/ })).toBeInTheDocument();
  });

  it("keeps the chat composer available while evidence is pending", async () => {
    mockedListEvents.mockResolvedValue({ task: { ...task, waiting_reason: "evidence" }, operations: [], events: [], cursor: "cursor-1" });
    render(
      <MemoryRouter initialEntries={["/tasks/task_001"]}>
        <Routes><Route path="/tasks/:taskId" element={<WorkbenchPage />} /></Routes>
      </MemoryRouter>,
    );
    const input = await screen.findByPlaceholderText("请输入修改建议或反馈...");
    fireEvent.change(input, { target: { value: "Please revise the final step." } });
    fireEvent.click(screen.getByRole("button", { name: /发送/ }));
    await waitFor(() => expect(mockedSubmitFeedback).toHaveBeenCalled());
    expect(mockedUploadSupplement).not.toHaveBeenCalled();
  });

  it("rejects non-image files in the chat composer", async () => {
    mockedListEvents.mockResolvedValue({ task: { ...task, waiting_reason: "evidence" }, operations: [], events: [], cursor: "cursor-1" });
    render(
      <MemoryRouter initialEntries={["/tasks/task_001"]}>
        <Routes><Route path="/tasks/:taskId" element={<WorkbenchPage />} /></Routes>
      </MemoryRouter>,
    );
    await screen.findByPlaceholderText("请输入修改建议或反馈...");
    const input = document.querySelector('input[type="file"][accept="image/*"]') as HTMLInputElement;
    const submitCallCount = mockedSubmitFeedback.mock.calls.length;
    fireEvent.change(input, { target: { files: [new File(["video"], "clip.mp4", { type: "video/mp4" })] } });
    expect(screen.queryByText("clip.mp4")).not.toBeInTheDocument();
    expect(mockedSubmitFeedback.mock.calls).toHaveLength(submitCallCount);
  });

  it("renders evidence review details without internal decision fields", async () => {
    mockedListEvents.mockResolvedValue({
      task: { ...task, waiting_reason: "evidence" },
      operations: [],
      events: [
        {
          event_id: "trace-start",
          event_type: "node_started",
          timestamp: "2026-08-08T00:00:00+00:00",
          agent: "周报 Review",
          role: "assistant",
          kind: "action",
          content: "正在审核项目周报",
          action_label: "周报 Review",
          session_id: "trace",
          sequence: 1,
          request_id: null,
        },
        {
          event_id: "trace-complete",
          event_type: "node_completed",
          timestamp: "2026-08-08T00:00:01+00:00",
          agent: "周报 Review",
          role: "assistant",
          kind: "action",
          content: "审核发现需要补充证据\n问题：无法确认操作结果\n需要证据：展示完成画面",
          action_label: "周报 Review",
          session_id: "trace",
          sequence: 2,
          request_id: null,
        },
      ],
      cursor: "cursor-1",
    });
    render(
      <MemoryRouter initialEntries={["/tasks/task_001"]}>
        <Routes><Route path="/tasks/:taskId" element={<WorkbenchPage />} /></Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText(/审核发现需要补充证据/)).toBeInTheDocument();
    expect(screen.getByText(/无法确认操作结果/)).toBeInTheDocument();
    expect(screen.getByText(/展示完成画面/)).toBeInTheDocument();
    expect(screen.queryByText("request_evidence")).not.toBeInTheDocument();
    expect(screen.queryByText("trace-complete")).not.toBeInTheDocument();
  });

  it("shows a confirmation action for an approved final review", async () => {
    mockedListEvents.mockResolvedValue({
      task: { ...task, waiting_reason: "human" },
      operations: [],
      events: [{
        event_id: "review-approved",
        event_type: "node_completed",
        timestamp: "2026-08-08T00:00:01+00:00",
        agent: "周报 Review",
        role: "assistant",
        kind: "action",
        content: "审核通过\n是否选择当前周报作为最终版",
        action_label: "周报 Review",
        session_id: "trace",
        sequence: 1,
        request_id: null,
      }],
      cursor: "cursor-1",
    });
    render(
      <MemoryRouter initialEntries={["/tasks/task_001"]}>
        <Routes><Route path="/tasks/:taskId" element={<WorkbenchPage />} /></Routes>
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "确认最终周报" }));
    await waitFor(() => expect(mockedConfirmTask).toHaveBeenCalledWith("task_001"));
  });

  it("shows the final confirmation action when review reaches its limit", async () => {
    mockedListEvents.mockResolvedValue({
      task: { ...task, waiting_reason: "human" },
      operations: [],
      events: [{
        event_id: "review-limit",
        event_type: "node_completed",
        timestamp: "2026-08-08T00:00:01+00:00",
        agent: "周报 Review",
        role: "assistant",
        kind: "action",
        content: "已达到自动审核轮次上限，当前周报仍有未解决问题。\n是否选择当前周报作为最终版？",
        action_label: "周报 Review",
        session_id: "trace",
        sequence: 1,
        request_id: null,
      }],
      cursor: "cursor-1",
    });
    render(
      <MemoryRouter initialEntries={["/tasks/task_001"]}>
        <Routes><Route path="/tasks/:taskId" element={<WorkbenchPage />} /></Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText(/自动审核轮次上限/)).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "确认最终周报" }));
    await waitFor(() => expect(mockedConfirmTask).toHaveBeenCalledWith("task_001"));
  });
});
