"""Records console errors/warnings and uncaught page exceptions."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass

from playwright.async_api import ConsoleMessage, Error, Page

from app.storage.manager import SessionStorage

RECORDED_TYPES = frozenset({"error", "warning", "assert"})
TEXT_LIMIT = 2_000


@dataclass
class ConsoleEvent:
    seq: int
    action_seq: int
    kind: str  # console | pageerror
    level: str  # error | warning | assert
    text: str
    url: str | None
    line: int | None
    page_url: str
    at: float
    stack: str | None = None


class ConsoleRecorder:
    def __init__(self, storage: SessionStorage, action_seq: Callable[[], int]):
        self._storage = storage
        self._action_seq = action_seq
        self._seq = 0
        self.events: list[ConsoleEvent] = []

    def attach(self, page: Page) -> None:
        page.on("console", lambda msg: self._on_console(page, msg))
        page.on("pageerror", lambda err: self._on_page_error(page, err))

    def since(self, action_seq: int) -> list[ConsoleEvent]:
        return [e for e in self.events if e.action_seq >= action_seq]

    def _on_console(self, page: Page, msg: ConsoleMessage) -> None:
        if msg.type not in RECORDED_TYPES:
            return
        location = msg.location or {}
        self._record(ConsoleEvent(
            seq=0, action_seq=0, kind="console", level=msg.type, text=msg.text[:TEXT_LIMIT],
            url=location.get("url") or None, line=location.get("lineNumber"),
            page_url=page.url, at=time.time(),
        ))

    def _on_page_error(self, page: Page, error: Error) -> None:
        self._record(ConsoleEvent(
            seq=0, action_seq=0, kind="pageerror", level="error",
            text=f"{error.name}: {error.message}"[:TEXT_LIMIT] if error.name else error.message[:TEXT_LIMIT],
            url=None, line=None, page_url=page.url, at=time.time(),
            stack=(error.stack or "")[:TEXT_LIMIT] or None,
        ))

    def _record(self, event: ConsoleEvent) -> None:
        self._seq += 1
        event.seq = self._seq
        event.action_seq = self._action_seq()
        self.events.append(event)
        self._storage.append_jsonl(self._storage.paths.console_events, asdict(event))
