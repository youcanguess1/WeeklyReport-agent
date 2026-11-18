from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ProjectArtifactLibrary(StrEnum):
    RULES = "rules"
    TEMPLATES = "templates"


class ArtifactLibraryFilter(StrEnum):
    ALL = "all"
    RULES = "rules"
    TEMPLATES = "templates"
    TASKS = "tasks"


class ArtifactListItem(BaseModel):
    artifact_id: str
    task_id: str | None = None
    library: str
    filename: str
    kind: str
    description: str | None = None
    size_bytes: int | None = None
    created_at: datetime
    media_type: str


class ArtifactListResponse(BaseModel):
    items: list[ArtifactListItem]
    total: int
    page: int
    page_size: int


class ProjectArtifactUploadResponse(BaseModel):
    artifact: ArtifactListItem
