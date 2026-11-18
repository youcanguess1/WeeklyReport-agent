from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from contextlib import contextmanager
from functools import wraps
import importlib
import inspect
import logging
from typing import Any, Iterator, Literal, TypeVar, cast

from app.services.config_service import ObservabilitySettings

ObservationType = Literal[
    "span",
    "generation",
    "tool",
    "embedding",
    "agent",
    "chain",
    "retriever",
    "evaluator",
    "guardrail",
]
F = TypeVar("F", bound=Callable[..., Any])
logger = logging.getLogger(__name__)


class ObservabilityProvider(ABC):
    provider_name = "none"
    enabled = False

    def __init__(self, settings: ObservabilitySettings) -> None:
        self.settings = settings

    @property
    def project_name(self) -> str:
        return self.settings.project_name

    @property
    def environment(self) -> str | None:
        return self.settings.environment

    @property
    def capture_input(self) -> bool:
        return self.settings.capture_input

    @property
    def capture_output(self) -> bool:
        return self.settings.capture_output

    @abstractmethod
    def observe(
        self,
        *,
        name: str | None = None,
        as_type: ObservationType = "span",
        capture_input: bool | None = None,
        capture_output: bool | None = None,
    ) -> Callable[[F], F]:
        raise NotImplementedError

    def observe_span(
        self,
        *,
        name: str | None = None,
        capture_input: bool | None = None,
        capture_output: bool | None = None,
    ) -> Callable[[F], F]:
        return self.observe(
            name=name,
            as_type="span",
            capture_input=capture_input,
            capture_output=capture_output,
        )

    def observe_generation(
        self,
        *,
        name: str | None = None,
        capture_input: bool | None = None,
        capture_output: bool | None = None,
    ) -> Callable[[F], F]:
        return self.observe(
            name=name,
            as_type="generation",
            capture_input=capture_input,
            capture_output=capture_output,
        )

    def observe_tool(
        self,
        *,
        name: str | None = None,
        capture_input: bool | None = None,
        capture_output: bool | None = None,
    ) -> Callable[[F], F]:
        return self.observe(
            name=name,
            as_type="tool",
            capture_input=capture_input,
            capture_output=capture_output,
        )

    def get_sdk_client(self) -> Any | None:
        return None

    @contextmanager
    def observation(
        self,
        *,
        name: str,
        as_type: ObservationType = "span",
        trace_id: str | None = None,
        input: Any | None = None,
        metadata: Any | None = None,
    ) -> Iterator[Any | None]:
        """Create an explicitly current observation when the SDK supports it.

        The decorator API is useful for simple functions, but explicit current
        observations are required here because workflow nodes and tools update
        their structured summaries after nested work has completed.
        """
        sdk_client = self.get_sdk_client()
        start = getattr(sdk_client, "start_as_current_observation", None)
        if not self.enabled or not callable(start):
            yield None
            return

        kwargs: dict[str, Any] = {
            "name": name,
            "as_type": as_type,
            "input": input,
            "metadata": metadata,
        }
        if trace_id:
            kwargs["trace_context"] = {"trace_id": trace_id}
        with start(**kwargs) as observation:
            yield observation

    def get_query_client(self) -> Any | None:
        return None

    def is_query_service_available(self) -> bool:
        return False

    def get_trace_url(self, trace_id: str | None = None) -> str | None:
        return None

    def flush(self) -> None:
        return None


class NoOpObservabilityProvider(ObservabilityProvider):
    provider_name = "none"
    enabled = False

    def observe(
        self,
        *,
        name: str | None = None,
        as_type: ObservationType = "span",
        capture_input: bool | None = None,
        capture_output: bool | None = None,
    ) -> Callable[[F], F]:
        del name, as_type, capture_input, capture_output

        def decorator(func: F) -> F:
            return func

        return decorator


