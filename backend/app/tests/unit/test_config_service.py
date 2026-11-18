from __future__ import annotations

from pathlib import Path

from app.services.config_service import ConfigService


def test_config_service_supports_per_model_endpoint_credentials(
    monkeypatch, tmp_path: Path
) -> None:
    config_dir = tmp_path / "config"
    repo_root = tmp_path / "repo"
    config_dir.mkdir()
    repo_root.mkdir()

    (config_dir / "settings.example.yaml").write_text(
        "\n".join(
            [
                "app:",
                "  name: WeeklyReport API",
                "model:",
                "  provider: openai-compatible",
                "  base_url: https://shared.example/v1",
                "  api_key_env: SHARED_MODEL_API_KEY",
                "  chat:",
                "    model: gpt-4.1-mini",
                "  vision:",
                "    model: gpt-4.1-mini",
                "    base_url: https://vision.example/v1",
                "    api_key_env: VISION_MODEL_API_KEY",
                "storage:",
                "  artifact_root: data/artifacts",
                "task_runner:",
                "  backend: local",
                "  max_workers: 2",
            ]
        ),
        encoding="utf-8",
    )
    (config_dir / "settings.local.yaml").write_text(
        "\n".join(
            [
                "model:",
                "  provider: ollama",
                "  chat:",
                "    model: qwen3:8b",
                "    base_url: http://localhost:11434/v1",
                "    api_key_env: OLLAMA_CHAT_API_KEY",
                "task_runner:",
                "  max_workers: 4",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("SHARED_MODEL_API_KEY", "shared-secret")
    monkeypatch.setenv("VISION_MODEL_API_KEY", "vision-secret")
    monkeypatch.setenv("OLLAMA_CHAT_API_KEY", "chat-secret")

    service = ConfigService(config_dir=config_dir, repo_root=repo_root)
    settings = service.load()

    assert settings.model.provider == "ollama"
    assert settings.model.chat_model == "qwen3:8b"
    assert settings.model.chat.base_url == "http://localhost:11434/v1"
    assert settings.model.chat.api_key is not None
    assert settings.model.chat.api_key.get_secret_value() == "chat-secret"
    assert settings.model.vision.base_url == "https://vision.example/v1"
    assert settings.model.vision.api_key is not None
    assert settings.model.vision.api_key.get_secret_value() == "vision-secret"
    assert settings.task_runner.max_workers == 4
    assert settings.storage.artifact_root == (repo_root / "data/artifacts").resolve()


def test_config_service_reads_api_keys_from_dotenv_files(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "backend" / "config"
    repo_root = tmp_path / "repo"
    config_dir.mkdir(parents=True)
    repo_root.mkdir()

    (config_dir / "settings.example.yaml").write_text(
        "\n".join(
            [
                "app:",
                "  name: WeeklyReport API",
                "model:",
                "  provider: openai-compatible",
                "  base_url: https://shared.example/v1",
                "  api_key_env: SHARED_MODEL_API_KEY",
                "  chat:",
                "    model: gpt-4.1-mini",
                "  vision:",
                "    model: gpt-4.1-mini",
                "    api_key_env: VISION_MODEL_API_KEY",
                "storage:",
                "  artifact_root: data/artifacts",
                "task_runner:",
                "  backend: local",
                "  max_workers: 1",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / ".env").write_text(
        "SHARED_MODEL_API_KEY=shared-from-dotenv\n",
        encoding="utf-8",
    )
    (config_dir.parent / ".env").write_text(
        "VISION_MODEL_API_KEY=vision-from-backend-dotenv\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("SHARED_MODEL_API_KEY", raising=False)
    monkeypatch.delenv("VISION_MODEL_API_KEY", raising=False)

    service = ConfigService(config_dir=config_dir, repo_root=repo_root)
    settings = service.load()

    assert settings.model.chat.api_key is not None
    assert settings.model.chat.api_key.get_secret_value() == "shared-from-dotenv"
    assert settings.model.vision.api_key is not None
    assert settings.model.vision.api_key.get_secret_value() == "vision-from-backend-dotenv"


def test_config_service_prefers_real_environment_over_dotenv(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    repo_root = tmp_path / "repo"
    config_dir.mkdir()
    repo_root.mkdir()

    (config_dir / "settings.example.yaml").write_text(
        "\n".join(
            [
                "app:",
                "  name: WeeklyReport API",
                "model:",
                "  provider: openai-compatible",
                "  api_key_env: SHARED_MODEL_API_KEY",
                "  chat:",
                "    model: gpt-4.1-mini",
                "storage:",
                "  artifact_root: data/artifacts",
                "task_runner:",
                "  backend: local",
                "  max_workers: 1",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / ".env").write_text(
        "SHARED_MODEL_API_KEY=from-dotenv\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SHARED_MODEL_API_KEY", "from-environment")

    service = ConfigService(config_dir=config_dir, repo_root=repo_root)
    settings = service.load()

    assert settings.model.chat.api_key is not None
    assert settings.model.chat.api_key.get_secret_value() == "from-environment"


def test_config_service_reads_langfuse_observability_credentials(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    repo_root = tmp_path / "repo"
    config_dir.mkdir()
    repo_root.mkdir()

    (config_dir / "settings.example.yaml").write_text(
        "\n".join(
            [
                "app:",
                "  name: WeeklyReport API",
                "  environment: local",
                "model:",
                "  provider: openai-compatible",
                "  chat:",
                "    model: gpt-4.1-mini",
                "storage:",
                "  artifact_root: data/artifacts",
                "task_runner:",
                "  backend: local",
                "  max_workers: 1",
                "observability:",
                "  provider: langfuse",
                "  host: http://localhost:3000",
                "  public_key_env: LANGFUSE_PUBLIC_KEY",
                "  secret_key_env: LANGFUSE_SECRET_KEY",
                "  project_name: weekly_report-test",
                "  capture_input: false",
                "  capture_output: false",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")

    service = ConfigService(config_dir=config_dir, repo_root=repo_root)
    settings = service.load()

    assert settings.observability.provider == "langfuse"
    assert settings.observability.host == "http://localhost:3000"
    assert settings.observability.project_name == "weekly_report-test"
    assert settings.observability.environment == "local"
    assert settings.observability.public_key is not None
    assert settings.observability.public_key.get_secret_value() == "pk-lf-test"
    assert settings.observability.secret_key is not None
    assert settings.observability.secret_key.get_secret_value() == "sk-lf-test"
    assert settings.observability.capture_input is False
    assert settings.observability.capture_output is False


def test_config_service_resolves_logging_path(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    repo_root = tmp_path / "repo"
    config_dir.mkdir()
    repo_root.mkdir()

    (config_dir / "settings.example.yaml").write_text(
        "\n".join(
            [
                "app:",
                "  name: WeeklyReport API",
                "model:",
                "  provider: openai-compatible",
                "  chat:",
                "    model: gpt-4.1-mini",
                "storage:",
                "  artifact_root: data/artifacts",
                "task_runner:",
                "  backend: local",
                "  max_workers: 1",
                "logging:",
                "  path: data/custom_logs/system.jsonl",
                "  level: WARNING",
            ]
        ),
        encoding="utf-8",
    )

    service = ConfigService(config_dir=config_dir, repo_root=repo_root)
    settings = service.load()

    assert settings.logging.path == (repo_root / "data/custom_logs/system.jsonl").resolve()
    assert settings.logging.level == "WARNING"
    assert settings.logging.enabled is True
