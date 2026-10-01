"""Tab sessions from the Chrome extension, and the two WebSockets of the CDP relay."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, WebSocket

from app.api.deps import AppContext, ctx
from app.api.security import websocket_allowed
from app.db.repositories import projects as project_repo
from app.db.repositories import sessions as session_repo
from app.schemas.api import TabSessionCreate
from app.services.session_manager import SessionError

router = APIRouter(tags=["extension"])
Ctx = Annotated[AppContext, Depends(ctx)]


@router.post("/api/tab-sessions", status_code=201)
async def create_tab_session(body: TabSessionCreate, c: Ctx) -> dict:
    """Test the user's current tab. The extension then connects to the returned relay."""
    parts = urlsplit(body.url)
    origin = f"{parts.scheme}://{parts.netloc}"
    async with c.db.session() as db:
        project = await session_repo.find_project_by_url(db, origin)
        if project is None:
            project = await project_repo.create(db, name=f"{parts.netloc} (your browser)", target_url=origin,
                                                allowed_domains=[], persistent_profile=False)
    session_id = await c.manager.create(project, body.objective, mode=body.mode, browser="tab")
    try:
        await c.manager.start(session_id, project)
    except SessionError as e:
        raise HTTPException(409, str(e)) from None
    return {"session_id": session_id, "project_id": project.id,
            "relay_path": f"/api/relay/{session_id}/extension"}


@router.websocket("/api/relay/{relay_id}/extension")
async def extension_socket(ws: WebSocket, relay_id: str) -> None:
    await _serve(ws, relay_id, "extension")


@router.websocket("/api/relay/{relay_id}/cdp")
async def playwright_socket(ws: WebSocket, relay_id: str) -> None:
    await _serve(ws, relay_id, "cdp")


async def _serve(ws: WebSocket, relay_id: str, side: str) -> None:
    c: AppContext = ws.app.state.ctx
    relay = c.manager.relays.get(relay_id)
    allowed = websocket_allowed(ws, c.token, c.settings.server.allowed_origins, c.extra_hosts,
                                c.settings.server.allow_extension)
    if relay is None or not allowed:
        await ws.close(code=1008)
        return
    await ws.accept()
    if side == "extension":
        await relay.serve_extension(ws)
    else:
        await relay.serve_playwright(ws)
