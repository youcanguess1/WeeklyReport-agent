from pathlib import Path

import pytest

from app.schemas.task import TaskType
from app.schemas.task_operation import TaskOperationStatus, TaskOperationType
from app.services.artifact_service import ArtifactService
from app.services.task_operation_service import ActiveTaskOperationError, TaskOperationService


def test_task_operations_are_idempotent_and_single_active(tmp_path: Path) -> None:
    artifact_service = ArtifactService(tmp_path / "artifacts", tmp_path / "sqlite" / "weekly_report.db")
    task_id = artifact_service.create_task("Operation task", TaskType.WEEKLY_REPORT)
    service = TaskOperationService(artifact_service.session_factory)

    operation, created = service.create_or_get(
        task_id=task_id,
        request_id="request-1",
        operation_type=TaskOperationType.FEEDBACK,
        input_data={"feedback_text": "revise"},
    )
    assert created
    assert operation.status == TaskOperationStatus.QUEUED

    same_operation, same_created = service.create_or_get(
        task_id=task_id,
        request_id="request-1",
        operation_type=TaskOperationType.FEEDBACK,
    )
    assert not same_created
    assert same_operation.operation_id == operation.operation_id

    with pytest.raises(ActiveTaskOperationError):
        service.create_or_get(
            task_id=task_id,
            request_id="request-2",
            operation_type=TaskOperationType.SUPPLEMENT,
        )

    service.mark_completed(operation.operation_id, stage="completed")
    next_operation, next_created = service.create_or_get(
        task_id=task_id,
        request_id="request-2",
        operation_type=TaskOperationType.SUPPLEMENT,
    )
    assert next_created
    assert next_operation.status == TaskOperationStatus.QUEUED
