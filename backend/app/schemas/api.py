"""Request and response bodies of the HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.safety.domain_scope import DomainScope, ScopeError


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


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    target_url: str
    allowed_domains: list[str]
    persistent_profile: bool
    created_at: datetime


class SessionCreate(BaseModel):
    project_id: str
    objective: str = Field(min_length=3, max_length=4000)
    start: bool = Field(True, description="start immediately")


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    objective: str
    status: str
    outcome: str | None
    reason: str | None
    model: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
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


class ConfirmationAnswer(BaseModel):
    confirmation_id: str
    allow: bool


class EvidenceFile(BaseModel):
    path: str
    type: str
    size: int
