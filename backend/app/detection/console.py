"""Signals from uncaught exceptions and console errors of first-party code."""

from __future__ import annotations

import re

from app.browser.console import ConsoleEvent
from app.detection import signatures
from app.safety.domain_scope import DomainScope
from app.schemas.bug import Signal

_FIRST_URL = re.compile(r"https?://[^\s)'\"]+")
# Resource load failures are reported by the HTTP detector with better detail.
_DUPLICATES_HTTP = "failed to load resource"


def console_signals(events: list[ConsoleEvent], scope: DomainScope) -> list[tuple[Signal, ConsoleEvent]]:
    out = []
    for e in events:
        if e.level != "error" or _third_party(e, scope):
            continue
        if e.kind == "pageerror":
            sig = Signal(kind="js_exception", strong=True, summary=f"Uncaught {e.text}",
                         signature=signatures.js(e.text), action_seq=e.action_seq)
        elif _DUPLICATES_HTTP in e.text.lower():
            continue
        else:
            sig = Signal(kind="console_error", strong=False, summary=f"Console error: {e.text}",
                         signature=signatures.console(e.text), action_seq=e.action_seq)
        out.append((sig, e))
    return out


def _third_party(e: ConsoleEvent, scope: DomainScope) -> bool:
    """Where the error came from: the logging script, else the top of the stack. Unknown counts as first-party."""
    source = e.url
    if not source and e.stack:
        match = _FIRST_URL.search(e.stack)
        source = match[0] if match else None
    return bool(source) and not scope.is_first_party(source)
