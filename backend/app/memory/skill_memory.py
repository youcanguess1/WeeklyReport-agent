from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from sqlalchemy import select

from app.db.models import SkillIndex
from app.db.session import create_sqlite_session_factory
from app.schemas.memory import MemoryKind, SkillBrief, SkillRecord, SkillStatus, SkillTrigger


class SkillMemory:
    def __init__(self, sqlite_path: Path) -> None:
        self.sqlite_path = sqlite_path.resolve()
        self.session_factory = create_sqlite_session_factory(self.sqlite_path)

    def save_skill(self, skill: SkillRecord) -> SkillRecord:
        with self.session_factory() as session:
            row = session.get(SkillIndex, skill.skill_id)
            if row is None:
                row = SkillIndex(skill_id=skill.skill_id, created_at=self._iso_now(), updated_at=self._iso_now())
                session.add(row)
            self._apply_skill_record(row, skill)
            session.commit()
            session.refresh(row)
            return self._to_skill_record(row)

    def create_skill(
        self,
        *,
        title: str,
        description: str,
        kind: MemoryKind,
        status: SkillStatus,
        content: str,
        trigger: SkillTrigger | None = None,
        confidence: float = 0.0,
        hit_count: int = 0,
        disabled: bool = False,
        skill_id: str | None = None,
    ) -> SkillRecord:
        now = datetime.now(UTC)
        return self.save_skill(
            SkillRecord(
                skill_id=skill_id or self.generate_skill_id(),
                title=title,
                description=description,
                kind=kind,
                status=status,
                content=content,
                trigger=trigger or SkillTrigger(),
                confidence=confidence,
                hit_count=hit_count,
                disabled=disabled,
                created_at=now,
                updated_at=now,
            )
        )

    def get_skill(self, skill_id: str) -> SkillRecord:
        with self.session_factory() as session:
            row = session.get(SkillIndex, skill_id)
            if row is None:
                raise KeyError(f"Unknown skill id: {skill_id}")
            return self._to_skill_record(row)

    def get_skill_by_title(self, title: str) -> SkillRecord:
        with self.session_factory() as session:
            row = session.scalar(select(SkillIndex).where(SkillIndex.title == title))
            if row is None:
                raise KeyError(f"Unknown skill title: {title}")
            return self._to_skill_record(row)

    def delete_skill(self, skill_id: str) -> None:
        with self.session_factory() as session:
            row = session.get(SkillIndex, skill_id)
            if row is None:
                raise KeyError(f"Unknown skill id: {skill_id}")
            session.delete(row)
            session.commit()

    def list_skills(
        self,
        *,
        statuses: Iterable[SkillStatus] | None = None,
        kind: MemoryKind | None = None,
        include_disabled: bool = False,
    ) -> list[SkillRecord]:
        with self.session_factory() as session:
            query = select(SkillIndex)
            if statuses is not None:
                query = query.where(SkillIndex.status.in_([item.value for item in statuses]))
            if kind is not None:
                query = query.where(SkillIndex.kind == kind.value)
            if not include_disabled:
                query = query.where(SkillIndex.disabled.is_(False))
            rows = session.scalars(query.order_by(SkillIndex.updated_at.desc(), SkillIndex.created_at.desc())).all()
        return [self._to_skill_record(row) for row in rows]

    def list_briefs(
        self,
        *,
        statuses: Iterable[SkillStatus] | None = None,
        kind: MemoryKind | None = None,
        include_disabled: bool = False,
    ) -> list[SkillBrief]:
        return [self.to_skill_brief(item) for item in self.list_skills(statuses=statuses, kind=kind, include_disabled=include_disabled)]

    def update_hit_count(self, skill_id: str, increment: int = 1) -> SkillRecord:
        with self.session_factory() as session:
            row = session.get(SkillIndex, skill_id)
            if row is None:
                raise KeyError(f"Unknown skill id: {skill_id}")
            row.hit_count += increment
            row.updated_at = self._iso_now()
            session.commit()
            session.refresh(row)
            return self._to_skill_record(row)

    def update_status(self, skill_id: str, status: SkillStatus, *, disabled: bool | None = None) -> SkillRecord:
        with self.session_factory() as session:
            row = session.get(SkillIndex, skill_id)
            if row is None:
                raise KeyError(f"Unknown skill id: {skill_id}")
            row.status = status.value
            if disabled is not None:
                row.disabled = disabled
            row.updated_at = self._iso_now()
            session.commit()
            session.refresh(row)
            return self._to_skill_record(row)

    def record_candidate(
        self,
        *,
        title: str,
        description: str,
        kind: MemoryKind,
        content: str,
        trigger: SkillTrigger | None = None,
        confidence: float = 0.0,
        similar_skill_id: str | None = None,
        hit_increment: int = 1,
        promote_threshold: int = 3,
    ) -> SkillRecord:
        if similar_skill_id:
            return self.merge_candidate(
                similar_skill_id,
                description=description,
                content=content,
                confidence=confidence,
                hit_increment=hit_increment,
                promote_threshold=promote_threshold,
            )

        status = SkillStatus.ACTIVE if hit_increment >= promote_threshold else SkillStatus.CANDIDATE
        return self.create_skill(
            title=title,
            description=description,
            kind=kind,
            status=status,
            content=content,
            trigger=trigger,
            confidence=confidence,
            hit_count=hit_increment,
        )

    def merge_candidate(
        self,
        skill_id: str,
        *,
        description: str,
        content: str,
        confidence: float,
        hit_increment: int = 1,
        promote_threshold: int = 3,
    ) -> SkillRecord:
        with self.session_factory() as session:
            row = session.get(SkillIndex, skill_id)
            if row is None:
                raise KeyError(f"Unknown skill id: {skill_id}")
            row.description = description
            row.content = content
            row.confidence = confidence
            row.hit_count += hit_increment
            if row.status == SkillStatus.CANDIDATE.value and row.hit_count >= promote_threshold and not row.disabled:
                row.status = SkillStatus.ACTIVE.value
            row.updated_at = self._iso_now()
            session.commit()
            session.refresh(row)
            return self._to_skill_record(row)

    @staticmethod
    def to_skill_brief(skill: SkillRecord) -> SkillBrief:
        return SkillBrief(
            skill_id=skill.skill_id,
            title=skill.title,
            description=skill.description,
            kind=skill.kind,
            trigger=skill.trigger,
            confidence=skill.confidence,
            hit_count=skill.hit_count,
        )

    @staticmethod
    def generate_skill_id() -> str:
        return f"skill_{uuid4().hex[:16]}"

    @staticmethod
    def _iso_now() -> str:
        return datetime.now(UTC).isoformat()

    def _apply_skill_record(self, row: SkillIndex, skill: SkillRecord) -> None:
        row.title = skill.title
        row.description = skill.description
        row.kind = skill.kind.value
        row.status = skill.status.value
        row.content = skill.content
        row.task_type = skill.trigger.task_type
        row.agent_stage = skill.trigger.agent_stage
        row.report_section = skill.trigger.report_section
        row.issue_type = skill.trigger.issue_type
        row.confidence = skill.confidence
        row.hit_count = skill.hit_count
        row.disabled = skill.disabled
        row.created_at = skill.created_at.isoformat()
        row.updated_at = skill.updated_at.isoformat()

    @staticmethod
    def _to_skill_record(row: SkillIndex) -> SkillRecord:
        return SkillRecord(
            skill_id=row.skill_id,
            title=row.title,
            description=row.description,
            kind=MemoryKind(row.kind),
            status=SkillStatus(row.status),
            content=row.content,
            trigger=SkillTrigger(
                task_type=row.task_type,
                agent_stage=row.agent_stage,
                report_section=row.report_section,
                issue_type=row.issue_type,
            ),
            confidence=row.confidence,
            hit_count=row.hit_count,
            disabled=row.disabled,
            created_at=datetime.fromisoformat(row.created_at),
            updated_at=datetime.fromisoformat(row.updated_at),
        )
