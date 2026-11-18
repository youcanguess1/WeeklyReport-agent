from __future__ import annotations

from pathlib import Path

from app.memory.skill_memory import SkillMemory
from app.memory.skill_selector import SkillSelector
from app.schemas.memory import MemoryKind, SkillStatus, SkillTrigger
from app.tools.skill_injection_tool import SkillInjectionTool


def test_skill_injection_tool_loads_content_by_title(tmp_path: Path) -> None:
    memory = SkillMemory(tmp_path / "sqlite" / "skills.db")
    selector = SkillSelector(memory)
    tool = SkillInjectionTool(selector)
    memory.create_skill(
        title="Review final state",
        description="Ask for a visible end state.",
        kind=MemoryKind.REVIEW_RULE,
        status=SkillStatus.ACTIVE,
        content="Ask for the visible end state, badge, dialog close, or tray alignment.",
        trigger=SkillTrigger(task_type="weekly_report", agent_stage="review"),
        confidence=0.91,
        hit_count=3,
    )

    result = tool.execute({"title": "Review final state"})

    assert result["status"] == "ok"
    assert result["title"] == "Review final state"
    assert "visible end state" in result["content"]


def test_skill_injection_tool_returns_not_found_without_raising(tmp_path: Path) -> None:
    memory = SkillMemory(tmp_path / "sqlite" / "skills.db")
    selector = SkillSelector(memory)
    tool = SkillInjectionTool(selector)

    result = tool.execute({"title": "Unknown skill"})

    assert result["status"] == "not_found"
    assert "Unknown skill" in result["summary"]
