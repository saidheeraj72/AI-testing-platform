"""Hard limits for one session. Crossing one ends the session cleanly."""

from __future__ import annotations

import time

from app.config import AgentSettings


class BudgetExceeded(RuntimeError):
    pass


class SessionBudget:
    def __init__(self, settings: AgentSettings):
        self.settings = settings
        self.model_calls = 0
        self._started = time.monotonic()

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self._started

    def check_time(self) -> None:
        if self.elapsed_seconds > self.settings.max_duration_seconds:
            raise BudgetExceeded(f"time limit of {self.settings.max_duration_seconds:.0f}s reached")

    def before_model_call(self) -> None:
        self.check_time()
        if self.model_calls >= self.settings.max_model_calls:
            raise BudgetExceeded(f"model call limit of {self.settings.max_model_calls} reached")
        self.model_calls += 1
