from __future__ import annotations

import hmac
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.config import get_settings

SESSION_USER_KEY = "dashboard_user"

_OPEN_PREFIXES = ("/static", "/slack", "/github", "/docs", "/redoc")
_OPEN_PATHS = {"/health", "/login", "/logout", "/openapi.json", "/favicon.ico"}


def safe_next_path(value: str | None) -> str:
    path = (value or "/").strip() or "/"
    if not path.startswith("/") or path.startswith("//") or "://" in path:
        return "/"
    return path


def _same_secret(expected: str, provided: str) -> bool:
    left = expected.encode("utf-8")
    right = provided.encode("utf-8")
    if len(left) != len(right):
        hmac.compare_digest(left, left)
        return False
    return hmac.compare_digest(left, right)


def credentials_match(username: str, password: str, expected_user: str, expected_password: str) -> bool:
    return _same_secret(expected_user, username) and _same_secret(expected_password, password)


def is_signed_in(request: Request, username: str) -> bool:
    return request.session.get(SESSION_USER_KEY) == username


def _needs_dashboard_auth(request: Request) -> bool:
    path = request.url.path
    if path in _OPEN_PATHS or path.startswith(_OPEN_PREFIXES):
        return False
    if path == "/jobs" and request.method == "POST":
        return False
    return path == "/" or path.startswith("/tickets") or path.startswith("/jobs")


class DashboardAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        settings = get_settings()
        if not _needs_dashboard_auth(request):
            return await call_next(request)
        if is_signed_in(request, settings.dashboard_username):
            return await call_next(request)
        if request.url.path.startswith("/jobs"):
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        nxt = quote(safe_next_path(request.url.path), safe="/")
        return RedirectResponse(f"/login?next={nxt}", status_code=303)
