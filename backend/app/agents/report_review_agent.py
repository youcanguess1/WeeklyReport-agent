from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool

from app.agents.langchain_runtime import run_tool_loop
from app.agents.utils.prompt_loader import load_agent_prompt
from app.memory.memory_tool import AgentSessionRuntime
from app.observability import ObservedToolExecutor
from app.schemas.evidence import MaterialPack
from app.schemas.memory import SkillBrief
from app.schemas.review import ReviewDraft, ReviewResult
from app.schemas.report import WeeklyReportDocument
from app.tools.skill_injection_tool import format_skill_briefs

class WeeklyReportReviewAgent:
    def __init__(
        self,
        model: BaseChatModel,
        system_prompt: str | None = None,
        tools: list[Any] | None = None,
        tool_executor: ObservedToolExecutor | None = None,
        max_tool_rounds: int = 4,
    ) -> None:
        self._model = model
        self._system_prompt = system_prompt or load_agent_prompt("report_review_system_prompt.md")
        self._tools = [item for item in tools or [] if isinstance(item, BaseTool)]
        self._tool_executor = tool_executor or ObservedToolExecutor.noop()
        self._max_tool_rounds = max(1, max_tool_rounds)
        self.last_messages = []
        self.last_turn_messages = []

    def review_report(
        self,
        *,
        task_id: str,
        report_document: WeeklyReportDocument,
        task_name: str | None = None,
        task_notes: str | None = None,
        material_pack: MaterialPack | None = None,
        skill_briefs: list[SkillBrief] | None = None,
        runtime: AgentSessionRuntime | None = None,
        messages: list[BaseMessage] | None = None,
        supplement_round: int = 0,
        max_supplement_rounds: int = 4,
    ) -> ReviewResult:
        return self._request_review_result(
            task_id,
            self._build_review_context(
                task_id,
                report_document,
                task_name,
                task_notes,
                material_pack,
                skill_briefs,
                supplement_round,
                max_supplement_rounds,
            ),
            messages=messages,
        )

    def structure_human_feedback(
        self,
        *,
        task_id: str,
        feedback_text: str,
        feedback_content: list[dict[str, Any]] | None = None,
        report_document: WeeklyReportDocument | None = None,
        task_name: str | None = None,
        task_notes: str | None = None,
        material_pack: MaterialPack | None = None,
        skill_briefs: list[SkillBrief] | None = None,
        runtime: AgentSessionRuntime | None = None,
        messages: list[BaseMessage] | None = None,
        supplement_round: int = 0,
        max_supplement_rounds: int = 4,
    ) -> ReviewResult:
        return self._request_review_result(
            task_id,
            self._build_human_feedback_context(
                task_id,
                feedback_text,
                report_document,
                task_name,
                task_notes,
                material_pack,
                skill_briefs,
                supplement_round,
                max_supplement_rounds,
            ),
            user_content=feedback_content,
            messages=messages,
        )

    def _request_review_result(
        self,
        task_id: str,
        context_text: str,
        *,
        user_content: list[dict[str, Any]] | None = None,
        messages: list[BaseMessage] | None,
    ) -> ReviewResult:
        prior_messages = list(messages or [])
        if not any(isinstance(message, SystemMessage) for message in prior_messages):
            prior_messages.insert(0, SystemMessage(content=self._system_prompt))
        if user_content:
            prior_messages.append(
                HumanMessage(
                    content=[{"type": "text", "text": context_text}, *user_content]
                )
            )
        else:
            prior_messages.append(HumanMessage(content=context_text))
        result = run_tool_loop(
            model=self._model,
            messages=prior_messages,
            tools=self._tools,
            max_tool_rounds=self._max_tool_rounds,
            output_schema=ReviewDraft,
            observability_provider=self._tool_executor.provider,
            observation_name="report_review",
        )
        self.last_messages = result.messages
        self.last_turn_messages = result.new_messages
        draft = result.structured
        if not isinstance(draft, ReviewDraft):
            raise RuntimeError("WeeklyReport review did not return a structured review draft.")
        return ReviewResult(
            review_id=f"review_{uuid4().hex[:16]}",
            task_id=task_id,
            decision=draft.decision,
            summary=draft.summary,
            issues=draft.issues,
            evidence_requests=draft.evidence_requests,
            created_at=datetime.now(UTC),
        )

    @staticmethod
    def _build_review_context(
        task_id: str,
        report_document: WeeklyReportDocument,
        task_name: str | None,
        task_notes: str | None,
        material_pack: MaterialPack | None,
        skill_briefs: list[SkillBrief] | None,
        supplement_round: int,
        max_supplement_rounds: int,
    ) -> str:
        return "\n".join(
            [
                f"Task ID: {task_id}",
                f"Task Name: {task_name or report_document.project_name}",
                f"Task Notes: {task_notes.strip() if task_notes and task_notes.strip() else '(none)'}",
                "Structured Weekly Report JSON:",
                report_document.model_dump_json(indent=2),
                "",
                "Evidence Pack JSON:",
                material_pack.model_dump_json(indent=2) if material_pack else "(none)",
                "",
                "",
                "Relevant Skills (title and description only):",
                format_skill_briefs(skill_briefs),
                "Use get_skill_content with the exact title only when a brief is relevant and you need the full details.",
                "",
                "Return a single review decision with actionable issues.",
                f"Supplement evidence rounds used: {supplement_round}/{max_supplement_rounds}.",
                "Review the weekly report, not an operating procedure. It must contain the five sections: 本周完成、进行中、风险与阻塞、下周计划、待确认事项.",
                "Check that each statement is supported by task notes or existing workflow materials. Unsupported status, risk, plan, or commitment belongs in 待确认事项 or should be removed.",
                (
                    "The supplement evidence limit has been reached. Do not request more evidence; "
                    "continue with the available evidence and state any limitations."
                    if supplement_round >= max_supplement_rounds
                    else "Use the remaining supplement rounds only when the missing evidence is essential."
                ),
            ]
        )

    @staticmethod
    def _build_human_feedback_context(
        task_id: str,
        feedback_text: str,
        report_document: WeeklyReportDocument | None,
        task_name: str | None,
        task_notes: str | None,
        material_pack: MaterialPack | None,
        skill_briefs: list[SkillBrief] | None,
        supplement_round: int,
        max_supplement_rounds: int,
    ) -> str:
        return "\n".join(
            [
                f"Task ID: {task_id}",
                f"Task Name: {task_name or (report_document.project_name if report_document else '(none)')}",
                f"Task Notes: {task_notes.strip() if task_notes and task_notes.strip() else '(none)'}",
                "Current Structured Weekly Report JSON:",
                report_document.model_dump_json(indent=2) if report_document else "(none)",
                "",
                "Current Evidence Pack JSON:",
                material_pack.model_dump_json(indent=2) if material_pack else "(none)",
                "",
                "Relevant Skills (title and description only):",
                format_skill_briefs(skill_briefs),
                "",
                "Human Feedback Text:",
                feedback_text.strip(),
                "",
                "Convert the human feedback into actionable structured review issues.",
                'Preserve every explicit value, status, dependency, and requested removal from the feedback so the Writer can verify it independently.',
                "Keep the review focused on the five weekly-report sections and unsupported claims; do not request a new input field.",
                f"Supplement evidence rounds used: {supplement_round}/{max_supplement_rounds}.",
                (
                    "The user has indicated that no more evidence should be provided, does not want changes, "
                    "or wants to export directly. Return approved so the workflow can ask for final confirmation. "
                    "Do not return request_evidence or revise_report; use the current report as-is and ask whether it "
                    "should be selected as the final version."
                    if _declines_more_evidence(feedback_text)
                    else "If the user explicitly declines more evidence, do not request evidence again."
                ),
            ]
        )


def _declines_more_evidence(feedback_text: str) -> bool:
    normalized = "".join(feedback_text.lower().split())
    phrases = (
        "不再补充证据",
        "不需要补充证据",
        "不需要补充",
        "无需补充",
        "不用补充",
        "没有其他补充证据",
        "直接输出终版",
        "不需要修改",
        "无需修改",
        "不用修改",
        "直接导出",
        "直接生成终版",
        "no more evidence",
        "no additional evidence",
        "no further evidence",
        "final version directly",
    )
    return any("".join(phrase.lower().split()) in normalized for phrase in phrases)
