from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from app.schemas.evidence import MaterialPack
from app.schemas.report import WeeklyReportDocument
from app.schemas.workbench import (
    WeeklyReportEvidenceImageResponse,
    WeeklyReportVersionResponse,
    TaskWorkbenchResponse,
    WorkbenchEventsResponse,
)
from app.services.artifact_service import ArtifactService
from app.services.task_operation_service import TaskOperationService
from app.services.task_query_service import TaskQueryService
from app.services.trace_service import TraceService
from app.services.workbench_event_query_service import WorkbenchEventQuery
from app.tools.report_render_tool import WeeklyReportRenderTool


class WorkbenchQueryService:
    def __init__(
        self,
        artifact_service: ArtifactService,
        task_query_service: TaskQueryService,
        task_operation_service: TaskOperationService | None = None,
        trace_service: TraceService | None = None,
    ) -> None:
        self.artifact_service = artifact_service
        self.task_query_service = task_query_service
        self.task_operation_service = task_operation_service or TaskOperationService(
            artifact_service.session_factory
        )
        self.event_query = WorkbenchEventQuery(
            artifact_service,
            trace_service or TraceService(artifact_service.sqlite_path),
        )

    def get_task(self, task_id: str) -> TaskWorkbenchResponse:
        item = self.task_query_service.get_task(task_id)
        return TaskWorkbenchResponse(
            task_id=item.task_id,
            task_name=item.task_name,
            task_type=item.task_type,
            description=item.description,
            status=item.status,
            current_node=item.current_node,
            waiting_reason=item.waiting_reason,
            current_report_version=item.current_report_version,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )

    def list_versions(self, task_id: str) -> list[WeeklyReportVersionResponse]:
        versions: list[WeeklyReportVersionResponse] = []
        material_pack = self._load_material_pack(task_id)
        for item in self.artifact_service.list_report_versions(task_id):
            artifact = self.artifact_service.get_artifact_ref(str(item["markdown_artifact_id"]))
            markdown = Path(artifact.path).read_text(encoding="utf-8")
            evidence_images: list[WeeklyReportEvidenceImageResponse] = []
            if material_pack is not None and isinstance(item.get("structured_snapshot"), dict):
                try:
                    document = WeeklyReportDocument.model_validate(item["structured_snapshot"])
                    image_urls = WeeklyReportRenderTool(self.artifact_service).build_evidence_image_urls(
                        task_id, material_pack
                    )
                    for material_id in document.material_refs:
                        url = image_urls.get(material_id)
                        if url is not None:
                            evidence_images.append(WeeklyReportEvidenceImageResponse(material_id=material_id, url=url))
                except (TypeError, ValueError):
                    pass
            versions.append(
                WeeklyReportVersionResponse(
                    report_version_id=str(item["report_version_id"]),
                    version=str(item["version"]),
                    created_at=_as_datetime(item["created_at"]),
                    change_note=item["change_note"],
                    markdown=markdown,
                    evidence_images=evidence_images,
                )
            )
        return versions

    def _load_material_pack(self, task_id: str) -> MaterialPack | None:
        try:
            state = self.artifact_service.load_workflow_state(task_id)
            evidence_ref = state.get("material_pack_ref") if isinstance(state, dict) else None
            artifact_id = evidence_ref.get("artifact_id") if isinstance(evidence_ref, dict) else None
            if not artifact_id:
                return None
            return MaterialPack.model_validate(self.artifact_service.load_json_artifact(str(artifact_id)))
        except (KeyError, TypeError, ValueError):
            return None

    def list_events(self, task_id: str, cursor: str | None = None) -> WorkbenchEventsResponse:
        events, next_cursor = self.event_query.list_events(task_id, cursor)
        return WorkbenchEventsResponse(
            task=self.get_task(task_id),
            operations=self.task_operation_service.list_for_task(task_id),
            events=events,
            cursor=next_cursor,
        )


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
