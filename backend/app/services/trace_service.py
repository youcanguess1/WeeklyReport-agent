from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from app.db.models import TaskIndex, TaskTraceIndex, TraceLogIndex
from app.db.session import create_sqlite_session_factory
from app.schemas.trace import TaskTrace, TraceLogEntry, TraceLogLevel, TraceStatus, TraceTimeline


class TraceService:
    def __init__(self, sqlite_path: Path) -> None:
        self.sqlite_path = sqlite_path.resolve()
        self.session_factory = create_sqlite_session_factory(self.sqlite_path)

    def start_task_trace(
        self,
        task_id: str,
        current_stage: str,
        summary: str | None = None,
        trace_id: str | None = None,
    ) -> TaskTrace:
        now = self._iso_now()
        trace_id = trace_id or self.generate_trace_id()

        with self.session_factory() as session:
            task = self._require_task(session, task_id)
            existing = session.scalar(
                select(TaskTraceIndex).where(TaskTraceIndex.task_id == task_id)
            )

            if existing is None:
                row = TaskTraceIndex(
                    trace_id=trace_id,
                    task_id=task.task_id,
                    task_name=task.task_name,
                    task_type=task.task_type,
                    status=TraceStatus.RUNNING.value,
                    current_stage=current_stage,
                    started_at=now,
                    ended_at=None,
                    updated_at=now,
                    summary=summary,
                    error_message=None,
                )
                session.add(row)
            else:
                if not self.is_valid_trace_id(existing.trace_id):
                    migrated_trace_id = self.generate_trace_id(seed=existing.trace_id)
                    self._rekey_trace(session, existing.trace_id, migrated_trace_id)
                    existing.trace_id = migrated_trace_id
                existing.task_name = task.task_name
                existing.task_type = task.task_type
                existing.status = TraceStatus.RUNNING.value
                existing.current_stage = current_stage
                existing.updated_at = now
                if summary is not None:
                    existing.summary = summary
                row = existing

            session.commit()
            session.refresh(row)
            return self._to_task_trace(row)

    def update_task_trace(
        self,
        trace_id: str,
        status: TraceStatus | None = None,
        current_stage: str | None = None,
        summary: str | None = None,
        error_message: str | None = None,
    ) -> TaskTrace:
        now = self._iso_now()
        with self.session_factory() as session:
            row = self._require_trace(session, trace_id)
            if status is not None:
                row.status = status.value
                if status in {TraceStatus.COMPLETED, TraceStatus.FAILED, TraceStatus.CANCELED}:
                    row.ended_at = now
            if current_stage is not None:
                row.current_stage = current_stage
            if summary is not None:
                row.summary = summary
            if error_message is not None:
                row.error_message = error_message
            row.updated_at = now
            session.commit()
            session.refresh(row)
            return self._to_task_trace(row)

    def append_log(
        self,
        trace_id: str,
        level: TraceLogLevel,
        stage: str,
        event_type: str,
        message: str,
        artifact_id: str | None = None,
        report_version_id: str | None = None,
        issue_id: str | None = None,
        extra: dict[str, Any] | None = None,
        log_id: str | None = None,
    ) -> TraceLogEntry:
        log_id = log_id or self.generate_log_id()
        timestamp = self._iso_now()
        with self.session_factory() as session:
            trace = self._require_trace(session, trace_id)
            row = TraceLogIndex(
                log_id=log_id,
                trace_id=trace.trace_id,
                task_id=trace.task_id,
                timestamp=timestamp,
                level=level.value,
                stage=stage,
                event_type=event_type,
                message=message,
                artifact_id=artifact_id,
                report_version_id=report_version_id,
                issue_id=issue_id,
                extra_json=self._json_dump(extra or {}),
            )
            session.add(row)
            trace.updated_at = timestamp
            trace.current_stage = stage
            session.commit()
            session.refresh(row)
            return self._to_trace_log(row)

    def get_trace_by_task_id(self, task_id: str) -> TaskTrace:
        with self.session_factory() as session:
            row = session.scalar(select(TaskTraceIndex).where(TaskTraceIndex.task_id == task_id))
            if row is None:
                raise KeyError(f"No trace found for task id: {task_id}")
            return self._to_task_trace(row)

    def get_trace(self, trace_id: str) -> TaskTrace:
        with self.session_factory() as session:
            row = self._require_trace(session, trace_id)
            return self._to_task_trace(row)

    def get_trace_timeline(self, task_id: str) -> TraceTimeline:
        with self.session_factory() as session:
            trace = session.scalar(select(TaskTraceIndex).where(TaskTraceIndex.task_id == task_id))
            if trace is None:
                raise KeyError(f"No trace found for task id: {task_id}")
            logs = session.scalars(
                select(TraceLogIndex)
                .where(TraceLogIndex.trace_id == trace.trace_id)
                .order_by(TraceLogIndex.timestamp.asc(), TraceLogIndex.log_id.asc())
            ).all()
        return TraceTimeline(
            trace=self._to_task_trace(trace),
            logs=[self._to_trace_log(item) for item in logs],
        )

    def list_logs(self, trace_id: str) -> list[TraceLogEntry]:
        with self.session_factory() as session:
            self._require_trace(session, trace_id)
            rows = session.scalars(
                select(TraceLogIndex)
                .where(TraceLogIndex.trace_id == trace_id)
                .order_by(TraceLogIndex.timestamp.asc(), TraceLogIndex.log_id.asc())
            ).all()
        return [self._to_trace_log(item) for item in rows]

    @staticmethod
    def generate_trace_id(seed: str | None = None) -> str:
        normalized = (seed or "").strip()
        if not normalized:
            return uuid4().hex
        return hashlib.md5(normalized.encode("utf-8"), usedforsecurity=False).hexdigest()

    @staticmethod
    def generate_log_id() -> str:
        return f"log_{uuid4().hex[:16]}"

    @staticmethod
    def is_valid_trace_id(trace_id: str) -> bool:
        return bool(re.fullmatch(r"[0-9a-f]{32}", trace_id))

    @staticmethod
    def _iso_now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _json_dump(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _json_load(payload: str) -> dict[str, Any]:
        return json.loads(payload)

    @staticmethod
    def _to_task_trace(row: TaskTraceIndex) -> TaskTrace:
        return TaskTrace(
            trace_id=row.trace_id,
            task_id=row.task_id,
            task_name=row.task_name,
            task_type=row.task_type,
            status=TraceStatus(row.status),
            current_stage=row.current_stage,
            started_at=datetime.fromisoformat(row.started_at),
            ended_at=datetime.fromisoformat(row.ended_at) if row.ended_at else None,
            updated_at=datetime.fromisoformat(row.updated_at),
            summary=row.summary,
            error_message=row.error_message,
        )

    def _to_trace_log(self, row: TraceLogIndex) -> TraceLogEntry:
        return TraceLogEntry(
            log_id=row.log_id,
            trace_id=row.trace_id,
            task_id=row.task_id,
            timestamp=datetime.fromisoformat(row.timestamp),
            level=TraceLogLevel(row.level),
            stage=row.stage,
            event_type=row.event_type,
            message=row.message,
            artifact_id=row.artifact_id,
            report_version_id=row.report_version_id,
            issue_id=row.issue_id,
            extra=self._json_load(row.extra_json),
        )

    @staticmethod
    def _require_task(session: Any, task_id: str) -> TaskIndex:
        task = session.get(TaskIndex, task_id)
        if task is None:
            raise KeyError(f"Unknown task id: {task_id}")
        return task

    @staticmethod
    def _require_trace(session: Any, trace_id: str) -> TaskTraceIndex:
        trace = session.get(TaskTraceIndex, trace_id)
        if trace is None:
            raise KeyError(f"Unknown trace id: {trace_id}")
        return trace

    @staticmethod
    def _rekey_trace(session: Any, previous_trace_id: str, next_trace_id: str) -> None:
        log_rows = session.scalars(
            select(TraceLogIndex).where(TraceLogIndex.trace_id == previous_trace_id)
        ).all()
        for row in log_rows:
            row.trace_id = next_trace_id
