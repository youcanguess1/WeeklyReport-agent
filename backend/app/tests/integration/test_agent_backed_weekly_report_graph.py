from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path


from app.schemas.evidence import ArtifactKind, MaterialUnderstanding, ImageMaterial, MaterialPack
from app.schemas.memory import MemoryKind, SkillBrief, SkillTrigger
from app.schemas.review import ReviewIssue, ReviewResult, ReviewSeverity, WorkflowDecision
from app.schemas.report import WeeklyReportDocument
from app.schemas.task import TaskStatus, TaskType
from app.services.artifact_service import ArtifactService, StorageGroup
from app.tools.report_render_tool import WeeklyReportRenderTool
from app.workflows.graph_state import TaskState, WorkflowNode, WorkflowWaitReason
from app.workflows.weekly_report_graph import (
    WeeklyReportGraphNodes,
    build_weekly_report_graph,
)
from app.workflows.graph_io import (
    build_current_report_loader,
    build_material_pack_loader,
    build_material_pack_saver,
)


def _invoke_with_final_confirmation(graph, state: TaskState) -> TaskState:
    result = TaskState.model_validate(graph.invoke(state))
    while result.waiting_reason == WorkflowWaitReason.HUMAN and result.latest_review_result is not None and result.latest_review_result.decision == WorkflowDecision.APPROVED:
        result = TaskState.model_validate(
            graph.invoke(
                result.model_copy(
                    update={
                        "task_status": TaskStatus.QUEUED,
                        "waiting_reason": None,
                        "last_completed_node": WorkflowNode.WAIT_FOR_HUMAN,
                    }
                )
            )
        )
    return result


class _FakeVisualUnderstandingAgent:
    def __init__(self) -> None:
        self.calls = 0

    def understand_material_pack(self, material_pack: MaterialPack) -> MaterialPack:
        self.calls += 1
        return material_pack.model_copy(
            update={
                "understandings": [
                    MaterialUnderstanding(
                        material_id="material-001",
                        content_summary="Operator opens the tray.",
                    ),
                    MaterialUnderstanding(
                        material_id="material-002",
                        content_summary="Operator confirms the tray is fully inserted.",
                    ),
                ]
            }
        )


class _FakeWriterAgent:
    def __init__(self) -> None:
        self.initial_calls = 0
        self.revise_calls = 0
        self.report_context: list[tuple[str | None, str | None]] = []

    def write_report(
        self,
        material_pack: MaterialPack,
        *,
        version: str = "V0",
        project_name: str | None = None,
        task_notes: str | None = None,
        previous_report: WeeklyReportDocument,
        review_result: ReviewResult | None = None,
    ) -> WeeklyReportDocument:
        self.report_context.append((project_name, task_notes))
        if review_result is None:
            self.initial_calls += 1
            return WeeklyReportDocument(project_name=project_name or "Printer reload", version=version, completed_this_week=["Paper reload completed per task notes."], in_progress=["Printer validation remains in progress."], pending_confirmations=["Confirm the validation schedule."])
        self.revise_calls += 1
        return WeeklyReportDocument(project_name=project_name or previous_report.project_name, version=version, completed_this_week=previous_report.completed_this_week, in_progress=previous_report.in_progress, risks_and_blockers=previous_report.risks_and_blockers, next_week_plan=previous_report.next_week_plan, pending_confirmations=previous_report.pending_confirmations)


class _FakeReviewAgent:
    def __init__(self) -> None:
        self.calls = 0
        self.report_context: list[tuple[str | None, str | None]] = []

    def review_report(
        self,
        *,
        task_id: str,
        report_document: WeeklyReportDocument,
        task_name: str | None = None,
        task_notes: str | None = None,
        material_pack: MaterialPack | None = None,
    ) -> ReviewResult:
        self.calls += 1
        self.report_context.append((task_name, task_notes))
        if self.calls == 1:
            return ReviewResult(
                review_id="review-001",
                task_id=task_id,
                decision=WorkflowDecision.REVISE_REPORT,
                summary="Need a final-state confirmation step.",
                issues=[
                    ReviewIssue(
                        issue_id="issue-001",
                        section_id="completed_this_week",
                        severity=ReviewSeverity.HIGH,
                        category="missing_expected_result",
                        description="The final tray position is not described.",
                        suggestion="Add a confirmation step after reinserting the tray.",
                        requires_evidence=False,
                    )
                ],
                created_at=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
            )

        return ReviewResult(
            review_id="review-002",
            task_id=task_id,
            decision=WorkflowDecision.APPROVED,
            summary="WeeklyReport is clear and complete.",
            issues=[],
            created_at=datetime(2026, 7, 24, 12, 5, tzinfo=UTC),
        )


