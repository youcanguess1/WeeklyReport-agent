# 接口说明

默认后端地址为 `http://127.0.0.1:8000`。启动后可在 `/docs` 查看交互文档，在 `/openapi.json` 查看完整请求与响应模型。以下路径区分 HTTP 方法，花括号表示路径参数。

创建、反馈、补图、规则导入和文件上传使用表单；规则编辑使用 JSON。不要为 `FormData` 手工指定缺少 boundary 的 `Content-Type`。

## 任务与工作台

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/healthz` | 应用健康信息，不验证模型凭据 |
| GET | `/api/tasks` | 分页任务列表；支持 `q`、`status`、`task_type`、`page`、`page_size` |
| GET | `/api/tasks/summary` | 看板统计 |
| POST | `/api/tasks` | 创建任务，返回包含 `task` 的对象；不会自动启动 |
| GET | `/api/tasks/{task_id}` | 任务工作台概要 |
| GET | `/api/tasks/{task_id}/events` | 查询工作台事件和操作状态；可传上次返回的 `cursor` |
| GET | `/api/tasks/{task_id}/weekly-report-versions` | 周报版本、Markdown 和对应图片 |
| GET | `/api/tasks/{task_id}/artifacts/{artifact_id}/content` | 获取属于该任务的图片文件 |
| POST | `/api/tasks/{task_id}/start` | 将已创建的任务排入执行 |
| POST | `/api/tasks/{task_id}/pause` | 请求暂停，在执行边界生效 |
| POST | `/api/tasks/{task_id}/resume` | 恢复手动暂停的任务 |
| POST | `/api/tasks/{task_id}/cancel` | 取消任务 |
| POST | `/api/tasks/{task_id}/feedback` | 提交修改意见，返回 202 和异步操作记录 |
| POST | `/api/tasks/{task_id}/supplements` | 等待材料时补充一张图片，返回 202 和异步操作记录 |
| POST | `/api/tasks/{task_id}/confirm` | 确认最终周报并继续完成流程，返回 202 和任务概要 |

任务状态为 `created`、`queued`、`running`、`waiting`、`completed`、`failed`、`canceled`。`waiting_reason` 进一步说明等待原因；确认、反馈和补图各自有条件，不能对任意 `waiting` 任务执行所有操作。

### 创建任务

| 表单字段 | 要求 |
| --- | --- |
| `task_name` | 必填，1–200 字符，表示项目名称 |
| `task_type` | 必填，固定为 `weekly_report` |
| `notes` | 必填且不能仅为空白，填写本周记录、风险和已有计划 |
| `files` | 可选，可重复该字段上传多张 PNG、JPEG、WebP |
| `template_name` | 可选，最多 120 字符，仅保存元数据，不绑定上传模板 |

以下 PowerShell 示例创建文字任务并启动：

```powershell
$api = "http://127.0.0.1:8000"
$created = Invoke-RestMethod -Method Post -Uri "$api/api/tasks" -Body @{
  task_name = "订单工作台"
  task_type = "weekly_report"
  notes = "本周完成登录接口；报表页面联调中；测试环境阻塞；下周计划完成联调。"
}
$taskId = $created.task.task_id
Invoke-RestMethod -Method Post -Uri "$api/api/tasks/$taskId/start"
Invoke-RestMethod -Uri "$api/api/tasks/$taskId/events"
```

有图片时使用 multipart，例如在已有示例图片路径替换后执行：

```powershell
curl.exe -X POST "$api/api/tasks" -F "task_name=示例项目" -F "task_type=weekly_report" -F "notes=本周完成接口，图片为验收截图" -F "files=@./example.png"
```

### 异步反馈与补图

反馈字段为必填 `feedback_text`、必填 `request_id`，以及可选的一份 `file` 图片。仅在等待人工处理或等待材料时接受反馈。反馈附件随会话传入 Review 使用的 `chat` 模型，该模型需支持图片输入；如需持久化为材料及版本图片引用，应在等待材料时使用补图接口。

```powershell
$feedbackRequestId = [guid]::NewGuid().ToString()
$operation = Invoke-RestMethod -Method Post -Uri "$api/api/tasks/$taskId/feedback" -Body @{
  request_id = $feedbackRequestId
  feedback_text = "请把测试环境不可用写入风险与阻塞，保持其他事实不变。"
}
Invoke-RestMethod -Uri "$api/api/tasks/$taskId/events"
```

收到等待材料的状态后可提交补图：

```powershell
$supplementRequestId = [guid]::NewGuid().ToString()
curl.exe -X POST "$api/api/tasks/$taskId/supplements" -F "request_id=$supplementRequestId" -F "file=@./example.png" -F "section_id=completed_this_week" -F "note=本周验收结果"
```

补图字段为 `request_id`、一份 `file`，以及可选的 `section_id`、`note`。`section_id` 使用[五段正文枚举](weekly_report_workflow.md)；补充材料最多四轮。接口仅接受 `waiting_reason=evidence` 的任务。

`request_id` 长度为 1–128。同一任务复用同一个 ID 会返回已记录的操作，重试同一次提交时保留 ID，新提交需生成新 ID。反馈和补图共用该去重范围。任务有其他活动操作时可能返回 409。

操作响应包含 `operation_id`、`task_id`、`request_id`、`operation_type`、`status`、`stage`、`error_message`、`result`、`events` 及时间字段；状态为 `queued`、`processing`、`completed`、`failed`。通过任务 `/events` 响应中的 `operations` 跟踪结果；`events` 和返回的 `cursor` 用于增量读取。202 只表示已接受处理，不保证模型执行成功。

### 最终确认与版本下载

仅在等待人工确认，且 Review 为 `approved`，或达到审核轮数上限仍为 `revise_report` 时接受确认；其他状态返回 409。确认不需要表单或 JSON 请求体：

```powershell
Invoke-RestMethod -Method Post -Uri "$api/api/tasks/$taskId/confirm"
$versions = @(Invoke-RestMethod -Uri "$api/api/tasks/$taskId/weekly-report-versions")
```

继续读取任务状态直到 `completed`；失败时检查 Trace。版本记录包含 `report_version_id`、`version`、`created_at`、`change_note`、`markdown`、`evidence_images`，图片项包含 `material_id` 和 `url`。

前端下载所选版本的 `markdown`；后端没有独立的 Markdown 下载路由。也可按版本标识保存文本：

```powershell
$selected = $versions | Where-Object { $_.version -eq "V0" } | Select-Object -First 1
if ($null -ne $selected) {
  [System.IO.File]::WriteAllText((Join-Path $PWD "weekly-report.md"), $selected.markdown, [System.Text.UTF8Encoding]::new($false))
}
```

## 文件库

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/artifacts` | 列出文件，支持 `library`、`kind`、`task_id`、`page`、`page_size` |
| POST | `/api/artifacts` | multipart 上传项目资料：`library`、`file`，可选 `display_name`、`description`、`tags` |

