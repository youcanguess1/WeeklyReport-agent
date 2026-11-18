from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr


class AppMetadataSettings(BaseModel):
    name: str = "WeeklyReport API"
    environment: Literal["local", "dev", "test", "prod"] = "local"


class ModelEndpointSettings(BaseModel):
    model: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    api_key: SecretStr | None = None


class ModelSettings(BaseModel):
    provider: str = "openai-compatible"
    base_url: str | None = None
    api_key_env: str | None = None
    api_key: SecretStr | None = None
    chat: ModelEndpointSettings = Field(
        default_factory=lambda: ModelEndpointSettings(model="gpt-4.1-mini")
    )
    vision: ModelEndpointSettings = Field(
        default_factory=lambda: ModelEndpointSettings(model="gpt-4.1-mini")
    )

    @property
    def chat_model(self) -> str | None:
        return self.chat.model

    @property
    def vision_model(self) -> str | None:
        return self.vision.model


class StorageSettings(BaseModel):
    artifact_root: Path = Path("data/artifacts")
    sqlite_path: Path = Path("data/sqlite/weekly_report.db")


class TaskRunnerSettings(BaseModel):
    backend: Literal["local", "redis"] = "local"
    max_workers: int = Field(default=2, ge=1, le=32)
    queue_name: str = "weekly_report-local"
    redis_url: str | None = None


class ObservabilitySettings(BaseModel):
    provider: Literal["none", "langfuse"] = "none"
    host: str = "http://localhost:3000"
    public_key_env: str | None = None
    secret_key_env: str | None = None
    public_key: SecretStr | None = None
    secret_key: SecretStr | None = None
    environment: str | None = None
    project_name: str = "weekly_report-local"
    capture_input: bool = False
    capture_output: bool = False

    @property
    def has_credentials(self) -> bool:
        return self.public_key is not None and self.secret_key is not None

    @property
    def enabled(self) -> bool:
        return self.provider == "langfuse" and self.has_credentials


class LoggingSettings(BaseModel):
    enabled: bool = True
    path: Path = Path("data/logs/weekly_report.log.jsonl")
    level: str = "INFO"
    max_bytes: int = 10_485_760
    backup_count: int = 5


class AppSettings(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    app: AppMetadataSettings = Field(default_factory=AppMetadataSettings)
    model: ModelSettings = Field(default_factory=ModelSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    task_runner: TaskRunnerSettings = Field(default_factory=TaskRunnerSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)


class ConfigService:
    def __init__(
        self,
        config_dir: Path | None = None,
        repo_root: Path | None = None,
        example_filename: str = "settings.example.yaml",
        local_filename: str = "settings.local.yaml",
    ) -> None:
        backend_root = Path(__file__).resolve().parents[2]
        self.repo_root = repo_root or backend_root.parent
        self.config_dir = config_dir or backend_root / "config"
        self.example_path = self.config_dir / example_filename
        self.local_path = self.config_dir / local_filename
        self._dotenv_values = self._load_dotenv_values()

    def load(self, settings_path: Path | None = None) -> AppSettings:
        if settings_path is not None:
            raw_config = self._read_yaml(settings_path)
        else:
            raw_config = self._read_yaml(self.example_path)
            if self.local_path.exists():
                raw_config = self._deep_merge(raw_config, self._read_yaml(self.local_path))

        settings = AppSettings.model_validate(raw_config)
        return self._resolve_runtime_values(settings)

    def _resolve_runtime_values(self, settings: AppSettings) -> AppSettings:
        settings.storage.artifact_root = self._resolve_path(settings.storage.artifact_root)
        settings.storage.sqlite_path = self._resolve_path(settings.storage.sqlite_path)
        settings.logging.path = self._resolve_path(settings.logging.path)
        settings.model.api_key = self._read_secret(settings.model.api_key_env)

        for endpoint in (
            settings.model.chat,
            settings.model.vision,
        ):
            endpoint.base_url = endpoint.base_url or settings.model.base_url
            endpoint.api_key_env = endpoint.api_key_env or settings.model.api_key_env
            endpoint.api_key = self._read_secret(endpoint.api_key_env) or settings.model.api_key

        settings.observability.public_key = self._read_secret(
            settings.observability.public_key_env
        )
        settings.observability.secret_key = self._read_secret(
            settings.observability.secret_key_env
        )
        settings.observability.environment = (
            settings.observability.environment or settings.app.environment
        )

        return settings

    def _read_secret(self, env_name: str | None) -> SecretStr | None:
        if not env_name:
            return None

        env_value = os.getenv(env_name)
        if not env_value:
            env_value = self._dotenv_values.get(env_name)
        if not env_value:
            return None

        return SecretStr(env_value)

    def _load_dotenv_values(self) -> dict[str, str]:
        merged: dict[str, str] = {}
        for path in (
            self.repo_root / ".env",
            self.config_dir.parent / ".env",
        ):
            if not path.exists():
                continue
            values = dotenv_values(path)
            for key, value in values.items():
                if key and value is not None:
                    merged[key] = value
        return merged

    def _resolve_path(self, value: Path) -> Path:
        return value if value.is_absolute() else (self.repo_root / value).resolve()

    def _read_yaml(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(f"Settings file not found: {path}")

        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}

        if not isinstance(data, dict):
            raise ValueError(f"Settings file must contain a mapping at the root: {path}")

        return data

    def _deep_merge(self, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in override.items():
            if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
                merged[key] = self._deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return ConfigService().load()
