from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.schemas.evidence import ArtifactKind
from app.schemas.report import WeeklyReportDocument
from app.schemas.task import TaskStatus, TaskType
from app.services.artifact_service import ArtifactService, StorageGroup


def test_artifact_service_creates_task_dirs_and_indexes_task(tmp_path: Path) -> None:
    service = ArtifactService(
        artifact_root=tmp_path / "artifacts",
        sqlite_path=tmp_path / "sqlite" / "weekly_report.db",
    )

    task_id = service.create_task(
        task_name="Printer reload demo",
        task_type=TaskType.WEEKLY_REPORT,
        status=TaskStatus.CREATED,
        metadata={"template": "default"},
    )

    task_root = service.task_root(task_id)
    assert not (task_root / "uploads" / "videos").exists()
    assert (task_root / "uploads" / "images").exists()
    assert not (task_root / "derived" / "frames").exists()
    assert (task_root / "derived" / "reports").exists()
    assert (task_root / "derived" / "agent_sessions").exists()

    task_index = service.get_task_index(task_id)
    assert task_index["task_name"] == "Printer reload demo"
    assert task_index["task_type"] == TaskType.WEEKLY_REPORT.value
    assert task_index["status"] == TaskStatus.CREATED.value
    assert task_index["metadata"] == {"template": "default"}


