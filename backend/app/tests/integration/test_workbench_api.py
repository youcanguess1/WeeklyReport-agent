from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from app.api.routes_tasks import router as tasks_router
from app.schemas.evidence import ArtifactKind, ImageMaterial, MaterialPack
from app.schemas.report import WeeklyReportDocument
from app.tools.report_render_tool import WeeklyReportRenderTool
from app.workflows.graph_io import build_material_pack_saver
from app.schemas.task import TaskStatus, TaskType
from app.schemas.trace import TraceLogLevel
from app.services.artifact_service import ArtifactService, StorageGroup
from app.services.task_execution_service import TaskExecutionService
from app.services.task_query_service import TaskQueryService
from app.services.task_runner import TaskRunner, TaskSubmission
from app.services.workbench_query_service import WorkbenchQueryService
from app.workflows.graph_nodes import WeeklyReportGraphNodes
from app.workflows.graph_state import TaskState, WorkflowNode, WorkflowWaitReason


PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\x0dIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00"
    b"\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
)


class RecordingRunner(TaskRunner):
    def __init__(self) -> None:
        self.submissions: list[tuple[object, object, tuple[object, ...]]] = []

    def submit(self, task_name, func, *args, **kwargs):
        self.submissions.append((task_name, func, args))
        return TaskSubmission(task_name, task_name, "fake", datetime.now(UTC))

    def get_status(self, task_id: str) -> str:
        return "pending"

    def get_result(self, task_id: str, timeout: float | None = None):
        raise KeyError(task_id)

    def shutdown(self, wait: bool = True) -> None:
        return None


class FakePreprocess:
    def __init__(self) -> None:
        self.calls = 0

    def preprocess_image(self, **kwargs):
        self.calls += 1
        return MaterialPack(
            material_pack_id="evidence-image",
            task_id=kwargs["task_id"],
            source_artifact_id=kwargs["source_image"].artifact_id,
        )


class FakeImageUnderstandingAgent:
    def understand_material_pack(self, material_pack):
        return material_pack


def _app(tmp_path: Path) -> tuple[FastAPI, ArtifactService]:
    artifact_service = ArtifactService(tmp_path / "artifacts", tmp_path / "sqlite" / "weekly_report.db")
    task_query_service = TaskQueryService(artifact_service)
    app = FastAPI()
    app.state.artifact_service = artifact_service
    app.state.task_query_service = task_query_service
    app.state.workbench_query_service = WorkbenchQueryService(artifact_service, task_query_service)
    app.include_router(tasks_router)
    return app, artifact_service


def test_empty_workbench_and_report_versions_are_safe(tmp_path: Path) -> None:
    app, artifact_service = _app(tmp_path)
    task_id = artifact_service.create_task("No output", TaskType.WEEKLY_REPORT)

    with TestClient(app) as client:
        task = client.get(f"/api/tasks/{task_id}")
        events = client.get(f"/api/tasks/{task_id}/events")
        versions = client.get(f"/api/tasks/{task_id}/weekly-report-versions")

    assert task.status_code == 200
    assert task.json()["status"] == TaskStatus.CREATED.value
    assert events.json()["events"] == []
    assert versions.json() == []


def test_events_show_node_lifecycle_and_tool_actions_without_private_payloads(tmp_path: Path) -> None:
    app, artifact_service = _app(tmp_path)
    task_id = artifact_service.create_task("Message task", TaskType.WEEKLY_REPORT)
    trace_service = app.state.workbench_query_service.event_query.trace_service
    trace = trace_service.start_task_trace(task_id, "material_understanding")
    trace_service.append_log(trace.trace_id, TraceLogLevel.INFO, "material_understanding", "node_started", "private start")
    runtime = artifact_service.memory_tool.start_agent_runtime(
        task_id=task_id,
        workflow_node="material_understanding",
        agent_name="WeeklyReportReviewAgent",
        round_index=1,
    )
    artifact_service.memory_tool.append_tool_result(
        runtime,
        "get_skill_content",
        {"id": "call-1", "name": "get_skill_content", "arguments": {"title": "secret"}},
        {"secret": "private result"},
    )
    artifact_service.memory_tool.complete_agent_session(runtime, {"understanding_count": 2})
    trace_service.append_log(trace.trace_id, TraceLogLevel.INFO, "material_understanding", "node_completed", "private completion")

    with TestClient(app) as client:
        response = client.get(f"/api/tasks/{task_id}/events")

    assert response.status_code == 200
    payload = response.json()["events"]
    assert [item["event_type"] for item in payload] == ["node_started", "action", "node_completed"]
    assert payload[1]["action_label"] == "读取技能内容"
    assert payload[2]["content"] == "材料分析完成，识别出 2 个内容阶段"
    assert "secret" not in response.text
    assert "private result" not in response.text


