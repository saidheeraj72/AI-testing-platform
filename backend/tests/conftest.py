"""Shared fixtures: a local fixture website and a BrowserSession factory.

Two servers run on 127.0.0.1: `site` is the target (in scope) and `other` is
a different port, which DomainScope treats as out of scope for loopback targets.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


class _Handler(BaseHTTPRequestHandler):
    routes: dict[str, Callable[[_Handler], None]] = {}

    def do_GET(self):  # noqa: N802
        self._dispatch()

    def do_POST(self):  # noqa: N802
        self._dispatch()

    def _dispatch(self):
        path = self.path.split("?")[0]
        handler = self.routes.get(path)
        if handler is None:
            page = FIXTURES / f"{path.strip('/') or 'index'}.html"
            if page.exists():
                return self.send_body(200, page.read_text(), "text/html")
            return self.send_body(404, "not found", "text/plain")
        handler(self)

    def send_body(self, status: int, body: str, content_type: str, headers: dict | None = None):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # keep test output quiet
        pass


def _json(status: int, body: dict) -> Callable[[_Handler], None]:
    return lambda h: h.send_body(status, json.dumps(body), "application/json")


def _slow(h: _Handler) -> None:
    time.sleep(0.8)
    h.send_body(200, json.dumps({"ok": True}), "application/json")


def _set_cookie(h: _Handler) -> None:
    h.send_body(200, "<p>cookie set</p>", "text/html",
                {"Set-Cookie": "remember=yes; Max-Age=3600; Path=/"})


def _whoami(h: _Handler) -> None:
    cookie = h.headers.get("Cookie", "")
    h.send_body(200, f"<h1>Cookie: {cookie or 'none'}</h1>", "text/html")


class _Server:
    def __init__(self, routes: dict):
        handler = type("Handler", (_Handler,), {"routes": routes})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()


@pytest.fixture(scope="session")
def other_site():
    server = _Server({})
    yield server.url
    server.close()


@pytest.fixture(scope="session")
def site(other_site):
    def redirect_out(h: _Handler):
        h.send_body(302, "", "text/plain", {"Location": f"{other_site}/page2"})

    server = _Server({
        "/api/fail": _json(500, {"error": "boom"}),
        "/api/ok": _json(200, {"ok": True}),
        "/api/slow": _slow,
        "/api/login": _json(200, {"ok": True}),
        "/set-cookie": _set_cookie,
        "/whoami": _whoami,
        "/redirect-out": redirect_out,
    })
    yield server.url
    server.close()
