from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import delete, func, select

from .config import settings
from .database import SessionLocal
from .models import AuthFailure

basic_security = HTTPBasic(auto_error=False)

# Environments where the dashboard may run without a password, and only for
# requests from this machine. Anything else (including typos) fails closed.
OPEN_ENVIRONMENTS = {"dev"}
STRICT_ENVIRONMENTS = {"pilot", "production", "prod"}
LOCAL_CLIENTS = {"127.0.0.1", "::1", "localhost", "testclient"}
MIN_PASSWORD_LENGTH = 12
MIN_SECRET_LENGTH = 16
KNOWN_WEAK_SECRETS = {"change-me", "changeme", "secret", "password"}
RUSSIAN_LLM_HOSTS = ("giga.chat", "sberbank.ru", "yandex.net", "yandexcloud.net", "yandex.ru")


def app_env() -> str:
    return (settings.app_env or "").strip().lower()


# --- brute-force protection ------------------------------------------------------
# Failures live in the database: every API process sees them and a restart
# does not reset an attacker's counter.


def _client_host(request: Request | None) -> str:
    if request is None or request.client is None:
        return "unknown"
    return request.client.host[:64]


def _window_start() -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=settings.auth_failure_window_seconds)


def _too_many_failures(host: str) -> bool:
    with SessionLocal() as db:
        count = db.scalar(
            select(func.count(AuthFailure.id)).where(
                AuthFailure.client == host, AuthFailure.created_at > _window_start()
            )
        )
    return int(count or 0) >= settings.auth_max_failures


def _record_failure(host: str) -> None:
    with SessionLocal() as db:
        db.add(AuthFailure(client=host))
        # Keep the table small: old attempts no longer matter.
        db.execute(delete(AuthFailure).where(AuthFailure.created_at <= _window_start()))
        db.commit()


def reset_auth_failures() -> None:
    with SessionLocal() as db:
        db.execute(delete(AuthFailure))
        db.commit()


# --- dashboard -------------------------------------------------------------------


def require_dashboard_auth(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(basic_security),
) -> str | None:
    """Return the dashboard account, or None for the local dev mode."""
    if not _login_required():
        _require_local_dev(request)
        return None
    host = _client_host(request)
    if _too_many_failures(host):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Слишком много неудачных попыток входа. Повторите позже.",
            headers={"Retry-After": str(settings.auth_failure_window_seconds)},
        )
    if credentials is not None:
        for username, password in (
            (settings.dashboard_user, settings.dashboard_password),
            (settings.dashboard_viewer_user, settings.dashboard_viewer_password),
        ):
            if username and password:
                user_ok = _matches(credentials.username, username)
                password_ok = _matches(credentials.password, password)
                if user_ok and password_ok:
                    return username
        _record_failure(host)
    raise _unauthorized()


def _require_local_dev(request: Request | None) -> None:
    """Without a password the dashboard is only reachable from this machine."""
    if app_env() in OPEN_ENVIRONMENTS and _client_host(request) in LOCAL_CLIENTS:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Доступ по сети закрыт: задайте DASHBOARD_USER и DASHBOARD_PASSWORD.",
    )


def _login_required() -> bool:
    return bool(settings.dashboard_password or settings.dashboard_viewer_password)


def _matches(supplied: str, expected: str) -> bool:
    return secrets.compare_digest(supplied.encode(), expected.encode())


def can_dashboard_write(actor: str | None) -> bool:
    if not _login_required():
        return True
    return bool(actor and settings.dashboard_password and actor == settings.dashboard_user)


def require_dashboard_write(
    actor: str | None = Depends(require_dashboard_auth),
) -> str:
    if not can_dashboard_write(actor):
        raise HTTPException(status_code=403, detail="Для изменения данных нужна учётная запись HR.")
    return actor or "local-hr"


# --- startup checks --------------------------------------------------------------


def _weak_secret(value: str | None) -> bool:
    return not value or value.strip().lower() in KNOWN_WEAK_SECRETS or len(value) < MIN_SECRET_LENGTH


def _llm_is_cross_border() -> bool:
    provider = (settings.llm_provider or "mock").strip().lower()
    if provider in {"mock", "yandex", "yandexgpt", "gigachat"}:
        return False
    host = (urlsplit(settings.llm_base_url).hostname or "").lower()
    return not (host.endswith(".ru") or any(host == item or host.endswith("." + item) for item in RUSSIAN_LLM_HOSTS))


def validate_llm_location() -> None:
    """Refuse a foreign LLM where real data is processed (API and worker)."""
    if app_env() in OPEN_ENVIRONMENTS or app_env() == "demo":
        return
    if _llm_is_cross_border() and not settings.llm_allow_cross_border:
        raise RuntimeError(
            "LLM_BASE_URL sends personal data abroad. Use gigachat/yandex, or set "
            "LLM_ALLOW_CROSS_BORDER=true after notifying Roskomnadzor (152-FZ art. 12)"
        )


def validate_deployment_auth() -> None:
    validate_llm_location()
    hr_pair = bool(settings.dashboard_user), bool(settings.dashboard_password)
    viewer_pair = bool(settings.dashboard_viewer_user), bool(settings.dashboard_viewer_password)
    if hr_pair[1] and not hr_pair[0]:
        raise RuntimeError("Set DASHBOARD_USER when using DASHBOARD_PASSWORD")
    if viewer_pair[0] != viewer_pair[1]:
        raise RuntimeError("Set both DASHBOARD_VIEWER_USER and DASHBOARD_VIEWER_PASSWORD")
    if viewer_pair[0] and settings.dashboard_viewer_user == settings.dashboard_user:
        raise RuntimeError("HR and read-only dashboard usernames must differ")

    env = app_env()
    if env in OPEN_ENVIRONMENTS:
        return
    # Every other environment (demo, pilot, production, typos) is network-facing.
    if not all(hr_pair) or _weak_secret(settings.webhook_secret):
        raise RuntimeError(
            f"APP_ENV={env or '<empty>'}: set an HR login, password and unique webhook secret "
            f"(at least {MIN_SECRET_LENGTH} characters), or use APP_ENV=dev on localhost"
        )
    for password in (settings.dashboard_password, settings.dashboard_viewer_password):
        if password and len(password) < MIN_PASSWORD_LENGTH:
            raise RuntimeError(f"Dashboard passwords must be at least {MIN_PASSWORD_LENGTH} characters")
    if env in STRICT_ENVIRONMENTS:
        if settings.demo_feedback_enabled or settings.seed_on_startup:
            raise RuntimeError("Use a separate demo environment for feedback and seed data")


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Требуется авторизация",
        headers={"WWW-Authenticate": "Basic"},
    )


# --- webhooks --------------------------------------------------------------------


def check_webhook_secret(request: Request, secret_from_header: str | None) -> None:
    """Accept the secret only in a header: query strings end up in access logs."""
    if not settings.webhook_secret:
        _require_local_dev(request)
        return
    if not secret_from_header or not _matches(secret_from_header, settings.webhook_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bad webhook secret")
