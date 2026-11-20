from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BASE_DIR.parent
ENV_FILE = ROOT_DIR / ".env"


class Settings(BaseSettings):
    """Application settings.

    Local development defaults to SQLite so the demo can be started without
    Docker. Docker Compose overrides DATABASE_URL with PostgreSQL.
    """

    app_name: str = "МГТС · Сигналы по стажёрам"
    app_env: str = "dev"

    database_url: str = f"sqlite:///{(BASE_DIR / 'data' / 'app.db').as_posix()}"
    seed_on_startup: bool = False
    seed_analyze: bool = False
    analyze_in_background: bool = True

    llm_provider: str = "mock"  # mock | openai | yandex | gigachat
    llm_api_key: str | None = None
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"
    llm_folder_id: str | None = None
    llm_timeout_seconds: float = 40.0
    llm_max_retries: int = 2
    llm_temperature: float = 0.0
    gigachat_base_url: str = "https://api.giga.chat/v1"
    gigachat_model: str = "GigaChat"
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_ca_bundle: str = ""

    webhook_secret: str | None = None
    dashboard_user: str | None = None
    dashboard_password: str | None = None

    rules_path: Path = BASE_DIR / "config" / "rules.json"
    seed_dir: Path = BASE_DIR / "data" / "seed"
    frontend_dir: Path = ROOT_DIR / "frontend"
    worker_poll_seconds: float = 2.0

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
