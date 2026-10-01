"""Request and response bodies of the HTTP API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.safety.domain_scope import DomainScope, ScopeError


def _utc(value: datetime) -> datetime:
    """SQLite returns naive datetimes; they are stored in UTC."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


UtcDatetime = Annotated[datetime, AfterValidator(_utc)]


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    target_url: str
    allowed_domains: list[str] = Field(default_factory=list, description="extra domains, e.g. an SSO provider")
    persistent_profile: bool = Field(True, description="keep logins between sessions")

    @field_validator("target_url")
    @classmethod
    def _valid_target(cls, v: str) -> str:
        try:
            DomainScope.from_target(v)
        except ScopeError as e:
            raise ValueError(str(e)) from None
        return v


class ProjectUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    allowed_domains: list[str] | None = None
    persistent_profile: bool | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    target_url: str
    allowed_domains: list[str]
    persistent_profile: bool
    created_at: UtcDatetime


class SessionCreate(BaseModel):
    project_id: str
    mode: Literal["objective", "explore"] = "objective"
    objective: str = Field("", max_length=4000, description="explore mode: optional notes, e.g. credentials")
    start: bool = Field(True, description="start immediately")

    @model_validator(mode="after")
    def _objective_needed(self) -> SessionCreate:
        if self.mode == "objective" and len(self.objective.strip()) < 3:
            raise ValueError("objective is required (at least 3 characters)")
        return self


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    objective: str
    mode: str = "objective"
    browser: str = "managed"
    status: str
    outcome: str | None
    reason: str | None
    model: str | None
    created_at: UtcDatetime
    started_at: UtcDatetime | None
    finished_at: UtcDatetime | None
    duration_ms: int | None
    steps_planned: int
    steps_completed: int
    steps_failed: int
    steps_could_not_verify: int
    steps_skipped: int
    pages_visited: int
    bugs_found: int
    live: dict[str, Any] | None = None


class StepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sequence: int
    goal: str
    status: str
    reason: str | None
    success_criteria: list[Any]
    checks: list[Any]


class OccurrenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    step: int | None
    action_seq: int
    url: str
    screenshot_path: str | None


class BugOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    title: str
    severity: str
    category: str
    summary: str
    expected: str
    actual: str
    url: str
    described_by: str
    steps_to_reproduce: list[str]
    status: str
    occurrences: list[OccurrenceOut]


class SessionDetail(SessionOut):
    steps: list[StepOut]
    bugs: list[BugOut]


class Coverage(BaseModel):
    steps_planned: int
    steps_completed: int
    steps_failed: int
    steps_could_not_verify: int
    steps_skipped: int
    pages_visited: int
    could_not_verify: list[dict[str, Any]] = Field(default_factory=list)
    not_tested: list[dict[str, Any]] = Field(default_factory=list)


class TabSessionCreate(BaseModel):
    """From the Chrome extension: test the user's current tab."""

    url: str
    mode: Literal["objective", "explore"] = "objective"
    objective: str = Field("", max_length=4000)

    @field_validator("url")
    @classmethod
    def _valid_url(cls, v: str) -> str:
        try:
            DomainScope.from_target(v)
        except ScopeError as e:
            raise ValueError(str(e)) from None
        return v

    @model_validator(mode="after")
    def _objective_needed(self) -> TabSessionCreate:
        if self.mode == "objective" and len(self.objective.strip()) < 3:
            raise ValueError("objective is required (at least 3 characters)")
        return self


class ConfirmationAnswer(BaseModel):
    confirmation_id: str
    allow: bool


class EvidenceFile(BaseModel):
    path: str
    type: str
    size: int
