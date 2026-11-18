import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, it } from "node:test";
import { expect, apiMock, spies } from "../../test/helpers";

import { ApiError } from "../../api/client";
import { getTask } from "../../api/tasks";
import { AppShell } from "./AppShell";


const mockedGetTask = apiMock("getTask");

describe("AppShell task context", () => {
  beforeEach(() => {
    window.localStorage.clear();
    mockedGetTask.mockReset();
  });

  it("starts without a selected task or seeded task context", () => {
    render(<MemoryRouter initialEntries={["/tasks"]}><AppShell /></MemoryRouter>);

    expect(mockedGetTask).not.toHaveBeenCalled();
    expect(screen.getByRole("link", { name: "周报工作台" })).toHaveAttribute("href", "/tasks");
    expect(document.querySelector("#sidebarTaskId")).toBeNull();
  });

  it("clears deleted tasks and cached names without exposing them", async () => {
    window.localStorage.setItem("weeklyreport.activeTaskId", "deleted-task");
    window.localStorage.setItem("weeklyreport.activeTaskName", "Private saved case");
    mockedGetTask.mockRejectedValue(new ApiError("Task not found", 404));

    render(<MemoryRouter initialEntries={["/tasks"]}><AppShell /></MemoryRouter>);

    expect(screen.queryByText("Private saved case")).not.toBeInTheDocument();
    await waitFor(() => expect(window.localStorage.getItem("weeklyreport.activeTaskId")).toBeNull());
    expect(window.localStorage.getItem("weeklyreport.activeTaskName")).toBeNull();
    expect(screen.getByRole("link", { name: "周报工作台" })).toHaveAttribute("href", "/tasks");
  });

  it("uses actual task data even when its identifier matches a former demo", async () => {
    mockedGetTask.mockResolvedValue({ task_id: "task1", task_name: "Actual weekly report" } as Awaited<ReturnType<typeof getTask>>);

    render(<MemoryRouter initialEntries={["/tasks/task1"]}><AppShell /></MemoryRouter>);

    expect(await screen.findByText("Actual weekly report")).toBeInTheDocument();
    expect(screen.getByText("#task1")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "运行追踪" })).toHaveAttribute("href", "/tasks/task1/trace");
  });

  it("preserves the task identifier on a temporary server failure", async () => {
    window.localStorage.setItem("weeklyreport.activeTaskId", "existing-task");
    mockedGetTask.mockRejectedValue(new ApiError("Unavailable", 503));

    render(<MemoryRouter initialEntries={["/tasks"]}><AppShell /></MemoryRouter>);

    await waitFor(() => expect(mockedGetTask).toHaveBeenCalledWith("existing-task"));
    expect(window.localStorage.getItem("weeklyreport.activeTaskId")).toBe("existing-task");
    expect(screen.getByRole("link", { name: "周报工作台" })).toHaveAttribute("href", "/tasks");
  });
});
