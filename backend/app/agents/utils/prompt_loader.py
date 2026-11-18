from __future__ import annotations

from pathlib import Path


def load_agent_prompt(filename: str) -> str:
    prompt_path = Path(__file__).resolve().parents[1] / "AGENTMD" / filename
    return prompt_path.read_text(encoding="utf-8").strip()
