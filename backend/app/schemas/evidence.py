from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class ArtifactKind(StrEnum):
    IMAGE_MATERIAL = "image_material"
    MANIFEST = "manifest"
    WEEKLY_REPORT_MARKDOWN = "weekly_report_markdown"
    TRACE_EXPORT = "trace_export"


class ArtifactRef(BaseModel):
    artifact_id: str = Field(min_length=1)
    kind: ArtifactKind
    path: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    sha256: str | None = None
    created_at: datetime


class ImageMaterial(BaseModel):
    material_id: str = Field(min_length=1)
    source_artifact_id: str = Field(min_length=1)
    image_path: str = Field(min_length=1)
    note: str | None = None


class MaterialUnderstanding(BaseModel):
    material_id: str = Field(min_length=1)
    content_summary: str = Field(min_length=1)


class MaterialUnderstandingDraft(BaseModel):
    content_summary: str = Field(min_length=1)


class MaterialPack(BaseModel):
    material_pack_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    source_artifact_id: str = Field(min_length=1)
    manifest: ArtifactRef | None = None
    materials: list[ImageMaterial] = Field(default_factory=list)
    understandings: list[MaterialUnderstanding] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_material_references(self) -> MaterialPack:
        material_ids = [item.material_id for item in self.materials]
        if len(set(material_ids)) != len(material_ids):
            raise ValueError("Material IDs must be unique within a pack.")
        summary_ids = [item.material_id for item in self.understandings]
        if len(set(summary_ids)) != len(summary_ids):
            raise ValueError("Each material may have only one understanding.")
        if not set(summary_ids).issubset(material_ids):
            raise ValueError("Understandings must reference materials in the pack.")
        return self
