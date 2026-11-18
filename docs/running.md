# 运行与验证

## 本地环境

前端使用 esbuild、Tailwind PostCSS 与 Node 原生测试运行器，不需要安装 Vite 或 Vitest 运行器。依赖由锁文件固定；`pnpm audit` 应包含开发依赖，接口限流或连接失败不能视为审计通过。

使用 Python 3.11+、Node.js 24 LTS、pnpm。以下命令均从仓库根目录使用 PowerShell 执行，不需要激活虚拟环境。

首次安装：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade "pip>=26.2"
.\.venv\Scripts\python.exe -m pip install -e ".\backend[dev]"
pnpm --dir .\frontend install --frozen-lockfile
```

确认 `py -3` 对应 Python 3.11+；如果启动器提示没有已安装的 Python，可用已安装 Python 的 `python.exe` 完整路径执行 `-m venv .venv`。2026-11-18 的独立安装使用 Python 3.12.8、Node.js 24.20.0、pnpm 11.25.0，未复用原项目的依赖目录。

首次复制配置时执行，已有本地文件时直接编辑：

```powershell
Copy-Item .\backend\config\settings.example.yaml .\backend\config\settings.local.yaml
Copy-Item .\.env.example .\.env
```

按[配置说明](configuration.md)填写真实端点、模型名和密钥；默认示例不可直接请求模型。纯文字任务需要文本模型，图片任务额外需要视觉模型。

后端和前端在两个终端分别启动：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --app-dir .\backend --host 127.0.0.1 --port 8000
```

```powershell
pnpm --dir .\frontend dev
```

后端健康检查在 `http://127.0.0.1:8000/healthz`，API 文档在 `http://127.0.0.1:8000/docs`，前端在 `http://127.0.0.1:5174`。这些是开发运行方式；修改代码导致自动重载会中断正在执行的任务，验收长任务时可去掉 `--reload`。

## 工作台操作

可直接输入的合成工作记录、逐步演示和结果检查见[周报演示指南](demo-guide.md)。

1. 打开 `/tasks/create`，输入项目名与工作记录。例如：“本周完成登录接口；报表联调进行中；测试环境不可用；下周计划完成联调”。
2. 文字流程可不上传图片；图片流程可附上 PNG、JPEG、WebP 截图，并在备注中说明截图与工作的关系。
3. 创建后返回任务看板，点击任务进入 `/tasks/{taskId}`，启动后观察 Writer、Review 和任务事件。
4. 等待人工处理时提交明确反馈，例如要求补充某条已有事实或修正段落归属；等待材料时可使用补图入口。
5. 在版本区域检查新旧 Markdown 和图片，确认事实准确后点击最终确认，等待任务完成。
6. 下载当前选中的版本，检查文件是对应版本的五段式正文。切换版本后再下载可获得该版本内容。
7. 在 `/tasks/{taskId}/trace` 检查阶段与日志；`/skills` 管理规则，`/artifacts` 查看文件库。

创建、启动、反馈、补图和确认是不同请求；API 示例与状态限制见[接口说明](api.md)。任务列表在 `/tasks`，根路径会跳转至该页面。文件库上传模板不会改变当前 Writer 的运行模板。

## 回归与构建

```powershell
.\.venv\Scripts\python.exe -m pytest backend/app/tests
pnpm --dir .\frontend test
pnpm --dir .\frontend build
```

`build` 先运行 `tsc --noEmit`，再运行 esbuild 与 Tailwind PostCSS 构建。`test` 使用 Node 原生测试运行器、jsdom、Testing Library 和独立断言/间谍库；页面测试在 HTTP 请求边界提供合成响应。测试主要使用替身和隔离存储，不等同于真实模型验收。已有验证范围及待办见[功能状态](progress.md)。

开发预览生产构建可执行：

```powershell
pnpm --dir .\frontend preview
```

