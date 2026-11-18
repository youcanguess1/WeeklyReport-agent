import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, it } from "node:test";
import { expect, apiMock, spies } from "../../test/helpers";

import { listArtifacts, uploadProjectArtifact } from "../../api/artifacts";
import { listTasks } from "../../api/tasks";
import { ArtifactCenterPage } from "./ArtifactCenterPage";


const mockedListArtifacts = apiMock("listArtifacts");
const mockedUpload = apiMock("uploadProjectArtifact");
const mockedListTasks = apiMock("listTasks");

const artifact = {
  artifact_id: "artifact_1",
  task_id: null,
  library: "rules",
  filename: "policy.md",
  kind: "markdown",
  description: "Internal policy",
  size_bytes: 12,
  created_at: "2026-08-08T00:00:00+00:00",
  media_type: "text/markdown",
};

const taskArtifact = {
  ...artifact,
  artifact_id: "artifact_task",
  task_id: "task_001",
  filename: "input.png",
  kind: "image_material",
  media_type: "image/png",
};

const weeklyReportArtifact = {
  ...artifact,
  artifact_id: "artifact_report",
  task_id: "task_001",
  library: "tasks",
  filename: "final_report_v1.md",
  kind: "weekly_report_markdown",
};

describe("ArtifactCenterPage", () => {
  beforeEach(() => {
    mockedListArtifacts.mockResolvedValue({ items: [artifact, taskArtifact, weeklyReportArtifact], total: 3, page: 1, page_size: 200 });
    mockedListTasks.mockResolvedValue({
      items: [{
        task_id: "task_001",
        task_name: "Printer reload",
        task_type: "weekly_report",
        description: "",
        status: "created",
        current_node: null,
        waiting_reason: null,
        current_report_version: null,
        artifact_count: 1,
        created_at: "2026-08-08T00:00:00+00:00",
        updated_at: "2026-08-08T00:00:00+00:00",
      }],
      total: 1,
      page: 1,
      page_size: 100,
    });
    mockedUpload.mockResolvedValue({ artifact });
  });

  it("loads directories and uploads a project artifact", async () => {
    const { container } = render(<ArtifactCenterPage />);
    expect(await screen.findByText("policy.md")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /上传文件/ }));
    expect(screen.getByRole("option", { name: "周报规则库" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "周报模板库" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /任务/ })).not.toBeInTheDocument();
    fireEvent.change(container.querySelector('input[type="file"]') as HTMLInputElement, {
      target: { files: [new File(["# policy"], "policy.md", { type: "text/markdown" })] },
    });
    fireEvent.click(screen.getByRole("button", { name: /^写作规则$/ }));
    fireEvent.click(screen.getByRole("button", { name: "确认上传" }));
    await waitFor(() => expect(mockedUpload).toHaveBeenCalled());
    const submitted = mockedUpload.mock.calls[0][0];
    expect(submitted).toBeInstanceOf(FormData);
    expect(submitted.get("library")).toBe("rules");
    expect(submitted.get("file")).toBeInstanceOf(File);
    expect(submitted.get("tags")).toBe("写作规则");
  });

  it("shows task subdirectories and keeps task artifacts read-only", async () => {
    render(<ArtifactCenterPage />);
    expect(await screen.findByRole("button", { name: /Printer reload/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Printer reload/ }));
    expect(screen.getByText("input.png")).toBeInTheDocument();
    expect(screen.getByText("final_weekly_report_v1.md")).toBeInTheDocument();
    expect(screen.queryByText("final_report_v1.md")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /上传文件/ })).not.toBeInTheDocument();
  });
});