def test_events_cursor_returns_only_new_tool_actions(tmp_path: Path) -> None:
    app, artifact_service = _app(tmp_path)
    task_id = artifact_service.create_task("Incremental task", TaskType.WEEKLY_REPORT)
    runtime = artifact_service.memory_tool.start_agent_runtime(
        task_id=task_id,
        workflow_node="material_understanding",
        agent_name="WeeklyReportReviewAgent",
        round_index=1,
    )
    artifact_service.memory_tool.append_tool_result(runtime, "get_skill_content", {"id": "call-1"}, {})
    query_service = app.state.workbench_query_service

    initial = query_service.list_events(task_id)
    assert [event.action_label for event in initial.events] == ["读取技能内容"]
    assert initial.cursor

    artifact_service.memory_tool.append_tool_result(runtime, "get_skill_content", {"id": "call-2"}, {})
    delta = query_service.list_events(task_id, initial.cursor)

    assert [event.action_label for event in delta.events] == ["读取技能内容"]
    assert delta.events[0].event_id.endswith(":2")


def test_events_restore_persisted_human_feedback_messages(tmp_path: Path) -> None:
    app, artifact_service = _app(tmp_path)
    task_id = artifact_service.create_task("Feedback task", TaskType.WEEKLY_REPORT)
    runtime = artifact_service.memory_tool.start_agent_runtime(
        task_id=task_id,
        workflow_node="human_feedback",
        agent_name="User",
        round_index=1,
    )
    artifact_service.memory_tool.append_messages(
        runtime,
        [HumanMessage(content="No more evidence is available.", additional_kwargs={"request_id": "req-1"})],
    )
    artifact_service.memory_tool.complete_agent_session(runtime, {"feedback_text": "No more evidence is available."})

    with TestClient(app) as client:
        response = client.get(f"/api/tasks/{task_id}/events")

    assert response.status_code == 200
    events = response.json()["events"]
    assert [(item["role"], item["content"], item["request_id"]) for item in events] == [
        ("user", "No more evidence is available.", "req-1"),
    ]


def test_feedback_review_result_is_projected_as_review_event(tmp_path: Path) -> None:
    app, artifact_service = _app(tmp_path)
    task_id = artifact_service.create_task("Feedback review", TaskType.WEEKLY_REPORT)
    runtime = artifact_service.memory_tool.start_agent_runtime(
        task_id=task_id,
        workflow_node="human_feedback",
        agent_name="WeeklyReportReviewAgent",
        round_index=1,
    )
    artifact_service.memory_tool.append_messages(
        runtime,
        [HumanMessage(content="不需要修改，直接导出", additional_kwargs={"request_id": "req-final"})],
    )
    artifact_service.memory_tool.complete_agent_session(
        runtime,
        {
            "feedback_text": "不需要修改，直接导出",
            "review_result": {"decision": "approved", "summary": "approved"},
        },
    )
    trace_service = app.state.workbench_query_service.event_query.trace_service
    trace = trace_service.start_task_trace(task_id, "start_workflow")
    trace_service.append_log(trace.trace_id, TraceLogLevel.INFO, "start_workflow", "node_completed", "started")
    trace_service.append_log(trace.trace_id, TraceLogLevel.INFO, "wait_for_human", "node_completed", "hidden wait")

    response = TestClient(app).get(f"/api/tasks/{task_id}/events")

    assert response.status_code == 200
    events = response.json()["events"]
    assert any(item["agent"] == "周报准备" for item in events)
    assert any(
        item["agent"] == "周报 Review"
        and "是否选择当前周报作为最终版" in item["content"]
        for item in events
    )
    assert not any(item["agent"] in {"Workflow", "人工处理"} for item in events)


