from __future__ import annotations

from langchain_openai import ChatOpenAI

from app.services.config_service import ModelEndpointSettings, ModelSettings


class ChatModelFactory:
    """Create official LangChain chat models from WeeklyReport model settings."""

    def __init__(self, settings: ModelSettings) -> None:
        self.settings = settings

    def chat(self) -> ChatOpenAI:
        return self._build(self.settings.chat)

    def vision(self) -> ChatOpenAI:
        return self._build(self.settings.vision)

    def _build(self, endpoint: ModelEndpointSettings) -> ChatOpenAI:
        if not endpoint.model:
            raise ValueError("A model name is required for the requested endpoint.")
        configured_key = endpoint.api_key or self.settings.api_key
        api_key = configured_key.get_secret_value() if configured_key else None
        return ChatOpenAI(
            model=endpoint.model,
            base_url=endpoint.base_url or self.settings.base_url,
            # ChatOpenAI validates the key at construction time. A sentinel
            # keeps health checks and graph construction lazy when credentials
            # are intentionally absent; the provider returns the real error on
            # the first model call.
            api_key=api_key or "not-configured",
        )
