from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.api.deps import AppContext, ctx, get_project_or_404
from app.db.repositories import sessions as session_repo
from app.schemas.api import (
    BugOut,
    ConfirmationAnswer,
    Coverage,
    EvidenceFile,
    SessionCreate,
    SessionDetail,
    SessionOut,
)
from app.services.session_manager import SessionError

router = APIRouter(prefix="/api/sessions", tags=["sessions"])
Ctx = Annotated[AppContext, Depends(ctx)]

EVIDENCE_TYPES = {".png": "screenshot", ".zip": "trace", ".json": "json", ".jsonl": "log", ".yaml": "snapshot"}


@router.post("", status_code=201)
async def create_session(body: SessionCreate, c: Ctx) -> SessionOut:
    project = await get_project_or_404(c, body.project_id)
    session_id = await c.manager.create(project, body.objective, mode=body.mode)
    if body.start:
        await _start(c, session_id)
    return await _session_out(c, session_id)


@router.get("")
async def list_sessions(c: Ctx, project_id: str | None = None, limit: int = 50) -> list[SessionOut]:
    async with c.db.session() as db:
        rows = await session_repo.list_recent(db, project_id=project_id, limit=min(limit, 200))
    return [_with_live(c, SessionOut.model_validate(r)) for r in rows]


@router.get("/{session_id}")
async def get_session(session_id: str, c: Ctx) -> SessionDetail:
    async with c.db.session() as db:
        row = await session_repo.get_detail(db, session_id)
    if row is None:
        raise HTTPException(404, "session not found")
    return _with_live(c, SessionDetail.model_validate(row))


@router.post("/{session_id}/start")
async def start_session(session_id: str, c: Ctx) -> SessionOut:
    await _start(c, session_id)
    return await _session_out(c, session_id)


@router.post("/{session_id}/pause")
async def pause_session(session_id: str, c: Ctx) -> SessionOut:
    await _control(c.manager.pause(session_id))
    return await _session_out(c, session_id)


@router.post("/{session_id}/resume")
async def resume_session(session_id: str, c: Ctx) -> SessionOut:
    await _control(c.manager.resume(session_id))
    return await _session_out(c, session_id)


@router.post("/{session_id}/stop", status_code=202)
async def stop_session(session_id: str, c: Ctx) -> dict:
    """Asks the session to stop; it saves its trace and report, then ends as CANCELLED."""
    await _control(c.manager.stop(session_id))
    return {"stopping": True}


@router.post("/{session_id}/confirm")
async def confirm_action(session_id: str, body: ConfirmationAnswer, c: Ctx) -> dict:
    await _control(c.manager.confirm(session_id, body.confirmation_id, body.allow))
    return {"ok": True}


@router.get("/{session_id}/bugs")
async def session_bugs(session_id: str, c: Ctx) -> list[BugOut]:
    return (await get_session(session_id, c)).bugs


@router.get("/{session_id}/coverage")
async def session_coverage(session_id: str, c: Ctx) -> Coverage:
    session = await get_session(session_id, c)
    report = _report(c, session_id) or {}
    return Coverage(
        **{k: getattr(session, k) for k in ("steps_planned", "steps_completed", "steps_failed",
                                            "steps_could_not_verify", "steps_skipped", "pages_visited")},
        could_not_verify=report.get("could_not_verify", []),
        not_tested=report.get("not_tested", []),
    )


@router.get("/{session_id}/report")
async def session_report(session_id: str, c: Ctx) -> dict:
    report = _report(c, session_id)
    if report is None:
        raise HTTPException(404, "no report yet")
    return report


@router.get("/{session_id}/events")
async def session_events(session_id: str, c: Ctx) -> list[dict]:
    """Events so far (the WebSocket streams them live)."""
    return c.hub.history(session_id)


@router.get("/{session_id}/evidence")
async def list_evidence(session_id: str, c: Ctx) -> list[EvidenceFile]:
    root = _session_dir(c, session_id)
    return [
        EvidenceFile(path=p.relative_to(root).as_posix(), type=EVIDENCE_TYPES.get(p.suffix, "file"),
                     size=p.stat().st_size)
        for p in sorted(root.rglob("*")) if p.is_file() and not p.name.endswith(".tmp")
    ]


@router.get("/{session_id}/files/{file_path:path}")
async def get_file(session_id: str, file_path: str, c: Ctx) -> FileResponse:
    root = _session_dir(c, session_id).resolve()
    target = (root / file_path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(target)


async def _start(c: AppContext, session_id: str) -> None:
    async with c.db.session() as db:
        row = await session_repo.get(db, session_id)
    if row is None:
        raise HTTPException(404, "session not found")
    project = await get_project_or_404(c, row.project_id)
    await _control(c.manager.start(session_id, project))


async def _control(coro) -> None:
    try:
        await coro
    except SessionError as e:
        raise HTTPException(409, str(e)) from None


async def _session_out(c: AppContext, session_id: str) -> SessionOut:
    async with c.db.session() as db:
        row = await session_repo.get(db, session_id)
    if row is None:
        raise HTTPException(404, "session not found")
    return _with_live(c, SessionOut.model_validate(row))


def _with_live(c: AppContext, out):
    if live := c.manager.live(out.id):
        out.live = live
        out.status = live["status"]
    return out


def _session_dir(c: AppContext, session_id: str):
    root = c.sessions_dir / session_id
    if "/" in session_id or ".." in session_id or not root.is_dir():
        raise HTTPException(404, "session not found")
    return root


def _report(c: AppContext, session_id: str) -> dict | None:
    path = _session_dir(c, session_id) / "report.json"
    return json.loads(path.read_text()) if path.exists() else None
