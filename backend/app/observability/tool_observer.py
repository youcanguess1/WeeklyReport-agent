from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
import traceback
from collections.abc import Callable
from typing import Any, TypeVar

from app.observability.langfuse_provider import NoOpObservabilityProvider, ObservabilityProvider
from app.observability.trace_context import get_current_trace_context
from app.services.config_service import ObservabilitySettings

T = TypeVar("T")

_MAX_SUMMARY_DEPTH = 5
_MAX_SUMMARY_ITEMS = 10
_MAX_SUMMARY_TEXT_LENGTH = 800
_MAX_ERROR_TEXT_LENGTH = 4000


def _truncate_text(text: str, *, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...<truncated {len(text) - limit} chars>"


def _looks_like_data_uri(text: str) -> bool:
    lowered = text.lower()
    return lowered.startswith("data:image/") or lowered.startswith("data:application/octet-stream")


def _is_binary_key(key: str) -> bool:
    lowered = key.lower()
    return lowered.endswith("_bytes") or lowered.endswith("_blob") or lowered.endswith("_binary")


def _preserve_input_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= _MAX_SUMMARY_DEPTH:
        return {"type": type(value).__name__, "truncated": True}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if _looks_like_data_uri(value):
            return {"type": "data_uri", "length": len(value), "content": "<omitted>"}
        return value
    if isinstance(value, bytes):
        return {"type": "bytes", "byte_length": len(value), "content": "<omitted>"}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "model_dump"):
        return _preserve_input_value(value.model_dump(mode="json"), depth=depth + 1)
    if is_dataclass(value):
        return _preserve_input_value(asdict(value), depth=depth + 1)
    if isinstance(value, dict):
        return {
            str(key): (
                {"type": "bytes", "content": "<omitted>"}
                if _is_binary_key(str(key))
                else _preserve_input_value(item, depth=depth + 1)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_preserve_input_value(item, depth=depth + 1) for item in value]
    return _truncate_text(repr(value), limit=_MAX_SUMMARY_TEXT_LENGTH)


def _summarize_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= _MAX_SUMMARY_DEPTH:
        return {"type": type(value).__name__, "truncated": True}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if _looks_like_data_uri(value):
            return {"type": "data_uri", "length": len(value), "content": "<omitted>"}
        return _truncate_text(value, limit=_MAX_SUMMARY_TEXT_LENGTH)
    if isinstance(value, bytes):
        return {"type": "bytes", "byte_length": len(value), "content": "<omitted>"}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "model_dump"):
        return _summarize_value(value.model_dump(mode="json"), depth=depth + 1)
    if is_dataclass(value):
        return _summarize_value(asdict(value), depth=depth + 1)
    if isinstance(value, dict):
        summarized: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_SUMMARY_ITEMS:
                summarized["__truncated_keys__"] = len(value) - _MAX_SUMMARY_ITEMS
                break
            key_text = str(key)
            if _is_binary_key(key_text):
                summarized[key_text] = {
                    "type": "bytes",
                    "content": "<omitted>",
                }
            else:
                summarized[key_text] = _summarize_value(item, depth=depth + 1)
        return summarized
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        summarized_items = [
            _summarize_value(item, depth=depth + 1)
            for item in items[:_MAX_SUMMARY_ITEMS]
        ]
        if len(items) > _MAX_SUMMARY_ITEMS:
            summarized_items.append({"truncated_items": len(items) - _MAX_SUMMARY_ITEMS})
        return summarized_items
    return _truncate_text(repr(value), limit=_MAX_SUMMARY_TEXT_LENGTH)


