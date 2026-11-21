from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .config import settings

basic_security = HTTPBasic(auto_error=False)


def require_dashboard_auth(
    credentials: HTTPBasicCredentials | None = Depends(basic_security),
) -> str | None:
    """Return the configured dashboard account, or None in local demo mode."""
    if not _login_required():
        return None
    if credentials is None:
        raise _unauthorized()
    for username, password in (
        (settings.dashboard_user, settings.dashboard_password),
        (settings.dashboard_viewer_user, settings.dashboard_viewer_password),
    ):
        if username and password:
            user_ok = _matches(credentials.username, username)
            password_ok = _matches(credentials.password, password)
            if user_ok and password_ok:
                return username
    raise _unauthorized()


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


def validate_deployment_auth() -> None:
    hr_pair = bool(settings.dashboard_user), bool(settings.dashboard_password)
    viewer_pair = bool(settings.dashboard_viewer_user), bool(settings.dashboard_viewer_password)
    if hr_pair[1] and not hr_pair[0]:
        raise RuntimeError("Set DASHBOARD_USER when using DASHBOARD_PASSWORD")
    if viewer_pair[0] != viewer_pair[1]:
        raise RuntimeError("Set both DASHBOARD_VIEWER_USER and DASHBOARD_VIEWER_PASSWORD")
    if viewer_pair[0] and settings.dashboard_viewer_user == settings.dashboard_user:
        raise RuntimeError("HR and read-only dashboard usernames must differ")
    if settings.app_env.strip().lower() in {"pilot", "production"}:
        if not all(hr_pair) or not settings.webhook_secret or settings.webhook_secret == "change-me":
            raise RuntimeError("Set an HR login, password and unique webhook secret for the pilot")
        if settings.demo_feedback_enabled or settings.seed_on_startup:
            raise RuntimeError("Use a separate demo environment for feedback and seed data")


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Требуется авторизация",
        headers={"WWW-Authenticate": "Basic"},
    )


def check_webhook_secret(secret_from_query: str | None, secret_from_header: str | None) -> None:
    if not settings.webhook_secret:
        return
    supplied = secret_from_header or secret_from_query
    if not supplied or not _matches(supplied, settings.webhook_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bad webhook secret")
