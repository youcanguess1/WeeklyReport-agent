# 周报工作流程

~~~mermaid
flowchart TD
    A[项目名称 + 碎片化工作记录] --> B{是否上传图片?}
    B -->|否| D[周报 Writer]
    B -->|可选图片| C[材料理解]
    C --> D
    D --> E[周报 Review]
    E --> F[人工反馈与修订]
    F --> D
    E --> G[确认最终周报]
    G --> H[保存 Markdown Artifact]
    H --> I[浏览器下载]
~~~

用户文字是主要事实来源。仅在上传图片时执行材料理解；周报在最终确认后输出 Markdown。

创建任务和补充材料接受 PNG、JPEG、WebP 图片。图片预处理直接校验并读取原图。视觉 Agent 对每张新图片分别生成摘要，补充材料保留已有摘要；纯文字任务跳过此节点。附图时需要配置支持图片输入的视觉端点和模型，端点不可用时任务会记录执行错误。


## 周报与材料契约

任务类型为 `weekly_report`。文字任务跳过 `material_understanding`，随后执行 `write_report` 和 `review_report`；审核通过后等待用户明确确认，反馈可触发修订或补充图片，确认后保存最终 Markdown。

`WeeklyReportDocument` 使用 `project_name`，正文由以下五个列表组成；Review 问题和补充材料的可选 `section_id` 使用相同枚举值：

| 字段 / section_id | 周报段落 |
| --- | --- |
| `completed_this_week` | 本周完成 |
| `in_progress` | 进行中 |
| `risks_and_blockers` | 风险与阻塞 |
| `next_week_plan` | 下周计划 |
| `pending_confirmations` | 待确认事项 |

`MaterialPack` 包含 `materials` 和 `understandings`。每个 `ImageMaterial` 通过 `material_id`、`source_artifact_id` 和 `image_path` 引用原始图片，可附带 `note`；`MaterialUnderstanding` 通过同一个 `material_id` 记录 `content_summary`。材料编号和摘要编号各自唯一，摘要必须对应已有材料。合并补图时保留旧摘要，并同步重映射发生冲突的编号。

版本查询接口为 `GET /api/tasks/{task_id}/weekly-report-versions`，返回 `report_version_id`、版本信息、Markdown 和 `evidence_images`。每个版本保存自己的 `material_refs`，预览仅展示该版本保存时的图片；后续补图不会出现在历史版本中。图片响应包含 `material_id` 和任务归属校验后的内容 URL，Markdown 保持五段正文。

规则触发条件使用 `task_type`、`agent_stage`、`report_section` 和 `issue_type`。记忆演进比较五段正文内容，并结合按段落关联的材料、补充说明与会话记录。

配套说明：[架构](architecture.md)、[接口](api.md)、[配置](configuration.md)、[运行与验证](running.md)。
