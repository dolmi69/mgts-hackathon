from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .config import settings

basic_security = HTTPBasic(auto_error=False)


def require_dashboard_auth(
    credentials: HTTPBasicCredentials | None = Depends(basic_security),
) -> None:
    """Optional Basic Auth for dashboard endpoints.

    The demo can run without auth locally. For a public stand set
    DASHBOARD_USER and DASHBOARD_PASSWORD, or put Caddy in front.
    """
    if not settings.dashboard_password:
        return
    if credentials is None:
        raise _unauthorized()
    user_ok = secrets.compare_digest(credentials.username, settings.dashboard_user or "")
    password_ok = secrets.compare_digest(credentials.password, settings.dashboard_password)
    if not (user_ok and password_ok):
        raise _unauthorized()


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
    if not supplied or not secrets.compare_digest(supplied, settings.webhook_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bad webhook secret")
