"""Local API protection.

The API drives a browser that may be logged in to real systems, so "it's only
localhost" is not enough: any web page the user opens could otherwise call it.

- Bound to a loopback address only (enforced in config).
- Host header must be a loopback name: blocks DNS-rebinding attacks.
- Origin, when a browser sends one, must be an allowed UI origin.
- Every request carries the startup token (X-AI-Tester-Token, or ?token= for
  WebSockets, which cannot set headers). Web pages cannot read it.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import Request, WebSocket
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

TOKEN_HEADER = "X-AI-Tester-Token"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]", "::1"})
PUBLIC_PATHS = frozenset({"/api/system/ping"})


def new_token() -> str:
    return secrets.token_urlsafe(32)


def write_token_file(path: Path, token: str) -> None:
    """For the UI launcher: readable by this user only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token)
    path.chmod(0o600)


def host_allowed(host_header: str | None, extra_hosts: frozenset[str]) -> bool:
    if not host_header:
        return False
    host = host_header.rsplit(":", 1)[0] if not host_header.startswith("[") else host_header.split("]")[0] + "]"
    return host in LOOPBACK_HOSTS or host in extra_hosts


def token_ok(given: str | None, expected: str) -> bool:
    return bool(given) and secrets.compare_digest(given, expected)


class LocalOnlyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, token: str, allowed_origins: list[str], extra_hosts: frozenset[str]):
        super().__init__(app)
        self.token = token
        self.origins = frozenset(allowed_origins)
        self.extra_hosts = extra_hosts

    async def dispatch(self, request: Request, call_next):
        if not host_allowed(request.headers.get("host"), self.extra_hosts):
            return JSONResponse({"detail": "invalid Host header"}, status_code=400)
        origin = request.headers.get("origin")
        if origin and origin not in self.origins:
            return JSONResponse({"detail": "origin not allowed"}, status_code=403)
        if request.method != "OPTIONS" and request.url.path not in PUBLIC_PATHS:
            if not token_ok(request.headers.get(TOKEN_HEADER), self.token):
                return JSONResponse({"detail": "missing or invalid token"}, status_code=401)
        return await call_next(request)


def websocket_allowed(ws: WebSocket, token: str, allowed_origins: list[str], extra_hosts: frozenset[str]) -> bool:
    origin = ws.headers.get("origin")
    return (
        host_allowed(ws.headers.get("host"), extra_hosts)
        and (not origin or origin in allowed_origins)
        and token_ok(ws.query_params.get("token"), token)
    )
