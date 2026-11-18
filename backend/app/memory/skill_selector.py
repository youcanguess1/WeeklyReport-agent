from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.memory.skill_memory import SkillMemory
from app.schemas.memory import MemoryKind, SkillBrief, SkillRecord, SkillStatus


@dataclass(frozen=True)
class SkillSelector:
    skill_memory: SkillMemory
    default_limit: int = 3

    def select_skills(
        self,
        *,
        task_type: str | None = None,
        agent_stage: str | None = None,
        issue_type: str | None = None,
        kind: MemoryKind | None = None,
        limit: int | None = None,
    ) -> list[SkillBrief]:
        return self._select_briefs(
            statuses=[SkillStatus.ACTIVE],
            task_type=task_type,
            agent_stage=agent_stage,
            issue_type=issue_type,
            kind=kind,
            limit=limit,
        )

    def select_similar_skills(
        self,
        *,
        task_type: str | None = None,
        agent_stage: str | None = None,
        issue_type: str | None = None,
        kind: MemoryKind | None = None,
        limit: int | None = None,
    ) -> list[SkillBrief]:
        return self._select_briefs(
            statuses=[SkillStatus.CANDIDATE, SkillStatus.ACTIVE],
            task_type=task_type,
            agent_stage=agent_stage,
            issue_type=issue_type,
            kind=kind,
            limit=limit,
        )

    def get_skill_content(self, *, skill_id: str | None = None, title: str | None = None) -> str:
        if skill_id:
            return self.skill_memory.get_skill(skill_id).content
        if title:
            return self.skill_memory.get_skill_by_title(title).content
        raise ValueError("skill_id or title is required to load skill content")

    def _select_briefs(
        self,
        *,
        statuses: list[SkillStatus],
        task_type: str | None,
        agent_stage: str | None,
        issue_type: str | None,
        kind: MemoryKind | None,
        limit: int | None,
    ) -> list[SkillBrief]:
        skills = self.skill_memory.list_skills(
            statuses=statuses,
            kind=kind,
            include_disabled=False,
        )
        matched = [skill for skill in skills if self._matches(skill, task_type, agent_stage, issue_type)]
        ordered = sorted(
            matched,
            key=lambda skill: (
                -self._score(skill, task_type, agent_stage, issue_type),
                -skill.hit_count,
                -skill.confidence,
                -self._timestamp(skill.updated_at),
                -self._timestamp(skill.created_at),
                skill.title,
            ),
        )
        selected = ordered[: max(1, limit or self.default_limit)]
        return [self.skill_memory.to_skill_brief(skill) for skill in selected]

    @staticmethod
    def _matches(
        skill: SkillRecord,
        task_type: str | None,
        agent_stage: str | None,
        issue_type: str | None,
    ) -> bool:
        trigger = skill.trigger
        checks = (
            (trigger.task_type, task_type),
            (trigger.agent_stage, agent_stage),
            (trigger.issue_type, issue_type),
        )
        for trigger_value, query_value in checks:
            if trigger_value is not None and query_value is not None and trigger_value != query_value:
                return False
        return True

    @staticmethod
    def _score(
        skill: SkillRecord,
        task_type: str | None,
        agent_stage: str | None,
        issue_type: str | None,
    ) -> int:
        trigger = skill.trigger
        score = 0
        if trigger.task_type is not None and trigger.task_type == task_type:
            score += 4
        if trigger.agent_stage is not None and trigger.agent_stage == agent_stage:
            score += 3
        if trigger.issue_type is not None and trigger.issue_type == issue_type:
            score += 2
        if trigger.report_section is not None:
            score += 1
        return score

    @staticmethod
    def _timestamp(value: datetime) -> float:
        return value.timestamp()
