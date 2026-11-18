# 配置说明

## 配置文件与优先级

后端默认读取 `backend/config/settings.example.yaml`，再递归合并本机的 `backend/config/settings.local.yaml`。本地文件只需写要覆盖的字段；未覆盖的字段沿用示例。不要将真实密钥写入 YAML。

公开文件保留 `.env.example` 和 `settings.example.yaml`；真实 `.env`、本地配置、私钥和证书文件由 `.gitignore` 排除。运行数据库、日志与 Artifact 也不属于公开示例数据。提交前仍需核对文件清单，不能仅依赖忽略规则。

密钥按 YAML 指定的环境变量名读取，非空值的优先顺序为：

1. 启动后端的进程环境变量。
2. `backend/.env`。
3. 仓库根目录 `.env`。

这套环境变量机制用于 `api_key_env`、`public_key_env`、`secret_key_env` 等明确指定的密钥字段；设置任意环境变量不会自动覆盖同名 YAML 配置。相对存储和日志路径以仓库根目录解析。配置会缓存，修改配置或 `.env` 后重启后端。

## 模型配置

在 `settings.local.yaml` 配置文本端点；以下 URL 和模型名均需替换为服务商提供的实际值：

```yaml
model:
  provider: openai-compatible
  base_url: https://api.example.com/v1
  api_key_env: OPENAI_SHARED_API_KEY
  chat:
    model: your-text-model-name
  vision:
    model: your-vision-model-name
    base_url: https://vision.example.com/v1
    api_key_env: OPENAI_VISION_API_KEY
```

根目录 `.env` 示例：

```dotenv
OPENAI_SHARED_API_KEY=replace-with-your-text-model-key
OPENAI_VISION_API_KEY=replace-with-your-vision-model-key
```

文本模型通过 `langchain_openai.ChatOpenAI` 调用 OpenAI 兼容服务，并承担 Writer、Review、反馈处理与记忆演进。模型需兼容这些 Agent 使用的结构化响应和工具调用。图片模型需要支持图片输入。反馈接口附带的图片直接交给 Review 使用的 `chat` 模型，因此使用反馈图片时，`chat` 模型也需要支持图片输入；创建任务和补图的材料理解使用 `vision` 模型。

`chat` 和 `vision` 的端点地址、密钥变量名在为空时继承共享设置；若独立密钥变量没有读到值，实际密钥仍会回退到共享密钥。不要依赖“未填写视觉密钥”来禁止图片调用。仅使用文字时可保留示例视觉配置，文字任务不会执行视觉理解；图片任务前必须核对视觉端点可用。

若文本和图片使用同一服务商、同一密钥，可明确设置视觉端点继承共享配置：

```yaml
model:
  vision:
    model: your-multimodal-model-name
    base_url: null
    api_key_env: OPENAI_SHARED_API_KEY
```

此片段是对前述配置的增量覆盖，文本端点仍需填写。删除本地 `vision` 字段不会禁用它，合并后仍保留示例中的视觉设置。

后端允许在没有有效密钥时完成应用构造和健康检查，首次模型调用时才会暴露鉴权或连接错误。`/healthz` 不是模型连通性检测。

## 存储、调度和日志

```yaml
storage:
  artifact_root: data/artifacts
  sqlite_path: data/sqlite/weekly_report.db
task_runner:
  backend: local
  max_workers: 2
logging:
  enabled: true
  path: data/logs/weekly_report.log.jsonl
  level: INFO
  max_bytes: 10485760
  backup_count: 5
```

| 字段 | 含义与范围 |
| --- | --- |
| `storage.artifact_root` | 上传材料及生成文件的存储根目录 |
| `storage.sqlite_path` | 业务数据库位置；检查点文件 `langgraph_checkpoints.db` 放在同一目录 |
| `task_runner.backend` | 使用 `local`；`redis` 尚未实现 |
| `task_runner.max_workers` | 本地线程池大小，1–32，默认 2 |
| `task_runner.queue_name` / `redis_url` | 配置预留字段，当前没有可用的 Redis 作业队列 |
| `logging.level` | 应用日志等级，示例为 `INFO` |
| `logging.max_bytes` / `backup_count` | 单日志文件大小阈值与轮转备份数量 |

`app.name` 和 `app.environment` 用于应用标识与健康检查；修改它们不会改变监听地址。监听地址和端口通过 Uvicorn 命令指定。

## 前端代理

开发服务默认监听 `127.0.0.1:5174`，`/api` 转发到 `http://127.0.0.1:8000`。如需修改后端地址，在启动前端的终端设置：

```powershell
$env:WEEKLYREPORT_BACKEND_URL = "http://127.0.0.1:8001"
pnpm --dir .\frontend dev
```

`WEEKLYREPORT_BACKEND_URL` 由 Node HTTP 服务从进程环境读取，不进入浏览器构建。开发与本地生产预览均支持该变量，前端使用相对 `/api` 地址。正式部署静态目录时，需要由部署服务器配置 API 代理和页面路由回退。

## 可选 Langfuse

默认 `observability.provider: none`，本地 Trace 不依赖 Langfuse。启用前安装 `backend[observability]`，准备 Langfuse 服务及项目密钥，再配置：

```yaml
observability:
  provider: langfuse
  host: http://localhost:3000
  public_key_env: LANGFUSE_LOCAL_PUBLIC_KEY
  secret_key_env: LANGFUSE_LOCAL_SECRET_KEY
  project_name: weekly-report-local
  capture_input: false
  capture_output: false
```

只有 provider 为 `langfuse` 且公钥、私钥均可读时才启用。`capture_input`、`capture_output` 控制观测内容采集，默认关闭。部署步骤和本地启动凭据见 [Langfuse 指南](../infra/langfuse/README.md)。该目录的 `.env` 供 Docker Compose 使用，后端不会自动读取它；SDK 密钥需放在后端可读取的环境中。

`.env`、`settings.local.yaml`、数据库、日志及 Artifact 已由 `.gitignore` 排除。本地配置、密钥和任务材料不应加入公开仓库。
