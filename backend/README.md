# WeeklyReport Agent 后端

基于 FastAPI 和 LangGraph，实现项目周报的生成、审核、人工修订、版本管理与 Markdown 产物存储。任务需要项目名与工作记录，图片材料可选；周报包含本周完成、进行中、风险与阻塞、下周计划、待确认事项。

## 开发入口

从仓库根目录按[快速开始](../README.md)创建 `.venv` 并安装 `backend[dev]`，配置模型后执行：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --app-dir .\backend --host 127.0.0.1 --port 8000
```

健康检查为 `/healthz`，交互接口文档为 `/docs`，OpenAPI 为 `/openapi.json`。健康检查不会调用模型。

## 代码与文档

- `app/main.py`：应用组装、服务与生命周期。
- `app/api/`：任务、文件库、规则和 Trace 路由。
- `app/workflows/`、`app/agents/`、`app/tools/`：编排、Agent 与工具。
- `app/services/`、`app/memory/`：执行、配置、存储、查询及规则记忆。
- `app/schemas/`：数据契约；`app/tests/`：后端回归。

参阅[架构](../docs/architecture.md)、[接口](../docs/api.md)、[配置](../docs/configuration.md)与[运行说明](../docs/running.md)。图片预处理由 Pillow 读取原图；视觉理解需要支持图片输入的模型端点。

## 测试与观测

```powershell
.\.venv\Scripts\python.exe -m pytest backend/app/tests
```

默认运行本地 Trace。Langfuse 可选依赖与服务配置见[观测指南](../infra/langfuse/README.md)。数据写入配置指定的 SQLite、Artifact 和日志目录；本地线程池不支持进程重启后自动恢复作业。
