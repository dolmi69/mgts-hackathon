from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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
    demo_feedback_enabled: bool = False
    yandex_forms_fields: Annotated[dict[str, dict[str, str]], NoDecode] = Field(default_factory=dict)

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

    # Request limits: keep a single client from exhausting memory or LLM budget.
    max_webhook_bytes: int = 256_000
    max_import_bytes: int = 10_000_000
    max_import_rows: int = 20_000
    max_comment_chars: int = 4_000
    # Brute-force protection for Basic Auth (per client address).
    auth_max_failures: int = 10
    auth_failure_window_seconds: int = 300
    # Personal data must stay in Russia unless the operator explicitly allows
    # a cross-border LLM (152-FZ art. 12). Checked outside the dev environment.
    llm_allow_cross_border: bool = False
    # Find names outside the roster with Natasha NER (if installed).
    anonymizer_ner: bool = True
    # What a webhook does with an intern ID that is not in the roster:
    # "create" adds the intern (demo convenience), "hold" parks the answer in
    # ingest_events for HR to map. Empty: create in dev/demo, hold elsewhere.
    webhook_unknown_interns: str = ""

    webhook_secret: str | None = None
    dashboard_user: str | None = None
    dashboard_password: str | None = None
    dashboard_viewer_user: str | None = None
    dashboard_viewer_password: str | None = None

    rules_path: Path = BASE_DIR / "config" / "rules.json"
    seed_dir: Path = BASE_DIR / "data" / "seed"
    frontend_dir: Path = ROOT_DIR / "frontend"
    worker_poll_seconds: float = 2.0

    @field_validator("yandex_forms_fields", mode="before")
    @classmethod
    def parse_yandex_fields(cls, value: object) -> object:
        if isinstance(value, str):
            return json.loads(value.strip() or "{}")
        return value

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