def test_review_event_keeps_evidence_details_for_waiting_node(tmp_path: Path) -> None:
    app, artifact_service = _app(tmp_path)
    task_id = artifact_service.create_task("Evidence review", TaskType.WEEKLY_REPORT)
    runtime = artifact_service.memory_tool.start_agent_runtime(
        task_id=task_id,
        workflow_node="review_report",
        agent_name="WeeklyReportReviewAgent",
        round_index=1,
    )
    artifact_service.memory_tool.complete_agent_session(
        runtime,
        {
            "review_result": {
                "review_id": "review_private",
                "decision": "request_evidence",
                "issues": [{"requires_evidence": True, "description": "无法确认最新项目状态"}],
                "evidence_requests": [{"reason": "需要展示项目完成状态的截图", "requested_inputs": ["image"]}],
            }
        },
    )
    trace_service = app.state.workbench_query_service.event_query.trace_service
    trace = trace_service.start_task_trace(task_id, "review_report")
    trace_service.append_log(trace.trace_id, TraceLogLevel.INFO, "review_report", "node_completed", "private review")

    response = TestClient(app).get(f"/api/tasks/{task_id}/events")

    assert response.status_code == 200
    content = response.json()["events"][0]["content"]
    assert "审核发现需要补充证据" in content
    assert "无法确认最新项目状态" in content
    assert "需要展示项目完成状态的截图" in content
    assert "request_evidence" not in content
    assert "review_private" not in content


def test_max_review_event_requests_final_report_confirmation(tmp_path: Path) -> None:
    app, artifact_service = _app(tmp_path)
    task_id = artifact_service.create_task("Review limit", TaskType.WEEKLY_REPORT)
    state = TaskState(
        task_id=task_id,
        thread_id=f"thread_{task_id}",
        run_id=f"run_{task_id}",
        task_status=TaskStatus.WAITING,
        waiting_reason=WorkflowWaitReason.HUMAN,
        last_completed_node=WorkflowNode.WAIT_FOR_HUMAN,
        review_round=3,
        max_review_rounds=3,
    )
    artifact_service.save_workflow_state(task_id, state.model_dump(mode="json"), status=state.task_status)
    runtime = artifact_service.memory_tool.start_agent_runtime(
        task_id=task_id,
        workflow_node="review_report",
        agent_name="WeeklyReportReviewAgent",
        round_index=3,
    )
    artifact_service.memory_tool.complete_agent_session(
        runtime,
        {"decision": "revise_report", "summary": "unresolved issues"},
    )
    trace_service = app.state.workbench_query_service.event_query.trace_service
    trace = trace_service.start_task_trace(task_id, "review_report")
    trace_service.append_log(trace.trace_id, TraceLogLevel.INFO, "review_report", "node_completed", "private review")

    response = TestClient(app).get(f"/api/tasks/{task_id}/events")

    assert response.status_code == 200
    assert response.json()["events"][0]["content"] == (
        "已达到自动审核轮次上限，当前周报仍有未解决问题。\n"
        "是否选择当前周报作为最终版？"
    )


def test_evidence_waiting_event_includes_review_details(tmp_path: Path) -> None:
    app, artifact_service = _app(tmp_path)
    task_id = artifact_service.create_task("Evidence waiting", TaskType.WEEKLY_REPORT)
    runtime = artifact_service.memory_tool.start_agent_runtime(
        task_id=task_id,
        workflow_node="review_report",
        agent_name="WeeklyReportReviewAgent",
        round_index=1,
    )
    artifact_service.memory_tool.complete_agent_session(
        runtime,
        {
            "review_result": {
                "decision": "request_evidence",
                "issues": [{"requires_evidence": True, "description": "无法确认最终结果"}],
                "evidence_requests": [{"reason": "需要展示完成画面", "requested_inputs": ["image"]}],
            }
        },
    )
    trace_service = app.state.workbench_query_service.event_query.trace_service
    trace = trace_service.start_task_trace(task_id, "wait_for_evidence")
    trace_service.append_log(
        trace.trace_id,
        TraceLogLevel.INFO,
        "review_report",
        "node_completed",
        "private review",
    )
    trace_service.append_log(
        trace.trace_id,
        TraceLogLevel.INFO,
        "wait_for_evidence",
        "node_started",
        "private waiting start",
    )

    response = TestClient(app).get(f"/api/tasks/{task_id}/events")

    assert response.status_code == 200
    events = response.json()["events"]
    review_event = next(item for item in events if item["agent"] == "周报 Review")
    assert "审核发现需要补充证据" in review_event["content"]
    assert "无法确认最终结果" in review_event["content"]
    assert "需要展示完成画面" in review_event["content"]
    assert not any(item["agent"] == "补充证据" for item in events)


