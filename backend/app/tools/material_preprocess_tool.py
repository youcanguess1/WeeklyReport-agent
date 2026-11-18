from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from PIL import Image

from app.schemas.evidence import ArtifactKind, ArtifactRef, ImageMaterial, MaterialPack
from app.schemas.task import TaskType
from app.services.artifact_service import ArtifactService, StorageGroup


class MaterialPreprocessTool:
    def __init__(self, artifact_service: ArtifactService) -> None:
        self.artifact_service = artifact_service

    def preprocess_image(
        self,
        task_id: str,
        source_image: ArtifactRef,
        task_type: TaskType,
        *,
        note: str | None = None,
        material_id: str | None = None,
    ) -> MaterialPack:
        if source_image.kind != ArtifactKind.IMAGE_MATERIAL:
            raise ValueError('Only image materials are supported.')
        with Image.open(Path(source_image.path)) as image:
            if image.format not in {'PNG', 'JPEG', 'WEBP'}:
                raise ValueError('Image materials must be PNG, JPEG, or WebP.')
            width, height = image.size
            image.verify()
        material_id = material_id or f'image_{source_image.artifact_id}'
        material = ImageMaterial(
            material_id=material_id,
            source_artifact_id=source_image.artifact_id,
            note=note.strip() if note and note.strip() else None,
            image_path=source_image.path,
        )
        evidence = MaterialPack(
            material_pack_id=f'evidence_{uuid4().hex[:16]}',
            task_id=task_id,
            source_artifact_id=source_image.artifact_id,
            materials=[material],
            metadata={
                'schema': 'image_material_v2',
                'source_type': 'image',
                'source_artifact_id': source_image.artifact_id,
                'task_type': task_type.value,
                'width': str(width),
                'height': str(height),
            },
        )
        manifest = self.artifact_service.save_json(
            task_id=task_id,
            kind=ArtifactKind.MANIFEST,
            filename=f'{material_id}_manifest.json',
            payload=evidence.model_dump(mode='json'),
            storage_group=StorageGroup.DERIVED,
            parent_artifact_id=source_image.artifact_id,
            metadata={'source_artifact_id': source_image.artifact_id},
        )
        return evidence.model_copy(update={'manifest': manifest})
