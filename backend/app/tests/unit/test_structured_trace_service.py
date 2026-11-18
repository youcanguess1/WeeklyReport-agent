from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.observability import ObservabilityProvider
from app.schemas.task import TaskType
from app.schemas.trace import TraceLogLevel
from app.services.artifact_service import ArtifactService
from app.services.config_service import ObservabilitySettings
from app.services.structured_trace_service import StructuredTraceService
from app.services.trace_service import TraceService


class _FakeTraceClient:
    def __init__(self, trace_obj: object | Exception) -> None:
        self._trace_obj = trace_obj
        self.calls: list[str] = []

    def get(self, trace_id: str) -> object:
        self.calls.append(trace_id)
        if isinstance(self._trace_obj, Exception):
            raise self._trace_obj
        return self._trace_obj


class _RetryableNotFoundError(Exception):
    status_code = 404


class _EventuallyAvailableTraceClient:
    def __init__(self, trace_obj: object) -> None:
        self.trace_obj = trace_obj
        self.calls: list[str] = []

    def get(self, trace_id: str) -> object:
        self.calls.append(trace_id)
        if len(self.calls) == 1:
            raise _RetryableNotFoundError("trace is still being ingested")
        return self.trace_obj


class _FakeObservationsClient:
    def __init__(self, response_obj: object) -> None:
        self._response_obj = response_obj
        self.calls: list[str] = []

    def get_many(self, *, trace_id: str) -> object:
        self.calls.append(trace_id)
        return self._response_obj


class _FakeQueryClient:
    def __init__(self, trace_obj: object | Exception, observation_response: object | None = None) -> None:
        self.trace = _FakeTraceClient(trace_obj)
        self.observations = _FakeObservationsClient(
            observation_response if observation_response is not None else SimpleNamespace(data=[])
        )


class _FakeObservabilityProvider(ObservabilityProvider):
    provider_name = "langfuse"
    enabled = True

    def __init__(self, query_client: object | None) -> None:
        super().__init__(ObservabilitySettings(provider="langfuse"))
        self.query_client = query_client
        self.flush_calls = 0

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

    def flush(self) -> None:
        self.flush_calls += 1


def _build_services(tmp_path: Path):
    artifact_service = ArtifactService(
        artifact_root=tmp_path / "artifacts",
        sqlite_path=tmp_path / "sqlite" / "weekly_report.db",
    )
    task_id = artifact_service.create_task("Trace demo", TaskType.WEEKLY_REPORT)
    trace_service = TraceService(artifact_service.sqlite_path)
    trace = trace_service.start_task_trace(
        task_id=task_id,
        current_stage="start_workflow",
        summary="started",
    )
    trace_service.append_log(
        trace_id=trace.trace_id,
        level=TraceLogLevel.INFO,
        stage="start_workflow",
        event_type="workflow_started",
        message="Workflow execution started.",
        extra={"task_status": "queued", "thread_id": "thread-demo"},
    )
    trace_service.append_log(
        trace_id=trace.trace_id,
        level=TraceLogLevel.ERROR,
        stage="review_report",
        event_type="node_failed",
        message="Review failed.",
        extra={"error_type": "RuntimeError", "updated_keys": ["task_status"]},
    )
    return artifact_service, trace_service, trace


def _live_trace(trace_id: str) -> object:
    return SimpleNamespace(
        id=trace_id,
        timestamp="2026-07-28T09:00:00+00:00",
        latency=321.0,
        total_cost=0.42,
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
                metadata={"thread_id": "thread-001"},
                usage={},
                usage_details={},
                cost_details={},
                level="DEFAULT",
                status_message=None,
                parent_observation_id=None,
            ),
            SimpleNamespace(
                id="obs-child",
                trace_id=trace_id,
                type="GENERATION",
                name="model.invoke_response",
                start_time="2026-07-28T09:00:00.100000+00:00",
                end_time="2026-07-28T09:00:00.400000+00:00",
                model="gpt-4.1-mini",
                metadata={"endpoint": "chat"},
                usage={"input": 10, "output": 4, "total": 14},
                usage_details={},
                cost_details={"total": 0.12},
                level="WARNING",
                status_message="slow request",
                parent_observation_id="obs-root",
            ),
        ],
    )


def test_structured_trace_service_normalizes_live_trace_and_writes_cache(tmp_path: Path) -> None:
    artifact_service, trace_service, trace = _build_services(tmp_path)
    provider = _FakeObservabilityProvider(_FakeQueryClient(_live_trace(trace.trace_id)))
    service = StructuredTraceService(trace_service, artifact_service, provider)

    result = service.get_structured_trace(trace.trace_id)

    assert result.provider == "langfuse"
    assert result.source.value == "langfuse_live"
    assert result.root is not None
    assert result.root.name == "weekly_report.workflow"
    assert len(result.root.children) == 1
    assert result.root.children[0].name == "model.invoke_response"
    assert result.metrics.observation_count == 2
    assert result.metrics.warning_count == 1
    assert result.metrics.total_tokens == 14
    assert result.metrics.total_cost == 0.42
    assert result.trace_url == f"http://localhost:3000/project/demo/traces/{trace.trace_id}"
    assert result.cache_artifact_id is not None
    artifacts = artifact_service.list_task_artifacts(trace.task_id)
    cached = [item for item in artifacts if item["kind"] == "trace_export"]
    assert len(cached) == 1
    assert cached[0]["metadata"]["trace_id"] == trace.trace_id
    assert provider.flush_calls == 1


