from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypedDict, get_type_hints

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.agents.langchain_runtime import run_tool_loop
from app.agents.report_review_agent import WeeklyReportReviewAgent
from app.agents.report_writer_agent import WeeklyReportWriterAgent
from app.agents.visual_understanding_agent import VisualUnderstandingAgent
from app.workflows.graph_state import WorkflowState
from app.schemas.evidence import ImageMaterial, MaterialPack
from app.schemas.review import ReviewResult, WorkflowDecision
from app.schemas.report import WeeklyReportDocument


def test_workflow_state_declares_reducer_backed_agent_message_keys() -> None:
    annotation = get_type_hints(WorkflowState, include_extras=True)["report_writer_messages"]
    assert annotation.__metadata__
    assert annotation.__metadata__[0].__module__ == "langgraph.graph.message"


@tool
def _add_numbers(a: int, b: int) -> int:
    """Add two numbers."""

    return a + b


class _ToolCallingModel(BaseChatModel):
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "test-tool-calling-model"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "_ToolCallingModel":
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        if not any(isinstance(message, ToolMessage) for message in messages):
            response = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "_add_numbers",
                        "args": {"a": 2, "b": 3},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            )
        else:
            response = AIMessage(content="5")
        return ChatResult(generations=[ChatGeneration(message=response)])


def test_official_tool_node_loop_executes_tool_and_returns_tool_message() -> None:
    result = run_tool_loop(
        model=_ToolCallingModel(),
        messages=[HumanMessage(content="add 2 and 3")],
        tools=[_add_numbers],
        max_tool_rounds=2,
    )

    assert any(isinstance(message, ToolMessage) for message in result.messages)
    assert result.messages[-1].content == "5"


class _CounterState(TypedDict):
    count: int


def test_memory_saver_isolated_by_thread_id() -> None:
    builder = StateGraph(_CounterState)
    builder.add_node("increment", lambda state: {"count": state["count"] + 1})
    builder.add_edge(START, "increment")
    builder.add_edge("increment", END)
    graph = builder.compile(checkpointer=MemorySaver())

    config_a = {"configurable": {"thread_id": "thread-a"}}
    config_b = {"configurable": {"thread_id": "thread-b"}}
    graph.invoke({"count": 1}, config=config_a)
    graph.invoke({"count": 10}, config=config_b)

    assert graph.get_state(config_a).values["count"] == 2
    assert graph.get_state(config_b).values["count"] == 11


class _AgentModel(BaseChatModel):
    payload: dict[str, object] = {}
    pending_tool_calls: list[dict[str, object]] = []

    @property
    def _llm_type(self) -> str:
        return "test-agent-model"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "_AgentModel":
        return self

    def with_structured_output(self, schema: Any, **kwargs: Any) -> RunnableLambda:
        return RunnableLambda(lambda _: schema.model_validate(self.payload))

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if self.pending_tool_calls:
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[self.pending_tool_calls.pop(0)],
                        )
                    )
                ]
            )
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="done"))])


def _material_pack() -> MaterialPack:
    return MaterialPack(
        material_pack_id="evidence-001",
        task_id="task-001",
        source_artifact_id="artifact-001",
        materials=[
            ImageMaterial(material_id="material-001", source_artifact_id="artifact-001", image_path="/tmp/material.png", note="Open the tray.")
        ],
    )


