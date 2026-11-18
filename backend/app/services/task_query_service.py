from __future__ import annotations

import json
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import aliased

from app.db.models import ArtifactIndex, TaskIndex, TaskStateSnapshotIndex
from app.schemas.task import TaskStatus, TaskType
from app.schemas.task_api import TaskBoardSummary, TaskListItem, TaskListResponse
from app.services.artifact_service import ArtifactService


class TaskQueryService:
    def __init__(self, artifact_service: ArtifactService) -> None:
        self.session_factory = artifact_service.session_factory

    def list_tasks(
        self,
        *,
        query: str | None = None,
        status: TaskStatus | None = None,
        task_type: TaskType | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> TaskListResponse:
        filters = self._filters(query=query, status=status, task_type=task_type)
        total, rows = self._query_rows(filters=filters, page=page, page_size=page_size)
        return TaskListResponse(
            items=[self._to_item(task, snapshot, artifact_count) for task, snapshot, artifact_count in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    def get_task(self, task_id: str) -> TaskListItem:
        filters = [TaskIndex.task_id == task_id]
        _, rows = self._query_rows(filters=filters, page=1, page_size=1)
        if not rows:
            raise KeyError(f"Unknown task id: {task_id}")
        task, snapshot, artifact_count = rows[0]
        return self._to_item(task, snapshot, artifact_count)

    def summary(self) -> TaskBoardSummary:
        _, rows = self._query_rows(filters=[], page=1, page_size=None)
        statuses = [snapshot.status if snapshot is not None else task.status for task, snapshot, _ in rows]
        return TaskBoardSummary(
            total_count=len(statuses),
            running_count=sum(status in {TaskStatus.QUEUED.value, TaskStatus.RUNNING.value} for status in statuses),
            pending_count=sum(status in {TaskStatus.CREATED.value, TaskStatus.WAITING.value} for status in statuses),
            completed_count=sum(status == TaskStatus.COMPLETED.value for status in statuses),
        )

    def _query_rows(
        self,
        *,
        filters: list[Any],
        page: int,
        page_size: int | None,
    ) -> tuple[int, list[tuple[TaskIndex, TaskStateSnapshotIndex | None, int]]]:
        latest_snapshot = aliased(TaskStateSnapshotIndex)
        artifact_counts = (
            select(ArtifactIndex.task_id, func.count(ArtifactIndex.artifact_id).label("artifact_count"))
            .group_by(ArtifactIndex.task_id)
            .subquery()
        )
        with self.session_factory() as session:
            total = session.scalar(select(func.count(TaskIndex.task_id)).where(*filters)) or 0
            statement = (
                select(
                    TaskIndex,
                    latest_snapshot,
                    func.coalesce(artifact_counts.c.artifact_count, 0),
                )
                .outerjoin(
                    latest_snapshot,
                    and_(
                        latest_snapshot.task_id == TaskIndex.task_id,
                        latest_snapshot.is_latest.is_(True),
                    ),
                )
                .outerjoin(artifact_counts, artifact_counts.c.task_id == TaskIndex.task_id)
                .where(*filters)
                .order_by(TaskIndex.updated_at.desc(), TaskIndex.task_id.desc())
            )
            if page_size is not None:
                statement = statement.offset((page - 1) * page_size).limit(page_size)
            rows = session.execute(statement).all()
        return total, [(task, snapshot, int(artifact_count)) for task, snapshot, artifact_count in rows]

    @staticmethod
    def _filters(
        *,
        query: str | None,
        status: TaskStatus | None,
        task_type: TaskType | None,
    ) -> list[Any]:
        filters: list[Any] = []
        if query:
            pattern = f"%{query}%"
            filters.append(or_(TaskIndex.task_id.ilike(pattern), TaskIndex.task_name.ilike(pattern)))
        if status is not None:
            filters.append(TaskIndex.status == status.value)
        if task_type is not None:
            filters.append(TaskIndex.task_type == task_type.value)
        return filters

    @staticmethod
    def _to_item(
        task: TaskIndex,
        snapshot: TaskStateSnapshotIndex | None,
        artifact_count: int,
    ) -> TaskListItem:
        metadata = json.loads(task.metadata_json)
        return TaskListItem(
            task_id=task.task_id,
            task_name=task.task_name,
            task_type=TaskType(task.task_type),
            description=str(metadata.get("notes", "")),
            status=TaskStatus(snapshot.status if snapshot is not None else task.status),
            current_node=snapshot.current_node if snapshot is not None else None,
            waiting_reason=snapshot.waiting_reason if snapshot is not None else None,
            current_report_version=task.current_report_version,
            artifact_count=artifact_count,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )
