"""In-memory event stream per session, with replay for late subscribers (e.g. a UI opened mid-run)."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Any

HISTORY_LIMIT = 5_000
END = {"type": "stream_end"}


class EventHub:
    def __init__(self) -> None:
        self._history: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._closed: set[str] = set()

    def publish(self, session_id: str, event: dict[str, Any]) -> None:
        history = self._history[session_id]
        event = {"seq": len(history) + 1, "at": time.time(), "session_id": session_id, **event}
        history.append(event)
        del history[:-HISTORY_LIMIT]
        for queue in self._subscribers[session_id]:
            queue.put_nowait(event)

    def close(self, session_id: str) -> None:
        """No more events for this session; subscribers get END after the history."""
        self._closed.add(session_id)
        for queue in self._subscribers[session_id]:
            queue.put_nowait(END)

    def subscribe(self, session_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        for event in self._history.get(session_id, []):
            queue.put_nowait(event)
        if session_id in self._closed:
            queue.put_nowait(END)
        self._subscribers[session_id].add(queue)
        return queue

    def unsubscribe(self, session_id: str, queue: asyncio.Queue) -> None:
        self._subscribers[session_id].discard(queue)

    def history(self, session_id: str) -> list[dict[str, Any]]:
        return list(self._history.get(session_id, []))