class ObservedToolExecutor:
    def __init__(self, provider: ObservabilityProvider) -> None:
        self._provider = provider

    @property
    def provider(self) -> ObservabilityProvider:
        return self._provider

    @classmethod
    def noop(cls) -> ObservedToolExecutor:
        return cls(NoOpObservabilityProvider(ObservabilitySettings(provider="none")))

    def wrap(
        self,
        tool_name: str,
        execute: Callable[..., T],
        *,
        capture_input: bool | None = None,
        capture_output: bool | None = None,
    ) -> Callable[..., T]:
        capture_input_value = self._provider.capture_input if capture_input is None else capture_input
        capture_output_value = self._provider.capture_output if capture_output is None else capture_output
        return self._provider.observe_tool(
            name=f"tool.{tool_name}",
            capture_input=capture_input_value,
            capture_output=capture_output_value,
        )(execute)

    def execute(
        self,
        tool_name: str,
        execute: Callable[[], T],
        *,
        raw_input: Any | None = None,
        tool_call_id: str | None = None,
        output_summarizer: Callable[[T], Any] | None = None,
        failure_summarizer: Callable[[Exception], Any] | None = None,
        extra_metadata: dict[str, Any] | None = None,
        capture_input: bool | None = None,
        capture_output: bool | None = None,
    ) -> T:
        provider = self._provider
        sdk_client = provider.get_sdk_client() if provider.enabled else None
        metadata = self._build_metadata(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            raw_input=raw_input,
            extra_metadata=extra_metadata,
        )

        def operation(**_kwargs: Any) -> T:
            self._update_current_tool_observation(sdk_client, metadata=metadata)
            try:
                result = execute()
            except Exception as exc:
                error_output = self._build_error_output(exc, failure_summarizer=failure_summarizer)
                self._update_current_tool_observation(
                    sdk_client,
                    level="ERROR",
                    status_message=_truncate_text(str(exc), limit=_MAX_SUMMARY_TEXT_LENGTH),
                    metadata={
                        **metadata,
                        "error_type": type(exc).__name__,
                    },
                    output=error_output,
                )
                raise

            output = (
                output_summarizer(result)
                if output_summarizer is not None
                else self._build_success_output(result)
            )
            self._update_current_tool_observation(
                sdk_client,
                output=output,
            )
            return result

        if not provider.enabled:
            return operation()

        sdk_client = provider.get_sdk_client()
        if callable(getattr(sdk_client, "start_as_current_observation", None)):
            with provider.observation(
                name=f"tool.{tool_name}",
                as_type="tool",
                input=metadata,
                metadata=metadata,
            ) as observation:
                try:
                    result = execute()
                except Exception as exc:
                    error_output = self._build_error_output(exc, failure_summarizer=failure_summarizer)
                    if observation is not None:
                        observation.update(
                            level="ERROR",
                            status_message=_truncate_text(str(exc), limit=_MAX_SUMMARY_TEXT_LENGTH),
                            metadata={**metadata, "error_type": type(exc).__name__},
                            output=error_output,
                        )
                    raise

                output = (
                    output_summarizer(result)
                    if output_summarizer is not None
                    else self._build_success_output(result)
                )
                if observation is not None:
                    observation.update(output=output)
                return result

        observed_execute = self.wrap(
            tool_name,
            operation,
            capture_input=capture_input,
            capture_output=capture_output,
        )
        return observed_execute()

    def _build_metadata(
        self,
        *,
        tool_name: str,
        tool_call_id: str | None,
        raw_input: Any | None,
        extra_metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        trace_context = get_current_trace_context()
        metadata: dict[str, Any] = {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "tool_input": _preserve_input_value(raw_input),
        }
        if trace_context is not None:
            metadata.update(
                {
                    "task_id": trace_context.task_id,
                    "trace_id": trace_context.trace_id,
                    "workflow_node": trace_context.workflow_node,
                    "agent_session_id": trace_context.agent_session_id,
                }
            )
        if extra_metadata:
            metadata.update({key: value for key, value in extra_metadata.items() if value is not None})
        return metadata

    @staticmethod
    def _build_success_output(result: Any) -> dict[str, Any]:
        return {
            "status": "ok",
            "result_type": type(result).__name__,
            "result_summary": _summarize_value(result),
        }

    @staticmethod
    def _build_error_output(
        exc: Exception,
        *,
        failure_summarizer: Callable[[Exception], Any] | None,
    ) -> dict[str, Any]:
        if failure_summarizer is not None:
            summarized = failure_summarizer(exc)
        else:
            summarized = {
                "error_type": type(exc).__name__,
                "error_message": _truncate_text(str(exc), limit=_MAX_SUMMARY_TEXT_LENGTH),
                "stack": _truncate_text(traceback.format_exc(), limit=_MAX_ERROR_TEXT_LENGTH),
            }
        return {
            "status": "error",
            "error": _summarize_value(summarized),
        }

    @staticmethod
    def _update_current_tool_observation(
        sdk_client: Any | None,
        **kwargs: Any,
    ) -> None:
        if sdk_client is None:
            return
        if hasattr(sdk_client, "update_current_observation"):
            sdk_client.update_current_observation(**kwargs)
            return
        if hasattr(sdk_client, "update_current_span"):
            sdk_client.update_current_span(**kwargs)
