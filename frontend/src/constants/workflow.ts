import type { TaskType } from "../types/api";

export const taskTypeOptions: Array<{ value: TaskType; label: string }> = [
  { value: "weekly_report", label: "项目周报" },
];

export const weeklyReportTemplateOptions = [
  {
    value: "docs/weekly_report_template.md",
    label: "基础周报模板",
    description: "项目默认周报结构模板",
  },
] as const;

export const defaultWeeklyReportTemplateName = weeklyReportTemplateOptions[0].value;

export const agentStageOptions = [
  { value: "material_understanding", label: "图片分析 / 材料理解" },
  { value: "writer", label: "周报撰写 Agent" },
  { value: "review", label: "周报审核 Agent" },
] as const;

export const taskTypeLabels: Record<string, string> = Object.fromEntries(
  taskTypeOptions.map((item) => [item.value, item.label]),
);

export const agentStageLabels: Record<string, string> = Object.fromEntries(
  agentStageOptions.map((item) => [item.value, item.label]),
);

export const skillKindLabels: Record<string, string> = {
  writer_skill: "撰写规则",
  review_rule: "审核规则",
  failure_pattern: "失败模式",
};

export const skillStatusLabels: Record<string, string> = {
  candidate: "候选",
  active: "激活",
  disabled: "停用",
};
