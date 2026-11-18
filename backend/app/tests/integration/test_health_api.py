from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.config_service import ConfigService


def test_healthz_returns_runtime_summary(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    repo_root = tmp_path / "repo"
    config_dir.mkdir()
    repo_root.mkdir()

    (config_dir / "settings.example.yaml").write_text(
        "\n".join(
            [
                "app:",
                "  name: WeeklyReport Test API",
                "  environment: test",
                "model:",
                "  provider: openai-compatible",
                "storage:",
                "  artifact_root: data/artifacts",
                "task_runner:",
                "  backend: local",
                "  max_workers: 1",
            ]
        ),
        encoding="utf-8",
    )

    app = create_app(config_service=ConfigService(config_dir=config_dir, repo_root=repo_root))

    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["environment"] == "test"
    assert response.json()["task_runner_backend"] == "local"
