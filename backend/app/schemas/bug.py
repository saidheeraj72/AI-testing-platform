"""Bug candidates (what detectors find) and bugs (what the report says)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SignalKind = Literal["http_error", "network_failure", "js_exception", "console_error", "assertion_failure"]
Severity = Literal["critical", "high", "medium", "low"]
Category = Literal["functional", "validation", "ui", "performance", "security", "other"]

# Order used to pick an incident's primary signal.
SIGNAL_PRIORITY: dict[SignalKind, int] = {
    "http_error": 0, "js_exception": 1, "network_failure": 2, "assertion_failure": 3, "console_error": 4,
}


class Signal(BaseModel):
    kind: SignalKind
    strong: bool = Field(description="strong signals are always reported; weak ones need the analyzer")
    summary: str
    signature: str = Field(description="normalized key used for baseline filtering and deduplication")
    action_seq: int


class NetworkEvidence(BaseModel):
    method: str
    url: str
    status: int | None = None
    failure: str | None = None
    response_body: str | None = None


class Candidate(BaseModel):
    """One incident: every signal raised by the same action (e.g. a 500, its console error, a failed check)."""

    action_seq: int
    step: int | None = None
    step_goal: str | None = None
    url: str
    action: str | None = Field(None, description="e.g. click button 'Create customer'")
    signals: list[Signal]
    network: list[NetworkEvidence] = Field(default_factory=list)
    console: list[str] = Field(default_factory=list)
    failed_check: str | None = None
    check_detail: str | None = None
    screenshot: str | None = None

    @property
    def primary(self) -> Signal:
        return min(self.signals, key=lambda s: (SIGNAL_PRIORITY[s.kind], not s.strong))

    @property
    def strong(self) -> bool:
        return any(s.strong for s in self.signals)


class Analysis(BaseModel):
    """What the analyzer model returns for one candidate."""

    is_bug: bool
    title: str = Field(description="at most 12 words, says what is broken")
    severity: Severity
    category: Category
    summary: str
    expected: str
    actual: str


class Occurrence(BaseModel):
    step: int | None
    action_seq: int
    url: str
    screenshot: str | None = None


class Bug(BaseModel):
    id: str
    title: str
    severity: Severity
    category: Category
    summary: str
    expected: str
    actual: str
    url: str
    signature: str
    signals: list[str]
    described_by: Literal["analyzer", "rules"]
    steps_to_reproduce: list[str]
    occurrences: list[Occurrence]
    network: list[NetworkEvidence]
    console: list[str]
