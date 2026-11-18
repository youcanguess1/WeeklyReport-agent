from __future__ import annotations

import json
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool

from app.agents.langchain_runtime import run_tool_loop
from app.agents.utils.memory_evolution_context import build_memory_evolution_context
from app.agents.utils.prompt_loader import load_agent_prompt
from app.memory.evidence_memory import EvidenceMemory
from app.memory.skill_memory import SkillMemory
from app.memory.skill_selector import SkillSelector
from app.observability import ObservedToolExecutor
from app.services.artifact_service import ArtifactService
from app.tools.langchain_tools import ListSimilarSkillsTool, RecordSkillCandidateTool
from app.workflows.graph_state import TaskState

class MemoryEvolutionAgent:
    """Official LangChain implementation for terminal memory evolution."""

    def __init__(
        self,
        model: BaseChatModel,
        artifact_service: ArtifactService,
        *,
        skill_memory: SkillMemory | None = None,
        skill_selector: SkillSelector | None = None,
        system_prompt: str | None = None,
        tool_executor: ObservedToolExecutor | None = None,
        max_tool_rounds: int = 8,
        max_skill_writes: int = 3,
    ) -> None:
        self._model = model
        self._artifact_service = artifact_service
        self._memory_tool = artifact_service.memory_tool
        self._evidence_memory = EvidenceMemory(artifact_service)
        self._skill_memory = skill_memory or SkillMemory(artifact_service.sqlite_path)
        self._skill_selector = skill_selector or SkillSelector(self._skill_memory)
        self._system_prompt = system_prompt or load_agent_prompt("memory_evolution_system_prompt.md")
        self._tool_executor = tool_executor or ObservedToolExecutor.noop()
        self._max_tool_rounds = max(1, max_tool_rounds)
        self._max_skill_writes = max(1, max_skill_writes)
        self.last_messages = []

    def evolve_task_memory(self, *, task_id: str, final_state: TaskState) -> str | None:
        context = build_memory_evolution_context(
            artifact_service=self._artifact_service,
            memory_tool=self._memory_tool,
            evidence_memory=self._evidence_memory,
            task_id=task_id,
            final_state=final_state,
        )
        tools: list[BaseTool] = [
            ListSimilarSkillsTool(self._skill_selector),
            RecordSkillCandidateTool(self._skill_memory),
        ]
        try:
            result = run_tool_loop(
                model=self._model,
                messages=[
                    SystemMessage(content=self._system_prompt),
                    HumanMessage(content=self._context_text(context)),
                ],
                tools=tools,
                max_tool_rounds=self._max_tool_rounds,
                observability_provider=self._tool_executor.provider,
                observation_name="memory_evolution",
            )
            self.last_messages = result.messages
            for message in reversed(result.messages):
                if isinstance(message, AIMessage) and message.content:
                    return str(message.content)
        except Exception:
            return None
        return None

    @staticmethod
    def _context_text(context: dict[str, Any]) -> str:
        return "\n\n".join(
            [
                "Task overview:",
                json.dumps(context["task_overview"], ensure_ascii=False, indent=2),
                "WeeklyReport diffs:",
                json.dumps(context["report_diffs"], ensure_ascii=False, indent=2),
                "Chronological event timeline:",
                json.dumps(context["chronological_event_timeline"], ensure_ascii=False, indent=2),
                "Report section evidence summaries:",
                json.dumps(context["section_evidence_summaries"], ensure_ascii=False, indent=2),
            ]
        )