def test_structured_trace_service_uses_observations_query_when_trace_has_no_observations(tmp_path: Path) -> None:
    artifact_service, trace_service, trace = _build_services(tmp_path)
    trace_obj = SimpleNamespace(
        id=trace.trace_id,
        timestamp="2026-07-28T09:00:00+00:00",
        latency=50.0,
        total_cost=0.1,
        metadata={},
        observations=[],
    )
    observation_response = SimpleNamespace(
        data=[
            SimpleNamespace(
                id="obs-only",
                trace_id=trace.trace_id,
                type="TOOL",
                name="tool.get_skill_content",
                start_time="2026-07-28T09:00:00+00:00",
                end_time="2026-07-28T09:00:00.100000+00:00",
                model=None,
                metadata={"tool_name": "get_skill_content"},
                usage={},
                usage_details={},
                cost_details={},
                level="ERROR",
                status_message="failed",
                parent_observation_id=None,
            )
        ]
    )
    query_client = _FakeQueryClient(trace_obj, observation_response)
    provider = _FakeObservabilityProvider(query_client)
    service = StructuredTraceService(trace_service, artifact_service, provider)

    result = service.get_structured_trace(trace.trace_id)

    assert result.metrics.observation_count == 1
    assert result.metrics.error_count == 1
    assert query_client.trace.calls == [trace.trace_id]
    assert query_client.observations.calls == [trace.trace_id]


def test_structured_trace_service_retries_recent_trace_ingestion(tmp_path: Path, monkeypatch) -> None:
    artifact_service, trace_service, trace = _build_services(tmp_path)
    query_client = _FakeQueryClient(_live_trace(trace.trace_id))
    eventual_trace_client = _EventuallyAvailableTraceClient(_live_trace(trace.trace_id))
    query_client.trace = eventual_trace_client
    provider = _FakeObservabilityProvider(query_client)
    service = StructuredTraceService(trace_service, artifact_service, provider)
    monkeypatch.setattr("app.services.structured_trace_service.time.sleep", lambda _delay: None)

    result = service.get_structured_trace(trace.trace_id)

    assert result.source.value == "langfuse_live"
    assert result.metrics.observation_count == 2
    assert eventual_trace_client.calls == [trace.trace_id, trace.trace_id]


def test_structured_trace_service_returns_local_fallback_without_query_client(tmp_path: Path) -> None:
    artifact_service, trace_service, trace = _build_services(tmp_path)
    provider = _FakeObservabilityProvider(query_client=None)
    service = StructuredTraceService(trace_service, artifact_service, provider)

    result = service.get_structured_trace(trace.trace_id)

    assert result.provider == "local_fallback"
    assert result.source.value == "local_fallback"
    assert result.root is not None
    assert len(result.observations) == 2
    assert result.metrics.error_count == 1
    assert result.sync_error is not None
    assert result.root.children[0].output == {"task_status": "queued", "thread_id": "thread-demo"}
    assert result.root.children[1].output == {
        "error_type": "RuntimeError",
        "updated_keys": ["task_status"],
    }


def test_structured_trace_service_logs_warning_on_live_sync_failure(
    tmp_path: Path,
) -> None:
    captured: list[dict[str, object]] = []

    def fake_warning(message, *args, **kwargs):  # noqa: ANN001
        captured.append({"message": message, **kwargs})

    artifact_service, trace_service, trace = _build_services(tmp_path)
    provider = _FakeObservabilityProvider(query_client=None)
    service = StructuredTraceService(trace_service, artifact_service, provider)
    from app.services import structured_trace_service as structured_trace_module

    original_warning = structured_trace_module.logger.warning
    structured_trace_module.logger.warning = fake_warning
    try:
        result = service.get_structured_trace(trace.trace_id)
    finally:
        structured_trace_module.logger.warning = original_warning

    assert result.provider == "local_fallback"
    assert any(item["extra"]["event"] == "langfuse_query_client_unavailable" for item in captured)
    assert any(item["extra"]["event"] == "structured_trace_live_sync_failed" for item in captured)


def test_structured_trace_service_prefers_cached_langfuse_trace_over_local_fallback(tmp_path: Path) -> None:
    artifact_service, trace_service, trace = _build_services(tmp_path)
    query_client = _FakeQueryClient(_live_trace(trace.trace_id))
    provider = _FakeObservabilityProvider(query_client)
    service = StructuredTraceService(trace_service, artifact_service, provider)

    live_result = service.get_structured_trace(trace.trace_id)
    assert live_result.cache_artifact_id is not None

    provider.query_client = _FakeQueryClient(RuntimeError("langfuse offline"))
    cached_result = service.get_structured_trace(trace.trace_id)

    assert cached_result.provider == "langfuse"
    assert cached_result.source.value == "langfuse_cache"
    assert cached_result.cache_artifact_id == live_result.cache_artifact_id
    assert cached_result.sync_error == "RuntimeError: langfuse offline"
