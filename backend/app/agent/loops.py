"""Detects an agent going in circles within one step."""

from __future__ import annotations

from collections import Counter

REPEAT_LIMIT = 3  # the same action on the same target
STALE_LIMIT = 4  # consecutive actions after which the page has not changed at all


class LoopDetector:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._actions: Counter[tuple] = Counter()
        self._fingerprints: list[str] = []

    def record_action(self, signature: tuple) -> str | None:
        self._actions[signature] += 1
        if self._actions[signature] >= REPEAT_LIMIT:
            return f"the same action was repeated {REPEAT_LIMIT} times: {signature[0]} {signature[1]}"
        return None

    def record_page(self, fingerprint: str) -> str | None:
        self._fingerprints.append(fingerprint)
        recent = self._fingerprints[-(STALE_LIMIT + 1):]
        if len(recent) > STALE_LIMIT and len(set(recent)) == 1:
            return f"the page did not change after {STALE_LIMIT} actions"
        return None