class _ApprovedReviewAgent:
    def review_report(
        self,
        *,
        task_id: str,
        report_document: WeeklyReportDocument,
        material_pack: MaterialPack | None = None,
    ) -> ReviewResult:
        return ReviewResult(
            review_id="review-approved-001",
            task_id=task_id,
            decision=WorkflowDecision.APPROVED,
            summary="Weekly report is ready for confirmation.",
            issues=[],
            created_at=datetime(2026, 7, 24, 12, 5, tzinfo=UTC),
        )


def test_image_backed_weekly_report_reaches_writer_review_and_final_artifact(tmp_path) -> None:
    artifact_service = ArtifactService(
        artifact_root=tmp_path / "artifacts",
        sqlite_path=tmp_path / "sqlite" / "weekly_report.db",
    )
    task_id = artifact_service.create_task(
        task_name="Printer reload",
        task_type=TaskType.WEEKLY_REPORT,
        metadata={"notes": "Paper reload is complete; printer validation remains in progress."},
    )
    source_image = artifact_service.save_bytes(
        task_id=task_id,
        kind=ArtifactKind.IMAGE_MATERIAL,
        filename="printer-note.png",
        content=b"image",
        storage_group=StorageGroup.UPLOAD,
    )
    material_pack = MaterialPack(
        material_pack_id="evidence-001",
        task_id=task_id,
        source_artifact_id=source_image.artifact_id,
        materials=[
            ImageMaterial(material_id="material-001", source_artifact_id=source_image.artifact_id, image_path="/tmp/material.png", note="Open the tray."),
            ImageMaterial(material_id="material-002", source_artifact_id=source_image.artifact_id, image_path="/tmp/material.png", note="Confirm the tray is back in place."),
        ],
    )
    manifest_ref = artifact_service.save_json(
        task_id=task_id,
        kind=ArtifactKind.MANIFEST,
        filename="image_manifest.json",
        payload=material_pack.model_dump(mode="json"),
        storage_group=StorageGroup.DERIVED,
        parent_artifact_id=source_image.artifact_id,
    )

    visual_agent = _FakeVisualUnderstandingAgent()
    writer_agent = _FakeWriterAgent()
    review_agent = _FakeReviewAgent()
    nodes = WeeklyReportGraphNodes(
        visual_understanding_agent=visual_agent,
        material_pack_loader=build_material_pack_loader(),
        material_pack_saver=build_material_pack_saver(artifact_service),
        writer_agent=writer_agent,
        review_agent=review_agent,
        report_render_tool=WeeklyReportRenderTool(artifact_service),
        current_report_loader=build_current_report_loader(artifact_service),
    )
    state = TaskState(
        task_id=task_id,
        thread_id="thread-001",
        run_id="run-001",
        input_artifact_refs=[source_image],
        material_pack_ref=manifest_ref,

    )

    final_state = _invoke_with_final_confirmation(
        build_weekly_report_graph(nodes=nodes),
        state,
    )

    assert final_state.task_status.value == "completed"
    assert final_state.last_completed_node == WorkflowNode.MARK_COMPLETED
    assert visual_agent.calls == 1
    assert writer_agent.initial_calls == 1
    assert writer_agent.revise_calls == 1
    assert review_agent.calls == 2
    expected_report_context = (
        "Printer reload",
        "Paper reload is complete; printer validation remains in progress.",
    )
    assert writer_agent.report_context == [expected_report_context, expected_report_context]
    assert review_agent.report_context == [expected_report_context, expected_report_context]
    assert final_state.writer_round == 2
    assert final_state.review_round == 2
    assert final_state.resolved_issue_ids == ["issue-001"]
    assert final_state.latest_review_result is not None
    assert final_state.latest_review_result.decision == WorkflowDecision.APPROVED
    assert final_state.current_report_version_ref is not None
    assert len(final_state.report_version_history) == 2

    versions = artifact_service.list_report_versions(task_id)
    assert [item["version"] for item in versions] == ["V0", "V1"]
    assert "steps" not in versions[-1]["structured_snapshot"]
    assert versions[-1]["structured_snapshot"]["completed_this_week"] == [
        "Paper reload completed per task notes."
    ]
    metadata = artifact_service.get_task_metadata(task_id)
    final_artifact_id = metadata.get("final_report_artifact_id")
    assert final_artifact_id
    final_artifact = artifact_service.get_artifact_ref(str(final_artifact_id))
    assert Path(final_artifact.path).name.endswith("__final_report_v1.md")
    assert Path(final_artifact.path).is_file()


