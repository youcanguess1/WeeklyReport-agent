# 本地 Langfuse 观测服务

此目录提供可选的 Langfuse Docker Compose 服务。WeeklyReport 的本地 Trace 不依赖它；需要模型调用的远程追踪与结构化视图时再启用。

## 准备与启动

需要已运行的 Docker Desktop，以及可用的本机端口 `3000`、`3030`、`9090`、`9091`、`8123`、`9000`、`6379`、`5432`。以下命令从仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".\backend[observability]"
Copy-Item .\infra\langfuse\.env.example .\infra\langfuse\.env
```

已有 `.env` 时直接编辑，不要覆盖已有设置。示例文件包含服务间连接设置、本地初始化项目和用户；启动前替换 `change-me-*` 凭据、全零 `ENCRYPTION_KEY` 及固定的示例项目公钥和私钥，同步修改相关连接串或引用值。新的加密密钥可使用 `openssl rand -hex 32` 生成。示例值仅用于说明配置结构，不能用作真实凭据。Compose 的宿主机端口均绑定 `127.0.0.1`。

示例固定 Langfuse Web / Worker 镜像版本为 `3.224.1`。这是仓库配置的版本，不代表已验证为最新版本。本轮文档整理未拉取镜像或运行服务。

```powershell
docker compose --env-file .\infra\langfuse\.env -f .\infra\langfuse\docker-compose.yml up -d
docker compose --env-file .\infra\langfuse\.env -f .\infra\langfuse\docker-compose.yml ps
```

打开 `http://localhost:3000`，使用本机配置中 `LANGFUSE_INIT_USER_EMAIL` 和 `LANGFUSE_INIT_USER_PASSWORD` 对应的账号。已有服务数据时，初始化变量不保证重新覆盖已有账号或项目。

## 连接后端

在仓库根目录 `.env` 或 `backend/.env` 填入该项目的 SDK 公钥和私钥；需对应本地初始化配置中的 `LANGFUSE_INIT_PROJECT_PUBLIC_KEY`、`LANGFUSE_INIT_PROJECT_SECRET_KEY`，或在控制台创建的项目密钥。

```dotenv
LANGFUSE_LOCAL_PUBLIC_KEY=replace-with-project-public-key
LANGFUSE_LOCAL_SECRET_KEY=replace-with-project-secret-key
```

在 `backend/config/settings.local.yaml` 添加：

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

重启后端，再检查 `/healthz` 的 `observability_provider`、`observability_enabled` 和查询服务连通信息。启用状态不等同于端到端模型调用已通过；实际任务执行后还需检查追踪内容。

`infra/langfuse/.env` 只供 Compose 使用，后端不会自动读取它。输入输出采集默认关闭；各设置与密钥优先级见[配置说明](../../docs/configuration.md)。

## 查看与停止

```powershell
docker compose --env-file .\infra\langfuse\.env -f .\infra\langfuse\docker-compose.yml logs -f langfuse-web
docker compose --env-file .\infra\langfuse\.env -f .\infra\langfuse\docker-compose.yml down
```

`down` 停止服务并保留卷数据。需要备份时先停止相关写入并保存服务数据；本指南不包含删除数据的操作。
