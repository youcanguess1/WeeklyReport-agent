from __future__ import annotations

from pathlib import Path

from app.memory.skill_memory import SkillMemory
from app.memory.skill_selector import SkillSelector
from app.schemas.memory import MemoryKind, SkillStatus, SkillTrigger


def test_skill_selector_prefers_more_specific_active_skills(tmp_path: Path) -> None:
    memory = SkillMemory(tmp_path / "sqlite" / "skills.db")
    selector = SkillSelector(memory, default_limit=3)

    general = memory.create_skill(
        title="General writer clarity",
        description="Prefer explicit action verbs and visible results.",
        kind=MemoryKind.WRITER_SKILL,
        status=SkillStatus.ACTIVE,
        content="Use explicit action verbs and observable outcomes.",
        trigger=SkillTrigger(agent_stage="writer"),
        confidence=0.75,
        hit_count=2,
    )
    task_specific = memory.create_skill(
        title="Computer writer locators",
        description="Mention UI region or color when controls are duplicated.",
        kind=MemoryKind.WRITER_SKILL,
        status=SkillStatus.ACTIVE,
        content="For duplicated controls, mention dialog region, color, or adjacent label.",
        trigger=SkillTrigger(task_type="weekly_report", agent_stage="writer"),
        confidence=0.82,
        hit_count=3,
    )
    most_specific = memory.create_skill(
        title="Ambiguous target writer fix",
        description="When ambiguous_target appears, add locator hints to the WeeklyReport step.",
        kind=MemoryKind.WRITER_SKILL,
        status=SkillStatus.ACTIVE,
        content="Add locator hints such as left panel, footer, modal title, or icon color.",
        trigger=SkillTrigger(
            task_type="weekly_report",
            agent_stage="writer",
            issue_type="ambiguous_target",
        ),
        confidence=0.95,
        hit_count=5,
    )
    memory.create_skill(
        title="Disabled skill",
        description="Should never be returned.",
        kind=MemoryKind.WRITER_SKILL,
        status=SkillStatus.ACTIVE,
        content="Do not return this skill.",
        trigger=SkillTrigger(task_type="weekly_report", agent_stage="writer"),
        confidence=0.99,
        hit_count=9,
        disabled=True,
    )
    memory.create_skill(
        title="Candidate skill",
        description="Should not be returned before activation.",
        kind=MemoryKind.WRITER_SKILL,
        status=SkillStatus.CANDIDATE,
        content="Candidate content.",
        trigger=SkillTrigger(task_type="weekly_report", agent_stage="writer"),
        confidence=0.9,
        hit_count=6,
    )

    selected = selector.select_skills(
        task_type="weekly_report",
        agent_stage="writer",
        issue_type="ambiguous_target",
    )

    assert [item.skill_id for item in selected] == [
        most_specific.skill_id,
        task_specific.skill_id,
        general.skill_id,
    ]
    assert all(not hasattr(item, "content") for item in selected)


def test_skill_selector_can_load_content_by_id_or_title(tmp_path: Path) -> None:
    memory = SkillMemory(tmp_path / "sqlite" / "skills.db")
    selector = SkillSelector(memory)
    skill = memory.create_skill(
        title="Review final state",
        description="Ask for the visible end state when the WeeklyReport is vague.",
        kind=MemoryKind.REVIEW_RULE,
        status=SkillStatus.ACTIVE,
        content="Ask for the visible end state, including UI badge, dialog close, or tray alignment.",
        trigger=SkillTrigger(task_type="weekly_report", agent_stage="review"),
        confidence=0.88,
        hit_count=4,
    )

    assert selector.get_skill_content(skill_id=skill.skill_id) == skill.content
    assert selector.get_skill_content(title=skill.title) == skill.content


def test_skill_selector_lists_similar_skills_for_agent_judgement(tmp_path: Path) -> None:
    memory = SkillMemory(tmp_path / "sqlite" / "skills.db")
    selector = SkillSelector(memory, default_limit=4)

    candidate = memory.create_skill(
        title="Ambiguous button candidate",
        description="Use nearby labels to distinguish buttons with the same text.",
        kind=MemoryKind.WRITER_SKILL,
        status=SkillStatus.CANDIDATE,
        content="Mention nearby labels or dialog regions when button text repeats.",
        trigger=SkillTrigger(
            task_type="weekly_report",
            agent_stage="writer",
            issue_type="ambiguous_target",
        ),
        confidence=0.72,
        hit_count=2,
    )
    active = memory.create_skill(
        title="General locator hints",
        description="Add region hints when the UI has repeated controls.",
        kind=MemoryKind.WRITER_SKILL,
        status=SkillStatus.ACTIVE,
        content="Use panel, modal, toolbar, or color hints to disambiguate controls.",
        trigger=SkillTrigger(task_type="weekly_report", agent_stage="writer"),
        confidence=0.81,
        hit_count=4,
    )
    memory.create_skill(
        title="Review-only skill",
        description="Should not be returned for writer-stage similarity lookup.",
        kind=MemoryKind.REVIEW_RULE,
        status=SkillStatus.CANDIDATE,
        content="Review-only content.",
        trigger=SkillTrigger(task_type="weekly_report", agent_stage="review"),
        confidence=0.95,
        hit_count=6,
    )

    selected = selector.select_similar_skills(
        task_type="weekly_report",
        agent_stage="writer",
        issue_type="ambiguous_target",
        kind=MemoryKind.WRITER_SKILL,
    )

    assert [item.skill_id for item in selected] == [candidate.skill_id, active.skill_id]
    assert all(not hasattr(item, "content") for item in selected)


def test_skill_selector_selects_without_kind_filter_for_injection(tmp_path: Path) -> None:
    memory = SkillMemory(tmp_path / "sqlite" / "skills.db")
    selector = SkillSelector(memory, default_limit=4)

    writer_skill = memory.create_skill(
        title="Writer locator hint",
        description="Use nearby labels to disambiguate controls.",
        kind=MemoryKind.WRITER_SKILL,
        status=SkillStatus.ACTIVE,
        content="Mention nearby labels when controls repeat.",
        trigger=SkillTrigger(task_type="weekly_report", agent_stage="review"),
        confidence=0.81,
        hit_count=4,
    )
    review_rule = memory.create_skill(
        title="Review final state",
        description="Require a visible end state in verification steps.",
        kind=MemoryKind.REVIEW_RULE,
        status=SkillStatus.ACTIVE,
        content="Ask for a visible end state when the step result is vague.",
        trigger=SkillTrigger(task_type="weekly_report", agent_stage="review"),
        confidence=0.74,
        hit_count=3,
    )

    selected = selector.select_skills(task_type="weekly_report", agent_stage="review")

    assert [item.skill_id for item in selected] == [writer_skill.skill_id, review_rule.skill_id]
