from __future__ import annotations

import base64
from pathlib import Path
from typing import Any


def build_input_image(path: Path) -> dict[str, str]:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "input_image", "image_url": f"data:{_media_type(path)};base64,{encoded}"}


def build_chat_image_content(content: bytes, media_type: str) -> dict[str, Any]:
    """Build an OpenAI-compatible image content block without persisting the upload."""

    encoded = base64.b64encode(content).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{media_type};base64,{encoded}"},
    }


def estimate_input_image_bytes(path: Path) -> int:
    size = path.stat().st_size
    return len(f"data:{_media_type(path)};base64,") + ((size + 2) // 3) * 4


def _media_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    return "application/octet-stream"
