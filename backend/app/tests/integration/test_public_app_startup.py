from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_app_starts_without_external_knowledge_or_computer_tools(tmp_path: Path) -> None:
    script = '''
import builtins
import sys
from pathlib import Path

original_import = builtins.__import__
blocked = ("mcp", "playwright", "pywinauto")

def import_without_optional_tools(name, *args, **kwargs):
    if name.split(".")[0] in blocked:
        raise AssertionError(f"App startup must not load {name}")
    return original_import(name, *args, **kwargs)

builtins.__import__ = import_without_optional_tools
from app.services.config_service import ConfigService

settings = ConfigService().load()
root = Path(sys.argv[1])
settings.storage.artifact_root = root / "artifacts"
settings.storage.sqlite_path = root / "database.db"
settings.logging.enabled = False
settings.observability.provider = "none"
settings.model.vision.model = None
ConfigService.load = lambda self, settings_path=None: settings

from fastapi.testclient import TestClient
from app.main import app

with TestClient(app) as client:
    assert client.get("/healthz").json()["status"] == "ok"
    assert client.get("/api/tasks").json()["items"] == []
    assert app.state.graph_nodes.review_agent is not None
    assert app.state.graph_nodes.writer_agent is not None
assert not any(name.split(".")[0] in blocked for name in sys.modules)
'''
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[3],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
    )

    assert result.returncode == 0, result.stdout + result.stderr
