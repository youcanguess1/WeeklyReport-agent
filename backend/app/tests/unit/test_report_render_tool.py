from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.schemas.evidence import ArtifactKind, ImageMaterial, MaterialPack
from app.schemas.report import WeeklyReportDocument
from app.schemas.task import TaskType
from app.services.artifact_service import ArtifactService, StorageGroup
from app.tools.report_render_tool import WeeklyReportRenderTool


def test_report_render_tool_renders_markdown_and_registers_report_version(tmp_path: Path) -> None:
    artifact_service = ArtifactService(
        artifact_root=tmp_path / "artifacts",
        sqlite_path=tmp_path / "sqlite" / "weekly_report.db",
    )
    task_id = artifact_service.create_task(
        task_name="Printer WeeklyReport",
        task_type=TaskType.WEEKLY_REPORT,
    )
    tool = WeeklyReportRenderTool(artifact_service=artifact_service)
    report_document = WeeklyReportDocument(project_name="打印机补纸", version="V1.0", completed_this_week=["已完成补纸流程确认。"], in_progress=["补纸后打印验证仍在进行。"], risks_and_blockers=["暂无已确认的风险或阻塞。"], next_week_plan=["计划日期待确认。"], pending_confirmations=["确认打印验证的具体安排。"], generated_at=datetime(2026, 7, 24, 12, 0, tzinfo=UTC), material_refs=["material-001"])

    result = tool.render_and_save(
        task_id=task_id,
        report_document=report_document,
        change_note="Initial structured draft",
    )

    expected_markdown = """# 打印机补纸 项目周报

- 版本：V1.0

---

## 一、本周完成
- 已完成补纸流程确认。

## 二、进行中
- 补纸后打印验证仍在进行。

## 三、风险与阻塞
- 暂无已确认的风险或阻塞。

## 四、下周计划
- 计划日期待确认。

## 五、待确认事项
- 确认打印验证的具体安排。
"""

    assert result.markdown == expected_markdown
    assert "标准作业程序" not in result.markdown
    assert "步骤1" not in result.markdown
    assert result.report_document.raw_markdown == expected_markdown
    assert Path(result.artifact.path).read_text(encoding="utf-8") == expected_markdown

    task_index = artifact_service.get_task_index(task_id)
    versions = artifact_service.list_report_versions(task_id)

    assert task_index["current_report_version"] == "V1.0"
    assert task_index["latest_report_artifact_id"] == result.artifact.artifact_id
    assert len(versions) == 1
    assert versions[0]["change_note"] == "Initial structured draft"
    assert versions[0]["structured_snapshot"]["completed_this_week"] == ["已完成补纸流程确认。"]
    assert versions[0]["structured_snapshot"]["pending_confirmations"] == ["确认打印验证的具体安排。"]


def test_report_render_tool_renders_explicit_report_period_only(tmp_path: Path) -> None:
    artifact_service = ArtifactService(
        artifact_root=tmp_path / 'artifacts',
        sqlite_path=tmp_path / 'sqlite' / 'weekly_report.db',
    )
    tool = WeeklyReportRenderTool(artifact_service=artifact_service)

    with_period = tool.render_markdown(
        WeeklyReportDocument(project_name='Release weekly report', version='V1', report_period='2026-10-19 \u81f3 2026-10-23', completed_this_week=["Confirmed milestone"], material_refs=["material-001"])
    )
    without_period = tool.render_markdown(WeeklyReportDocument(project_name='Release weekly report', completed_this_week=["Confirmed milestone"], material_refs=["material-001"]))

    assert '- \u62a5\u544a\u5468\u671f\uff1a2026-10-19 \u81f3 2026-10-23' in with_period
    assert '\u62a5\u544a\u5468\u671f\uff1a' not in without_period


def test_report_render_tool_keeps_evidence_image_lookup_without_inserting_into_report(tmp_path: Path) -> None:
    artifact_service = ArtifactService(
        artifact_root=tmp_path / "artifacts",
        sqlite_path=tmp_path / "sqlite" / "weekly_report.db",
    )
    task_id = artifact_service.create_task("Printer WeeklyReport", TaskType.WEEKLY_REPORT)
    frame = artifact_service.save_bytes(
        task_id=task_id,
        kind=ArtifactKind.IMAGE_MATERIAL,
        filename="material-001-frame-01.png",
        content=b"png",
        storage_group=StorageGroup.DERIVED,
    )
    material_pack = MaterialPack(
        material_pack_id="evidence-001",
        task_id=task_id,
        source_artifact_id="image-001",
        materials=[
            ImageMaterial(material_id="material-001", source_artifact_id=frame.artifact_id, image_path=frame.path)
        ],
    )
    document = WeeklyReportDocument(project_name="Printer WeeklyReport", version="V1", completed_this_week=["Confirmed milestone"], material_refs=["material-001"])

    markdown = WeeklyReportRenderTool(artifact_service).render_markdown(
        document,
    )

    image_url = f"/api/tasks/{task_id}/artifacts/{frame.artifact_id}/content"
    assert image_url not in markdown
    image_urls = WeeklyReportRenderTool(artifact_service).build_evidence_image_urls(task_id, material_pack)
    assert image_urls["material-001"] == image_url


def test_report_render_tool_separates_draft_and_final_report_images(tmp_path: Path) -> None:
    artifact_service = ArtifactService(
        artifact_root=tmp_path / "artifacts",
        sqlite_path=tmp_path / "sqlite" / "weekly_report.db",
    )
    task_id = artifact_service.create_task("Printer WeeklyReport", TaskType.WEEKLY_REPORT)
    frame = artifact_service.save_bytes(
        task_id=task_id,
        kind=ArtifactKind.IMAGE_MATERIAL,
        filename="original-frame-name.png",
        content=b"png",
        storage_group=StorageGroup.DERIVED,
    )
    material_pack = MaterialPack(
        material_pack_id="evidence-final-001",
        task_id=task_id,
        source_artifact_id="image-001",
        materials=[
            ImageMaterial(material_id="material-final-001", source_artifact_id=frame.artifact_id, image_path=frame.path)
        ],
    )
    document = WeeklyReportDocument(project_name="Printer WeeklyReport", version="V2", completed_this_week=["Confirmed milestone"], material_refs=["material-001"])
    tool = WeeklyReportRenderTool(artifact_service)

    draft = tool.render_and_save(
        task_id=task_id,
        report_document=document,
        material_pack=material_pack,
    )
    final = tool.render_final_report(
        task_id=task_id,
        report_document=document,
        material_pack=material_pack,
    )
    image_url = f"/api/tasks/{task_id}/artifacts/{frame.artifact_id}/content"
    draft_markdown = Path(draft.artifact.path).read_text(encoding="utf-8")
    final_markdown = Path(final.path).read_text(encoding="utf-8")

    assert "![" not in draft_markdown
    assert "original-frame-name.png" not in draft_markdown
    assert image_url not in draft_markdown
    assert image_url not in final_markdown
    assert "## 一、本周完成" in draft_markdown
    assert "## 五、待确认事项" in final_markdown
    assert "original-frame-name.png" not in final_markdown
