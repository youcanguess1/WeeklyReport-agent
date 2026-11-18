from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from app.memory.skill_memory import SkillMemory
from app.schemas.memory import MemoryKind, SkillStatus, SkillTrigger
from app.schemas.skill_api import SkillImportRequest, SkillResponse, SkillUpdateRequest

router = APIRouter(prefix="/api/skills", tags=["skills"])


def _memory_from(request: Request) -> SkillMemory:
    return request.app.state.skill_memory


def _response(skill) -> SkillResponse:
    return SkillResponse(
        skill_id=skill.skill_id,
        title=skill.title,
        description=skill.description,
        kind=skill.kind,
        status=skill.status,
        content=skill.content,
        task_type=skill.trigger.task_type,
        agent_stage=skill.trigger.agent_stage,
        report_section=skill.trigger.report_section,
        issue_type=skill.trigger.issue_type,
        confidence=skill.confidence,
        hit_count=skill.hit_count,
        disabled=skill.disabled,
        created_at=skill.created_at,
        updated_at=skill.updated_at,
    )


@router.get("", response_model=list[SkillResponse])
async def list_skills(request: Request) -> list[SkillResponse]:
    return [_response(item) for item in _memory_from(request).list_skills(include_disabled=True)]


@router.post("", response_model=SkillResponse)
async def create_skill(
    request: Request,
    file: UploadFile = File(...),
    title: str | None = Form(None),
    description: str = Form(""),
    kind: MemoryKind = Form(MemoryKind.WRITER_SKILL),
    task_type: str = Form(...),
    agent_stage: str = Form(...),
    content: str | None = Form(None),
) -> SkillResponse:
    uploaded_content = (await file.read()).decode("utf-8")
    payload = SkillImportRequest(
        title=(title or file.filename or "Imported Skill").strip(),
        description=description,
        content=content if content is not None else uploaded_content,
        task_type=task_type,
        agent_stage=agent_stage,
    )
    skill = _memory_from(request).create_skill(
        title=payload.title,
        description=payload.description,
        kind=kind,
        status=SkillStatus.CANDIDATE,
        content=payload.content,
        trigger=SkillTrigger(task_type=payload.task_type, agent_stage=payload.agent_stage),
    )
    return _response(skill)


@router.put("/{skill_id}", response_model=SkillResponse)
async def update_skill(skill_id: str, payload: SkillUpdateRequest, request: Request) -> SkillResponse:
    memory = _memory_from(request)
    try:
        current = memory.get_skill(skill_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    updated = current.model_copy(
        update={
            "title": payload.title,
            "description": payload.description,
            "content": payload.content,
            "trigger": SkillTrigger(
                task_type=payload.task_type,
                agent_stage=payload.agent_stage,
                report_section=payload.report_section,
                issue_type=payload.issue_type,
            ),
            "updated_at": datetime.now(UTC),
        }
    )
    return _response(memory.save_skill(updated))


@router.delete("/{skill_id}", status_code=204)
async def delete_skill(skill_id: str, request: Request) -> None:
    try:
        _memory_from(request).delete_skill(skill_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
