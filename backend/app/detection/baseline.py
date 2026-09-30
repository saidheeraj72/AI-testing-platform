"""Errors the page already had before the test did anything.

The first action of a session loads the target. Any error signature seen by
then is pre-existing: it is not attributed to the tested workflow, and the
same signature later (e.g. the same console error on every full page load)
is ignored too.
"""

from __future__ import annotations

from app.schemas.bug import Signal

BASELINE_ACTIONS = 1  # the initial navigation to the target


class Baseline:
    def __init__(self, signatures: set[str]):
        self.signatures = signatures

    @classmethod
    def from_signals(cls, signals: list[Signal], until_action: int = BASELINE_ACTIONS) -> Baseline:
        return cls({s.signature for s in signals if s.action_seq <= until_action})

    def filter(self, signals: list[Signal], until_action: int = BASELINE_ACTIONS) -> list[Signal]:
        return [s for s in signals if s.action_seq > until_action and s.signature not in self.signatures]
