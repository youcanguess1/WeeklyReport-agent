from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from app.memory.skill_selector import SkillSelector
from app.schemas.memory import SkillBrief


def format_skill_briefs(skill_briefs: Iterable[SkillBrief] | None) -> str:
    briefs = list(skill_briefs or [])
    if not briefs:
        return "(none)"
    lines = [f"- {brief.title}: {brief.description}" for brief in briefs]
    return "\n".join(lines)


@dataclass(frozen=True)
class SkillInjectionTool:
    skill_selector: SkillSelector
    name: str = "get_skill_content"

    @staticmethod
    def definition() -> dict[str, Any]:
        return {
            "type": "function",
            "name": "get_skill_content",
            "description": "Load the full skill content by exact skill title.",
            "parameters": {
                "type": "object",
                "required": ["title"],
                "properties": {
                    "title": {"type": "string"},
                },
            },
        }

    def execute(self, raw_args: dict[str, Any]) -> dict[str, Any]:
        title = str(raw_args.get("title", "")).strip()
        if not title:
            return {
                "status": "not_found",
                "summary": "Skill title is required to load skill content.",
            }
        try:
            content = self.skill_selector.get_skill_content(title=title)
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
