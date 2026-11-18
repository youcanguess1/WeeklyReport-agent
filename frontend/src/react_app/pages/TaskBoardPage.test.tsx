import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, it } from "node:test";
import { expect, apiMock, spies } from "../../test/helpers";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { getTaskSummary, listTasks } from "../../api/tasks";
import { TaskBoardPage } from "./TaskBoardPage";


const mockedListTasks = apiMock("listTasks");
const mockedGetTaskSummary = apiMock("getTaskSummary");

const task = {
  task_id: "task_001",
  task_name: "Printer reload",
  task_type: "weekly_report" as const,
  description: "Capture the tray reinsertion step.",
  status: "waiting" as const,
  current_node: "wait_for_evidence",
  waiting_reason: "evidence",
  current_report_version: null,
  artifact_count: 2,
  created_at: "2026-08-08T00:00:00+00:00",
  updated_at: "2026-08-08T00:00:00+00:00",
};

describe("TaskBoardPage", () => {
  beforeEach(() => {
    mockedListTasks.mockResolvedValue({ items: [task], total: 1, page: 1, page_size: 50 });
    mockedGetTaskSummary.mockResolvedValue({ total_count: 1, running_count: 0, pending_count: 1, completed_count: 0 });
  });

  it("shows an empty board when the database has no saved cases", async () => {
    mockedListTasks.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 50 });
    mockedGetTaskSummary.mockResolvedValue({ total_count: 0, running_count: 0, pending_count: 0, completed_count: 0 });

    render(<TaskBoardPage />, { wrapper: ({ children }) => <MemoryRouter>{children}</MemoryRouter> });

    expect(await screen.findByText("暂无周报")).toBeInTheDocument();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    expect(screen.getAllByText("0", { selector: "div" })).toHaveLength(4);
  });

  it("renders API-backed statistics and task cards", async () => {
    render(<TaskBoardPage />, { wrapper: ({ children }) => <MemoryRouter>{children}</MemoryRouter> });

    expect(await screen.findByText("Printer reload")).toBeInTheDocument();
    expect(screen.getByText("周报总数")).toBeInTheDocument();
    expect(screen.getByText("待处理", { selector: "div" })).toBeInTheDocument();
    expect(screen.getByText("2", { selector: "div" })).toBeInTheDocument();
  });

  it("filters loaded tasks and opens the task route", async () => {
    render(
      <MemoryRouter initialEntries={["/tasks"]}>
        <Routes>
          <Route path="/tasks" element={<TaskBoardPage />} />
          <Route path="/tasks/:taskId" element={<div>Workbench route</div>} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByText("Printer reload");
    fireEvent.change(screen.getByPlaceholderText("搜索周报 ID、项目名称或材料说明..."), { target: { value: "not found" } });
    expect(screen.getByText("没有匹配的周报")).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("搜索周报 ID、项目名称或材料说明..."), { target: { value: "Printer" } });
    fireEvent.click(screen.getByRole("button", { name: "打开 Printer reload" }));
    await waitFor(() => expect(screen.getByText("Workbench route")).toBeInTheDocument());
  });
});
