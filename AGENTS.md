# WeeklyReport Agent Rules

## Read Order

Start with `docs/README.md`.

Read `docs/architecture.md` for component responsibilities and workflow boundaries.

Read `docs/api.md`, `docs/configuration.md`, and `docs/running.md` for interface, setup, and validation details.

Use `docs/progress.md` to track task status.

## Project Defaults

- Project name: WeeklyReport Agent.
- Project positioning: 基于 LangGraph 的项目周报 Agent 工作台。
- Weekly report template source: `docs/weekly_report_template.md`.
- Main workflow: LangGraph.
- Backend API: FastAPI.
- Frontend: Dashboard Workbench with React + esbuild + TypeScript + Ant Design.
- Model access: OpenAI-compatible `ModelClient`.
- New report flow: project name and notes are required; image uploads are optional. Text-only tasks skip visual understanding, while image tasks use material understanding. The five-section report supports Review, human revision, version history, final confirmation, and Markdown download.
- Storage: SQLite + local filesystem, with reusable rules and agent memory stored locally.

## Development Rules

- If the boundaries and requirements of certain needs aren't clear, don't just go ahead or ignore them—ask questions first
- Preserve existing API, database, and Artifact compatibility unless a task explicitly requires changing those contracts.
- Choose the simplest implementation that fully meets the current requirements.
- prefer established, well-maintained libraries over custom implementations.
- Run this repo with `.\.venv\Scripts\python.exe`; do not use Conda `python` or `pip`.
- Install packages with `.\.venv\Scripts\python.exe -m pip ...`.
- Use `apply_patch` for manual edits.
- Do not delete or overwrite original artifacts unless the user asks.
- Update `docs/progress.md` when completing planned tasks.
