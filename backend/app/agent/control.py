"""Pause / resume from outside a running session (API, UI "Take control")."""

from __future__ import annotations

import asyncio
import time


class SessionControl:
    def __init__(self) -> None:
        self._running = asyncio.Event()
        self._running.set()
        self._paused_at: float | None = None
        self._paused_total = 0.0

    @property
    def paused(self) -> bool:
        return not self._running.is_set()

    def pause(self) -> None:
        if not self.paused:
            self._paused_at = time.monotonic()
            self._running.clear()

    def resume(self) -> None:
        if self.paused:
            self._paused_total += time.monotonic() - (self._paused_at or time.monotonic())
            self._paused_at = None
            self._running.set()

    def paused_seconds(self) -> float:
        current = time.monotonic() - self._paused_at if self._paused_at is not None else 0.0
        return self._paused_total + current

    async def checkpoint(self) -> None:
        """Wait here while paused. The agent re-observes the page afterwards, so user changes are seen."""
        await self._running.wait()
