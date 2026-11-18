from __future__ import annotations

from typing import Any

__all__ = [
    "AgentSessionStatus",
    "AgentTrimPolicy",
    "EvidenceMemory",
    "MemoryTool",
    "SkillMemory",
    "SkillSelector",
    "MaterialEvidenceRecord",
    "TaskMemory",
]


def __getattr__(name: str) -> Any:
    if name in {"AgentSessionStatus", "AgentTrimPolicy", "MemoryTool"}:
        from app.memory.memory_tool import AgentSessionStatus, AgentTrimPolicy, MemoryTool

        return {
            "AgentSessionStatus": AgentSessionStatus,
            "AgentTrimPolicy": AgentTrimPolicy,
            "MemoryTool": MemoryTool,
        }[name]
    if name in {"EvidenceMemory", "MaterialEvidenceRecord"}:
        from app.memory.evidence_memory import EvidenceMemory, MaterialEvidenceRecord

        return {
            "EvidenceMemory": EvidenceMemory,
            "MaterialEvidenceRecord": MaterialEvidenceRecord,
        }[name]
    if name == "SkillMemory":
        from app.memory.skill_memory import SkillMemory

        return SkillMemory
    if name == "SkillSelector":
        from app.memory.skill_selector import SkillSelector

        return SkillSelector
    if name == "TaskMemory":
        from app.memory.task_memory import TaskMemory

        return TaskMemory
    raise AttributeError(name)
