from __future__ import annotations

from pathlib import Path
from typing import Any

from app.schemas.evidence import ImageMaterial, MaterialPack
from app.services.llm_image_content import build_input_image


def build_image_content(material_pack: MaterialPack, material: ImageMaterial) -> list[dict[str, Any]]:
    image = build_input_image(Path(material.image_path))
    return [
        {
            'type': 'text',
            'text': (
                f'Task: {material_pack.task_id}\nMaterial: {material.material_id}\n'
                f'User note: {material.note or "(none)"}\n'
                'Describe visible progress, metrics, and risks useful for a weekly report. '
                'Distinguish user notes from visible evidence.'
            ),
        },
        {'type': 'image_url', 'image_url': {'url': image['image_url']}},
    ]