def test_langchain_agents_use_chat_model_and_structured_output() -> None:
    writer_model = _AgentModel(
        payload={
            "project_name": "Printer reload",
            "completed_this_week": ["Task notes confirm the paper reload was completed."],
            "in_progress": ["Printer validation remains in progress."],
            "risks_and_blockers": [],
            "next_week_plan": [],
            "pending_confirmations": ["Confirm the planned validation date."],
        }
    )
    writer = WeeklyReportWriterAgent(writer_model, system_prompt="writer")
    report = writer.write_report(
        _material_pack(),
        project_name="Printer reload",
        task_notes="Reload is complete; validation date is not confirmed.",
    )
    assert isinstance(report, WeeklyReportDocument)
    assert report.completed_this_week == ["Task notes confirm the paper reload was completed."]
    assert report.in_progress == ["Printer validation remains in progress."]
    assert report.pending_confirmations == ["Confirm the planned validation date."]
    assert set(report.model_dump()).issuperset({"completed_this_week", "in_progress", "risks_and_blockers", "next_week_plan", "pending_confirmations"})
    assert any(
        isinstance(message, HumanMessage)
        and "Task Name: Printer reload" in str(message.content)
        and "Task Notes: Reload is complete; validation date is not confirmed." in str(message.content)
        for message in writer.last_messages
    )

    review_model = _AgentModel(
        payload={
            "decision": "approved",
            "summary": "WeeklyReport is complete.",
            "issues": [],
            "evidence_requests": [],
        }
    )
    review = WeeklyReportReviewAgent(review_model, system_prompt="review")
    result = review.review_report(
        task_id="task-001",
        task_name="Printer reload",
        task_notes="Reload is complete; validation date is not confirmed.",
        report_document=report,
    )
    assert result.decision.value == "approved"
    assert any(
        isinstance(message, HumanMessage)
        and "Task Name: Printer reload" in str(message.content)
        and "Task Notes: Reload is complete; validation date is not confirmed." in str(message.content)
        for message in review.last_messages
    )


def test_writer_uses_latest_feedback_and_explicit_report_period() -> None:
    writer_model = _AgentModel(
        payload={
            'project_name': 'Release weekly report',
            'completed_this_week': [],
            'in_progress': ['Detail page is 90% complete and has not been submitted for QA.'],
            'risks_and_blockers': [],
            'next_week_plan': [],
            'pending_confirmations': [],
        }
    )
    previous_report = WeeklyReportDocument(project_name='Release weekly report', version='V0', report_period='2026-10-12 \u81f3 2026-10-16', in_progress=['Detail page is 80% complete.'], completed_this_week=["Completed project milestone"])
    review_result = ReviewResult(
        review_id='review-001',
        task_id='task-001',
        decision=WorkflowDecision.REVISE_REPORT,
        summary='Apply the user corrections.',
        created_at=datetime(2026, 10, 26, tzinfo=UTC),
    )
    instructions = 'Change detail-page progress from 80% to 90%; it has not been submitted for QA.'
    writer = WeeklyReportWriterAgent(writer_model, system_prompt='writer')

    revised = writer.write_report(
        _material_pack(),
        version='V1',
        project_name='Release weekly report | 2026-10-19 - 2026-10-23',
        task_notes='The detail page was previously recorded as 80% complete.',
        previous_report=previous_report,
        review_result=review_result,
        revision_instructions=instructions,
    )

    context = '\n'.join(
        str(message.content)
        for message in writer.last_messages
        if isinstance(message, HumanMessage)
    )
    assert revised.report_period == '2026-10-19 \u81f3 2026-10-23'
    assert revised.in_progress == ['Detail page is 90% complete and has not been submitted for QA.']
    assert 'Latest User Revision Instructions:' in context
    assert instructions in context
    assert 'take priority over task notes and the previous weekly report' in context


def test_visual_agent_reads_image_material(tmp_path) -> None:
    model = _AgentModel(payload={"content_summary": "The operator opens the tray."})
    agent = VisualUnderstandingAgent(model, system_prompt="image")
    from PIL import Image


    image_path = tmp_path / "material.png"
    Image.new("RGB", (16, 16)).save(image_path)
    pack = _material_pack()
    pack.materials[0].image_path = str(image_path)
    result = agent.understand_material_pack(pack)
    assert [item.material_id for item in result.understandings] == ["material-001"]
    assert result.understandings[0].content_summary.startswith("The operator")
