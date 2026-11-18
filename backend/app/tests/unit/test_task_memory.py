from __future__ import annotations

import json
from pathlib import Path

from app.db.models import TaskIndex
from app.db.session import create_sqlite_session_factory
from app.memory.task_memory import TaskMemory
from app.schemas.task import TaskStatus, TaskType
from app.services.artifact_service import ArtifactService
from app.workflows.graph_state import TaskState, WorkflowNode, WorkflowWaitReason


def test_task_memory_loads_latest_snapshot_and_keeps_history(tmp_path: Path) -> None:
    service = ArtifactService(
        artifact_root=tmp_path / "artifacts",
        sqlite_path=tmp_path / "sqlite" / "weekly_report.db",
    )
    task_id = service.create_task("Memory flow", TaskType.WEEKLY_REPORT)
    memory = TaskMemory(service.sqlite_path)

    first = TaskState(
        task_id=task_id,
        thread_id="thread-001",
        run_id="run-001",
        task_status=TaskStatus.WAITING,
        waiting_reason=WorkflowWaitReason.EVIDENCE,
        last_completed_node=WorkflowNode.WAIT_FOR_EVIDENCE,
    )
    second = first.model_copy(
        update={
            "task_status": TaskStatus.COMPLETED,
            "waiting_reason": None,
            "last_completed_node": WorkflowNode.MARK_COMPLETED,
        }
    )

    memory.save_snapshot(task_id, first)
    memory.save_snapshot(task_id, second)

    latest = memory.load_latest_state(task_id)
    snapshots = memory.list_snapshots(task_id)

    assert latest is not None
    assert latest.task_status == TaskStatus.COMPLETED
    assert len(snapshots) == 2
    assert snapshots[-1]["is_latest"] is True
    assert service.get_task_index(task_id)["status"] == TaskStatus.COMPLETED.value


def test_task_memory_falls_back_to_legacy_metadata_only_when_snapshot_missing(tmp_path: Path) -> None:
    service = ArtifactService(
        artifact_root=tmp_path / "artifacts",
        sqlite_path=tmp_path / "sqlite" / "weekly_report.db",
    )
    task_id = service.create_task("Legacy memory", TaskType.WEEKLY_REPORT)
    memory = TaskMemory(service.sqlite_path)
    legacy_state = {
        "task_id": task_id,
        "thread_id": "thread-legacy",
        "run_id": "run-legacy",
        "task_status": "waiting",
        "waiting_reason": "human",
        "last_completed_node": "wait_for_human",
    }
    session_factory = create_sqlite_session_factory(service.sqlite_path)
    with session_factory() as session:
        task = session.get(TaskIndex, task_id)
        assert task is not None
        task.metadata_json = json.dumps({"workflow_state": legacy_state}, ensure_ascii=False)
        session.commit()

    loaded = memory.load_latest_state(task_id)

    assert loaded is not None
    assert loaded.thread_id == "thread-legacy"
    assert loaded.task_status == TaskStatus.WAITING
    assert loaded.waiting_reason == WorkflowWaitReason.HUMAN

    snapshot_state = TaskState(
        task_id=task_id,
        thread_id="thread-snapshot",
        run_id="run-snapshot",
        task_status=TaskStatus.COMPLETED,
        waiting_reason=None,
        last_completed_node=WorkflowNode.MARK_COMPLETED,
    )
    memory.save_snapshot(task_id, snapshot_state)

    loaded_after_snapshot = memory.load_latest_state(task_id)

    assert loaded_after_snapshot is not None
    assert loaded_after_snapshot.thread_id == "thread-snapshot"
    assert loaded_after_snapshot.task_status == TaskStatus.COMPLETED
