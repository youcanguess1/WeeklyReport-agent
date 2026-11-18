from __future__ import annotations

import json
from datetime import UTC, datetime
from threading import RLock
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import TaskOperationIndex
from app.schemas.task_operation import (
    TaskOperationResponse,
    TaskOperationStatus,
    TaskOperationType,
)


class ActiveTaskOperationError(Exception):
    def __init__(self, operation: TaskOperationResponse) -> None:
        self.operation = operation
        super().__init__(f"Task already has active operation {operation.operation_id}.")


class TaskOperationService:
    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory
        self._locks: dict[str, RLock] = {}
        self._locks_guard = RLock()

    def create_or_get(
        self,
        *,
        task_id: str,
        request_id: str,
        operation_type: TaskOperationType,
        input_data: dict[str, Any] | None = None,
        previous_state: dict[str, Any] | None = None,
    ) -> tuple[TaskOperationResponse, bool]:
        with self._lock_for(task_id):
            with self.session_factory() as session:
                existing = session.scalar(
                    select(TaskOperationIndex).where(
                        TaskOperationIndex.task_id == task_id,
                        TaskOperationIndex.request_id == request_id,
                    )
                )
                if existing is not None:
                    return self._to_response(existing), False
                active = session.scalar(
                    select(TaskOperationIndex)
                    .where(
                        TaskOperationIndex.task_id == task_id,
                        TaskOperationIndex.status.in_(
                            [
                                TaskOperationStatus.QUEUED.value,
                                TaskOperationStatus.PROCESSING.value,
                            ]
                        ),
                    )
                    .order_by(TaskOperationIndex.created_at.desc())
                )
                if active is not None:
                    raise ActiveTaskOperationError(self._to_response(active))
                now = _iso_now()
                row = TaskOperationIndex(
                    operation_id=f"op_{uuid4().hex[:16]}",
                    task_id=task_id,
                    request_id=request_id,
                    operation_type=operation_type.value,
                    status=TaskOperationStatus.QUEUED.value,
                    stage="accepted",
                    input_json=json.dumps(input_data or {}, ensure_ascii=False),
                    result_json=None,
                    previous_state_json=(
                        json.dumps(previous_state, ensure_ascii=False)
                        if previous_state is not None
                        else None
                    ),
                    error_message=None,
                    created_at=now,
                    started_at=None,
                    ended_at=None,
                    updated_at=now,
                )
                session.add(row)
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    existing = session.scalar(
                        select(TaskOperationIndex).where(
                            TaskOperationIndex.task_id == task_id,
                            TaskOperationIndex.request_id == request_id,
                        )
                    )
                    if existing is not None:
                        return self._to_response(existing), False
                    active = session.scalar(
                        select(TaskOperationIndex).where(
                            TaskOperationIndex.task_id == task_id,
                            TaskOperationIndex.status.in_(
                                [
                                    TaskOperationStatus.QUEUED.value,
                                    TaskOperationStatus.PROCESSING.value,
                                ]
                            ),
                        )
                    )
                    if active is not None:
                        raise ActiveTaskOperationError(self._to_response(active))
                    raise
                return self._to_response(row), True

    def get(self, operation_id: str) -> TaskOperationResponse:
        with self.session_factory() as session:
            row = session.get(TaskOperationIndex, operation_id)
            if row is None:
                raise KeyError(operation_id)
            return self._to_response(row)

    def get_by_request(self, task_id: str, request_id: str) -> TaskOperationResponse | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(TaskOperationIndex).where(
                    TaskOperationIndex.task_id == task_id,
                    TaskOperationIndex.request_id == request_id,
                )
            )
            return self._to_response(row) if row is not None else None

    def get_input(self, operation_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            row = session.get(TaskOperationIndex, operation_id)
            if row is None:
                raise KeyError(operation_id)
            value = json.loads(row.input_json or "{}")
            return value if isinstance(value, dict) else {}

    def update_input(self, operation_id: str, input_data: dict[str, Any]) -> TaskOperationResponse:
        return self._update(
            operation_id,
            input_json=json.dumps(input_data, ensure_ascii=False),
        )

    def get_previous_state(self, operation_id: str) -> dict[str, Any] | None:
        with self.session_factory() as session:
            row = session.get(TaskOperationIndex, operation_id)
            if row is None or not row.previous_state_json:
                return None
            value = json.loads(row.previous_state_json)
            return value if isinstance(value, dict) else None

    def list_for_task(self, task_id: str) -> list[TaskOperationResponse]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(TaskOperationIndex)
                .where(TaskOperationIndex.task_id == task_id)
                .order_by(TaskOperationIndex.created_at.asc())
            ).all()
            return [self._to_response(row) for row in rows]

    def mark_processing(self, operation_id: str, *, stage: str) -> TaskOperationResponse:
        return self._update(
            operation_id,
            status=TaskOperationStatus.PROCESSING,
            stage=stage,
            started_at=_iso_now(),
        )

    def update_stage(self, operation_id: str, *, stage: str) -> TaskOperationResponse:
        return self._update(operation_id, stage=stage)

    def mark_completed(
        self,
        operation_id: str,
        *,
        stage: str,
        result: dict[str, Any] | None = None,
    ) -> TaskOperationResponse:
        return self._update(
            operation_id,
            status=TaskOperationStatus.COMPLETED,
            stage=stage,
            result=result,
            ended_at=_iso_now(),
        )

    def mark_failed(
        self,
        operation_id: str,
        *,
        stage: str,
        error_message: str,
    ) -> TaskOperationResponse:
        return self._update(
            operation_id,
            status=TaskOperationStatus.FAILED,
            stage=stage,
            error_message=error_message,
            ended_at=_iso_now(),
        )

    def mark_interrupted(self) -> list[TaskOperationResponse]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(TaskOperationIndex).where(
                    TaskOperationIndex.status.in_(
                        [
                            TaskOperationStatus.QUEUED.value,
                            TaskOperationStatus.PROCESSING.value,
                        ]
                    )
                )
            ).all()
            now = _iso_now()
            for row in rows:
                row.status = TaskOperationStatus.FAILED.value
                row.stage = "interrupted"
                row.error_message = "Operation interrupted by service restart."
                row.ended_at = now
                row.updated_at = now
            session.commit()
            return [self._to_response(row) for row in rows]

    def _update(self, operation_id: str, **values: Any) -> TaskOperationResponse:
        with self.session_factory() as session:
            row = session.get(TaskOperationIndex, operation_id)
            if row is None:
                raise KeyError(operation_id)
            for key, value in values.items():
                if key == "status":
                    value = value.value
                elif key == "result":
                    key = "result_json"
                    value = json.dumps(value or {}, ensure_ascii=False)
                if hasattr(row, key):
                    setattr(row, key, value)
            row.updated_at = _iso_now()
            session.commit()
            return self._to_response(row)

    def _lock_for(self, task_id: str) -> RLock:
        with self._locks_guard:
            return self._locks.setdefault(task_id, RLock())

    @staticmethod
    def _to_response(row: TaskOperationIndex) -> TaskOperationResponse:
        return TaskOperationResponse(
            operation_id=row.operation_id,
            task_id=row.task_id,
            request_id=row.request_id,
            operation_type=TaskOperationType(row.operation_type),
            status=TaskOperationStatus(row.status),
            stage=row.stage,
            error_message=row.error_message,
            result=json.loads(row.result_json) if row.result_json else None,
            created_at=datetime.fromisoformat(row.created_at),
            started_at=(datetime.fromisoformat(row.started_at) if row.started_at else None),
            ended_at=(datetime.fromisoformat(row.ended_at) if row.ended_at else None),
            updated_at=datetime.fromisoformat(row.updated_at),
        )


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()
