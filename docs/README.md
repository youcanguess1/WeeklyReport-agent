# WeeklyReport Agent 文档

WeeklyReport Agent 是基于 LangGraph 的项目周报工作台，支持文字记录、可选图片材料、自动审核、人工修订、版本管理和 Markdown 导出。

| 文档 | 内容 |
| --- | --- |
| [项目说明与快速开始](../README.md) | 功能概览、安装配置与启动入口 |
| [架构说明](architecture.md) | 组件职责、执行流程、存储、规则和运行边界 |
| [接口说明](api.md) | 任务控制、反馈、补图、确认、版本、规则、文件库和 Trace |
| [配置说明](configuration.md) | 配置合并、密钥优先级、模型、存储、日志和观测 |
| [运行说明](running.md) | 本地启动、工作台操作和开发命令 |
| [周报演示指南](demo-guide.md) | 合成材料、人工修订、版本下载和可选图片演示 |
| [工作流程与契约](weekly_report_workflow.md) | 工作流节点、五段周报与图片材料结构 |
| [周报模板](weekly_report_template.md) | Writer 运行时使用的模板与事实约束 |
| [功能状态](progress.md) | 当前能力与开发待办 |
| [后端说明](../backend/README.md) | FastAPI 开发入口 |
| [前端说明](../frontend/README.md) | 页面路由、开发命令和代理 |
| [可选 Langfuse](../infra/langfuse/README.md) | 本地观测服务部署与 SDK 连接 |

首次使用从根 README 开始；接口模型以运行后端的 `/docs` 和 `/openapi.json` 为准。运行数据、本地配置及本地审计材料不属于公开示例数据。
