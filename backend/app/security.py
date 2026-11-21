"""HTTP hardening shared by every route: body limits, CSRF and response headers."""
from __future__ import annotations

from urllib.parse import urlsplit

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import settings

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
DEFAULT_BODY_LIMIT = 1_000_000

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'"
)
# Swagger UI loads its assets from a CDN and runs an inline bootstrap script.
DOCS_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://fastapi.tiangolo.com; frame-ancestors 'none'"
)
SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
]


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return None


async def _reject(send: Send, status: int, detail: str) -> None:
    body = ('{"detail":"%s"}' % detail).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json; charset=utf-8"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


def body_limit(path: str) -> int:
    if path.startswith("/api/webhooks/"):
        return settings.max_webhook_bytes
    if path.startswith("/api/import/"):
        return settings.max_import_bytes
    return DEFAULT_BODY_LIMIT


class BodySizeLimitMiddleware:
    """Reject oversized bodies before the app reads them into memory."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in UNSAFE_METHODS:
            await self.app(scope, receive, send)
            return
        limit = body_limit(scope["path"])
        declared = _header(scope, b"content-length")
        if declared is not None:
            try:
                if int(declared) > limit:
                    await _reject(send, 413, f"Тело запроса больше {limit} байт")
                    return
            except ValueError:
                await _reject(send, 400, "Некорректный Content-Length")
                return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # Chunked body without Content-Length: stop reading.
                    raise _BodyTooLarge
            return message

        response_started = False

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if not response_started:
                await _reject(send, 413, f"Тело запроса больше {limit} байт")


class _BodyTooLarge(Exception):
    pass


class SameOriginMiddleware:
    """CSRF guard: browsers attach Basic Auth to any request to this host,
    so state-changing requests from another site's page must be refused.
    Non-browser clients (curl, Yandex Forms) send no Origin and pass through.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["method"] in UNSAFE_METHODS
            and not scope["path"].startswith("/api/webhooks/")
        ):
            source = _header(scope, b"origin") or _header(scope, b"referer")
            host = _header(scope, b"host")
            if source and source != "null":
                if urlsplit(source).netloc.lower() != (host or "").lower():
                    await _reject(send, 403, "Запрос с чужого сайта отклонён")
                    return
            elif source == "null":
                await _reject(send, 403, "Запрос с чужого сайта отклонён")
                return
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        docs = scope["path"].startswith(("/docs", "/redoc"))

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [item for item in message.get("headers", []) if item[0] != b"server"]
                present = {key for key, _ in headers}
                for key, value in SECURITY_HEADERS:
                    if key not in present:
                        headers.append((key, value))
                if b"content-security-policy" not in present:
                    headers.append((b"content-security-policy", (DOCS_CSP if docs else CSP).encode()))
                if scope["path"].startswith("/api/"):
                    headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
