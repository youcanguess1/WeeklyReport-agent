from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from app.schemas.evidence import ArtifactKind, ArtifactRef, MaterialPack
from app.schemas.report import WeeklyReportDocument
from app.services.artifact_service import ArtifactService, StorageGroup
from app.workflows.graph_state import TaskState


MaterialPackLoader = Callable[[TaskState], MaterialPack]
MaterialPackSaver = Callable[[TaskState, MaterialPack], ArtifactRef]
WeeklyReportDocumentLoader = Callable[[TaskState], WeeklyReportDocument]


def load_material_pack_from_manifest_ref(ref: ArtifactRef) -> MaterialPack:
    return MaterialPack.model_validate_json(Path(ref.path).read_text(encoding="utf-8"))


def build_material_pack_loader() -> MaterialPackLoader:
    def _load(state: TaskState) -> MaterialPack:
        if state.material_pack_ref is None:
            raise KeyError("TaskState.material_pack_ref is not set.")
        return load_material_pack_from_manifest_ref(state.material_pack_ref)

    return _load


def build_current_report_loader(artifact_service: ArtifactService) -> WeeklyReportDocumentLoader:
    def _load(state: TaskState) -> WeeklyReportDocument:
        return artifact_service.get_latest_report_document(state.task_id)

    return _load


def build_material_pack_saver(artifact_service: ArtifactService) -> MaterialPackSaver:
    def _save(state: TaskState, material_pack: MaterialPack) -> ArtifactRef:
        return artifact_service.save_json(
            task_id=state.task_id,
            kind=ArtifactKind.MANIFEST,
            filename=f"material_pack_{material_pack.material_pack_id}.json",
            payload=material_pack.model_dump(mode="json"),
            storage_group=StorageGroup.DERIVED,
            parent_artifact_id=state.material_pack_ref.artifact_id if state.material_pack_ref else None,
            metadata={"source_task_id": state.task_id},
        )

    return _save


def merge_material_packs(base: MaterialPack, supplement: MaterialPack) -> MaterialPack:
    if base.task_id != supplement.task_id:
        raise ValueError("Cannot merge materials from different tasks.")
    material_ids = {item.material_id for item in base.materials}
    material_id_map: dict[str, str] = {}
    appended = []
    for material in supplement.materials:
        material_id = material.material_id
        suffix = 1
        while material_id in material_ids:
            material_id = f"{material.material_id}_{suffix}"
            suffix += 1
        material_ids.add(material_id)
        material_id_map[material.material_id] = material_id
        appended.append(material.model_copy(update={"material_id": material_id}))
    understandings = [
        item.model_copy(update={"material_id": material_id_map[item.material_id]})
        for item in supplement.understandings
    ]
    metadata = dict(base.metadata)
    supplement_ids = set(filter(None, metadata.get("supplement_artifact_ids", "").split(",")))
    supplement_ids.update(item.source_artifact_id for item in supplement.materials)
    metadata["supplement_artifact_ids"] = ",".join(sorted(supplement_ids))
    return MaterialPack.model_validate({
        **base.model_dump(),
        "materials": [*base.materials, *appended],
        "understandings": [*base.understandings, *understandings],
        "metadata": metadata,
    })


def build_image_supplement_material_id(source_artifact_id: str) -> str:
    return f"supp_{source_artifact_id}_image"
