from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from app.memory.skill_selector import SkillSelector
from app.schemas.memory import MemoryKind, SkillTrigger


class GetSkillContentInput(BaseModel):
    title: str = Field(min_length=1)


class GetSkillContentTool(BaseTool):
    name: str = "get_skill_content"
    description: str = "Load the full skill content by exact skill title."
    args_schema: type[BaseModel] = GetSkillContentInput
    skill_selector: Any

    def __init__(self, skill_selector: SkillSelector, **kwargs: Any) -> None:
        super().__init__(skill_selector=skill_selector, **kwargs)

    def _run(self, title: str) -> dict[str, Any]:
        try:
            content = self.skill_selector.get_skill_content(title=title.strip())
        except KeyError:
            return {
                "status": "not_found",
                "title": title,
                "summary": f"No skill content found for title: {title}",
            }
        return {
            "status": "ok",
            "title": title,
            "content": content,
            "summary": f"Loaded skill content for title: {title}",
        }


class ListSimilarSkillsInput(BaseModel):
    task_type: str | None = None
    agent_stage: str | None = None
    issue_type: str | None = None
    kind: MemoryKind | None = None
    limit: int | None = Field(default=None, ge=1, le=10)


class ListSimilarSkillsTool(BaseTool):
    name: str = "list_similar_skills"
    description: str = "List similar candidate or active skills for the current experience."
    args_schema: type[BaseModel] = ListSimilarSkillsInput
    skill_selector: Any

    def __init__(self, skill_selector: SkillSelector, **kwargs: Any) -> None:
        super().__init__(skill_selector=skill_selector, **kwargs)

    def _run(
        self,
        task_type: str | None = None,
        agent_stage: str | None = None,
        issue_type: str | None = None,
        kind: MemoryKind | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        items = self.skill_selector.select_similar_skills(
            task_type=_optional_text(task_type),
            agent_stage=_optional_text(agent_stage),
            issue_type=_optional_text(issue_type),
            kind=kind,
            limit=limit,
        )
        return {"skills": [item.model_dump(mode="json") for item in items]}


class RecordSkillCandidateInput(BaseModel):
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    kind: MemoryKind
    content: str = Field(min_length=1)
    task_type: str | None = None
    agent_stage: str | None = None
    report_section: str | None = None
    issue_type: str | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    similar_skill_id: str | None = None
    hit_increment: int = Field(default=1, ge=1, le=3)


class RecordSkillCandidateTool(BaseTool):
    name: str = "record_skill_candidate"
    description: str = "Create or merge a skill candidate based on the reviewed experience."
    args_schema: type[BaseModel] = RecordSkillCandidateInput
    skill_memory: Any

    def __init__(self, skill_memory: Any, **kwargs: Any) -> None:
        super().__init__(skill_memory=skill_memory, **kwargs)

    def _run(
        self,
        title: str,
        description: str,
        kind: MemoryKind,
        content: str,
        task_type: str | None = None,
        agent_stage: str | None = None,
        report_section: str | None = None,
        issue_type: str | None = None,
        confidence: float = 0.0,
        similar_skill_id: str | None = None,
        hit_increment: int = 1,
    ) -> dict[str, Any]:
        skill = self.skill_memory.record_candidate(
            title=title.strip(),
            description=description.strip(),
            kind=kind,
            content=content.strip(),
            trigger=SkillTrigger(
                task_type=_optional_text(task_type),
                agent_stage=_optional_text(agent_stage),
                report_section=_optional_text(report_section),
                issue_type=_optional_text(issue_type),
            ),
            confidence=confidence,
            similar_skill_id=_optional_text(similar_skill_id),
            hit_increment=hit_increment,
        )
        return {"status": "recorded", "skill": skill.model_dump(mode="json")}


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None
