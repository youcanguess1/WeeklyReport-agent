from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from app.schemas.evidence import ArtifactKind, ArtifactRef, MaterialPack
from app.schemas.report import WeeklyReportDocument
from app.services.artifact_service import ArtifactService, StorageGroup


class RenderedWeeklyReportResult(BaseModel):
    report_document: WeeklyReportDocument
    markdown: str
    artifact: ArtifactRef
    report_version_id: str


class WeeklyReportRenderTool:
    def __init__(self, artifact_service: ArtifactService) -> None:
        self.artifact_service = artifact_service

    def render_markdown(
        self,
        report_document: WeeklyReportDocument,
    ) -> str:
        lines = [
            f"# {report_document.project_name} 项目周报",
            "",
            f"- 版本：{report_document.version}",
            "",
            "---",
            "",
            "## 一、本周完成",
            *self._render_report_items(report_document.completed_this_week),
            "",
            "## 二、进行中",
            *self._render_report_items(report_document.in_progress),
            "",
            "## 三、风险与阻塞",
            *self._render_report_items(report_document.risks_and_blockers),
            "",
            "## 四、下周计划",
            *self._render_report_items(report_document.next_week_plan),
            "",
            "## 五、待确认事项",
            *self._render_report_items(report_document.pending_confirmations),
        ]

        if report_document.report_period:
            lines.insert(2, f'- \u62a5\u544a\u5468\u671f\uff1a{report_document.report_period}')
        return "\n".join(lines).strip() + "\n"

    def render_and_save(
        self,
        *,
        task_id: str,
        report_document: WeeklyReportDocument,
        filename: str | None = None,
        material_pack: MaterialPack | None = None,
        parent_artifact_id: str | None = None,
        change_note: str | None = None,
    ) -> RenderedWeeklyReportResult:
        markdown = self.render_markdown(report_document)
        filename = filename or f"report_{self._slugify(report_document.version)}.md"
        artifact = self.artifact_service.save_text(
            task_id=task_id,
            kind=ArtifactKind.WEEKLY_REPORT_MARKDOWN,
            filename=filename,
            text=markdown,
            storage_group=StorageGroup.DERIVED,
            parent_artifact_id=parent_artifact_id,
            metadata={
                "project_name": report_document.project_name,
                "version": report_document.version,
                'report_period': report_document.report_period,
            },
        )
        updated_document = report_document.model_copy(update={
            "raw_markdown": markdown,
            "material_refs": [item.material_id for item in material_pack.materials] if material_pack else report_document.material_refs,
        })
        report_version_id = self.artifact_service.register_report_version(
            task_id=task_id,
            version=updated_document.version,
            markdown_artifact_id=artifact.artifact_id,
            structured_snapshot=updated_document.model_dump(mode="json"),
            change_note=change_note,
        )
        return RenderedWeeklyReportResult(
            report_document=updated_document,
            markdown=markdown,
            artifact=artifact,
            report_version_id=report_version_id,
        )

    def render_final_report(
        self,
        *,
        task_id: str,
        report_document: WeeklyReportDocument,
        material_pack: MaterialPack | None,
    ) -> ArtifactRef:
        markdown = self.render_markdown(report_document)
        return self.artifact_service.save_text(
            task_id=task_id,
            kind=ArtifactKind.WEEKLY_REPORT_MARKDOWN,
            filename=f"final_report_{self._slugify(report_document.version)}.md",
            text=markdown,
            storage_group=StorageGroup.DERIVED,
            parent_artifact_id=(
                self.artifact_service.get_task_index(task_id).get("latest_report_artifact_id")
            ),
            metadata={
                "is_final_report": True,
                "project_name": report_document.project_name,
                "version": report_document.version,
                'report_period': report_document.report_period,
            },
        )

    @staticmethod
    def _render_report_items(items: list[str]) -> list[str]:
        clean_items = [item.strip() for item in items if item.strip()]
        return [f"- {item}" for item in clean_items] or ["- （暂无可据资料）"]

    def build_evidence_image_urls(self, task_id: str, material_pack: MaterialPack) -> dict[str, str]:
        artifact_ids = {str(item["artifact_id"]) for item in self.artifact_service.list_task_artifacts(task_id)}
        return {
            material.material_id: f"/api/tasks/{task_id}/artifacts/{material.source_artifact_id}/content"
            for material in material_pack.materials
            if material.source_artifact_id in artifact_ids
        }


    @staticmethod
    def _slugify(value: str) -> str:
        return "".join(ch.lower() if ch.isalnum() else "_" for ch in value).strip("_") or "v0"
