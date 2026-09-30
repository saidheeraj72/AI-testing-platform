"""SQLite metadata. Large artifacts stay in the session folder; rows point at them by relative path."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON, datetime: DateTime(timezone=True)}


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    target_url: Mapped[str] = mapped_column(Text)
    allowed_domains: Mapped[list[Any]] = mapped_column(default=list)
    persistent_profile: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    sessions: Mapped[list[Session]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    objective: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), index=True)
    outcome: Mapped[str | None] = mapped_column(String(30))
    reason: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    duration_ms: Mapped[int | None]
    steps_planned: Mapped[int] = mapped_column(default=0)
    steps_completed: Mapped[int] = mapped_column(default=0)
    steps_failed: Mapped[int] = mapped_column(default=0)
    steps_could_not_verify: Mapped[int] = mapped_column(default=0)
    steps_skipped: Mapped[int] = mapped_column(default=0)
    pages_visited: Mapped[int] = mapped_column(default=0)
    bugs_found: Mapped[int] = mapped_column(default=0)

    project: Mapped[Project] = relationship(back_populates="sessions")
    steps: Mapped[list[Step]] = relationship(cascade="all, delete-orphan", order_by="Step.position")
    actions: Mapped[list[Action]] = relationship(cascade="all, delete-orphan", order_by="Action.sequence")
    observations: Mapped[list[Observation]] = relationship(cascade="all, delete-orphan",
                                                           order_by="Observation.sequence")
    bugs: Mapped[list[Bug]] = relationship(cascade="all, delete-orphan", order_by="Bug.code")
    evidence: Mapped[list[Evidence]] = relationship(cascade="all, delete-orphan")


class Step(Base):
    __tablename__ = "steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    position: Mapped[int]  # order in the final plan, including replaced steps
    sequence: Mapped[int]
    goal: Mapped[str] = mapped_column(Text)
    success_criteria: Mapped[list[Any]] = mapped_column(default=list)
    checks: Mapped[list[Any]] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]


class Action(Base):
    __tablename__ = "actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int]
    action_type: Mapped[str] = mapped_column(String(30))
    element_id: Mapped[str | None] = mapped_column(String(40))
    target_role: Mapped[str | None] = mapped_column(String(60))
    target_name: Mapped[str | None] = mapped_column(Text)
    arguments_json: Mapped[dict[str, Any]] = mapped_column(default=dict)
    result: Mapped[str] = mapped_column(String(40))  # ok | error code
    message: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(default=0)
    screenshot_path: Mapped[str | None] = mapped_column(Text)


class Observation(Base):
    __tablename__ = "observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int]
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    observation_path: Mapped[str] = mapped_column(Text)
    screenshot_path: Mapped[str | None] = mapped_column(Text)


class Bug(Base):
    __tablename__ = "bugs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(20))  # BUG-001 within the session
    title: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(20))
    category: Mapped[str] = mapped_column(String(30))
    summary: Mapped[str] = mapped_column(Text)
    expected: Mapped[str] = mapped_column(Text)
    actual: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    signature: Mapped[str] = mapped_column(Text)
    described_by: Mapped[str] = mapped_column(String(20))
    steps_to_reproduce: Mapped[list[Any]] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(String(20), default="open")
    first_seen_step: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    occurrences: Mapped[list[BugOccurrence]] = relationship(cascade="all, delete-orphan",
                                                            order_by="BugOccurrence.action_seq")


class BugOccurrence(Base):
    __tablename__ = "bug_occurrences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bug_id: Mapped[int] = mapped_column(ForeignKey("bugs.id", ondelete="CASCADE"), index=True)
    step: Mapped[int | None]
    action_seq: Mapped[int]
    url: Mapped[str] = mapped_column(Text)
    screenshot_path: Mapped[str | None] = mapped_column(Text)


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    bug_id: Mapped[int | None] = mapped_column(ForeignKey("bugs.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(20))  # network | console | screenshot | trace
    path: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
