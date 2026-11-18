from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import create_app
from app.observability import ObservabilityProvider
from app.schemas.task import TaskType
from app.schemas.trace import TraceLogLevel
from app.services.artifact_service import ArtifactService
from app.services.config_service import ConfigService, ObservabilitySettings
from app.services.trace_service import TraceService


class _FakeTraceClient:
    def __init__(self, trace_obj: object | Exception) -> None:
        self._trace_obj = trace_obj

    def get(self, trace_id: str) -> object:
        del trace_id
        if isinstance(self._trace_obj, Exception):
            raise self._trace_obj
        return self._trace_obj


class _FakeObservationsClient:
    def __init__(self, response_obj: object | None = None) -> None:
        self._response_obj = response_obj or SimpleNamespace(data=[])

    def get_many(self, *, trace_id: str) -> object:
        del trace_id
        return self._response_obj


class _FakeQueryClient:
    def __init__(self, trace_obj: object | Exception, observation_response: object | None = None) -> None:
        self.trace = _FakeTraceClient(trace_obj)
        self.observations = _FakeObservationsClient(observation_response)


class _FakeObservabilityProvider(ObservabilityProvider):
    provider_name = "langfuse"
    enabled = True

    def __init__(self, query_client: object | None) -> None:
        super().__init__(ObservabilitySettings(provider="langfuse"))
        self.query_client = query_client

    def observe(
        self,
        *,
        name: str | None = None,
        as_type: str = "span",
        capture_input: bool | None = None,
        capture_output: bool | None = None,
    ):
        del name, as_type, capture_input, capture_output

        def decorator(func):
            return func

        return decorator

    def get_query_client(self):
        return self.query_client

    def get_trace_url(self, trace_id: str | None = None) -> str | None:
        if trace_id is None:
            return None
        return f"http://localhost:3000/project/demo/traces/{trace_id}"


def _live_trace(trace_id: str) -> object:
    return SimpleNamespace(
        id=trace_id,
        timestamp="2026-07-28T09:00:00+00:00",
        latency=200.0,
        total_cost=0.33,
        metadata={"task_id": "task-demo"},
        observations=[
            SimpleNamespace(
                id="obs-root",
                trace_id=trace_id,
                type="CHAIN",
                name="weekly_report.workflow",
                start_time="2026-07-28T09:00:00+00:00",
                end_time="2026-07-28T09:00:01+00:00",
                model=None,
                metadata={},
                usage={},
                usage_details={},
                cost_details={},
                level="DEFAULT",
                status_message=None,
                parent_observation_id=None,
            ),
            SimpleNamespace(
                id="obs-node",
                trace_id=trace_id,
                type="SPAN",
                name="workflow.write_report",
                start_time="2026-07-28T09:00:00.100000+00:00",
                end_time="2026-07-28T09:00:00.600000+00:00",
                model=None,
                metadata={"workflow_node": "write_report"},
                usage={},
                usage_details={},
                cost_details={},
                level="ERROR",
                status_message="node failed",
                parent_observation_id="obs-root",
            ),
        ],
    )


