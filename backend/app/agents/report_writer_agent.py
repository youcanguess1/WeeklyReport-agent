from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool

from app.agents.langchain_runtime import run_tool_loop
from app.agents.utils.prompt_loader import load_agent_prompt
from app.agents.utils.report_writer_support import (
    build_report_document,
    build_writer_context,
    extract_report_period,
)
from app.memory.memory_tool import AgentSessionRuntime
from app.observability import ObservedToolExecutor
from app.schemas.evidence import MaterialPack
from app.schemas.memory import SkillBrief
from app.schemas.review import ReviewResult
from app.schemas.report import WeeklyReportDocument, WeeklyReportDocumentDraft


class WeeklyReportWriterAgent:
    def __init__(
        self,
        model: BaseChatModel,
        template_path: Path | None = None,
        system_prompt: str | None = None,
        tools: list[Any] | None = None,
        tool_executor: ObservedToolExecutor | None = None,
        max_tool_rounds: int = 4,
    ) -> None:
        self._model = model
        self._template_path = template_path or (
            Path(__file__).resolve().parents[2].parent / "docs" / "weekly_report_template.md"
        )
        self._system_prompt = system_prompt or load_agent_prompt("report_writer_system_prompt.md")
        self._tools = [item if isinstance(item, BaseTool) else item for item in tools or []]
        self._tool_executor = tool_executor or ObservedToolExecutor.noop()
        self._max_tool_rounds = max(1, max_tool_rounds)
        self.last_messages = []
        self.last_turn_messages = []

    def write_report(
        self,
        material_pack: MaterialPack,
        *,
        version: str = "V0",
        project_name: str | None = None,
        task_notes: str | None = None,
        skill_briefs: list[SkillBrief] | None = None,
        previous_report: WeeklyReportDocument | None = None,
        review_result: ReviewResult | None = None,
        revision_instructions: str | None = None,
        runtime: AgentSessionRuntime | None = None,
        messages: list[BaseMessage] | None = None,
    ) -> WeeklyReportDocument:
        context_text = build_writer_context(
            material_pack=material_pack,
            project_name=project_name if previous_report is None else project_name or previous_report.project_name,
            task_notes=task_notes,
            template_path=self._template_path,
            skill_briefs=skill_briefs,
            previous_report=previous_report,
            review_result=review_result,
            revision_instructions=revision_instructions,
        )
        prior_messages = list(messages or [])
        if not any(isinstance(message, SystemMessage) for message in prior_messages):
            prior_messages.insert(0, SystemMessage(content=self._system_prompt))
        prior_messages.append(HumanMessage(content=context_text))
        result = run_tool_loop(
            model=self._model,
            messages=prior_messages,
            tools=self._tools,
            max_tool_rounds=self._max_tool_rounds,
            output_schema=WeeklyReportDocumentDraft,
            observability_provider=self._tool_executor.provider,
            observation_name="report_writer",
        )
        self.last_messages = result.messages
        self.last_turn_messages = result.new_messages
        draft = result.structured
        if not isinstance(draft, WeeklyReportDocumentDraft):
            raise RuntimeError("WeeklyReport writer did not return a structured WeeklyReport draft.")
        return build_report_document(
            draft=draft,
            version=version if previous_report is None else version or previous_report.version,
            project_name=project_name if previous_report is None else project_name or previous_report.project_name,
            report_period=(
                extract_report_period(project_name, task_notes)
                or (previous_report.report_period if previous_report is not None else None)
            ),
        )
