import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, it } from "node:test";
import { expect, apiMock, spies } from "../../test/helpers";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { listTasks } from "../../api/tasks";
import { getStructuredTrace, getTaskTrace } from "../../api/traces";
import { TracePage } from "./TracePage";


const mockedListTasks = apiMock("listTasks");
const mockedGetTaskTrace = apiMock("getTaskTrace");
const mockedGetStructuredTrace = apiMock("getStructuredTrace");

const child = {
  observation_id: "child-1",
  parent_observation_id: "root-1",
  name: "WriterAgent",
  type: "agent",
  level: "ok",
  status_message: "completed",
  started_at: null,
  ended_at: null,
  duration_ms: 120,
  model: "gpt-test",
  input_tokens: 2,
  output_tokens: 3,
  total_tokens: 5,
  total_cost: null,
  input: { prompt: "write" },
  output: { markdown: "# WeeklyReport" },
  metadata: {},
  children: [],
};

const latestChild = {
  ...child,
  observation_id: "child-2",
  name: "ReviewAgent",
  input: { prompt: "review" },
  output: { decision: "approved" },
};

describe("TracePage", () => {
  beforeEach(() => {
    mockedListTasks.mockResolvedValue({ items: [{ task_id: "task-1", task_name: "Printer reload", task_type: "weekly_report", description: "", status: "completed", current_node: null, waiting_reason: null, current_report_version: "V1", artifact_count: 0, created_at: "2026-08-08T00:00:00Z", updated_at: "2026-08-08T00:00:00Z" }], total: 1, page: 1, page_size: 50 });
    mockedGetTaskTrace.mockResolvedValue({ trace: { trace_id: "trace-1", task_id: "task-1", task_name: "Printer reload", task_type: "weekly_report", status: "completed", current_stage: "writer", started_at: "2026-08-08T00:00:00Z", ended_at: null, updated_at: "2026-08-08T00:00:00Z", summary: null, error_message: null }, logs: [], structured_trace_summary: null });
    mockedGetStructuredTrace.mockResolvedValue({ trace_id: "trace-1", task_id: "task-1", provider: "local_fallback", source: "structured", trace_url: null, status: "completed", synced_at: null, cache_artifact_id: null, sync_error: null, root: { ...child, observation_id: "root-1", name: "WeeklyReport Workflow", type: "root", children: [child, latestChild], input: { task: "input" }, output: { status: "done" }, duration_ms: 300, total_tokens: 8 }, observations: [], metrics: { observation_count: 3, error_count: 0, warning_count: 0, duration_ms: 300, input_tokens: 3, output_tokens: 5, total_tokens: 8, total_cost: null }, fallback_logs: [] });
  });

  it("renders real nodes and inspector payloads", async () => {
    render(<MemoryRouter initialEntries={["/tasks/task-1/trace"]}><Routes><Route path="/tasks/:taskId/trace" element={<TracePage />} /></Routes></MemoryRouter>);
    expect(await screen.findByText("WriterAgent")).toBeInTheDocument();
    fireEvent.click(screen.getByText("LangGraph Task Execution"));
    expect(screen.getByText("Inspector: LangGraph Task Execution")).toBeInTheDocument();
    expect(screen.queryByText("WeeklyReport Workflow")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("WriterAgent"));
    expect(screen.getByText(/prompt/)).toBeInTheDocument();
    expect(screen.getByText("gpt-test")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Copy Input Payload" }));
    await waitFor(() => expect(mockedGetStructuredTrace).toHaveBeenCalledWith("trace-1"));
  });

  it("waits for a trace that has not been created yet", async () => {
    mockedGetTaskTrace.mockRejectedValueOnce(new Error("No trace found for task id: task-1"));
    render(<MemoryRouter initialEntries={["/tasks/task-1/trace"]}><Routes><Route path="/tasks/:taskId/trace" element={<TracePage />} /></Routes></MemoryRouter>);
    expect(await screen.findByText("周报运行记录尚未产生，正在等待工作流启动..."))
      .toBeInTheDocument();
  });

  it("keeps the selected historical node when the trace is refreshed", async () => {
    render(<MemoryRouter initialEntries={["/tasks/task-1/trace"]}><Routes><Route path="/tasks/:taskId/trace" element={<TracePage />} /></Routes></MemoryRouter>);
    expect(await screen.findByText("ReviewAgent")).toBeInTheDocument();
    fireEvent.click(screen.getByText("WriterAgent"));
    expect(screen.getByText("Inspector: WriterAgent")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "刷新运行追踪" }));

    await waitFor(() => expect(mockedGetStructuredTrace).toHaveBeenCalledTimes(2));
    expect(screen.getByText("Inspector: WriterAgent")).toBeInTheDocument();
  });

  it("keeps a selectable fallback root when the structured endpoint fails", async () => {
    mockedGetTaskTrace.mockResolvedValueOnce({
      trace: {
        trace_id: "trace-fallback",
        task_id: "task-1",
        task_name: "Printer reload",
        task_type: "weekly_report",
        status: "completed",
        current_stage: "writer",
        started_at: "2026-08-08T00:00:00Z",
        ended_at: "2026-08-08T00:00:01Z",
        updated_at: "2026-08-08T00:00:01Z",
        summary: null,
        error_message: null,
      },
      logs: [],
      structured_trace_summary: null,
    });
    mockedGetStructuredTrace.mockRejectedValueOnce(new Error("Langfuse unavailable"));

    render(<MemoryRouter initialEntries={["/tasks/task-1/trace"]}><Routes><Route path="/tasks/:taskId/trace" element={<TracePage />} /></Routes></MemoryRouter>);

    expect(await screen.findByText("Inspector: LangGraph Task Execution")).toBeInTheDocument();
    expect(screen.getByText("Local Fallback")).toBeInTheDocument();
  });

  it("shows tool input and output in the inspector", async () => {
    mockedGetStructuredTrace.mockResolvedValueOnce({
      trace_id: "trace-tool",
      task_id: "task-1",
      provider: "langfuse",
      source: "langfuse_live",
      trace_url: null,
      status: "completed",
      synced_at: null,
      cache_artifact_id: null,
      sync_error: null,
      root: {
        ...child,
        observation_id: "root-tool",
        name: "weekly_report.workflow",
        type: "chain",
        children: [{
          ...child,
          observation_id: "tool-1",
          parent_observation_id: "root-tool",
          name: "tool.get_skill_content",
          type: "tool",
          model: null,
          input_tokens: null,
          output_tokens: null,
          total_tokens: null,
          input: { title: "Review final state" },
          output: { status: "ok", result_summary: "policy loaded" },
          children: [],
        }],
      },
      observations: [],
      metrics: { observation_count: 2, error_count: 0, warning_count: 0, duration_ms: 300, input_tokens: null, output_tokens: null, total_tokens: null, total_cost: null },
      fallback_logs: [],
    });

    render(<MemoryRouter initialEntries={["/tasks/task-1/trace"]}><Routes><Route path="/tasks/:taskId/trace" element={<TracePage />} /></Routes></MemoryRouter>);

    expect(await screen.findByText("get_skill_content")).toBeInTheDocument();
    fireEvent.click(screen.getByText("get_skill_content"));
    expect(screen.getByText(/Review final state/)).toBeInTheDocument();
    expect(screen.getByText(/policy loaded/)).toBeInTheDocument();
    expect(screen.getAllByText("TOOL").length).toBeGreaterThanOrEqual(2);
  });
});
