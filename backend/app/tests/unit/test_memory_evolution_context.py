from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from app.agents.utils.memory_evolution_context import build_memory_evolution_context
from app.memory.evidence_memory import EvidenceMemory
from app.schemas.evidence import ArtifactKind, MaterialUnderstanding, ImageMaterial, MaterialPack
from app.schemas.review import ReviewIssue, ReviewResult, ReviewSeverity, WorkflowDecision
from app.schemas.report import WeeklyReportDocument
from app.schemas.task import TaskStatus, TaskType
from app.services.artifact_service import ArtifactService, StorageGroup
from app.workflows.graph_state import TaskState, WorkflowNode


def test_build_memory_evolution_context_preserves_required_sections_and_timeline(tmp_path: Path) -> None:
    service = ArtifactService(
        artifact_root=tmp_path / "artifacts",
        sqlite_path=tmp_path / "sqlite" / "weekly_report.db",
    )
    task_id = service.create_task("Memory evolution", TaskType.WEEKLY_REPORT)
    source_image = service.save_bytes(
        task_id=task_id,
        kind=ArtifactKind.IMAGE_MATERIAL,
        filename="printer.png",
        content=b"image",
        storage_group=StorageGroup.UPLOAD,
    )
    frame_ref = service.save_bytes(
        task_id=task_id,
        kind=ArtifactKind.IMAGE_MATERIAL,
        filename="frame.png",
        content=b"frame",
        storage_group=StorageGroup.DERIVED,
        parent_artifact_id=source_image.artifact_id,
    )
    material_pack = MaterialPack(
        material_pack_id="evidence-001",
        task_id=task_id,
        source_artifact_id=source_image.artifact_id,
        materials=[
            ImageMaterial(material_id="material-001", source_artifact_id=frame_ref.artifact_id, image_path=frame_ref.path, note="Open the tray.")
        ],
        understandings=[MaterialUnderstanding(material_id="material-001", content_summary="Operator opens the tray.")],
    )
    manifest_ref = service.save_json(
        task_id=task_id,
        kind=ArtifactKind.MANIFEST,
        filename="evidence.json",
        payload=material_pack.model_dump(mode="json"),
        storage_group=StorageGroup.DERIVED,
        parent_artifact_id=source_image.artifact_id,
    )
    report_v0 = WeeklyReportDocument(project_name="Printer reload", version="V0", completed_this_week=["Initial milestone"], material_refs=["material-001"])
    report_v1 = WeeklyReportDocument(project_name="Printer reload", version="V1", completed_this_week=["Confirmed milestone"], material_refs=["material-001"])
    report_ref_v0 = service.save_text(
        task_id=task_id,
        kind=ArtifactKind.WEEKLY_REPORT_MARKDOWN,
        filename="report_v0.md",
        text="# V0",
        storage_group=StorageGroup.DERIVED,
    )
    report_ref_v1 = service.save_text(
        task_id=task_id,
        kind=ArtifactKind.WEEKLY_REPORT_MARKDOWN,
        filename="report_v1.md",
        text="# V1",
        storage_group=StorageGroup.DERIVED,
        parent_artifact_id=report_ref_v0.artifact_id,
    )
    service.register_report_version(
        task_id=task_id,
        version="V0",
        markdown_artifact_id=report_ref_v0.artifact_id,
        structured_snapshot=report_v0.model_dump(mode="json"),
        change_note="Initial draft",
    )
    service.register_report_version(
        task_id=task_id,
        version="V1",
        markdown_artifact_id=report_ref_v1.artifact_id,
        structured_snapshot=report_v1.model_dump(mode="json"),
        change_note="Review revision",
    )

    review_result = ReviewResult(
        review_id="review-001",
        task_id=task_id,
        decision=WorkflowDecision.REVISE_REPORT,
        summary="Need clearer final-state wording.",
        issues=[
            ReviewIssue(
                issue_id="issue-001",
                section_id="completed_this_week",
                severity=ReviewSeverity.HIGH,
                category="missing_expected_result",
                description="The expected result is too vague.",
                suggestion="Describe the fully open tray state.",
            )
        ],
        created_at=datetime(2026, 7, 26, 12, 0, tzinfo=UTC),
    )
    final_state = TaskState(
        task_id=task_id,
        thread_id="thread-001",
        run_id="run-001",
        task_status=TaskStatus.FAILED,
        last_completed_node=WorkflowNode.MARK_FAILED,
        material_pack_ref=manifest_ref,
        current_report_version_ref=report_ref_v1,
        report_version_history=[report_ref_v0, report_ref_v1],
        latest_review_result=review_result,
        writer_round=2,
        review_round=2,
        updated_at=datetime(2026, 7, 26, 12, 15, tzinfo=UTC),
    )
    service.save_workflow_state(task_id, final_state.model_dump(mode="json"), status=TaskStatus.FAILED)

    memory_tool = service.memory_tool
    _record_session(memory_tool, task_id, "write_report", "WeeklyReportWriterAgent", 1, "Writer created the first draft.", final_result={"version": "V0"})
    _record_session(
        memory_tool,
        task_id,
        "review_report",
        "WeeklyReportReviewAgent",
        1,
        "Review requested a clearer expected result.",
        final_result=review_result.model_dump(mode="json"),
    )
    _record_session(
        memory_tool,
        task_id,
        "human_feedback",
        "WeeklyReportReviewAgent",
        2,
        "Human feedback requested clearer confirmation wording.",
        final_result={
            "feedback_text": "Please say the tray must be fully open.",
            "review_result": review_result.model_dump(mode="json"),
        },
    )

    context = build_memory_evolution_context(
        artifact_service=service,
        memory_tool=memory_tool,
        evidence_memory=EvidenceMemory(service),
        task_id=task_id,
        final_state=final_state,
    )

    assert list(context.keys()) == [
        "task_overview",
        "report_diffs",
        "chronological_event_timeline",
        "section_evidence_summaries",
    ]
    assert "failed_sessions" not in context
    assert [item["workflow_node"] for item in context["chronological_event_timeline"]] == [
        "write_report",
        "review_report",
        "human_feedback",
    ]
    assert context["chronological_event_timeline"][2]["final_result"]["feedback_text"].startswith("Please say")
    assert context["report_diffs"][0]["section_changes"][0]["section_id"] == "completed_this_week"
    assert context["section_evidence_summaries"][0]["section_id"] == "completed_this_week"


