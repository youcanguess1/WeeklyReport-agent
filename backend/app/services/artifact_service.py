from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from app.db.models import ArtifactIndex, WeeklyReportVersionIndex, TaskIndex
from app.db.session import create_sqlite_session_factory
from app.memory.memory_tool import MemoryTool
from app.memory.task_memory import TaskMemory
from app.schemas.evidence import ArtifactKind, ArtifactRef
from app.schemas.report import WeeklyReportDocument
from app.schemas.task import TaskStatus, TaskType


logger = logging.getLogger(__name__)


class StorageGroup(StrEnum):
    UPLOAD = "upload"
    DERIVED = "derived"


class ArtifactService:
    SUPPLEMENT_LINKS_KEY = "supplement_links"

    def __init__(self, artifact_root: Path, sqlite_path: Path) -> None:
        self.artifact_root = artifact_root.resolve()
        self.sqlite_path = sqlite_path.resolve()
        self.session_factory = create_sqlite_session_factory(self.sqlite_path)
        self.task_memory = TaskMemory(self.sqlite_path)
        self.memory_tool = MemoryTool(self.artifact_root, self.sqlite_path)
        self.artifact_root.mkdir(parents=True, exist_ok=True)

    def create_task(
        self,
        task_name: str,
        task_type: TaskType,
        status: TaskStatus = TaskStatus.CREATED,
        task_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        task_id = task_id or self.generate_task_id()
        now = self._iso_now()
        self._ensure_task_directories(task_id)

        with self.session_factory() as session:
            existing = session.get(TaskIndex, task_id)
            if existing is None:
                session.add(
                    TaskIndex(
                        task_id=task_id,
                        task_name=task_name,
                        task_type=task_type.value,
                        status=status.value,
                        current_report_version=None,
                        latest_report_artifact_id=None,
                        created_at=now,
                        updated_at=now,
                        metadata_json=self._json_dump(metadata or {}),
                    )
                )
            else:
                existing.task_name = task_name
                existing.task_type = task_type.value
                existing.status = status.value
                existing.updated_at = now
                existing.metadata_json = self._json_dump(metadata or {})
            session.commit()

        return task_id

    def save_bytes(
        self,
        task_id: str,
        kind: ArtifactKind,
        filename: str,
        content: bytes,
        storage_group: StorageGroup,
        parent_artifact_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        artifact_id: str | None = None,
    ) -> ArtifactRef:
        artifact_id = artifact_id or self.generate_artifact_id()
        relative_path = self._build_relative_path(task_id, kind, storage_group, artifact_id, filename)
        full_path = self.artifact_root / relative_path
        try:
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_bytes(content)
        except Exception:
            logger.exception(
                "Failed to write artifact content.",
                extra={"event": "artifact_write_failed", "component": "artifact", "task_id": task_id},
            )
            raise

        sha256 = hashlib.sha256(content).hexdigest()
        created_at = self._iso_now()

        try:
            with self.session_factory() as session:
                self._require_task(session, task_id)
                session.add(
                    ArtifactIndex(
                        artifact_id=artifact_id,
                        task_id=task_id,
                        kind=kind.value,
                        relative_path=relative_path.as_posix(),
                        size_bytes=len(content),
                        sha256=sha256,
                        storage_group=storage_group.value,
                        parent_artifact_id=parent_artifact_id,
                        created_at=created_at,
                        metadata_json=self._json_dump(metadata or {}),
                    )
                )
                self._touch_task(session, task_id)
                session.commit()
        except Exception:
            logger.exception(
                "Failed to index artifact metadata.",
                extra={"event": "artifact_index_failed", "component": "artifact", "task_id": task_id},
            )
            raise

        return ArtifactRef(
            artifact_id=artifact_id,
            kind=kind,
            path=str(full_path),
            media_type=self._guess_media_type(full_path),
            sha256=sha256,
            created_at=datetime.fromisoformat(created_at),
        )

    def save_text(
        self,
        task_id: str,
        kind: ArtifactKind,
        filename: str,
        text: str,
        storage_group: StorageGroup,
        parent_artifact_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        artifact_id: str | None = None,
    ) -> ArtifactRef:
        return self.save_bytes(
            task_id=task_id,
            kind=kind,
            filename=filename,
            content=text.encode("utf-8"),
            storage_group=storage_group,
            parent_artifact_id=parent_artifact_id,
            metadata=metadata,
            artifact_id=artifact_id,
        )

    def save_json(
        self,
        task_id: str,
        kind: ArtifactKind,
        filename: str,
        payload: dict[str, Any] | list[Any],
        storage_group: StorageGroup,
        parent_artifact_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        artifact_id: str | None = None,
    ) -> ArtifactRef:
        return self.save_text(
            task_id=task_id,
            kind=kind,
            filename=filename,
            text=json.dumps(payload, ensure_ascii=False, indent=2),
            storage_group=storage_group,
            parent_artifact_id=parent_artifact_id,
            metadata=metadata,
            artifact_id=artifact_id,
        )

    def register_report_version(
        self,
        task_id: str,
        version: str,
        markdown_artifact_id: str,
        structured_snapshot: dict[str, Any] | None = None,
        change_note: str | None = None,
        report_version_id: str | None = None,
    ) -> str:
        report_version_id = report_version_id or self.generate_report_version_id()
        created_at = self._iso_now()

        with self.session_factory() as session:
            task = self._require_task(session, task_id)
            self._require_artifact(session, markdown_artifact_id)
            session.add(
                WeeklyReportVersionIndex(
                    report_version_id=report_version_id,
                    task_id=task_id,
                    version=version,
                    markdown_artifact_id=markdown_artifact_id,
                    structured_snapshot_json=(
                        self._json_dump(structured_snapshot) if structured_snapshot else None
                    ),
                    created_at=created_at,
                    change_note=change_note,
                )
            )
            task.current_report_version = version
            task.latest_report_artifact_id = markdown_artifact_id
            task.updated_at = created_at
            session.commit()

        return report_version_id

    def list_task_artifacts(self, task_id: str) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(ArtifactIndex)
                .where(ArtifactIndex.task_id == task_id)
                .order_by(ArtifactIndex.created_at.asc())
            ).all()

        artifacts: list[dict[str, Any]] = []
        for row in rows:
            full_path = self.artifact_root / row.relative_path
            artifacts.append(
                {
                    "artifact_id": row.artifact_id,
                    "task_id": row.task_id,
                    "kind": row.kind,
                    "relative_path": row.relative_path,
                    "absolute_path": str(full_path),
                    "size_bytes": row.size_bytes,
                    "sha256": row.sha256,
                    "storage_group": row.storage_group,
                    "parent_artifact_id": row.parent_artifact_id,
                    "created_at": row.created_at,
                    "metadata": self._json_load(row.metadata_json),
                    "media_type": self._guess_media_type(full_path),
                }
            )
        return artifacts

    def get_artifact_ref(self, artifact_id: str) -> ArtifactRef:
        with self.session_factory() as session:
            row = self._require_artifact(session, artifact_id)

        full_path = self.artifact_root / row.relative_path
        return ArtifactRef(
            artifact_id=row.artifact_id,
            kind=ArtifactKind(row.kind),
            path=str(full_path),
            media_type=self._guess_media_type(full_path),
            sha256=row.sha256,
            created_at=datetime.fromisoformat(row.created_at),
        )

    def load_json_artifact(self, artifact_id: str) -> dict[str, Any] | list[Any]:
        try:
            artifact_ref = self.get_artifact_ref(artifact_id)
            return self._json_load(Path(artifact_ref.path).read_text(encoding="utf-8"))
        except Exception:
            logger.exception(
                "Failed to load JSON artifact.",
                extra={"event": "artifact_json_load_failed", "component": "artifact"},
            )
            raise

    def get_task_index(self, task_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            task = self._require_task(session, task_id)
        return {
            "task_id": task.task_id,
            "task_name": task.task_name,
            "task_type": task.task_type,
            "status": task.status,
            "current_report_version": task.current_report_version,
            "latest_report_artifact_id": task.latest_report_artifact_id,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "metadata": self._json_load(task.metadata_json),
        }

    def get_task_metadata(self, task_id: str) -> dict[str, Any]:
        return dict(self.get_task_index(task_id)["metadata"])

    def update_task_status(self, task_id: str, status: TaskStatus) -> None:
        with self.session_factory() as session:
            task = self._require_task(session, task_id)
            task.status = status.value
            task.updated_at = self._iso_now()
            session.commit()

    def update_task_metadata(
        self,
        task_id: str,
        metadata_patch: dict[str, Any],
        *,
        replace: bool = False,
    ) -> dict[str, Any]:
        with self.session_factory() as session:
            task = self._require_task(session, task_id)
            metadata = {} if replace else self._json_load(task.metadata_json)
            metadata.update(metadata_patch)
            task.metadata_json = self._json_dump(metadata)
            task.updated_at = self._iso_now()
            session.commit()
            return metadata

    def save_workflow_state(
        self,
        task_id: str,
        workflow_state: dict[str, Any],
        *,
        status: TaskStatus | None = None,
    ) -> None:
        self.task_memory.save_snapshot(task_id, workflow_state, status=status)

    def load_workflow_state(self, task_id: str) -> dict[str, Any] | None:
        state = self.task_memory.load_latest_state(task_id)
        return state.model_dump(mode="json") if state is not None else None

    def append_supplement_link(
        self,
        task_id: str,
        *,
        artifact_id: str,
        request_id: str | None,
        section_id: str | None,
        evidence_refs: list[str],
    ) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            task = self._require_task(session, task_id)
            metadata = self._json_load(task.metadata_json)
            links = metadata.get(self.SUPPLEMENT_LINKS_KEY)
            if not isinstance(links, list):
                links = []
            links.append(
                {
                    "artifact_id": artifact_id,
                    "request_id": request_id,
                    "section_id": section_id,
                    "evidence_refs": list(evidence_refs),
                }
            )
            metadata[self.SUPPLEMENT_LINKS_KEY] = links
            task.metadata_json = self._json_dump(metadata)
            task.updated_at = self._iso_now()
            session.commit()
            return list(links)

    def list_supplement_links(
        self,
        task_id: str,
        *,
        section_id: str | None = None,
    ) -> list[dict[str, Any]]:
        metadata = self.get_task_metadata(task_id)
        links = metadata.get(self.SUPPLEMENT_LINKS_KEY)
        if not isinstance(links, list):
            return []
        if section_id is None:
            return list(links)
        return [
            item
            for item in links
            if isinstance(item, dict) and item.get("section_id") == section_id
        ]

    def list_report_versions(self, task_id: str) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(WeeklyReportVersionIndex)
                .where(WeeklyReportVersionIndex.task_id == task_id)
                .order_by(WeeklyReportVersionIndex.created_at.asc())
            ).all()

        return [
            {
                "report_version_id": row.report_version_id,
                "task_id": row.task_id,
                "version": row.version,
                "markdown_artifact_id": row.markdown_artifact_id,
                "structured_snapshot": (
                    self._json_load(row.structured_snapshot_json)
                    if row.structured_snapshot_json
                    else None
                ),
                "created_at": row.created_at,
                "change_note": row.change_note,
            }
            for row in rows
        ]

    def get_latest_report_document(self, task_id: str) -> WeeklyReportDocument:
        versions = self.list_report_versions(task_id)
        if not versions:
            raise KeyError(f"No WeeklyReport versions found for task id: {task_id}")

        latest_snapshot = versions[-1]["structured_snapshot"]
        if latest_snapshot is None:
            raise KeyError(f"Latest WeeklyReport version for task id {task_id} has no structured snapshot")
        return WeeklyReportDocument.model_validate(latest_snapshot)

    def task_root(self, task_id: str) -> Path:
        return self.artifact_root / task_id

    @staticmethod
    def generate_task_id() -> str:
        return f"task_{uuid4().hex[:16]}"

    @staticmethod
    def generate_artifact_id() -> str:
        return f"artifact_{uuid4().hex[:16]}"

    @staticmethod
    def generate_report_version_id() -> str:
        return f"reportver_{uuid4().hex[:16]}"

    def _ensure_task_directories(self, task_id: str) -> None:
        for relative_dir in (
            Path(task_id) / "uploads" / "images",
            Path(task_id) / "uploads" / "files",
            Path(task_id) / "derived" / "manifests",
            Path(task_id) / "derived" / "reports",
            Path(task_id) / "derived" / "agent_sessions",
            Path(task_id) / "derived" / "trace_exports",
            Path(task_id) / "derived" / "files",
        ):
            (self.artifact_root / relative_dir).mkdir(parents=True, exist_ok=True)

    def _build_relative_path(
        self,
        task_id: str,
        kind: ArtifactKind,
        storage_group: StorageGroup,
        artifact_id: str,
        filename: str,
    ) -> Path:
        safe_name = self._sanitize_filename(filename)
        subdir = self._subdir_for(kind, storage_group)
        return Path(task_id) / subdir / f"{artifact_id}__{safe_name}"

    def _subdir_for(self, kind: ArtifactKind, storage_group: StorageGroup) -> Path:
        if storage_group == StorageGroup.UPLOAD:
            if kind == ArtifactKind.IMAGE_MATERIAL:
                return Path("uploads/images")
            return Path("uploads/files")

        derived_map = {
            ArtifactKind.MANIFEST: Path("derived/manifests"),
            ArtifactKind.WEEKLY_REPORT_MARKDOWN: Path("derived/reports"),
            ArtifactKind.TRACE_EXPORT: Path("derived/trace_exports"),
        }
        return derived_map.get(kind, Path("derived/files"))

    def _require_task(self, session: Any, task_id: str) -> TaskIndex:
        task = session.get(TaskIndex, task_id)
        if task is None:
            raise KeyError(f"Unknown task id: {task_id}")
        return task

    def _require_artifact(self, session: Any, artifact_id: str) -> ArtifactIndex:
        artifact = session.get(ArtifactIndex, artifact_id)
        if artifact is None:
            raise KeyError(f"Unknown artifact id: {artifact_id}")
        return artifact

    def _touch_task(self, session: Any, task_id: str) -> None:
        task = self._require_task(session, task_id)
        task.updated_at = self._iso_now()

    @staticmethod
    def _iso_now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _json_dump(payload: dict[str, Any] | list[Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _json_load(payload: str) -> dict[str, Any] | list[Any]:
        return json.loads(payload)

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", filename.strip())
        return cleaned or "artifact.bin"

    @staticmethod
    def _guess_media_type(path: Path) -> str:
        guessed, _ = mimetypes.guess_type(str(path))
        return guessed or "application/octet-stream"
