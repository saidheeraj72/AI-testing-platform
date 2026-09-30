from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class ActionError(StrEnum):
    UNKNOWN_REF = "UNKNOWN_REF"                  # ref not in the latest observation
    ELEMENT_NOT_FOUND = "ELEMENT_NOT_FOUND"      # element gone after re-observing
    AMBIGUOUS_ELEMENT = "AMBIGUOUS_ELEMENT"      # several equally good matches
    BLOCKED_NAVIGATION = "BLOCKED_NAVIGATION"    # outside the domain scope
    NAVIGATION_FAILED = "NAVIGATION_FAILED"
    TIMEOUT = "TIMEOUT"
    ACTION_FAILED = "ACTION_FAILED"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"


class Target(BaseModel):
    role: str
    name: str


class ActionResult(BaseModel):
    sequence: int
    action: str
    arguments: dict[str, object]
    ok: bool
    error: ActionError | None = None
    message: str = ""
    target: Target | None = None
    resolved_ref: str | None = None
    url_before: str
    url_after: str
    http_status: int | None = None
    settled: bool = True
    duration_ms: int = 0

    @property
    def navigated(self) -> bool:
        return self.url_before != self.url_after