def test_failed_event_keeps_error_readable_without_internal_details(tmp_path: Path) -> None:
    app, artifact_service = _app(tmp_path)
    task_id = artifact_service.create_task("Failed workflow", TaskType.WEEKLY_REPORT)
    trace_service = app.state.workbench_query_service.event_query.trace_service
    trace = trace_service.start_task_trace(task_id, "material_understanding")
    trace_service.append_log(
        trace.trace_id,
        TraceLogLevel.ERROR,
        "material_understanding",
        "node_failed",
        "Workflow node material_understanding failed: task_1234567890 could not open input\nTraceback (most recent call last): {\"artifact_id\": \"artifact_secret\"}",
    )

    response = TestClient(app).get(f"/api/tasks/{task_id}/events")

    assert response.status_code == 200
    content = response.json()["events"][0]["content"]
    assert content == "处理失败：内部标识 could not open input"
    assert "task_1234567890" not in content
    assert "artifact_secret" not in content
    assert "Traceback" not in content


def test_completed_event_reports_existing_weekly_report_path(tmp_path: Path) -> None:
    app, artifact_service = _app(tmp_path)
    task_id = artifact_service.create_task("Completed WeeklyReport", TaskType.WEEKLY_REPORT)
    artifact = artifact_service.save_text(
        task_id,
        ArtifactKind.WEEKLY_REPORT_MARKDOWN,
        "report_v1.md",
        "# Printer WeeklyReport",
        StorageGroup.DERIVED,
    )
    artifact_service.register_report_version(task_id, "V1", artifact.artifact_id, change_note="Initial")
    trace_service = app.state.workbench_query_service.event_query.trace_service
    trace = trace_service.start_task_trace(task_id, "mark_completed")
    trace_service.append_log(trace.trace_id, TraceLogLevel.INFO, "mark_completed", "node_completed", "private completion")

    response = TestClient(app).get(f"/api/tasks/{task_id}/events")

    assert response.status_code == 200
    content = response.json()["events"][0]["content"]
    assert "项目周报已生成" in content
    assert str(artifact_service.artifact_root) in content


def test_report_version_response_contains_markdown_not_absolute_path(tmp_path: Path) -> None:
    app, artifact_service = _app(tmp_path)
    task_id = artifact_service.create_task("WeeklyReport task", TaskType.WEEKLY_REPORT)
    artifact = artifact_service.save_text(
        task_id,
        ArtifactKind.WEEKLY_REPORT_MARKDOWN,
        "report_v0.md",
        "# Printer WeeklyReport\n\nStep one.",
        StorageGroup.DERIVED,
    )
    artifact_service.register_report_version(task_id, "V0", artifact.artifact_id, change_note="Initial")

    with TestClient(app) as client:
        response = client.get(f"/api/tasks/{task_id}/weekly-report-versions")

    assert response.status_code == 200
    payload = response.json()[0]
    assert payload["markdown"] == "# Printer WeeklyReport\n\nStep one."
    assert "path" not in payload
    assert str(artifact_service.artifact_root) not in response.text


def test_task_artifact_content_serves_owned_images_only(tmp_path: Path) -> None:
    app, artifact_service = _app(tmp_path)
    task_id = artifact_service.create_task("Image task", TaskType.WEEKLY_REPORT)
    image = artifact_service.save_bytes(
        task_id=task_id,
        kind=ArtifactKind.IMAGE_MATERIAL,
        filename="frame.png",
        content=PNG_1X1,
        storage_group=StorageGroup.DERIVED,
    )

    with TestClient(app) as client:
        response = client.get(f"/api/tasks/{task_id}/artifacts/{image.artifact_id}/content")
        wrong_task = client.get(f"/api/tasks/other/artifacts/{image.artifact_id}/content")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content == PNG_1X1
    assert wrong_task.status_code == 404


