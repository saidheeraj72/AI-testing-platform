from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Action, Bug, BugOccurrence, Evidence, Observation, Session, Step
from app.storage.manager import SessionStorage

ACTIVE_STATUSES = ("RUNNING", "PAUSED", "WAITING_FOR_USER")


async def create(db: AsyncSession, *, session_id: str, project_id: str, objective: str, model: str,
                 mode: str = "objective") -> Session:
    row = Session(id=session_id, project_id=project_id, objective=objective, status="CREATED", model=model, mode=mode)
    db.add(row)
    await db.commit()
    return row


async def get(db: AsyncSession, session_id: str) -> Session | None:
    return await db.get(Session, session_id)


async def get_detail(db: AsyncSession, session_id: str) -> Session | None:
    return await db.scalar(
        select(Session).where(Session.id == session_id).options(
            selectinload(Session.steps),
            selectinload(Session.bugs).selectinload(Bug.occurrences),
        )
    )


async def list_recent(db: AsyncSession, *, project_id: str | None = None, limit: int = 50) -> list[Session]:
    query = select(Session).order_by(Session.created_at.desc()).limit(limit)
    if project_id:
        query = query.where(Session.project_id == project_id)
    return list((await db.scalars(query)).all())


async def set_status(db: AsyncSession, session_id: str, status: str, **fields: Any) -> None:
    await db.execute(update(Session).where(Session.id == session_id).values(status=status, **fields))
    await db.commit()


async def mark_interrupted(db: AsyncSession) -> int:
    """Sessions that were running when the server stopped cannot resume."""
    result = await db.execute(
        update(Session).where(Session.status.in_(ACTIVE_STATUSES))
        .values(status="INTERRUPTED", reason="the server stopped while the session was running")
    )
    await db.commit()
    return result.rowcount or 0


async def save_results(db: AsyncSession, session_id: str, storage: SessionStorage, status: str) -> None:
    """Copy a finished session's results from its folder into the database (idempotent)."""
    report = _read_json(storage.paths.report) or {}
    summary = report.get("summary", {})
    for model in (Step, Action, Observation, Evidence, Bug):
        await db.execute(delete(model).where(model.session_id == session_id))

    for position, s in enumerate(report.get("steps", [])):
        db.add(Step(
            session_id=session_id, position=position, sequence=s["sequence"], goal=s["goal"],
            success_criteria=s.get("criteria", []), checks=s.get("checks", []), status=s["status"],
            reason=s.get("reason"), started_at=_dt(s.get("started_at")), completed_at=_dt(s.get("completed_at")),
        ))

    for a in _read_jsonl(storage.paths.actions):
        target = a.get("target") or {}
        db.add(Action(
            session_id=session_id, sequence=a["sequence"], action_type=a["action"],
            element_id=a["arguments"].get("ref"), target_role=target.get("role"), target_name=target.get("name"),
            arguments_json=a["arguments"], result="ok" if a["ok"] else (a.get("error") or "error"),
            message=a.get("message") or None, url=a["url_after"], duration_ms=a.get("duration_ms", 0),
            screenshot_path=a.get("screenshot"),
        ))

    for path in sorted(storage.paths.observations.glob("*[0-9].json")):
        o = _read_json(path) or {}
        db.add(Observation(
            session_id=session_id, sequence=o.get("sequence", 0), url=o.get("url", ""), title=o.get("title", ""),
            observation_path=storage.relative(path), screenshot_path=o.get("screenshot_path"),
        ))

    for b in report.get("bugs", []):
        occurrences = b.get("occurrences", [])
        bug = Bug(
            session_id=session_id, code=b["id"], title=b["title"], severity=b["severity"], category=b["category"],
            summary=b["summary"], expected=b["expected"], actual=b["actual"], url=b["url"],
            signature=b["signature"], described_by=b["described_by"], steps_to_reproduce=b["steps_to_reproduce"],
            first_seen_step=occurrences[0]["step"] if occurrences else None,
            occurrences=[BugOccurrence(step=o["step"], action_seq=o["action_seq"], url=o["url"],
                                       screenshot_path=o.get("screenshot")) for o in occurrences],
        )
        db.add(bug)
        await db.flush()
        evidence = b.get("evidence", {})
        for n in evidence.get("network", []):
            db.add(Evidence(session_id=session_id, bug_id=bug.id, type="network", metadata_json=n))
        for c in evidence.get("console", []):
            db.add(Evidence(session_id=session_id, bug_id=bug.id, type="console", metadata_json={"text": c}))
        for shot in evidence.get("screenshots", []):
            db.add(Evidence(session_id=session_id, bug_id=bug.id, type="screenshot", path=shot))

    if report.get("trace"):
        db.add(Evidence(session_id=session_id, type="trace", path=report["trace"]))

    manifest = storage.read_manifest()
    await db.execute(update(Session).where(Session.id == session_id).values(
        status=status,
        outcome=report.get("outcome"),
        reason=report.get("reason") or None,
        finished_at=_dt(manifest.get("finished_at")) or datetime.now(timezone.utc),
        duration_ms=report.get("duration_ms"),
        steps_planned=summary.get("steps_planned", 0),
        steps_completed=summary.get("steps_passed", 0),
        steps_failed=summary.get("steps_failed", 0),
        steps_could_not_verify=summary.get("steps_could_not_verify", 0),
        steps_skipped=summary.get("steps_skipped", 0) + summary.get("steps_blocked", 0),
        pages_visited=summary.get("pages_visited", 0),
        bugs_found=summary.get("bugs", 0),
    ))
    await db.commit()


def _read_json(path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _read_jsonl(path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
