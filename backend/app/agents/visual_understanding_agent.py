from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.langchain_runtime import run_tool_loop
from app.agents.utils.prompt_loader import load_agent_prompt
from app.agents.utils.visual_understanding_content import build_image_content
from app.memory.memory_tool import AgentSessionRuntime
from app.observability import ObservedToolExecutor
from app.schemas.evidence import MaterialUnderstanding, MaterialUnderstandingDraft, MaterialPack


class VisualUnderstandingAgent:
    def __init__(
        self,
        model: BaseChatModel,
        system_prompt: str | None = None,
        *,
        tool_executor: ObservedToolExecutor | None = None,
    ) -> None:
        self._model = model
        self._system_prompt = system_prompt or load_agent_prompt('visual_understanding_system_prompt.md')
        self._tool_executor = tool_executor or ObservedToolExecutor.noop()
        self.last_messages = []
        self.last_turn_messages = []
        self.last_run_debug: dict[str, Any] = {'materials': []}

    def understand_material_pack(
        self,
        material_pack: MaterialPack,
        *,
        runtime: AgentSessionRuntime | None = None,
    ) -> MaterialPack:
        self.last_messages = []
        self.last_turn_messages = []
        self.last_run_debug = {'materials': []}
        understanding_by_id = {item.material_id: item for item in material_pack.understandings}
        for material in material_pack.materials:
            if material.material_id in understanding_by_id:
                continue
            result = run_tool_loop(
                model=self._model,
                messages=[
                    SystemMessage(content=self._system_prompt),
                    HumanMessage(content=build_image_content(material_pack, material)),
                ],
                tools=[],
                output_schema=MaterialUnderstandingDraft,
                observability_provider=self._tool_executor.provider,
                observation_name='material_understanding',
            )
            draft = result.structured
            if not isinstance(draft, MaterialUnderstandingDraft) or not draft.content_summary.strip():
                raise ValueError('Material understanding did not return a nonempty image summary.')
            understanding_by_id[material.material_id] = MaterialUnderstanding(
                material_id=material.material_id,
                content_summary=draft.content_summary.strip(),
            )
            self.last_messages.extend(result.messages)
            self.last_turn_messages.extend(result.new_messages)
            self.last_run_debug['materials'].append({'material_id': material.material_id})
        return material_pack.model_copy(update={
            'understandings': [understanding_by_id[material.material_id] for material in material_pack.materials],
        })
