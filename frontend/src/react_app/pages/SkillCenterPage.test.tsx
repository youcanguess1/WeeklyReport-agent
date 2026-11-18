import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, it } from "node:test";
import { expect, apiMock, spies } from "../../test/helpers";

import { createSkill, deleteSkill, listSkills, updateSkill } from "../../api/skills";
import { SkillCenterPage } from "./SkillCenterPage";


const mockedListSkills = apiMock("listSkills");
const mockedCreateSkill = apiMock("createSkill");
const mockedUpdateSkill = apiMock("updateSkill");
const mockedDeleteSkill = apiMock("deleteSkill");

const skill = {
  skill_id: "skill_1",
  title: "Review rule",
  description: "Check output",
  kind: "review_rule" as const,
  status: "candidate" as const,
  content: "# Review",
  task_type: "weekly_report",
  agent_stage: "review",
  report_section: null,
  issue_type: null,
  confidence: 0.5,
  hit_count: 2,
  disabled: false,
  created_at: "2026-08-08T00:00:00+00:00",
  updated_at: "2026-08-08T00:00:00+00:00",
};

const activeWriterSkill = {
  ...skill,
  skill_id: "skill_2",
  title: "Writer rule",
  kind: "writer_skill" as const,
  status: "active" as const,
  task_type: "weekly_report",
  agent_stage: "writer",
};

describe("SkillCenterPage", () => {
  beforeEach(() => {
    mockedListSkills.mockResolvedValue([skill, activeWriterSkill]);
    mockedCreateSkill.mockResolvedValue(skill);
    mockedUpdateSkill.mockResolvedValue({ ...skill, title: "Updated rule" });
    mockedDeleteSkill.mockResolvedValue(undefined);
    spies.spyOn(window, "confirm").mockReturnValue(true);
  });

  it("searches, edits, and deletes a loaded skill", async () => {
    render(<SkillCenterPage />);
    expect(await screen.findByText("Review rule")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "编辑 Review rule" }));
    fireEvent.change(screen.getByDisplayValue("Review rule"), { target: { value: "Updated rule" } });
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
    await waitFor(() => expect(mockedUpdateSkill).toHaveBeenCalledWith("skill_1", expect.objectContaining({ title: "Updated rule" })));
    fireEvent.click(screen.getByRole("button", { name: "删除 Review rule" }));
    await waitFor(() => expect(mockedDeleteSkill).toHaveBeenCalledWith("skill_1"));
  });

  it("uses derived tags with AND filtering and saves edited Markdown content", async () => {
    const { container } = render(<SkillCenterPage />);
    expect(await screen.findByText("Review rule")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "项目周报" }));
    fireEvent.click(screen.getByRole("button", { name: "周报审核 Agent" }));
    expect(screen.getByText("Review rule")).toBeInTheDocument();
    expect(screen.queryByText("Writer rule")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "全部" }));
    fireEvent.click(screen.getByRole("button", { name: /导入规则/ }));
    const file = new File(["# Original"], "skill.md", { type: "text/markdown" });
    fireEvent.change(container.querySelector('input[type="file"]') as HTMLInputElement, { target: { files: [file] } });
    const editor = await screen.findByPlaceholderText("# 在此编写规则的 Markdown 条款与 Prompt 说明...");
    await waitFor(() => expect(editor).toHaveValue("# Original"));
    fireEvent.change(editor, { target: { value: "# Edited\n\nUse the updated rule." } });
    fireEvent.click(screen.getByRole("button", { name: "仅预览" }));
    expect(screen.getByText("Edited")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认导入" }));

    await waitFor(() => expect(mockedCreateSkill).toHaveBeenCalledTimes(1));
    const submitted = mockedCreateSkill.mock.calls[0][0];
    expect(submitted.get("content")).toBe("# Edited\n\nUse the updated rule.");
    expect(submitted.get("task_type")).toBe("weekly_report");
    expect(submitted.get("agent_stage")).toBe("writer");
  });
});