def _record_session(
    memory_tool,
    task_id: str,
    workflow_node: str,
    agent_name: str,
    round_index: int,
    response_text: str,
    *,
    final_result: dict,
) -> None:
    runtime = memory_tool.start_agent_runtime(
        task_id=task_id,
        workflow_node=workflow_node,
        agent_name=agent_name,
        round_index=round_index,
    )
    memory_tool.append_request(
        runtime,
        {"input": [{"role": "user", "content": [{"type": "input_text", "text": response_text}]}]},
    )
    memory_tool.append_response(runtime, SimpleNamespace(output_text=response_text, output=[]))
    memory_tool.complete_agent_session(runtime, final_result)


def _record_tool_session(
    memory_tool,
    task_id: str,
    workflow_node: str,
    agent_name: str,
    round_index: int,
    response_text: str,
    *,
    tool_name: str,
    tool_result: dict,
    final_result: dict,
) -> None:
    runtime = memory_tool.start_agent_runtime(
        task_id=task_id,
        workflow_node=workflow_node,
        agent_name=agent_name,
        round_index=round_index,
    )
    memory_tool.append_request(
        runtime,
        {"input": [{"role": "user", "content": [{"type": "input_text", "text": response_text}]}]},
    )
    memory_tool.append_tool_result(
        runtime,
        tool_name,
        SimpleNamespace(call_id="call-001", name=tool_name, arguments={"section_id": "completed_this_week"}),
        tool_result,
    )
    memory_tool.append_response(runtime, SimpleNamespace(output_text=response_text, output=[]))
    memory_tool.complete_agent_session(runtime, final_result)
