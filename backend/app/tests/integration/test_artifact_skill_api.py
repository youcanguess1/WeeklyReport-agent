from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes_artifacts import router as artifacts_router
from app.api.routes_skills import router as skills_router
from app.memory.skill_memory import SkillMemory
from app.services.artifact_service import ArtifactService, StorageGroup
from app.services.project_artifact_service import ProjectArtifactService
from app.schemas.evidence import ArtifactKind
from app.schemas.task import TaskType


def _app(tmp_path: Path) -> tuple[FastAPI, ArtifactService, ProjectArtifactService]:
    artifact_service = ArtifactService(tmp_path / "artifacts", tmp_path / "sqlite" / "weekly_report.db")
    project_service = ProjectArtifactService(tmp_path / "artifacts", tmp_path / "sqlite" / "weekly_report.db")
    app = FastAPI()
    app.state.project_artifact_service = project_service
    app.state.skill_memory = SkillMemory(tmp_path / "sqlite" / "weekly_report.db")
    app.include_router(artifacts_router)
    app.include_router(skills_router)
    return app, artifact_service, project_service


def test_project_artifact_upload_and_task_listing_are_separate(tmp_path: Path) -> None:
    app, artifact_service, project_service = _app(tmp_path)
    task_id = artifact_service.create_task("Task artifact", TaskType.WEEKLY_REPORT)
    artifact_service.save_text(
        task_id,
        ArtifactKind.MANIFEST,
        "evidence.json",
        "{}",
        StorageGroup.DERIVED,
    )

    with TestClient(app) as client:
        upload = client.post(
            "/api/artifacts",
            files={"file": ("security.md", b"# Security", "text/markdown")},
            data={"library": "rules", "description": "Internal policy"},
        )
        all_items = client.get("/api/artifacts", params={"library": "all"})
        task_items = client.get("/api/artifacts", params={"library": "tasks"})

    assert upload.status_code == 200
    project_item = upload.json()["artifact"]
    assert project_item["library"] == "rules"
    assert project_item["task_id"] is None
    assert str(tmp_path) not in upload.text
    assert (tmp_path / "artifacts" / "project" / "rules" / f"{project_item['artifact_id']}__security.md").exists()
    assert all_items.status_code == 200
    assert {item["library"] for item in all_items.json()["items"]} == {"rules", "tasks"}
    assert len(task_items.json()["items"]) == 1
    assert task_items.json()["items"][0]["task_id"] == task_id
    assert project_service.list_artifacts(library="rules")["items"][0]["task_id"] is None


def test_skill_crud_and_delete_not_found(tmp_path: Path) -> None:
    app, _, _ = _app(tmp_path)

    with TestClient(app) as client:
        created = client.post(
            "/api/skills",
            files={"file": ("review.md", b"# Review rule", "text/markdown")},
            data={
                "title": "Review rule",
                "description": "Check output",
                "task_type": "weekly_report",
                "agent_stage": "review",
                "content": "# Edited review rule",
            },
        )
        skill_id = created.json()["skill_id"]
        listed = client.get("/api/skills")
        updated = client.put(
            f"/api/skills/{skill_id}",
            json={
                "title": "Updated rule",
                "description": "Updated description",
                "content": "# Updated",
                "agent_stage": "writer",
            },
        )
        deleted = client.delete(f"/api/skills/{skill_id}")
        missing = client.delete(f"/api/skills/{skill_id}")

    assert created.status_code == 200
    assert created.json()["content"] == "# Edited review rule"
    assert listed.status_code == 200
    assert listed.json()[0]["title"] == "Review rule"
    assert updated.status_code == 200
    assert updated.json()["title"] == "Updated rule"
    assert updated.json()["agent_stage"] == "writer"
    assert deleted.status_code == 204
    assert missing.status_code == 404