文件库包括 `rules`、`templates` 和任务产物视图 `tasks`。上传仅接受前两类；任务产物由工作流生成。将文件放入模板库不会自动替换 Writer 模板，放入规则文件库也不等同于创建可选用的活跃规则；规则管理使用下述接口。

## 规则中心

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/skills` | 列出规则，包含候选和禁用记录 |
| POST | `/api/skills` | multipart 导入规则文件，初始状态为 `candidate` |
| PUT | `/api/skills/{skill_id}` | JSON 编辑规则正文和触发条件 |
| DELETE | `/api/skills/{skill_id}` | 删除规则，成功返回 204 |

导入必填 `file`、`task_type`、`agent_stage`；可选 `title`、`description`、`kind`、`content`。文本文件按 UTF-8 解码，`content` 提供时覆盖文件正文。编辑请求必填 `title`、`content`，可带 `description`、`task_type`、`agent_stage`、`report_section`、`issue_type`。完整枚举和模型以 `/docs` 为准。

## Trace

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/traces/tasks/{task_id}` | 任务 Trace 时间线、日志及可选结构化摘要 |
| GET | `/api/traces/{trace_id}/logs` | 本地 Trace 日志 |
| GET | `/api/traces/{trace_id}/structured` | 结构化追踪详情 |
| POST | `/api/traces/{trace_id}/sync` | 从配置的观测后端同步结构化追踪 |

本地 Trace 独立于 Langfuse；远程结构化追踪内容取决于是否启用观测、凭据和服务连通性。`trace_id` 从任务 Trace 响应获取，与 `task_id` 分开使用。

## 常见响应

创建和查询通常返回 200；反馈、补图、确认返回 202；规则删除返回 204。参数或内容校验失败通常为 400/422，资源不存在为 404，任务状态或活动操作冲突为 409；模型和后台处理失败还需查看操作记录及 Trace。错误响应通常包含 `detail`。
