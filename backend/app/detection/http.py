"""Signals from first-party HTTP traffic. Third-party requests never raise signals."""

from __future__ import annotations

from app.browser.network import NetworkEvent
from app.detection import signatures
from app.schemas.bug import Signal

TRACKED_TYPES = ("fetch", "xhr", "document")
# Statuses an application returns on purpose: validation (400/409/422), auth checks (401/403).
EXPECTED_4XX = frozenset({400, 401, 403, 409, 422, 429})
IGNORED_FAILURES = ("ERR_BLOCKED_BY_CLIENT",)  # our own domain-scope guard


def http_signals(events: list[NetworkEvent]) -> list[tuple[Signal, NetworkEvent]]:
    out = []
    for e in events:
        if not e.first_party or e.resource_type not in TRACKED_TYPES or not e.is_error:
            continue
        if e.status is not None:
            if e.status in EXPECTED_4XX:
                continue
            strong = e.status >= 500
            summary = f"{e.method} {signatures.path_template(e.url)} returned HTTP {e.status}"
            sig = Signal(kind="http_error", strong=strong, summary=summary,
                         signature=signatures.http(e.method, e.url, e.status), action_seq=e.action_seq)
        else:
            if any(code in (e.failure or "") for code in IGNORED_FAILURES):
                continue
            summary = f"{e.method} {signatures.path_template(e.url)} failed: {e.failure}"
            sig = Signal(kind="network_failure", strong=True, summary=summary,
                         signature=signatures.network_failure(e.method, e.url, e.failure or ""),
                         action_seq=e.action_seq)
        out.append((sig, e))
    return out
