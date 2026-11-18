# WeeklyReport Agent 前端

React、esbuild、TypeScript 和 Ant Design 构建的项目周报工作台。支持任务看板、创建任务、人工反馈、版本预览、图片展示、Markdown 下载、规则管理、文件库和 Trace。

## 开发命令

从仓库根目录执行，环境与后端配置见[快速开始](../README.md)：

```powershell
pnpm --dir .\frontend install --frozen-lockfile
pnpm --dir .\frontend dev
pnpm --dir .\frontend test
pnpm --dir .\frontend build
```

开发服务默认使用 `127.0.0.1:5174`，端口占用时退出。`/api` 请求由 Node HTTP 服务代理至 `http://127.0.0.1:8000`；可通过启动终端的 `WEEKLYREPORT_BACKEND_URL` 修改目标。[配置说明](../docs/configuration.md)包含示例。

## 页面路由

| 路径 | 页面 |
| --- | --- |
| `/tasks` | 任务看板 |
| `/tasks/create` | 创建周报任务 |
| `/tasks/:taskId` | 任务工作台与版本预览 |
| `/tasks/:taskId/trace` | 任务追踪 |
| `/artifacts` | 文件库 |
| `/skills` | 规则中心 |

其他路径跳转到 `/tasks`。工作台下载将所选版本的 Markdown 转成 UTF-8 文件；版本图片通过任务所属的图片内容接口加载。

`build` 包含 TypeScript 检查与 esbuild/Tailwind 生产构建，输出到 `frontend/dist/`。本地 `preview` 同样支持 `/api` 代理和页面路由回退；正式静态部署需单独配置这些规则。完整操作步骤见[运行说明](../docs/running.md)。

Tailwind 样式通过 PostCSS 在本地构建，图标使用已安装的 Ant Design 图标；依赖版本由 `pnpm-lock.yaml` 锁定，页面不再加载 Tailwind CDN 或 unpkg 脚本。首次运行不会自动创建示例任务，侧栏仅显示后端确认存在的任务；已删除任务的旧浏览器缓存会自动清理。生产构建仍需要配套后端服务。

开发与本地预览使用 Node.js 24 LTS。源码变化后自动重建，浏览器手动刷新；正式样式要求 Chrome 111+、Safari 16.4+ 或 Firefox 128+。
