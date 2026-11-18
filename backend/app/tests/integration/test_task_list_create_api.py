from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes_tasks import router as tasks_router
from app.schemas.evidence import ArtifactKind
from app.schemas.task import TaskStatus, TaskType
from app.services.artifact_service import ArtifactService, StorageGroup
from app.services.task_query_service import TaskQueryService
from app.workflows.graph_state import TaskState, WorkflowNode, WorkflowWaitReason


PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\x0dIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00"
    b"\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _build_app(tmp_path: Path) -> tuple[FastAPI, ArtifactService]:
    artifact_service = ArtifactService(
        artifact_root=tmp_path / "artifacts",
        sqlite_path=tmp_path / "sqlite" / "weekly_report.db",
    )
    app = FastAPI()
    app.state.artifact_service = artifact_service
    app.state.task_query_service = TaskQueryService(artifact_service)
    app.include_router(tasks_router)
    return app, artifact_service


def test_task_board_is_empty_before_creation(tmp_path: Path) -> None:
    app, _artifact_service = _build_app(tmp_path)

    with TestClient(app) as client:
        task_response = client.get("/api/tasks")
        summary_response = client.get("/api/tasks/summary")

    assert task_response.status_code == 200
    assert task_response.json() == {"items": [], "total": 0, "page": 1, "page_size": 50}
    assert summary_response.status_code == 200
    assert summary_response.json() == {
        "total_count": 0,
        "running_count": 0,
        "pending_count": 0,
        "completed_count": 0,
    }


def test_create_task_persists_input_artifact_and_initial_state(tmp_path: Path) -> None:
    app, artifact_service = _build_app(tmp_path)

    with TestClient(app) as client:
        response = client.post(
            "/api/tasks",
            data={
                "task_name": "Printer reload demo",
                "task_type": TaskType.WEEKLY_REPORT.value,
                "template_name": "default.md",
                "notes": "Capture the tray reinsertion step.",
            },
            files=[("files", ("tray.png", PNG_1X1, "image/png"))],
        )

        assert response.status_code == 200
        created = response.json()["task"]
        listed = client.get("/api/tasks").json()["items"]

    assert created["task_id"] == listed[0]["task_id"]
    assert created["task_type"] == TaskType.WEEKLY_REPORT.value
    assert created["status"] == TaskStatus.CREATED.value
    assert created["description"] == "Capture the tray reinsertion step."
    assert created["artifact_count"] == 1
    assert created["current_node"] is None

    state = artifact_service.load_workflow_state(created["task_id"])
    assert state is not None
    assert state["task_status"] == TaskStatus.CREATED.value
    assert state["thread_id"] == f"thread_{created['task_id']}"
    assert len(state["input_artifact_refs"]) == 1
    assert artifact_service.get_task_metadata(created["task_id"]) == {
        "notes": "Capture the tray reinsertion step.",
        "template_name": "default.md",
    }

    reopened = ArtifactService(
        artifact_root=artifact_service.artifact_root,
        sqlite_path=artifact_service.sqlite_path,
    )
    reopened_items = TaskQueryService(reopened).list_tasks().items
    assert reopened_items[0].task_id == created["task_id"]


def test_create_text_only_task_without_uploads(tmp_path: Path) -> None:
    app, artifact_service = _build_app(tmp_path)

    with TestClient(app) as client:
        response = client.post(
            "/api/tasks",
            data={
                "task_name": "Text-only weekly report",
                "task_type": TaskType.WEEKLY_REPORT.value,
                "notes": "Loose notes about this week's project progress.",
            },
        )

    assert response.status_code == 200
    created = response.json()["task"]
    assert created["artifact_count"] == 0
    state = artifact_service.load_workflow_state(created["task_id"])
    assert state is not None
    assert state["input_artifact_refs"] == []


def test_task_list_and_summary_use_latest_snapshot_status(tmp_path: Path) -> None:
    app, artifact_service = _build_app(tmp_path)
    running_id = artifact_service.create_task("Running task", TaskType.WEEKLY_REPORT)
    waiting_id = artifact_service.create_task("Waiting task", TaskType.WEEKLY_REPORT)
    completed_id = artifact_service.create_task("Completed task", TaskType.WEEKLY_REPORT)
    artifact_service.save_workflow_state(
        running_id,
        TaskState(task_id=running_id, thread_id="thread-running", run_id="run-running", task_status=TaskStatus.RUNNING),
        status=TaskStatus.RUNNING,
    )
    artifact_service.save_workflow_state(
        waiting_id,
        TaskState(
            task_id=waiting_id,
            thread_id="thread-waiting",
            run_id="run-waiting",
            task_status=TaskStatus.WAITING,
            waiting_reason=WorkflowWaitReason.EVIDENCE,
            last_completed_node=WorkflowNode.WAIT_FOR_EVIDENCE,
        ),
        status=TaskStatus.WAITING,
    )
    source = artifact_service.save_bytes(
        task_id=completed_id,
        kind=ArtifactKind.IMAGE_MATERIAL,
        filename="source.mp4",
        content=b"video",
        storage_group=StorageGroup.UPLOAD,
    )
    artifact_service.save_workflow_state(
        completed_id,
        TaskState(
            task_id=completed_id,
            thread_id="thread-completed",
            run_id="run-completed",
            task_status=TaskStatus.COMPLETED,
            input_artifact_refs=[source],
        ),
        status=TaskStatus.COMPLETED,
    )

    with TestClient(app) as client:
        response = client.get("/api/tasks", params={"task_type": TaskType.WEEKLY_REPORT.value})
        summary = client.get("/api/tasks/summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert [item["task_name"] for item in payload["items"]] == ["Completed task", "Waiting task", "Running task"]
    waiting = next(item for item in payload["items"] if item["task_id"] == waiting_id)
    assert waiting["status"] == TaskStatus.WAITING.value
    assert waiting["current_node"] == WorkflowNode.WAIT_FOR_EVIDENCE.value
    assert waiting["waiting_reason"] == WorkflowWaitReason.EVIDENCE.value
    assert summary.json() == {
        "total_count": 3,
        "running_count": 1,
        "pending_count": 1,
        "completed_count": 1,
    }


def test_create_task_requires_name_and_notes_but_not_an_input_file(tmp_path: Path) -> None:
    app, _artifact_service = _build_app(tmp_path)

    with TestClient(app) as client:
        missing_file = client.post(
            "/api/tasks",
            data={"task_name": "Missing notes", "task_type": TaskType.WEEKLY_REPORT.value},
        )
        missing_name = client.post(
            "/api/tasks",
            data={"task_type": TaskType.WEEKLY_REPORT.value, "notes": "Some notes"},
            files=[("files", ("tray.png", PNG_1X1, "image/png"))],
        )

    assert missing_file.status_code == 422
    assert missing_name.status_code == 422


def test_task_creation_rejects_video_uploads(tmp_path: Path) -> None:
    app, _artifact_service = _build_app(tmp_path)

    with TestClient(app) as client:
        response = client.post(
            "/api/tasks",
            data={
                "task_name": "Reject video",
                "task_type": TaskType.WEEKLY_REPORT.value,
                "notes": "Weekly progress details.",
            },
            files=[("files", ("capture.mp4", b"video", "video/mp4"))],
        )

    assert response.status_code == 400
