from __future__ import annotations

from pathlib import Path

from app.memory.skill_memory import SkillMemory
from app.schemas.memory import MemoryKind, SkillStatus, SkillTrigger


def test_skill_memory_saves_and_lists_skills(tmp_path: Path) -> None:
    memory = SkillMemory(tmp_path / "sqlite" / "skills.db")

    candidate = memory.create_skill(
        title="Clarify duplicate labels",
        description="Use position or nearby text to distinguish duplicate labels.",
        kind=MemoryKind.WRITER_SKILL,
        status=SkillStatus.CANDIDATE,
        content="If the page has two Save buttons, mention the modal footer or toolbar location.",
        trigger=SkillTrigger(task_type="weekly_report", agent_stage="writer"),
        confidence=0.7,
        hit_count=1,
    )
    active = memory.create_skill(
        title="Ask for visible final state",
        description="Require the final visible state in verification-oriented WeeklyReport steps.",
        kind=MemoryKind.REVIEW_RULE,
        status=SkillStatus.ACTIVE,
        content="When expected_result is vague, ask for the final visible state or UI cue.",
        trigger=SkillTrigger(task_type="weekly_report", agent_stage="review"),
        confidence=0.9,
        hit_count=3,
    )

    loaded = memory.get_skill(candidate.skill_id)
    active_skills = memory.list_skills(statuses=[SkillStatus.ACTIVE])
    briefs = memory.list_briefs(statuses=[SkillStatus.ACTIVE])

    assert loaded.title == "Clarify duplicate labels"
    assert loaded.description.startswith("Use position")
    assert loaded.content.startswith("If the page has two Save buttons")
    assert loaded.trigger.agent_stage == "writer"
    assert [item.skill_id for item in active_skills] == [active.skill_id]
    assert [item.skill_id for item in briefs] == [active.skill_id]
    assert briefs[0].description == active.description


def test_skill_memory_updates_existing_skill_and_hit_count(tmp_path: Path) -> None:
    memory = SkillMemory(tmp_path / "sqlite" / "skills.db")
    skill = memory.create_skill(
        skill_id="skill-fixed",
        title="Initial title",
        description="Initial description.",
        kind=MemoryKind.WRITER_SKILL,
        status=SkillStatus.CANDIDATE,
        content="Initial content.",
        trigger=SkillTrigger(task_type="weekly_report", agent_stage="writer"),
        confidence=0.6,
        hit_count=1,
    )

    updated = skill.model_copy(
        update={
            "title": "Updated title",
            "description": "Updated description.",
            "content": "Updated content.",
            "status": SkillStatus.ACTIVE,
            "hit_count": 2,
        }
    )
    memory.save_skill(updated)
    incremented = memory.update_hit_count(skill.skill_id, increment=2)

    assert incremented.title == "Updated title"
    assert incremented.status == SkillStatus.ACTIVE
    assert incremented.hit_count == 4
    assert incremented.trigger.task_type == "weekly_report"


def test_skill_memory_records_candidate_merge_and_promotion(tmp_path: Path) -> None:
    memory = SkillMemory(tmp_path / "sqlite" / "skills.db")

    created = memory.record_candidate(
        title="Original title",
        description="First description.",
        kind=MemoryKind.WRITER_SKILL,
        content="First content.",
        trigger=SkillTrigger(task_type="weekly_report", agent_stage="writer"),
        confidence=0.55,
    )

    merged = memory.record_candidate(
        title="New title should not replace old title",
        description="Latest description.",
        kind=MemoryKind.WRITER_SKILL,
        content="Latest content.",
        trigger=SkillTrigger(task_type="weekly_report", agent_stage="writer"),
        confidence=0.91,
        similar_skill_id=created.skill_id,
        hit_increment=2,
    )

    assert created.status == SkillStatus.CANDIDATE
    assert created.hit_count == 1
    assert merged.skill_id == created.skill_id
    assert merged.title == "Original title"
    assert merged.description == "Latest description."
    assert merged.content == "Latest content."
    assert merged.confidence == 0.91
    assert merged.hit_count == 3
    assert merged.status == SkillStatus.ACTIVE