def test_start_persists_initial_evidence_and_task_thread(tmp_path: Path) -> None:
    app, artifact_service = _app(tmp_path)
    runner = RecordingRunner()
    preprocess = FakePreprocess()
    app.state.task_execution_service = TaskExecutionService(
        artifact_service=artifact_service,
        task_runner=runner,
        material_preprocess_tool=preprocess,
        graph_nodes=WeeklyReportGraphNodes(

            visual_understanding_agent=FakeImageUnderstandingAgent(),
        ),
        checkpointer=MemorySaver(),
    )

    with TestClient(app) as client:
        created = client.post(
            "/api/tasks",
            data={"task_name": "Start me", "task_type": TaskType.WEEKLY_REPORT.value, "notes": "Project notes"},
            files=[("files", ("input.png", PNG_1X1, "image/png"))],
        ).json()["task"]
        started = client.post(f"/api/tasks/{created['task_id']}/start")

    assert started.status_code == 200
    assert started.json()["status"] == TaskStatus.QUEUED.value
    assert len(runner.submissions) == 1
    runner.submissions[0][1](*runner.submissions[0][2])
    state = TaskState.model_validate(artifact_service.load_workflow_state(created["task_id"]))
    assert state.thread_id == f"thread_{created['task_id']}"
    assert state.material_pack_ref is not None
    assert preprocess.calls == 1


def test_control_conflict_returns_409(tmp_path: Path) -> None:
    app, artifact_service = _app(tmp_path)
    task_id = artifact_service.create_task("Already done", TaskType.WEEKLY_REPORT, status=TaskStatus.COMPLETED)

    class RejectingExecution:
        def cancel(self, task_id: str):
            from app.services.task_execution_service import TaskControlError

            raise TaskControlError("Terminal tasks cannot be canceled.")

    app.state.task_execution_service = RejectingExecution()
    with TestClient(app) as client:
        response = client.post(f"/api/tasks/{task_id}/cancel")

    assert response.status_code == 409


def test_report_versions_keep_image_snapshots_after_supplement(tmp_path: Path) -> None:
    app, artifact_service = _app(tmp_path)
    task_id = artifact_service.create_task("Milestone report", TaskType.WEEKLY_REPORT)
    images = [
        artifact_service.save_bytes(
            task_id=task_id,
            kind=ArtifactKind.IMAGE_MATERIAL,
            filename=f"material-{index}.png",
            content=PNG_1X1,
            storage_group=StorageGroup.UPLOAD,
        )
        for index in range(2)
    ]
    materials = [
        ImageMaterial(material_id=f"material-{index}", source_artifact_id=image.artifact_id, image_path=image.path)
        for index, image in enumerate(images)
    ]
    initial_pack = MaterialPack(
        material_pack_id="initial-materials",
        task_id=task_id,
        source_artifact_id=images[0].artifact_id,
        materials=materials[:1],
    )
    tool = WeeklyReportRenderTool(artifact_service)
    document = WeeklyReportDocument(project_name="Milestone", completed_this_week=["Completed milestone"])
    tool.render_and_save(task_id=task_id, report_document=document, material_pack=initial_pack)
    supplemented_pack = initial_pack.model_copy(update={"materials": materials})
    tool.render_and_save(
        task_id=task_id,
        report_document=document.model_copy(update={"version": "V1"}),
        material_pack=supplemented_pack,
    )
    state = TaskState(task_id=task_id, thread_id=f"thread_{task_id}", run_id="snapshot-test")
    material_ref = build_material_pack_saver(artifact_service)(state, supplemented_pack)
    artifact_service.save_workflow_state(task_id, state.model_copy(update={"material_pack_ref": material_ref}).model_dump(mode="json"))

    response = TestClient(app).get(f"/api/tasks/{task_id}/weekly-report-versions")

    assert response.status_code == 200
    by_version = {item["version"]: item for item in response.json()}
    assert [item["material_id"] for item in by_version["V0"]["evidence_images"]] == ["material-0"]
    assert [item["material_id"] for item in by_version["V1"]["evidence_images"]] == ["material-0", "material-1"]
    assert by_version["V0"]["evidence_images"][0]["url"] == f"/api/tasks/{task_id}/artifacts/{images[0].artifact_id}/content"
    assert str(artifact_service.artifact_root) not in response.text
