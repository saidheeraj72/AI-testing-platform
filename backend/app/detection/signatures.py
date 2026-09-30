"""Normalized keys for errors, so the same problem is recognized across pages, ids and runs."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

_ID_SEGMENT = re.compile(r"^(\d+|[0-9a-f]{8,}|[0-9a-f-]{36}|[A-Z]{2,5}-\d+)$", re.I)
_URL = re.compile(r"https?://\S+")
_NUMBER = re.compile(r"\d+")
_QUOTED = re.compile(r"(['\"`]).*?\1")


def path_template(url: str) -> str:
    """/api/orders/ORD-1001/items?x=1 -> /api/orders/:id/items"""
    path = urlsplit(url).path or "/"
    return "/".join(":id" if _ID_SEGMENT.match(seg) else seg for seg in path.split("/"))


def message(text: str) -> str:
    """Error text without URLs, numbers and quoted values."""
    text = _URL.sub("<url>", text)
    text = _QUOTED.sub("'…'", text)
    text = _NUMBER.sub("#", text)
    return " ".join(text.lower().split())[:200]


def http(method: str, url: str, status: int) -> str:
    return f"http {method.upper()} {path_template(url)} {status}"


def network_failure(method: str, url: str, failure: str) -> str:
    return f"netfail {method.upper()} {path_template(url)} {failure.split()[0] if failure else ''}"


def js(text: str) -> str:
    return f"js {message(text)}"


def console(text: str) -> str:
    return f"console {message(text)}"
