from __future__ import annotations

from fastapi import APIRouter, File, Form, Query, Request, UploadFile

from app.schemas.artifact_api import (
    ArtifactListResponse,
    ProjectArtifactLibrary,
    ProjectArtifactUploadResponse,
)

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


def _service_from(request: Request):
    return request.app.state.project_artifact_service


@router.get("", response_model=ArtifactListResponse)
async def list_artifacts(
    request: Request,
    library: str = Query("all"),
    kind: str | None = Query(None),
    task_id: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> ArtifactListResponse:
    return ArtifactListResponse.model_validate(
        _service_from(request).list_artifacts(
            library=library, kind=kind, task_id=task_id, page=page, page_size=page_size
        )
    )


@router.post("", response_model=ProjectArtifactUploadResponse)
async def upload_project_artifact(
    request: Request,
    library: ProjectArtifactLibrary = Form(...),
    file: UploadFile = File(...),
    display_name: str | None = Form(None),
    description: str | None = Form(None),
    tags: str | None = Form(None),
) -> ProjectArtifactUploadResponse:
    content = await file.read()
    artifact = _service_from(request).save_project_file(
        library=library,
        filename=display_name or file.filename or "uploaded_file",
        content=content,
        description=description,
        tags=[item.strip() for item in (tags or "").split(",") if item.strip()],
    )
    return ProjectArtifactUploadResponse(artifact=artifact)
