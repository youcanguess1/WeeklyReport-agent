from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.memory.memory_tool import AgentTrimPolicy, MemoryTool
from app.schemas.task import TaskType
from app.services.artifact_service import ArtifactService


def test_memory_tool_replays_trimmed_context_and_records_jsonl(tmp_path: Path) -> None:
    service = ArtifactService(
        artifact_root=tmp_path / "artifacts",
        sqlite_path=tmp_path / "sqlite" / "weekly_report.db",
    )
    task_id = service.create_task("Session flow", TaskType.WEEKLY_REPORT)
    memory_tool = MemoryTool(service.artifact_root, service.sqlite_path)
    runtime = memory_tool.start_agent_runtime(
        task_id=task_id,
        workflow_node="review_report",
        agent_name="WeeklyReportReviewAgent",
        round_index=1,
    )

    request_kwargs = {
        "model": "gpt-4.1-mini",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "base context"}]}],
    }
    memory_tool.append_request(runtime, request_kwargs)
    memory_tool.append_response(
        runtime,
        SimpleNamespace(
            output_text="Assistant acknowledged the tool result.",
            output=[
                SimpleNamespace(
                    type="message",
                    role="assistant",
                    content=[SimpleNamespace(type="output_text", text="Assistant acknowledged the tool result.")],
                )
            ],
        ),
    )
    memory_tool.append_tool_result(
        runtime,
        "get_skill_content",
        SimpleNamespace(call_id="call-001", name="get_skill_content", arguments={"title": "Weekly report review"}),
        {
            "status": "passed",
            "summary": "x" * 5000,
            "response_content": [
                {"type": "input_text", "text": "Loaded the review guideline."},
                {"type": "input_image", "image_url": "data:image/png;base64,new", "artifact_ref": "frame-new"},
            ],
        },
    )

    replayed = memory_tool.replay_runtime(
        runtime.agent_session_id,
        [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "base context"},
                    {"type": "input_image", "image_url": "data:image/png;base64,old", "artifact_ref": "frame-old"},
                ],
            }
        ],
        policy=AgentTrimPolicy(max_text_chars=20, keep_last_images=1),
    )
    records = memory_tool.load_records(runtime.agent_session_id)
    session_after = memory_tool.get_agent_session(runtime.agent_session_id)

    assert [record["record_type"] for record in records] == ["request", "response", "tool_result"]
    assert records[0]["payload"]["input"][0]["content"][0]["text"] == "base context"
    assert any(item.get("type") == "function_call_output" for item in replayed.context_items)
    old_image_message = next(
        item
        for item in replayed.context_items
        if item.get("role") == "user"
        and item["content"][1]["type"] == "input_text"
        and "Older image omitted" in item["content"][1]["text"]
    )
    assert "frame-old" in old_image_message["content"][1]["text"]
    assert session_after["latest_event_seq"] == 3
    assert "latest_context_json" not in session_after


def test_memory_tool_serializes_sdk_like_responses(tmp_path: Path) -> None:
    memory_tool = MemoryTool(tmp_path / "artifacts", tmp_path / "sqlite" / "weekly_report.db")
    payload = memory_tool.serialize_response(
        SimpleNamespace(
            id="resp-001",
            status="completed",
            output_text="Hello",
            output=[SimpleNamespace(type="message", role="assistant", content=[SimpleNamespace(type="output_text", text="Hello")])],
            usage=SimpleNamespace(total_tokens=42),
            error=None,
        )
    )

    assert payload["id"] == "resp-001"
    assert payload["status"] == "completed"
    assert payload["output_text"] == "Hello"
    assert payload["usage"]["total_tokens"] == 42
