from app.memory.evidence_memory import EvidenceMemory
from app.schemas.evidence import ArtifactKind, ImageMaterial, MaterialPack, MaterialUnderstanding
from app.schemas.report import WeeklyReportDocument
from app.schemas.task import TaskType
from app.services.artifact_service import ArtifactService, StorageGroup
from app.tools.report_render_tool import WeeklyReportRenderTool
from app.workflows.graph_state import TaskState


def test_section_evidence_resolves_images_notes_and_supplement_links(tmp_path):
    service = ArtifactService(tmp_path / "artifacts", tmp_path / "weekly_report.db")
    task_id = service.create_task("项目周报", TaskType.WEEKLY_REPORT)
    image = service.save_bytes(task_id, ArtifactKind.IMAGE_MATERIAL, "progress.png", b"image", StorageGroup.UPLOAD)
    pack = MaterialPack(
        material_pack_id="materials-1", task_id=task_id, source_artifact_id=image.artifact_id,
        materials=[ImageMaterial(material_id="image-1", source_artifact_id=image.artifact_id, image_path=image.path, note="进度 80%")],
        understandings=[MaterialUnderstanding(material_id="image-1", content_summary="图表显示当前进度")],
    )
    manifest = service.save_json(task_id, ArtifactKind.MANIFEST, "materials.json", pack.model_dump(mode="json"), StorageGroup.DERIVED)
    WeeklyReportRenderTool(service).render_and_save(task_id=task_id, report_document=WeeklyReportDocument(project_name="项目周报", in_progress=["进度 80%"]), material_pack=pack)
    service.save_workflow_state(task_id, TaskState(task_id=task_id, thread_id="thread-1", run_id="run-1", material_pack_ref=manifest).model_dump(mode="json"))
    service.append_supplement_link(task_id, artifact_id=image.artifact_id, request_id="request-1", section_id="in_progress", evidence_refs=["image-1"])
    record = EvidenceMemory(service).get_section_evidence(task_id, "in_progress")
    assert record.section_items == ["进度 80%"]
    assert record.material_refs == ["image-1"]
    assert record.materials[0].note == "进度 80%"
    assert record.descriptions[0].content_summary == "图表显示当前进度"
    assert record.artifacts == [image]
    assert len(record.supplement_links) == 1
    assert EvidenceMemory(service).get_section_evidence(task_id, "next_week_plan").supplement_links == []
