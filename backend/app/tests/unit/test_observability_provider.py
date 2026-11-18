from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import pytest

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.main import create_app
from app.observability import (
    NoOpObservabilityProvider,
    ObservabilityProvider,
    ObservedToolExecutor,
    TraceContext,
    clear_trace_context,
    create_observability_provider,
    get_current_trace_context,
    push_trace_context,
)
from app.observability.langfuse_provider import LangfuseObservabilityProvider
from app.services.artifact_service import ArtifactService
from app.services.config_service import ConfigService, ObservabilitySettings
from app.services.trace_service import TraceService
from app.workflows.graph_nodes import WeeklyReportGraphNodes


def test_create_observability_provider_returns_noop_when_disabled() -> None:
    settings = ObservabilitySettings(provider="none")

    provider = create_observability_provider(settings)

    assert isinstance(provider, NoOpObservabilityProvider)
    assert provider.enabled is False

    @provider.observe_span(name="noop-span")
    def sample() -> str:
        return "ok"

    assert sample() == "ok"


def test_create_observability_provider_builds_langfuse_clients(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeLangfuse:
        def __init__(self, **kwargs):
            captured["sdk_init"] = kwargs

        def flush(self) -> None:
            captured["flushed"] = True

        def get_trace_url(self, trace_id: str | None = None) -> str:
            suffix = trace_id or "current"
            return f"http://localhost:3000/trace/{suffix}"

    class FakeFernLangfuse:
        def __init__(self, **kwargs):
            captured["query_init"] = kwargs
            self.health = SimpleNamespace(
                health=lambda **health_kwargs: captured.update({"health_call": health_kwargs})
            )

    def fake_observe(**kwargs):
        captured["observe_init"] = kwargs

        def decorator(func):
            def wrapped(*args, **inner_kwargs):
                captured["observe_call_kwargs"] = dict(inner_kwargs)
                return func(*args, **inner_kwargs)

            return wrapped

        return decorator

    monkeypatch.setattr(
        LangfuseObservabilityProvider,
        "_import_langfuse_module",
        staticmethod(
            lambda: SimpleNamespace(
                Langfuse=FakeLangfuse,
                observe=fake_observe,
            )
        ),
    )
    monkeypatch.setattr(
        LangfuseObservabilityProvider,
        "_import_langfuse_query_client",
        staticmethod(lambda: FakeFernLangfuse),
    )

    settings = ObservabilitySettings(
        provider="langfuse",
        host="http://localhost:3000",
        public_key=SecretStr("pk-lf-test"),
        secret_key=SecretStr("sk-lf-test"),
        environment="test",
        capture_input=False,
        capture_output=False,
    )

    provider = create_observability_provider(settings)

    assert isinstance(provider, LangfuseObservabilityProvider)
    assert provider.enabled is True
    assert captured["sdk_init"] == {
        "public_key": "pk-lf-test",
        "secret_key": "sk-lf-test",
        "host": "http://localhost:3000",
        "environment": "test",
        "tracing_enabled": True,
    }
    assert captured["query_init"] == {
        "base_url": "http://localhost:3000",
        "x_langfuse_sdk_name": "weekly_report",
        "x_langfuse_sdk_version": "0.1.0",
        "x_langfuse_public_key": "pk-lf-test",
        "username": "pk-lf-test",
        "password": "sk-lf-test",
    }

    @provider.observe_tool(name="tool.demo")
    def sample_tool(**kwargs) -> str:
        return str(kwargs.get("langfuse_public_key"))

    assert sample_tool() == "pk-lf-test"
    assert captured["observe_init"] == {
        "name": "tool.demo",
        "as_type": "tool",
        "capture_input": False,
        "capture_output": False,
    }
    assert captured["observe_call_kwargs"] == {"langfuse_public_key": "pk-lf-test"}
    assert provider.get_trace_url("trace_123") == "http://localhost:3000/trace/trace_123"
    assert provider.is_query_service_available() is True
    assert captured["health_call"] == {
        "request_options": {"timeout_in_seconds": 2, "max_retries": 0}
    }

    provider.flush()
    assert captured["flushed"] is True


def test_langfuse_observe_generation_passes_public_key(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeLangfuse:
        def __init__(self, **kwargs):
            captured["sdk_init"] = kwargs

    class FakeFernLangfuse:
        def __init__(self, **kwargs):
            captured["query_init"] = kwargs

    def fake_observe(**kwargs):
        captured["observe_init"] = kwargs

        def decorator(func):
            def wrapped(*args, **inner_kwargs):
                captured["observe_call_kwargs"] = dict(inner_kwargs)
                return func(*args, **inner_kwargs)

            return wrapped

        return decorator

    monkeypatch.setattr(
        LangfuseObservabilityProvider,
        "_import_langfuse_module",
        staticmethod(
            lambda: SimpleNamespace(
                Langfuse=FakeLangfuse,
                observe=fake_observe,
            )
        ),
    )
    monkeypatch.setattr(
        LangfuseObservabilityProvider,
        "_import_langfuse_query_client",
        staticmethod(lambda: FakeFernLangfuse),
    )

    provider = create_observability_provider(
        ObservabilitySettings(
            provider="langfuse",
            public_key=SecretStr("pk-lf-test"),
            secret_key=SecretStr("sk-lf-test"),
            capture_input=False,
            capture_output=False,
        )
    )

    @provider.observe_generation(name="model.invoke_response")
    def sample_generation(**kwargs) -> str:
        return str(kwargs.get("langfuse_public_key"))

    assert sample_generation() == "pk-lf-test"
    assert captured["observe_init"] == {
        "name": "model.invoke_response",
        "as_type": "generation",
        "capture_input": False,
        "capture_output": False,
    }
    assert captured["observe_call_kwargs"] == {"langfuse_public_key": "pk-lf-test"}


def test_create_observability_provider_falls_back_to_noop_on_import_error(monkeypatch) -> None:
    monkeypatch.setattr(
        LangfuseObservabilityProvider,
        "_import_langfuse_module",
        staticmethod(lambda: (_ for _ in ()).throw(ImportError("langfuse missing"))),
    )

    settings = ObservabilitySettings(
        provider="langfuse",
        public_key=SecretStr("pk-lf-test"),
        secret_key=SecretStr("sk-lf-test"),
    )

    provider = create_observability_provider(settings)

    assert isinstance(provider, NoOpObservabilityProvider)


def test_create_observability_provider_logs_and_falls_back_on_runtime_error(
    monkeypatch,
) -> None:
    captured: list[dict[str, object]] = []

    def fake_warning(message, *args, **kwargs):  # noqa: ANN001
        captured.append({"message": message, **kwargs})

    monkeypatch.setattr(
        LangfuseObservabilityProvider,
        "_import_langfuse_module",
        staticmethod(
            lambda: SimpleNamespace(
                Langfuse=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("sdk init failed")),
                observe=lambda **kwargs: (lambda func: func),
            )
        ),
    )
    monkeypatch.setattr("app.observability.langfuse_provider.logger.warning", fake_warning)

    settings = ObservabilitySettings(
        provider="langfuse",
        public_key=SecretStr("pk-lf-test"),
        secret_key=SecretStr("sk-lf-test"),
    )

    provider = create_observability_provider(settings)

    assert isinstance(provider, NoOpObservabilityProvider)
    assert captured[0]["extra"]["event"] == "langfuse_provider_init_failed"


def test_trace_context_push_and_restore() -> None:
    clear_trace_context()
    assert get_current_trace_context() is None


def test_langfuse_provider_flush_logs_failure(monkeypatch) -> None:
    captured: list[dict[str, object]] = []

    def fake_warning(message, *args, **kwargs):  # noqa: ANN001
        captured.append({"message": message, **kwargs})

    class FakeLangfuse:
        def __init__(self, **kwargs):
            del kwargs

        def flush(self) -> None:
            raise RuntimeError("flush failed")

    class FakeFernLangfuse:
        def __init__(self, **kwargs):
            del kwargs

    monkeypatch.setattr(
        LangfuseObservabilityProvider,
        "_import_langfuse_module",
        staticmethod(
            lambda: SimpleNamespace(
                Langfuse=FakeLangfuse,
                observe=lambda **kwargs: (lambda func: func),
            )
        ),
    )
    monkeypatch.setattr(
        LangfuseObservabilityProvider,
        "_import_langfuse_query_client",
        staticmethod(lambda: FakeFernLangfuse),
    )
    monkeypatch.setattr("app.observability.langfuse_provider.logger.warning", fake_warning)
    provider = create_observability_provider(
        ObservabilitySettings(
            provider="langfuse",
            public_key=SecretStr("pk-lf-test"),
            secret_key=SecretStr("sk-lf-test"),
        )
    )

    provider.flush()

    assert captured[0]["extra"]["event"] == "langfuse_flush_failed"

    with push_trace_context(task_id="task-1", trace_id="trace-1") as root_context:
        assert root_context == TraceContext(task_id="task-1", trace_id="trace-1")
        assert get_current_trace_context() == root_context
        with push_trace_context(workflow_node="write_report", agent_session_id="session-1") as child:
            assert child == TraceContext(
                task_id="task-1",
                trace_id="trace-1",
                workflow_node="write_report",
                agent_session_id="session-1",
            )
            assert get_current_trace_context() == child
        assert get_current_trace_context() == root_context

    assert get_current_trace_context() is None


def test_observed_tool_executor_uses_provider_decorator() -> None:
    provider = NoOpObservabilityProvider(ObservabilitySettings(provider="none"))
    executor = ObservedToolExecutor(provider)

    result = executor.execute("echo", lambda: {"status": "ok"})

    assert result == {"status": "ok"}


def test_observed_tool_executor_records_raw_input_and_success_summary() -> None:
    captured: dict[str, object] = {}

    class FakeSdkClient:
        def __init__(self) -> None:
            self.update_calls: list[dict[str, object]] = []

        def update_current_observation(self, **kwargs) -> None:
            self.update_calls.append(kwargs)

    class FakeProvider(ObservabilityProvider):
        provider_name = "fake"
        enabled = True

        def __init__(self) -> None:
            super().__init__(ObservabilitySettings(provider="none"))
            self.sdk_client = FakeSdkClient()

        def observe(
            self,
            *,
            name: str | None = None,
            as_type: str = "span",
            capture_input: bool | None = None,
            capture_output: bool | None = None,
        ):
            captured["observe"] = {
                "name": name,
                "as_type": as_type,
                "capture_input": capture_input,
                "capture_output": capture_output,
            }

            def decorator(func):
                def wrapped(*args, **kwargs):
                    captured["call_kwargs"] = dict(kwargs)
                    return func(*args, **kwargs)

                return wrapped

            return decorator

        def get_sdk_client(self):
            return self.sdk_client

    provider = FakeProvider()
    executor = ObservedToolExecutor(provider)

    result = executor.execute(
        "demo_tool",
        lambda: {
            "status": "ok",
            "content": "x" * 1200,
            "response_content": [{"type": "input_image", "image_url": "data:image/png;base64,AAAA"}],
            "file_bytes": b"1234",
        },
        raw_input={"title": "Review final state", "binary_blob": b"1234"},
        tool_call_id="call-001",
        extra_metadata={"agent_name": "WeeklyReportReviewAgent"},
    )

    assert result["status"] == "ok"
    assert captured["observe"] == {
        "name": "tool.demo_tool",
        "as_type": "tool",
        "capture_input": False,
        "capture_output": False,
    }
    assert captured["call_kwargs"] == {}
    assert len(provider.sdk_client.update_calls) == 2
    first_call = provider.sdk_client.update_calls[0]
    assert first_call["metadata"]["tool_name"] == "demo_tool"
    assert first_call["metadata"]["tool_call_id"] == "call-001"
    assert first_call["metadata"]["tool_input"]["title"] == "Review final state"
    assert first_call["metadata"]["tool_input"]["binary_blob"]["content"] == "<omitted>"
    second_call = provider.sdk_client.update_calls[1]
    assert second_call["output"]["status"] == "ok"
    assert second_call["output"]["result_summary"]["content"].endswith("chars>")
    assert second_call["output"]["result_summary"]["response_content"][0]["image_url"]["content"] == "<omitted>"
    assert second_call["output"]["result_summary"]["file_bytes"]["content"] == "<omitted>"


def test_observed_tool_executor_records_error_output() -> None:
    class FakeSdkClient:
        def __init__(self) -> None:
            self.update_calls: list[dict[str, object]] = []

        def update_current_observation(self, **kwargs) -> None:
            self.update_calls.append(kwargs)

    class FakeProvider(ObservabilityProvider):
        provider_name = "fake"
        enabled = True

        def __init__(self) -> None:
            super().__init__(ObservabilitySettings(provider="none"))
            self.sdk_client = FakeSdkClient()

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

        def get_sdk_client(self):
            return self.sdk_client

    provider = FakeProvider()
    executor = ObservedToolExecutor(provider)

    with pytest.raises(RuntimeError, match="tool boom"):
        executor.execute(
            "broken_tool",
            lambda: (_ for _ in ()).throw(RuntimeError("tool boom " + ("x" * 1000))),
            raw_input={"material_id": "material-001"},
            tool_call_id="call-002",
        )

    assert len(provider.sdk_client.update_calls) == 2
    error_call = provider.sdk_client.update_calls[1]
    assert error_call["level"] == "ERROR"
    assert error_call["metadata"]["error_type"] == "RuntimeError"
    assert error_call["output"]["status"] == "error"
    assert error_call["output"]["error"]["stack"]


def test_create_app_registers_observability_provider_and_flushes_on_shutdown(
    tmp_path: Path,
) -> None:
    class FakeObservabilityProvider(ObservabilityProvider):
        provider_name = "fake"
        enabled = True

        def __init__(self) -> None:
            super().__init__(ObservabilitySettings(provider="none"))
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

        def flush(self) -> None:
            self.flush_calls += 1

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
                "observability:",
                "  provider: none",
            ]
        ),
        encoding="utf-8",
    )
    config_service = ConfigService(config_dir=config_dir, repo_root=repo_root)
    settings = config_service.load()
    artifact_service = ArtifactService(settings.storage.artifact_root, settings.storage.sqlite_path)
    trace_service = TraceService(settings.storage.sqlite_path)
    provider = FakeObservabilityProvider()
    app = create_app(
        config_service=config_service,
        artifact_service=artifact_service,
        trace_service=trace_service,
        material_preprocess_tool=object(),
        graph_nodes=WeeklyReportGraphNodes(),
        memory_evolution_agent=object(),
        observability_provider=provider,
    )

    with TestClient(app) as client:
        assert client.app.state.observability_provider is provider
        response = client.get("/healthz")
        assert response.status_code == 200

    assert provider.flush_calls == 1