class _RecordingSkillSelector:
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, str | None]] = []

    def select_skills(
        self,
        *,
        task_type: str | None = None,
        agent_stage: str | None = None,
        **_: object,
    ) -> list[SkillBrief]:
        self.calls.append((task_type, agent_stage))
        if agent_stage is None:
            return []
        return [
            SkillBrief(
                skill_id=f"skill-{agent_stage}",
                title=f"{agent_stage.title()} skill",
                description=f"Brief for {agent_stage}.",
                kind=MemoryKind.WRITER_SKILL,
                trigger=SkillTrigger(task_type=task_type, agent_stage=agent_stage),
                confidence=0.8,
                hit_count=3,
            )
        ]


class _RecordingWriterAgent(_FakeWriterAgent):
    def __init__(self) -> None:
        super().__init__()
        self.skill_titles: list[list[str]] = []

    def write_report(
        self,
        material_pack: MaterialPack,
        *,
        version: str = "V0",
        project_name: str | None = None,
        previous_report: WeeklyReportDocument,
        review_result: ReviewResult | None = None,
        skill_briefs: list[SkillBrief] | None = None,
    ) -> WeeklyReportDocument:
        self.skill_titles.append([item.title for item in skill_briefs or []])
        return super().write_report(
            material_pack,
            version=version,
            project_name=project_name,
            previous_report=previous_report,
            review_result=review_result,
        )


class _RecordingReviewAgent(_ApprovedReviewAgent):
    def __init__(self) -> None:
        self.skill_titles: list[list[str]] = []

    def review_report(
        self,
        *,
        task_id: str,
        report_document: WeeklyReportDocument,
        material_pack: MaterialPack | None = None,
        skill_briefs: list[SkillBrief] | None = None,
    ) -> ReviewResult:
        self.skill_titles.append([item.title for item in skill_briefs or []])
        return super().review_report(
            task_id=task_id,
            report_document=report_document,
            material_pack=material_pack,

        )


def test_agent_backed_workflow_injects_skill_briefs_by_stage(tmp_path) -> None:
    artifact_service = ArtifactService(
        artifact_root=tmp_path / "artifacts",
        sqlite_path=tmp_path / "sqlite" / "weekly_report.db",
    )
    task_id = artifact_service.create_task(
        task_name="Browser flow",
        task_type=TaskType.WEEKLY_REPORT,
    )
    source_image = artifact_service.save_bytes(
        task_id=task_id,
        kind=ArtifactKind.IMAGE_MATERIAL,
        filename="browser-skill-demo.mp4",
        content=b"image",
        storage_group=StorageGroup.UPLOAD,
    )
    material_pack = MaterialPack(
        material_pack_id="evidence-web-001",
        task_id=task_id,
        source_artifact_id=source_image.artifact_id,
        materials=[
            ImageMaterial(material_id="material-001", source_artifact_id=source_image.artifact_id, image_path="/tmp/material.png", note="Open the page and verify the form.")
        ],
    )
    manifest_ref = artifact_service.save_json(
        task_id=task_id,
        kind=ArtifactKind.MANIFEST,
        filename="browser_skill_manifest.json",
        payload=material_pack.model_dump(mode="json"),
        storage_group=StorageGroup.DERIVED,
        parent_artifact_id=source_image.artifact_id,
    )
    skill_selector = _RecordingSkillSelector()
    writer_agent = _RecordingWriterAgent()
    review_agent = _RecordingReviewAgent()
    nodes = WeeklyReportGraphNodes(
        visual_understanding_agent=_FakeVisualUnderstandingAgent(),
        material_pack_loader=build_material_pack_loader(),
        material_pack_saver=build_material_pack_saver(artifact_service),
        writer_agent=writer_agent,
        review_agent=review_agent,
        report_render_tool=WeeklyReportRenderTool(artifact_service),
        current_report_loader=build_current_report_loader(artifact_service),
        skill_selector=skill_selector,
        task_type_loader=lambda current_task_id: artifact_service.get_task_index(current_task_id)["task_type"],
    )
    state = TaskState(
        task_id=task_id,
        thread_id="thread-001",
        run_id="run-001",
        material_pack_ref=manifest_ref,


    )

    final_state = _invoke_with_final_confirmation(
        build_weekly_report_graph(nodes=nodes),
        state,
    )

    assert final_state.task_status == TaskStatus.COMPLETED
    assert skill_selector.calls == [
        ("weekly_report", "writer"),
        ("weekly_report", "review"),
    ]
    assert writer_agent.skill_titles == [["Writer skill"]]
    assert review_agent.skill_titles == [["Review skill"]]
