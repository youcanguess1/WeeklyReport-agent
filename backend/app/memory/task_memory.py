from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update

from app.db.models import TaskIndex, TaskStateSnapshotIndex
from app.db.session import create_sqlite_session_factory
from app.schemas.task import TaskStatus


class TaskMemory:
    STATE_SCHEMA_VERSION = "1"

    def __init__(self, sqlite_path: Path) -> None:
        self.sqlite_path = sqlite_path.resolve()
        self.session_factory = create_sqlite_session_factory(self.sqlite_path)

    def save_snapshot(
        self,
        task_id: str,
        state: Any | dict[str, Any],
        status: TaskStatus | None = None,
    ) -> str:
        from app.workflows.graph_state import TaskState

        snapshot = state if isinstance(state, TaskState) else TaskState.model_validate(state)
        snapshot_id = f"snapshot_{uuid4().hex[:16]}"
        now = self._iso_now()
        resolved_status = status.value if status is not None else snapshot.task_status.value
        with self.session_factory() as session:
            task = self._require_task(session, task_id)
            session.execute(
                update(TaskStateSnapshotIndex)
                .where(TaskStateSnapshotIndex.task_id == task_id)
                .values(is_latest=False, updated_at=now)
            )
            session.add(
                TaskStateSnapshotIndex(
                    snapshot_id=snapshot_id,
                    task_id=task_id,
                    run_id=snapshot.run_id,
                    thread_id=snapshot.thread_id,
                    status=resolved_status,
                    current_node=(
                        snapshot.last_completed_node.value
                        if snapshot.last_completed_node is not None
                        else None
                    ),
                    waiting_reason=(
                        snapshot.waiting_reason.value
                        if snapshot.waiting_reason is not None
                        else None
                    ),
                    state_schema_version=self.STATE_SCHEMA_VERSION,
                    state_json=self._json_dump(snapshot.model_dump(mode="json")),
                    created_at=now,
                    updated_at=now,
                    is_latest=True,
                )
            )
            task.status = resolved_status
            task.updated_at = now
            session.commit()
        return snapshot_id

    def load_latest_state(self, task_id: str) -> Any | None:
        from app.workflows.graph_state import TaskState

        payload = self.load_latest_state_payload(task_id)
        if payload is not None:
            return TaskState.model_validate(payload)
        with self.session_factory() as session:
            task = self._require_task(session, task_id)
            metadata = self._json_load(task.metadata_json)
        legacy_state = metadata.get("workflow_state")
        if isinstance(legacy_state, dict):
            return TaskState.model_validate(legacy_state)
        return None

    def load_latest_state_payload(self, task_id: str) -> dict[str, Any] | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(TaskStateSnapshotIndex)
                .where(TaskStateSnapshotIndex.task_id == task_id)
                .order_by(
                    TaskStateSnapshotIndex.is_latest.desc(),
                    TaskStateSnapshotIndex.updated_at.desc(),
                    TaskStateSnapshotIndex.created_at.desc(),
                )
            )
        if row is None:
            return None
        payload = self._json_load(row.state_json)
        return payload if isinstance(payload, dict) else None

    def list_snapshots(self, task_id: str) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(TaskStateSnapshotIndex)
                .where(TaskStateSnapshotIndex.task_id == task_id)
                .order_by(TaskStateSnapshotIndex.created_at.asc())
            ).all()
        return [
            {
                "snapshot_id": row.snapshot_id,
                "task_id": row.task_id,
                "run_id": row.run_id,
                "thread_id": row.thread_id,
                "status": row.status,
                "current_node": row.current_node,
                "waiting_reason": row.waiting_reason,
                "state_schema_version": row.state_schema_version,
                "state": self._json_load(row.state_json),
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "is_latest": row.is_latest,
            }
            for row in rows
        ]

    def mark_task_status(self, task_id: str, status: TaskStatus) -> None:
        with self.session_factory() as session:
            task = self._require_task(session, task_id)
            task.status = status.value
            task.updated_at = self._iso_now()
            session.commit()

    @staticmethod
    def _iso_now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _json_dump(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _json_load(payload: str) -> dict[str, Any] | list[Any]:
        return json.loads(payload)

    @staticmethod
    def _require_task(session: Any, task_id: str) -> TaskIndex:
        task = session.get(TaskIndex, task_id)
        if task is None:
            raise KeyError(f"Unknown task id: {task_id}")
        return task