开发及预览服务默认在 `127.0.0.1:5174` 提供页面，均支持 `/api` 代理和页面路由回退；开发模式在源码变更后自动重建，浏览器需手动刷新。可通过 `pnpm --dir .\frontend dev --port 5175` 指定端口。此命令供本地预览，不能替代完整部署。正式提供 `frontend/dist/` 时，需让 `/api` 转发到后端，并为 `/tasks/...` 等浏览器路由配置回退到 `index.html`；后端默认不提供静态页面。

## 真实模型验收清单

2026-11-18 已在独立安装环境使用真实文本与视觉模型完成下列接口验收；模型验收与自动化回归分别记录，不能互相替代。

| 项目 | 结果与范围 |
| --- | --- |
| 文字任务 | 创建、启动、Writer、Review、人工反馈产生新版本，最终 `completed`；五段正文完整 |
| 图片任务 | 从演示看板识别仅存在于图片的 18 项测试通过事实，图片预览接口返回原始图片字节 |
| 补充材料 | 真实 Review 请求补图；新图更新为 24 项并生成新版本，旧版本正文与图片引用保持一致 |
| 最终确认 | 文字和图片任务均已完成；版本接口返回对应五段 Markdown |
| 下载一致性 | 前端测试验证下载 Blob；另用本机 Chrome 的独立无头上下文加载生产构建，实际点击下载 V1、切换 V0 后下载，落盘中文文件名和 UTF-8 全文逐字一致，无页面 JavaScript 错误 |
| 异常处理 | 不可用模型端点下健康检查仍正常，任务失败并记录 `node_failed`；本地 Trace 返回错误记录 |
| 前端联通 | 首页及开发代理的任务、版本接口均返回成功；不代表浏览器交互验收 |

浏览器下载验收使用隔离的合成 API 响应，已完成实际点击及落盘；该结果不代表浏览器端真实模型全流程验收。用户已确认前端检查完成。可选 Docker/Langfuse 服务未启动验收，本地 Trace 已验证。

SDK 更新后已重新通过真实文字生成、人工新增事实修订、旧版本保持不变、最终确认，以及图片独有数字 24 的识别和 Writer/Review；本次没有重跑真实补图，补图表项记录的是更新前验收。完整结果与外部模型曾返回截断 JSON 的失败记录见[功能状态](progress.md)。

## 常见问题

| 现象 | 检查方式 |
| --- | --- |
| 健康检查正常但任务失败 | 检查模型 URL、模型名、密钥及服务商对结构化响应/工具调用的支持；健康检查不会请求模型 |
| 文字正常，图片失败 | 检查视觉端点与模型的图片输入支持，确认独立密钥或共享密钥回退符合预期 |
| 前端 `/api` 请求失败 | 确认后端在 8000 端口运行；自定义端口时设置启动前端的 `WEEKLYREPORT_BACKEND_URL` |
| 5174 端口被占用 | Node 服务监听失败时退出；释放端口后重启，或显式指定其他端口 |
| 反馈、补图或确认返回 409 | 检查 `status`、`waiting_reason` 和活动操作；补图仅在等待材料时可用 |
| 修改 `.env` 后仍使用旧值 | 重启后端，并检查进程环境或 `backend/.env` 是否覆盖根目录 `.env` |
| 暂停或取消后仍有网络活动 | 已开始的模型请求可能仍在结束；控制状态在执行边界检查 |
| 重启后作业未继续 | 线程池作业不持久化，检查点不负责自动重新入队；不要用重启作为恢复作业的操作 |
| 构建提示分包超过 500 kB | 已有验证中出现该体积提示，构建仍通过；当前未进行分包优化 |

应用数据默认写入 `data/`。如需备份，先停止运行中的任务与服务，再一起保存数据库、检查点和文件目录。当前没有一键清空数据或服务化部署流程。

## 可选观测

本地 Trace 随后端可用，无需 Docker。需要 Langfuse 时按[本地 Langfuse 指南](../infra/langfuse/README.md)启动服务、安装可选依赖并配置 SDK 密钥。
