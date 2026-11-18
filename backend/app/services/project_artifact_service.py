from __future__ import annotations

import hashlib
import json
import mimetypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from app.db.models import ArtifactIndex, ProjectArtifactIndex, TaskIndex
from app.db.session import create_sqlite_session_factory
from app.schemas.artifact_api import ProjectArtifactLibrary


class ProjectArtifactService:
    def __init__(self, artifact_root: Path, sqlite_path: Path) -> None:
        self.artifact_root = artifact_root.resolve()
        self.session_factory = create_sqlite_session_factory(sqlite_path.resolve())
        for library in ProjectArtifactLibrary:
            (self.artifact_root / "project" / library.value).mkdir(parents=True, exist_ok=True)

    def save_project_file(
        self,
        *,
        library: ProjectArtifactLibrary,
        filename: str,
        content: bytes,
        description: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        artifact_id = f"artifact_{uuid4().hex[:16]}"
        safe_filename = Path(filename).name or "uploaded_file"
        relative_path = Path("project") / library.value / f"{artifact_id}__{safe_filename}"
        full_path = self.artifact_root / relative_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)
        created_at = datetime.now(UTC).isoformat()
        metadata = {"description": description or "", "tags": tags or []}
        with self.session_factory() as session:
            session.add(
                ProjectArtifactIndex(
                    artifact_id=artifact_id,
                    library=library.value,
                    filename=safe_filename,
                    kind=self._kind_for(safe_filename),
                    relative_path=relative_path.as_posix(),
                    size_bytes=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                    created_at=created_at,
                    metadata_json=json.dumps(metadata, ensure_ascii=False),
                )
            )
            session.commit()
        return self._project_item_from_values(
            artifact_id=artifact_id,
            library=library.value,
            filename=safe_filename,
            kind=self._kind_for(safe_filename),
            size_bytes=len(content),
            created_at=created_at,
            metadata=metadata,
        )

    def list_artifacts(
        self,
        *,
        library: str = "all",
        kind: str | None = None,
        task_id: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        project_items: list[dict[str, Any]] = []
        task_items: list[dict[str, Any]] = []
        with self.session_factory() as session:
            if library in {"all", "rules", "templates"}:
                query = select(ProjectArtifactIndex)
                if library != "all":
                    query = query.where(ProjectArtifactIndex.library == library)
                if kind:
                    query = query.where(ProjectArtifactIndex.kind == kind)
                rows = session.scalars(query.order_by(ProjectArtifactIndex.created_at.desc())).all()
                project_items = [self._project_item(row) for row in rows]

            if library in {"all", "tasks"}:
                query = select(ArtifactIndex, TaskIndex.task_name).join(
                    TaskIndex, TaskIndex.task_id == ArtifactIndex.task_id
                )
                if task_id:
                    query = query.where(ArtifactIndex.task_id == task_id)
                if kind:
                    query = query.where(ArtifactIndex.kind == kind)
                rows = session.execute(query.order_by(ArtifactIndex.created_at.desc())).all()
                task_items = [self._task_item(row, task_name) for row, task_name in rows]

        items = sorted(project_items + task_items, key=lambda item: item["created_at"], reverse=True)
        total = len(items)
        start = (page - 1) * page_size
        return {"items": items[start : start + page_size], "total": total, "page": page, "page_size": page_size}

    def _project_item(self, row: ProjectArtifactIndex) -> dict[str, Any]:
        return self._project_item_from_values(
            artifact_id=row.artifact_id,
            library=row.library,
            filename=row.filename,
            kind=row.kind,
            size_bytes=row.size_bytes,
            created_at=row.created_at,
            metadata=json.loads(row.metadata_json or "{}"),
        )

    @staticmethod
    def _project_item_from_values(
        *, artifact_id: str, library: str, filename: str, kind: str,
        size_bytes: int | None, created_at: str, metadata: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "artifact_id": artifact_id,
            "task_id": None,
            "library": library,
            "filename": filename,
            "kind": kind,
            "description": metadata.get("description") or None,
            "size_bytes": size_bytes,
            "created_at": created_at,
            "media_type": mimetypes.guess_type(filename)[0] or "application/octet-stream",
        }

    @staticmethod
    def _task_item(row: ArtifactIndex, task_name: str) -> dict[str, Any]:
        metadata = json.loads(row.metadata_json or "{}")
        return {
            "artifact_id": row.artifact_id,
            "task_id": row.task_id,
            "library": "tasks",
            "filename": Path(row.relative_path).name.split("__", 1)[-1],
            "kind": row.kind,
            "description": metadata.get("note") or task_name,
            "size_bytes": row.size_bytes,
            "created_at": row.created_at,
            "media_type": mimetypes.guess_type(row.relative_path)[0] or "application/octet-stream",
        }

    @staticmethod
    def _kind_for(filename: str) -> str:
        suffix = Path(filename).suffix.lower()
        return {
            ".md": "markdown",
            ".markdown": "markdown",
            ".pdf": "pdf",
            ".jpg": "image",
            ".jpeg": "image",
            ".png": "image",
            ".webp": "image",
            ".zip": "archive",
            ".rar": "archive",
        }.get(suffix, "file")
