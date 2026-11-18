from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.evidence import ArtifactRef, ImageMaterial, MaterialUnderstanding
from app.schemas.report import ReportSection
from app.services.artifact_service import ArtifactService
from app.workflows.graph_io import load_material_pack_from_manifest_ref
from app.workflows.graph_state import TaskState


class SupplementLinkRecord(BaseModel):
    artifact_id: str = Field(min_length=1)
    request_id: str | None = None
    section_id: ReportSection | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class MaterialEvidenceRecord(BaseModel):
    task_id: str = Field(min_length=1)
    section_id: ReportSection
    report_version: str = Field(min_length=1)
    section_items: list[str] = Field(default_factory=list)
    material_refs: list[str] = Field(default_factory=list)
    materials: list[ImageMaterial] = Field(default_factory=list)
    descriptions: list[MaterialUnderstanding] = Field(default_factory=list)
    supplement_links: list[SupplementLinkRecord] = Field(default_factory=list)
    artifacts: list[ArtifactRef] = Field(default_factory=list)


class EvidenceMemory:
    def __init__(self, artifact_service: ArtifactService) -> None:
        self.artifact_service = artifact_service

    def get_section_evidence(self, task_id: str, section_id: ReportSection | str) -> MaterialEvidenceRecord:
        section = ReportSection(section_id)
        report = self.artifact_service.get_latest_report_document(task_id)
        payload = self.artifact_service.load_workflow_state(task_id)
        if payload is None:
            raise KeyError(f"No workflow state found for task id: {task_id}")
        state = TaskState.model_validate(payload)
        if state.material_pack_ref is None:
            raise KeyError(f"Task {task_id} does not have a material pack ref.")
        pack = load_material_pack_from_manifest_ref(state.material_pack_ref)
        links = [
            SupplementLinkRecord.model_validate(item)
            for item in self.artifact_service.list_supplement_links(task_id)
            if item.get("section_id") in (None, section.value)
        ]
        refs = list(dict.fromkeys([
            *report.material_refs,
            *(ref for link in links for ref in link.evidence_refs),
        ]))
        materials = [item for item in pack.materials if item.material_id in refs]
        material_ids = {item.material_id for item in materials}
        artifact_ids = list(dict.fromkeys(item.source_artifact_id for item in materials))
        return MaterialEvidenceRecord(
            task_id=task_id,
            section_id=section,
            report_version=report.version,
            section_items=list(getattr(report, section.value)),
            material_refs=[item.material_id for item in materials],
            materials=materials,
            descriptions=[item for item in pack.understandings if item.material_id in material_ids],
            supplement_links=links,
            artifacts=[self.artifact_service.get_artifact_ref(artifact_id) for artifact_id in artifact_ids],
        )