def test_trace_api_returns_task_timeline(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    repo_root = tmp_path / "repo"
    config_dir.mkdir()
    repo_root.mkdir()

    (config_dir / "settings.example.yaml").write_text(
        "\n".join(
            [
                "app:",
                "  name: WeeklyReport Test API",
                "  environment: test",
                "model:",
                "  provider: openai-compatible",
                "  chat:",
                "    model: gpt-4.1-mini",
                "storage:",
                "  artifact_root: data/artifacts",
                "  sqlite_path: data/sqlite/weekly_report.db",
                "task_runner:",
                "  backend: local",
                "  max_workers: 1",
            ]
        ),
        encoding="utf-8",
    )

    config_service = ConfigService(config_dir=config_dir, repo_root=repo_root)
    settings = config_service.load()
    artifact_service = ArtifactService(settings.storage.artifact_root, settings.storage.sqlite_path)
    task_id = artifact_service.create_task(
        task_name="Printer reload demo",
        task_type=TaskType.WEEKLY_REPORT,
    )
    trace_service = TraceService(settings.storage.sqlite_path)
    trace = trace_service.start_task_trace(
        task_id=task_id,
        current_stage="preprocess",
        summary="Task started.",
    )
    trace_service.append_log(
        trace_id=trace.trace_id,
        level=TraceLogLevel.INFO,
        stage="preprocess",
        event_type="task_started",
        message="Task trace started.",
    )

    app = create_app(config_service=config_service, trace_service=trace_service)

    with TestClient(app) as client:
        response = client.get(f"/api/traces/tasks/{task_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["trace"]["trace_id"] == trace.trace_id
    assert payload["trace"]["current_stage"] == "preprocess"
    assert payload["logs"][0]["event_type"] == "task_started"


def test_trace_api_returns_structured_trace_and_summary(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    repo_root = tmp_path / "repo"
    config_dir.mkdir()
    repo_root.mkdir()

    (config_dir / "settings.example.yaml").write_text(
        "\n".join(
            [
                "app:",
                "  name: WeeklyReport Test API",
                "  environment: test",
                "model:",
                "  provider: openai-compatible",
                "  chat:",
                "    model: gpt-4.1-mini",
                "storage:",
                "  artifact_root: data/artifacts",
                "  sqlite_path: data/sqlite/weekly_report.db",
                "task_runner:",
                "  backend: local",
                "  max_workers: 1",
            ]
        ),
        encoding="utf-8",
    )

    config_service = ConfigService(config_dir=config_dir, repo_root=repo_root)
    settings = config_service.load()
    artifact_service = ArtifactService(settings.storage.artifact_root, settings.storage.sqlite_path)
    task_id = artifact_service.create_task("Printer reload demo", TaskType.WEEKLY_REPORT)
    trace_service = TraceService(settings.storage.sqlite_path)
    trace = trace_service.start_task_trace(task_id=task_id, current_stage="write_report", summary="Task started.")
    trace_service.append_log(
        trace_id=trace.trace_id,
        level=TraceLogLevel.INFO,
        stage="write_report",
        event_type="node_started",
        message="write_report started",
    )
    provider = _FakeObservabilityProvider(_FakeQueryClient(_live_trace(trace.trace_id)))
    app = create_app(
        config_service=config_service,
        trace_service=trace_service,
        artifact_service=artifact_service,
        observability_provider=provider,
    )

    with TestClient(app) as client:
        structured_response = client.get(f"/api/traces/{trace.trace_id}/structured")
        timeline_response = client.get(f"/api/traces/tasks/{task_id}")

    assert structured_response.status_code == 200
    structured_payload = structured_response.json()
    assert structured_payload["provider"] == "langfuse"
    assert structured_payload["source"] == "langfuse_live"
    assert structured_payload["root"]["name"] == "weekly_report.workflow"
    assert structured_payload["metrics"]["error_count"] == 1

    assert timeline_response.status_code == 200
    timeline_payload = timeline_response.json()
    assert timeline_payload["trace"]["trace_id"] == trace.trace_id
    assert timeline_payload["structured_trace_summary"]["source"] == "langfuse_live"
    assert timeline_payload["structured_trace_summary"]["error_count"] == 1


def test_trace_api_sync_uses_cache_when_langfuse_is_unavailable(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    repo_root = tmp_path / "repo"
    config_dir.mkdir()
    repo_root.mkdir()

    (config_dir / "settings.example.yaml").write_text(
        "\n".join(
            [
                "app:",
                "  name: WeeklyReport Test API",
                "  environment: test",
                "model:",
                "  provider: openai-compatible",
                "  chat:",
                "    model: gpt-4.1-mini",
                "storage:",
                "  artifact_root: data/artifacts",
                "  sqlite_path: data/sqlite/weekly_report.db",
                "task_runner:",
                "  backend: local",
                "  max_workers: 1",
            ]
        ),
        encoding="utf-8",
    )

    config_service = ConfigService(config_dir=config_dir, repo_root=repo_root)
    settings = config_service.load()
    artifact_service = ArtifactService(settings.storage.artifact_root, settings.storage.sqlite_path)
    task_id = artifact_service.create_task("Printer reload demo", TaskType.WEEKLY_REPORT)
    trace_service = TraceService(settings.storage.sqlite_path)
    trace = trace_service.start_task_trace(task_id=task_id, current_stage="review_report", summary="Task started.")
    trace_service.append_log(
        trace_id=trace.trace_id,
        level=TraceLogLevel.ERROR,
        stage="review_report",
        event_type="node_failed",
        message="review_report failed",
    )
    provider = _FakeObservabilityProvider(_FakeQueryClient(_live_trace(trace.trace_id)))
    app = create_app(
        config_service=config_service,
        trace_service=trace_service,
        artifact_service=artifact_service,
        observability_provider=provider,
    )

    with TestClient(app) as client:
        first_response = client.post(f"/api/traces/{trace.trace_id}/sync")
        provider.query_client = _FakeQueryClient(RuntimeError("langfuse offline"))
        second_response = client.post(f"/api/traces/{trace.trace_id}/sync")

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    second_payload = second_response.json()
    assert second_payload["source"] == "langfuse_cache"
    assert second_payload["sync_error"] == "RuntimeError: langfuse offline"
    artifacts = artifact_service.list_task_artifacts(task_id)
    assert len([item for item in artifacts if item["kind"] == "trace_export"]) == 1


def test_trace_api_returns_local_fallback_without_langfuse_cache(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    repo_root = tmp_path / "repo"
    config_dir.mkdir()
    repo_root.mkdir()

    (config_dir / "settings.example.yaml").write_text(
        "\n".join(
            [
                "app:",
                "  name: WeeklyReport Test API",
                "  environment: test",
                "model:",
                "  provider: openai-compatible",
                "  chat:",
                "    model: gpt-4.1-mini",
                "storage:",
                "  artifact_root: data/artifacts",
                "  sqlite_path: data/sqlite/weekly_report.db",
                "task_runner:",
                "  backend: local",
                "  max_workers: 1",
            ]
        ),
        encoding="utf-8",
    )

    config_service = ConfigService(config_dir=config_dir, repo_root=repo_root)
    settings = config_service.load()
    artifact_service = ArtifactService(settings.storage.artifact_root, settings.storage.sqlite_path)
    task_id = artifact_service.create_task("Printer reload demo", TaskType.WEEKLY_REPORT)
    trace_service = TraceService(settings.storage.sqlite_path)
    trace = trace_service.start_task_trace(task_id=task_id, current_stage="review_report", summary="Task started.")
    trace_service.append_log(
        trace_id=trace.trace_id,
        level=TraceLogLevel.ERROR,
        stage="review_report",
        event_type="node_failed",
        message="review_report failed",
    )
    provider = _FakeObservabilityProvider(_FakeQueryClient(RuntimeError("langfuse offline")))
    app = create_app(
        config_service=config_service,
        trace_service=trace_service,
        artifact_service=artifact_service,
        observability_provider=provider,
    )

    with TestClient(app) as client:
        response = client.get(f"/api/traces/{trace.trace_id}/structured")

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "local_fallback"
    assert payload["source"] == "local_fallback"
    assert payload["sync_error"] == "RuntimeError: langfuse offline"
    assert payload["fallback_logs"][0]["event_type"] == "node_failed"
