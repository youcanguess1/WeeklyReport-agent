import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, it } from "node:test";
import { expect, apiMock, spies } from "../../test/helpers";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { createTask } from "../../api/tasks";
import { CreateTaskPage } from "./CreateTaskPage";


const mockedCreateTask = apiMock("createTask");

describe("CreateTaskPage", () => {
  beforeEach(() => {
    mockedCreateTask.mockClear();
    mockedCreateTask.mockResolvedValue({
      task: {
        task_id: "task_new",
        task_name: "New task",
        task_type: "weekly_report",
        description: "",
        status: "created",
        current_node: null,
        waiting_reason: null,
        current_report_version: null,
        artifact_count: 1,
        created_at: "2026-08-08T00:00:00+00:00",
        updated_at: "2026-08-08T00:00:00+00:00",
      },
    });
  });

  it("creates a text-only weekly report without requiring an upload", async () => {
    render(
      <MemoryRouter initialEntries={["/tasks/create"]}>
        <Routes>
          <Route path="/tasks/create" element={<CreateTaskPage />} />
          <Route path="/tasks" element={<div>Task board route</div>} />
        </Routes>
      </MemoryRouter>,
    );

    fireEvent.change(screen.getByRole("textbox", { name: /周报名称/ }), { target: { value: "New task" } });
    fireEvent.change(screen.getByRole("textbox", { name: /项目背景与碎片化工作记录/ }), { target: { value: "本周完成首页评审。" } });

    fireEvent.click(screen.getByRole("button", { name: /创建周报/ }));
    await waitFor(() => expect(mockedCreateTask).toHaveBeenCalledTimes(1));

    const submitted = mockedCreateTask.mock.calls[0][0];
    expect(submitted).toBeInstanceOf(FormData);
    expect(submitted.get("task_name")).toBe("New task");
    expect(submitted.get("notes")).toBe("本周完成首页评审。");
    expect(submitted.get("task_type")).toBe("weekly_report");
    expect(submitted.get("template_name")).toBe("docs/weekly_report_template.md");
    expect(submitted.get("files")).toBeNull();
    expect(await screen.findByText("Task board route")).toBeInTheDocument();
  });

  it("accepts image uploads but rejects video files and requires project notes", async () => {
    render(
      <MemoryRouter initialEntries={["/tasks/create"]}>
        <Routes>
          <Route path="/tasks/create" element={<CreateTaskPage />} />
          <Route path="/tasks" element={<div>Task board route</div>} />
        </Routes>
      </MemoryRouter>,
    );

    const fileInput = document.querySelector<HTMLInputElement>("input[type=file]")!;
    expect(fileInput.accept).toBe("image/png,image/jpeg,image/webp");
    expect(screen.queryByText(/视频/)).not.toBeInTheDocument();

    fireEvent.change(screen.getByRole("textbox", { name: /周报名称/ }), { target: { value: "New task" } });
    expect(screen.getByRole("button", { name: /创建周报/ })).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: /项目背景与碎片化工作记录/ }), { target: { value: "Project notes" } });

    const video = new File(["video"], "capture.mp4", { type: "video/mp4", lastModified: 1 });
    fireEvent.change(fileInput, { target: { files: [video] } });
    expect(await screen.findByText("仅支持 PNG、JPG、JPEG 或 WebP 图片")).toBeInTheDocument();
    expect(screen.queryByText("capture.mp4")).not.toBeInTheDocument();

    const image = new File(["image"], "supplement.png", { type: "image/png", lastModified: 2 });
    fireEvent.change(fileInput, { target: { files: [image] } });
    fireEvent.click(screen.getByRole("button", { name: /创建周报/ }));
    await waitFor(() => expect(mockedCreateTask).toHaveBeenCalledTimes(1));
    expect(mockedCreateTask.mock.calls[0][0].get("files")).toBe(image);
  });
});
