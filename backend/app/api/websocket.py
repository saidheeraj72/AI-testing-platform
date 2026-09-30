from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.security import websocket_allowed
from app.services.events import END

router = APIRouter()


@router.websocket("/api/sessions/{session_id}/events")
async def session_events(ws: WebSocket, session_id: str) -> None:
    """Replays the session's events so far, then streams new ones until the session ends."""
    c = ws.app.state.ctx
    if not websocket_allowed(ws, c.token, c.settings.server.allowed_origins, c.extra_hosts):
        await ws.close(code=1008)
        return
    await ws.accept()
    queue = c.hub.subscribe(session_id)
    try:
        while True:
            event = await queue.get()
            await ws.send_json(event)
            if event is END:
                break
    except WebSocketDisconnect:
        pass
    finally:
        c.hub.unsubscribe(session_id, queue)
    try:
        await ws.close()
    except RuntimeError:
        pass
