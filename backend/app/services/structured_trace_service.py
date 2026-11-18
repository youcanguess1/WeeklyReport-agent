from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable

from app.observability import ObservabilityProvider
from app.schemas.evidence import ArtifactKind
from app.schemas.structured_trace import (
    StructuredTraceSource,
    StructuredTraceSummary,
    StructuredTraceTimeline,
    TraceMetricsSummary,
    TraceObservationNode,
)
from app.schemas.trace import TaskTrace, TraceLogEntry, TraceStatus
from app.services.artifact_service import ArtifactService, StorageGroup
from app.services.trace_service import TraceService


logger = logging.getLogger(__name__)
_LIVE_TRACE_RETRY_DELAYS_SECONDS = (0.25, 0.5, 1.0, 2.0, 3.0)
_LIVE_TRACE_RECENCY_WINDOW = timedelta(seconds=60)


class StructuredTraceService:
    def __init__(
        self,
        trace_service: TraceService,
        artifact_service: ArtifactService,
        observability_provider: ObservabilityProvider,
    ) -> None:
        self._trace_service = trace_service
        self._artifact_service = artifact_service
        self._observability_provider = observability_provider

    def get_structured_trace(self, trace_id: str) -> StructuredTraceTimeline:
        return self._resolve_trace(trace_id)

    def sync_trace(self, trace_id: str) -> StructuredTraceTimeline:
        return self._resolve_trace(trace_id)

    def get_structured_trace_summary(self, trace_id: str) -> StructuredTraceSummary:
        return self._build_summary(self.get_structured_trace(trace_id))

    def _resolve_trace(self, trace_id: str) -> StructuredTraceTimeline:
        task_trace = self._trace_service.get_trace(trace_id)
        fallback_logs = self._trace_service.list_logs(trace_id)
        try:
            return self._sync_live_trace(task_trace, fallback_logs)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to sync structured trace from Langfuse.",
                extra={
                    "event": "structured_trace_live_sync_failed",
                    "component": "structured_trace",
                    "task_id": task_trace.task_id,
                    "trace_id": task_trace.trace_id,
                },
                exc_info=True,
            )
            try:
                cached_timeline = self._load_cached_trace(task_trace)
            except Exception:
                logger.warning(
                    "Failed to load cached structured trace.",
                    extra={
                        "event": "structured_trace_cache_load_failed",
                        "component": "structured_trace",
                        "task_id": task_trace.task_id,
                        "trace_id": task_trace.trace_id,
                    },
                    exc_info=True,
                )
                cached_timeline = None
            if cached_timeline is not None:
                return cached_timeline.model_copy(
                    update={
                        "sync_error": self._format_sync_error(exc),
                        "fallback_logs": fallback_logs,
                    }
                )
            return self._build_local_fallback(
                task_trace,
                fallback_logs,
                sync_error=self._format_sync_error(exc),
            )

    def _sync_live_trace(
        self,
        task_trace: TaskTrace,
        fallback_logs: list[TraceLogEntry],
    ) -> StructuredTraceTimeline:
        self._observability_provider.flush()
        query_client = self._observability_provider.get_query_client()
        if query_client is None:
            logger.warning(
                "Langfuse query client is unavailable.",
                extra={
                    "event": "langfuse_query_client_unavailable",
                    "component": "structured_trace",
                    "task_id": task_trace.task_id,
                    "trace_id": task_trace.trace_id,
                },
            )
            raise RuntimeError("Langfuse query client is unavailable.")

        retry_eventual_consistency = self._is_recent_trace(task_trace)
        live_trace = self._query_with_retry(
            lambda: query_client.trace.get(task_trace.trace_id),
            retry_eventual_consistency=retry_eventual_consistency,
        )
        observations = list(self._as_sequence(self._lookup(live_trace, "observations")))
        if not observations:
            observations = self._query_observations_with_retry(
                query_client,
                task_trace.trace_id,
                retry_eventual_consistency=retry_eventual_consistency,
            )

        timeline = self._normalize_live_trace(
            task_trace=task_trace,
            live_trace=live_trace,
            observations=observations,
            fallback_logs=fallback_logs,
        )
        cache_ref = self._save_trace_cache(task_trace, timeline)
        return timeline.model_copy(update={"cache_artifact_id": cache_ref.artifact_id})

    @classmethod
    def _query_with_retry(
        cls,
        query: Any,
        *,
        retry_eventual_consistency: bool,
    ) -> Any:
        try:
            return query()
        except Exception as exc:  # noqa: BLE001
            if not retry_eventual_consistency or not cls._is_not_found_error(exc):
                raise
            last_error = exc
            for delay in _LIVE_TRACE_RETRY_DELAYS_SECONDS:
                time.sleep(delay)
                try:
                    return query()
                except Exception as retry_error:  # noqa: BLE001
                    if not cls._is_not_found_error(retry_error):
                        raise
                    last_error = retry_error
            raise last_error

    @classmethod
    def _query_observations_with_retry(
        cls,
        query_client: Any,
        trace_id: str,
        *,
        retry_eventual_consistency: bool,
    ) -> list[Any]:
        response = cls._query_with_retry(
            lambda: query_client.observations.get_many(trace_id=trace_id),
            retry_eventual_consistency=retry_eventual_consistency,
        )
        observations = list(cls._as_sequence(cls._lookup(response, "data")))
        if observations or not retry_eventual_consistency:
            return observations

        for delay in _LIVE_TRACE_RETRY_DELAYS_SECONDS:
            time.sleep(delay)
            response = cls._query_with_retry(
                lambda: query_client.observations.get_many(trace_id=trace_id),
                retry_eventual_consistency=False,
            )
            observations = list(cls._as_sequence(cls._lookup(response, "data")))
            if observations:
                return observations
        return observations

    @staticmethod
    def _is_recent_trace(task_trace: TaskTrace) -> bool:
        return datetime.now(UTC) - task_trace.updated_at <= _LIVE_TRACE_RECENCY_WINDOW

    @staticmethod
    def _is_not_found_error(exc: Exception) -> bool:
        return (
            getattr(exc, "status_code", None) == 404
            or type(exc).__name__ in {"NotFoundError", "LangfuseNotFoundError"}
        )

    def _normalize_live_trace(
        self,
        *,
        task_trace: TaskTrace,
        live_trace: Any,
        observations: list[Any],
        fallback_logs: list[TraceLogEntry],
    ) -> StructuredTraceTimeline:
        flat_nodes = [
            self._normalize_observation(observation, index=index)
            for index, observation in enumerate(observations)
        ]
        flat_nodes.sort(
            key=lambda item: (
                item.started_at or datetime.max.replace(tzinfo=UTC),
                item.observation_id,
            )
        )
        root = self._build_tree(
            trace_id=task_trace.trace_id,
            live_trace=live_trace,
            observations=flat_nodes,
        )
        return StructuredTraceTimeline(
            trace_id=task_trace.trace_id,
            task_id=task_trace.task_id,
            provider="langfuse",
            source=StructuredTraceSource.LANGFUSE_LIVE,
            trace_url=self._observability_provider.get_trace_url(task_trace.trace_id),
            status=task_trace.status,
            synced_at=datetime.now(UTC),
            cache_artifact_id=None,
            sync_error=None,
            root=root,
            observations=[
                item.model_copy(update={"children": []}, deep=True)
                for item in flat_nodes
            ],
            metrics=self._build_live_metrics(live_trace, flat_nodes),
            fallback_logs=fallback_logs,
        )

    def _normalize_observation(
        self,
        observation: Any,
        *,
        index: int,
    ) -> TraceObservationNode:
        observation_id = str(self._lookup(observation, "id", "") or f"observation_{index}").strip()
        parent_observation_id = self._optional_str(
            self._lookup(observation, "parent_observation_id")
        )
        started_at = self._parse_datetime(self._lookup(observation, "start_time"))
        ended_at = self._parse_datetime(self._lookup(observation, "end_time"))
        usage_details = self._coerce_mapping(self._lookup(observation, "usage_details"))
        usage = self._coerce_mapping(self._lookup(observation, "usage"))
        input_tokens = self._extract_token_count(usage_details, usage, "input")
        output_tokens = self._extract_token_count(usage_details, usage, "output")
        total_tokens = self._extract_total_tokens(usage_details, usage, input_tokens, output_tokens)
        cost_details = self._coerce_mapping(self._lookup(observation, "cost_details"))

        return TraceObservationNode(
            observation_id=observation_id,
            parent_observation_id=parent_observation_id,
            name=self._optional_str(self._lookup(observation, "name")) or observation_id,
            type=self._normalize_label(self._lookup(observation, "type")) or "observation",
            level=self._normalize_label(self._lookup(observation, "level")),
            status_message=self._optional_str(self._lookup(observation, "status_message")),
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=self._duration_ms(started_at, ended_at),
            model=self._optional_str(self._lookup(observation, "model")),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            total_cost=self._extract_total_cost(cost_details, observation),
            input=self._to_jsonable(self._lookup(observation, "input")),
            output=self._to_jsonable(self._lookup(observation, "output")),
            metadata=self._coerce_mapping(self._lookup(observation, "metadata")),
            children=[],
        )

    def _build_tree(
        self,
        *,
        trace_id: str,
        live_trace: Any,
        observations: list[TraceObservationNode],
    ) -> TraceObservationNode | None:
        if not observations:
            return self._build_synthetic_root(trace_id=trace_id, live_trace=live_trace, children=[])

        nodes_by_id = {
            item.observation_id: item.model_copy(deep=True)
            for item in observations
        }
        top_level_ids: list[str] = []
        for item in observations:
            node = nodes_by_id[item.observation_id]
            parent_id = item.parent_observation_id
            if parent_id and parent_id in nodes_by_id and parent_id != item.observation_id:
                nodes_by_id[parent_id].children.append(node)
                continue
            top_level_ids.append(item.observation_id)

        for item in nodes_by_id.values():
            item.children.sort(
                key=lambda child: (
                    child.started_at or datetime.max.replace(tzinfo=UTC),
                    child.observation_id,
                )
            )

        root_candidate = next(
            (
                nodes_by_id[item_id]
                for item_id in top_level_ids
                if nodes_by_id[item_id].name == "weekly_report.workflow"
            ),
            None,
        )
        if root_candidate is not None:
            extra_top_level = [
                nodes_by_id[item_id]
                for item_id in top_level_ids
                if item_id != root_candidate.observation_id
            ]
            root_candidate.children.extend(extra_top_level)
            root_candidate.children.sort(
                key=lambda child: (
                    child.started_at or datetime.max.replace(tzinfo=UTC),
                    child.observation_id,
                )
            )
            return root_candidate

        return self._build_synthetic_root(
            trace_id=trace_id,
            live_trace=live_trace,
            children=[nodes_by_id[item_id] for item_id in top_level_ids],
        )

    def _build_live_metrics(
        self,
        live_trace: Any,
        observations: list[TraceObservationNode],
    ) -> TraceMetricsSummary:
        input_token_values = [item.input_tokens for item in observations if item.input_tokens is not None]
        output_token_values = [item.output_tokens for item in observations if item.output_tokens is not None]
        total_token_values = [item.total_tokens for item in observations if item.total_tokens is not None]
        total_cost_values = [item.total_cost for item in observations if item.total_cost is not None]
        duration_ms = self._coerce_float(self._lookup(live_trace, "latency"))
        if duration_ms is None:
            duration_ms = self._duration_ms(
                min((item.started_at for item in observations if item.started_at is not None), default=None),
                max((item.ended_at for item in observations if item.ended_at is not None), default=None),
            )

        trace_total_cost = self._coerce_float(self._lookup(live_trace, "total_cost"))
        return TraceMetricsSummary(
            observation_count=len(observations),
            error_count=sum(1 for item in observations if item.level == "error"),
            warning_count=sum(1 for item in observations if item.level == "warning"),
            duration_ms=duration_ms,
            input_tokens=sum(input_token_values) if input_token_values else None,
            output_tokens=sum(output_token_values) if output_token_values else None,
            total_tokens=sum(total_token_values) if total_token_values else None,
            total_cost=trace_total_cost if trace_total_cost is not None else (sum(total_cost_values) if total_cost_values else None),
        )

    def _build_local_fallback(
        self,
        task_trace: TaskTrace,
        fallback_logs: list[TraceLogEntry],
        *,
        sync_error: str | None,
    ) -> StructuredTraceTimeline:
        fallback_nodes = [
            TraceObservationNode(
                observation_id=f"log_{item.log_id}",
                parent_observation_id=task_trace.trace_id,
                name=item.event_type,
                type="log",
                level=item.level.value,
                status_message=item.message,
                started_at=item.timestamp,
                ended_at=item.timestamp,
                duration_ms=0.0,
                model=None,
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
                total_cost=None,
                input=(
                    item.extra
                    if item.event_type.endswith("_started") or item.event_type == "workflow_started"
                    else None
                ),
                output=(
                    item.extra
                    if item.event_type.endswith("_completed")
                    or item.event_type.endswith("_failed")
                    or item.event_type.startswith("workflow_")
                    else None
                ),
                metadata={
                    "stage": item.stage,
                    "artifact_id": item.artifact_id,
                    "report_version_id": item.report_version_id,
                    "issue_id": item.issue_id,
                    **item.extra,
                },
                children=[],
            )
            for item in fallback_logs
        ]
        root = TraceObservationNode(
            observation_id=task_trace.trace_id,
            parent_observation_id=None,
            name="weekly_report.workflow",
            type="trace",
            level="error" if task_trace.status == TraceStatus.FAILED else None,
            status_message=task_trace.error_message,
            started_at=task_trace.started_at,
            ended_at=task_trace.ended_at,
            duration_ms=self._duration_ms(task_trace.started_at, task_trace.ended_at),
            model=None,
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            total_cost=None,
            metadata={
                "task_id": task_trace.task_id,
                "current_stage": task_trace.current_stage,
                "summary": task_trace.summary,
            },
            children=[item.model_copy(deep=True) for item in fallback_nodes],
        )
        return StructuredTraceTimeline(
            trace_id=task_trace.trace_id,
            task_id=task_trace.task_id,
            provider="local_fallback",
            source=StructuredTraceSource.LOCAL_FALLBACK,
            trace_url=self._observability_provider.get_trace_url(task_trace.trace_id),
            status=task_trace.status,
            synced_at=None,
            cache_artifact_id=None,
            sync_error=sync_error,
            root=root,
            observations=fallback_nodes,
            metrics=TraceMetricsSummary(
                observation_count=len(fallback_nodes),
                error_count=sum(1 for item in fallback_nodes if item.level == "error"),
                warning_count=sum(1 for item in fallback_nodes if item.level == "warning"),
                duration_ms=self._duration_ms(task_trace.started_at, task_trace.ended_at),
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
                total_cost=None,
            ),
            fallback_logs=fallback_logs,
        )

    def _build_synthetic_root(
        self,
        *,
        trace_id: str,
        live_trace: Any,
        children: list[TraceObservationNode],
    ) -> TraceObservationNode:
        started_at = min((item.started_at for item in children if item.started_at is not None), default=None)
        ended_at = max((item.ended_at for item in children if item.ended_at is not None), default=None)
        trace_started_at = self._parse_datetime(self._lookup(live_trace, "timestamp"))
        duration_ms = self._coerce_float(self._lookup(live_trace, "latency"))
        if duration_ms is None:
            duration_ms = self._duration_ms(started_at or trace_started_at, ended_at)
        return TraceObservationNode(
            observation_id=f"synthetic_root_{trace_id}",
            parent_observation_id=None,
            name="weekly_report.workflow",
            type="trace",
            level=None,
            status_message=None,
            started_at=started_at or trace_started_at,
            ended_at=ended_at,
            duration_ms=duration_ms,
            model=None,
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            total_cost=self._coerce_float(self._lookup(live_trace, "total_cost")),
            metadata=self._coerce_mapping(self._lookup(live_trace, "metadata")),
            children=children,
        )

    def _save_trace_cache(
        self,
        task_trace: TaskTrace,
        timeline: StructuredTraceTimeline,
    ):
        return self._artifact_service.save_json(
            task_id=task_trace.task_id,
            kind=ArtifactKind.TRACE_EXPORT,
            filename=f"langfuse_trace_{task_trace.trace_id}.json",
            payload=timeline.model_dump(mode="json"),
            storage_group=StorageGroup.DERIVED,
            metadata={
                "trace_id": task_trace.trace_id,
                "provider": "langfuse",
                "synced_at": timeline.synced_at.isoformat() if timeline.synced_at is not None else None,
            },
        )

    def _load_cached_trace(self, task_trace: TaskTrace) -> StructuredTraceTimeline | None:
        candidate: dict[str, Any] | None = None
        for artifact in self._artifact_service.list_task_artifacts(task_trace.task_id):
            metadata = artifact.get("metadata", {})
            if artifact.get("kind") != ArtifactKind.TRACE_EXPORT.value:
                continue
            if not isinstance(metadata, dict):
                continue
            if metadata.get("trace_id") != task_trace.trace_id:
                continue
            if metadata.get("provider") != "langfuse":
                continue
            if candidate is None or str(artifact.get("created_at", "")) > str(candidate.get("created_at", "")):
                candidate = artifact
        if candidate is None:
            return None
        payload = self._artifact_service.load_json_artifact(str(candidate["artifact_id"]))
        timeline = StructuredTraceTimeline.model_validate(payload)
        return timeline.model_copy(
            update={
                "source": StructuredTraceSource.LANGFUSE_CACHE,
                "cache_artifact_id": str(candidate["artifact_id"]),
                "trace_url": self._observability_provider.get_trace_url(task_trace.trace_id),
                "status": task_trace.status,
            }
        )

    @staticmethod
    def _build_summary(timeline: StructuredTraceTimeline) -> StructuredTraceSummary:
        return StructuredTraceSummary(
            trace_id=timeline.trace_id,
            task_id=timeline.task_id,
            provider=timeline.provider,
            source=timeline.source,
            trace_url=timeline.trace_url,
            status=timeline.status,
            synced_at=timeline.synced_at,
            cache_artifact_id=timeline.cache_artifact_id,
            observation_count=timeline.metrics.observation_count,
            error_count=timeline.metrics.error_count,
            warning_count=timeline.metrics.warning_count,
        )

    @staticmethod
    def _lookup(payload: Any, key: str, default: Any = None) -> Any:
        if payload is None:
            return default
        if isinstance(payload, dict):
            return payload.get(key, default)
        return getattr(payload, key, default)

    @staticmethod
    def _as_sequence(value: Any) -> Iterable[Any]:
        if value is None:
            return ()
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return value
        return (value,)

    @classmethod
    def _coerce_mapping(cls, payload: Any) -> dict[str, Any]:
        if payload is None:
            return {}
        if isinstance(payload, dict):
            return {str(key): cls._to_jsonable(value) for key, value in payload.items()}
        model_dump = getattr(payload, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump(mode="json")
            if isinstance(dumped, dict):
                return {str(key): cls._to_jsonable(value) for key, value in dumped.items()}
        return {}

    @classmethod
    def _to_jsonable(cls, payload: Any) -> Any:
        if payload is None or isinstance(payload, (bool, int, float, str)):
            return payload
        if isinstance(payload, datetime):
            return payload.isoformat()
        if isinstance(payload, Path):
            return str(payload)
        if isinstance(payload, dict):
            return {str(key): cls._to_jsonable(value) for key, value in payload.items()}
        if isinstance(payload, (list, tuple, set)):
            return [cls._to_jsonable(value) for value in payload]
        model_dump = getattr(payload, "model_dump", None)
        if callable(model_dump):
            return cls._to_jsonable(model_dump(mode="json"))
        return str(payload)

    @staticmethod
    def _optional_str(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def _normalize_label(cls, value: Any) -> str | None:
        normalized = cls._optional_str(value)
        return normalized.lower() if normalized is not None else None

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        if isinstance(value, str):
            normalized = value.replace("Z", "+00:00")
            return datetime.fromisoformat(normalized)
        return None

    @staticmethod
    def _duration_ms(started_at: datetime | None, ended_at: datetime | None) -> float | None:
        if started_at is None or ended_at is None:
            return None
        return max((ended_at - started_at).total_seconds() * 1000.0, 0.0)

    @staticmethod
    def _coerce_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _extract_token_count(
        self,
        usage_details: dict[str, Any],
        usage: dict[str, Any],
        direction: str,
    ) -> int | None:
        candidates = []
        if direction == "input":
            candidates = ["input", "input_tokens", "prompt", "prompt_tokens"]
        if direction == "output":
            candidates = ["output", "output_tokens", "completion", "completion_tokens"]
        for key in candidates:
            value = self._coerce_int(usage_details.get(key))
            if value is not None:
                return value
        for key in candidates:
            value = self._coerce_int(usage.get(key))
            if value is not None:
                return value
        return None

    def _extract_total_tokens(
        self,
        usage_details: dict[str, Any],
        usage: dict[str, Any],
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> int | None:
        for key in ("total", "total_tokens"):
            value = self._coerce_int(usage_details.get(key))
            if value is not None:
                return value
        for key in ("total", "total_tokens"):
            value = self._coerce_int(usage.get(key))
            if value is not None:
                return value
        if input_tokens is None and output_tokens is None:
            return None
        return (input_tokens or 0) + (output_tokens or 0)

    def _extract_total_cost(self, cost_details: dict[str, Any], observation: Any) -> float | None:
        for key in ("total", "total_cost"):
            value = self._coerce_float(cost_details.get(key))
            if value is not None:
                return value
        return self._coerce_float(self._lookup(observation, "total_cost"))

    @staticmethod
    def _format_sync_error(exc: Exception) -> str:
        return f"{type(exc).__name__}: {exc}"