def test_artifact_service_upgrades_existing_tasks_table_with_created_at(tmp_path: Path) -> None:
    sqlite_path = tmp_path / "sqlite" / "weekly_report.db"
    sqlite_path.parent.mkdir(parents=True)
    with sqlite3.connect(sqlite_path) as connection:
        connection.execute(
            """
            CREATE TABLE tasks (
                task_id VARCHAR(64) PRIMARY KEY,
                task_name VARCHAR(200) NOT NULL,
                task_type VARCHAR(64) NOT NULL,
                status VARCHAR(64) NOT NULL,
                current_report_version VARCHAR(32),
                latest_report_artifact_id VARCHAR(64),
                updated_at VARCHAR(40) NOT NULL,
                metadata_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "task-old",
                "Old task",
                TaskType.WEEKLY_REPORT.value,
                TaskStatus.CREATED.value,
                None,
                None,
                "2026-08-08T00:00:00+00:00",
                "{}",
            ),
        )
        connection.commit()

    service = ArtifactService(
        artifact_root=tmp_path / "artifacts",
        sqlite_path=sqlite_path,
    )

    assert service.get_task_index("task-old")["created_at"] == "2026-08-08T00:00:00+00:00"
    new_task_id = service.create_task("New task", TaskType.WEEKLY_REPORT)
    assert service.get_task_index(new_task_id)["created_at"]


def test_artifact_service_saves_upload_and_derived_artifacts(tmp_path: Path) -> None:
    service = ArtifactService(
        artifact_root=tmp_path / "artifacts",
        sqlite_path=tmp_path / "sqlite" / "weekly_report.db",
    )
    task_id = service.create_task(
        task_name="Project status board",
        task_type=TaskType.WEEKLY_REPORT,
    )

    image = service.save_bytes(
        task_id=task_id,
        kind=ArtifactKind.IMAGE_MATERIAL,
        filename="project board.png",
        content=b"image-binary",
        storage_group=StorageGroup.UPLOAD,
        metadata={"segment": 1},
    )
    manifest = service.save_json(
        task_id=task_id,
        kind=ArtifactKind.MANIFEST,
        filename="manifest.json",
        payload={"materials": 1},
        storage_group=StorageGroup.DERIVED,
        parent_artifact_id=image.artifact_id,
    )

    artifacts = service.list_task_artifacts(task_id)

    assert len(artifacts) == 2
    assert any(item["storage_group"] == StorageGroup.UPLOAD.value for item in artifacts)
    assert any(item["storage_group"] == StorageGroup.DERIVED.value for item in artifacts)
    assert Path(image.path).exists()
    assert Path(manifest.path).exists()
    assert "__project_board.png" in image.path
    assert json.loads(Path(manifest.path).read_text(encoding="utf-8")) == {"materials": 1}

    manifest_row = next(item for item in artifacts if item["artifact_id"] == manifest.artifact_id)
    assert manifest_row["parent_artifact_id"] == image.artifact_id
    assert manifest_row["kind"] == ArtifactKind.MANIFEST.value


def test_artifact_service_tracks_report_versions_under_same_task(tmp_path: Path) -> None:
    service = ArtifactService(
        artifact_root=tmp_path / "artifacts",
        sqlite_path=tmp_path / "sqlite" / "weekly_report.db",
    )
    task_id = service.create_task(
        task_name="Printer WeeklyReport",
        task_type=TaskType.WEEKLY_REPORT,
    )

    report_v0 = service.save_text(
        task_id=task_id,
        kind=ArtifactKind.WEEKLY_REPORT_MARKDOWN,
        filename="report_v0.md",
        text="# WeeklyReport V0",
        storage_group=StorageGroup.DERIVED,
    )
    report_v1 = service.save_text(
        task_id=task_id,
        kind=ArtifactKind.WEEKLY_REPORT_MARKDOWN,
        filename="report_v1.md",
        text="# WeeklyReport V1",
        storage_group=StorageGroup.DERIVED,
    )

    service.register_report_version(
        task_id=task_id,
        version="V0",
        markdown_artifact_id=report_v0.artifact_id,
        structured_snapshot={"completed_this_week": []},
        change_note="Initial draft",
    )
    service.register_report_version(
        task_id=task_id,
        version="V1.0",
        markdown_artifact_id=report_v1.artifact_id,
        structured_snapshot={"completed_this_week": ["Project milestone completed"]},
        change_note="Approved version",
    )

    task_index = service.get_task_index(task_id)
    versions = service.list_report_versions(task_id)

    assert task_index["current_report_version"] == "V1.0"
    assert task_index["latest_report_artifact_id"] == report_v1.artifact_id
    assert [item["version"] for item in versions] == ["V0", "V1.0"]
    assert versions[0]["change_note"] == "Initial draft"
    assert versions[1]["structured_snapshot"] == {"completed_this_week": ["Project milestone completed"]}


def test_artifact_service_can_load_latest_structured_report_document(tmp_path: Path) -> None:
    service = ArtifactService(
        artifact_root=tmp_path / "artifacts",
        sqlite_path=tmp_path / "sqlite" / "weekly_report.db",
    )
    task_id = service.create_task(
        task_name="Printer WeeklyReport",
        task_type=TaskType.WEEKLY_REPORT,
    )

    report_artifact = service.save_text(
        task_id=task_id,
        kind=ArtifactKind.WEEKLY_REPORT_MARKDOWN,
        filename="report_v0.md",
        text="# WeeklyReport V0",
        storage_group=StorageGroup.DERIVED,
    )
    service.register_report_version(
        task_id=task_id,
        version="V0",
        markdown_artifact_id=report_artifact.artifact_id,
        structured_snapshot={
            "project_name": "Printer reload",
            "version": "V0",
            "raw_markdown": "# WeeklyReport V0",
            "completed_this_week": [],
        },
    )

    latest = service.get_latest_report_document(task_id)

    assert isinstance(latest, WeeklyReportDocument)
    assert latest.project_name == "Printer reload"
    assert latest.version == "V0"


def test_artifact_service_persists_workflow_state_and_supplement_links(tmp_path: Path) -> None:
    service = ArtifactService(
        artifact_root=tmp_path / "artifacts",
        sqlite_path=tmp_path / "sqlite" / "weekly_report.db",
    )
    task_id = service.create_task(
        task_name="Supplement flow",
        task_type=TaskType.WEEKLY_REPORT,
    )

    service.save_workflow_state(
        task_id,
        {
            "task_id": task_id,
            "thread_id": "thread-001",
            "run_id": "run-001",
            "task_status": "waiting",
            "waiting_reason": "evidence",
            "last_completed_node": "wait_for_evidence",
        },
        status=TaskStatus.WAITING,
    )
    service.append_supplement_link(
        task_id,
        artifact_id="artifact-001",
        request_id="req-001",
        section_id="completed_this_week",
        evidence_refs=["material-001", "material-001_frame_01"],
    )

    task_index = service.get_task_index(task_id)
    workflow_state = service.load_workflow_state(task_id)

    assert task_index["status"] == TaskStatus.WAITING.value
    assert workflow_state is not None
    assert workflow_state["waiting_reason"] == "evidence"
    assert "workflow_state" not in task_index["metadata"]
    assert task_index["metadata"]["supplement_links"] == [
        {
            "artifact_id": "artifact-001",
            "request_id": "req-001",
            "section_id": "completed_this_week",
            "evidence_refs": ["material-001", "material-001_frame_01"],
        }
    ]