class LangfuseObservabilityProvider(ObservabilityProvider):
    provider_name = "langfuse"
    enabled = True

    def __init__(self, settings: ObservabilitySettings) -> None:
        super().__init__(settings)
        self._public_key = (
            settings.public_key.get_secret_value() if settings.public_key is not None else None
        )
        self._secret_key = (
            settings.secret_key.get_secret_value() if settings.secret_key is not None else None
        )
        if not self._public_key or not self._secret_key:
            raise ValueError("Langfuse provider requires both public and secret keys.")
        self._sdk_client = self._build_sdk_client()
        self._query_client = self._build_query_client()

    @property
    def public_key(self) -> str:
        return self._public_key or ""

    @property
    def secret_key(self) -> str:
        return self._secret_key or ""

    def observe(
        self,
        *,
        name: str | None = None,
        as_type: ObservationType = "span",
        capture_input: bool | None = None,
        capture_output: bool | None = None,
    ) -> Callable[[F], F]:
        langfuse_module = self._import_langfuse_module()
        observe = getattr(langfuse_module, "observe")
        capture_input_value = self.capture_input if capture_input is None else capture_input
        capture_output_value = self.capture_output if capture_output is None else capture_output
        decorator = observe(
            name=name,
            as_type=as_type,
            capture_input=capture_input_value,
            capture_output=capture_output_value,
        )

        def apply(func: F) -> F:
            observed_func = decorator(func)
            if inspect.iscoroutinefunction(observed_func):

                @wraps(observed_func)
                async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                    kwargs.setdefault("langfuse_public_key", self.public_key)
                    return await observed_func(*args, **kwargs)

                return cast(F, async_wrapper)

            @wraps(observed_func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                kwargs.setdefault("langfuse_public_key", self.public_key)
                return observed_func(*args, **kwargs)

            return cast(F, sync_wrapper)

        return apply

    def get_sdk_client(self) -> Any | None:
        return self._sdk_client

    def get_query_client(self) -> Any | None:
        return self._query_client

    def is_query_service_available(self) -> bool:
        if self._query_client is None or not hasattr(self._query_client, "health"):
            return False
        try:
            self._query_client.health.health(
                request_options={"timeout_in_seconds": 2, "max_retries": 0}
            )
            return True
        except Exception:
            logger.warning(
                "Langfuse query service is unavailable.",
                extra={"event": "langfuse_query_service_unavailable", "component": "observability"},
                exc_info=True,
            )
            return False

    def get_trace_url(self, trace_id: str | None = None) -> str | None:
        if self._sdk_client is None or not hasattr(self._sdk_client, "get_trace_url"):
            return None
        return cast(str | None, self._sdk_client.get_trace_url(trace_id=trace_id))

    def flush(self) -> None:
        if self._sdk_client is None or not hasattr(self._sdk_client, "flush"):
            return None
        try:
            self._sdk_client.flush()
        except Exception:
            logger.warning(
                "Failed to flush Langfuse SDK client.",
                extra={"event": "langfuse_flush_failed", "component": "observability"},
                exc_info=True,
            )
            return None

    def _build_sdk_client(self) -> Any:
        langfuse_module = self._import_langfuse_module()
        langfuse_class = getattr(langfuse_module, "Langfuse")
        return langfuse_class(
            public_key=self.public_key,
            secret_key=self.secret_key,
            host=self.settings.host,
            environment=self.settings.environment,
            tracing_enabled=True,
        )

    def _build_query_client(self) -> Any:
        query_client_class = self._import_langfuse_query_client()
        return query_client_class(
            base_url=self.settings.host,
            x_langfuse_sdk_name="weekly_report",
            x_langfuse_sdk_version="0.1.0",
            x_langfuse_public_key=self.public_key,
            username=self.public_key,
            password=self.secret_key,
        )

    @staticmethod
    def _import_langfuse_module() -> Any:
        return importlib.import_module("langfuse")

    @staticmethod
    def _import_langfuse_query_client() -> Any:
        module = importlib.import_module("langfuse.api.client")
        return getattr(module, "FernLangfuse")


def create_observability_provider(settings: ObservabilitySettings) -> ObservabilityProvider:
    if not settings.enabled:
        return NoOpObservabilityProvider(settings)

    if settings.provider != "langfuse":
        return NoOpObservabilityProvider(settings)

    try:
        return LangfuseObservabilityProvider(settings)
    except (ImportError, ValueError, Exception):
        logger.warning(
            "Failed to initialize Langfuse observability provider; falling back to no-op.",
            extra={"event": "langfuse_provider_init_failed", "component": "observability"},
            exc_info=True,
        )
        return NoOpObservabilityProvider(settings)
