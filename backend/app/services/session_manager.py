"""Owns running sessions: start, pause, resume, stop, risky-action confirmation, persistence.

One session per project at a time (a browser profile can only be open once),
and at most `max_concurrent` sessions overall.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.agent.control import SessionControl
from app.config import ModelSettings, Settings
from app.db.database import Database
from app.db.models import Project
from app.db.repositories import sessions as session_repo
from app.model.provider import ModelProvider, create_provider
from app.run import run_session
from app.services.events import EventHub
from app.storage.manager import SessionStorage
from app.storage.paths import new_session_id

log = logging.getLogger(__name__)


class SessionError(Exception):
    """A request that cannot be done in the session's current state (HTTP 409)."""


@dataclass
class Confirmation:
    id: str
    action: str
    future: asyncio.Future


@dataclass
class Running:
    session_id: str
    project_id: str
    control: SessionControl
    task: asyncio.Task | None = None
    status: str = "RUNNING"
    confirmation: Confirmation | None = None
    live: dict[str, Any] = field(default_factory=lambda: {
        "steps_planned": 0, "steps_completed": 0, "current_step": None, "actions": 0, "bugs_found": 0,
    })


class SessionManager:
    def __init__(
        self,
        *,
        db: Database,
        settings: Settings,
        hub: EventHub,
        sessions_dir: Path,
        max_concurrent: int = 1,
        runner: Callable[..., Any] = run_session,
        provider_factory: Callable[[ModelSettings], ModelProvider] = create_provider,
    ):
        self.db = db
        self.settings = settings
        self.hub = hub
        self.sessions_dir = sessions_dir
        self.max_concurrent = max_concurrent
        self.runner = runner
        self.provider_factory = provider_factory
        self.running: dict[str, Running] = {}

    # ------------------------------------------------------------------ lifecycle

    async def create(self, project: Project, objective: str) -> str:
        session_id = new_session_id()
        SessionStorage.create(self.sessions_dir, session_id)
        async with self.db.session() as db:
            await session_repo.create(db, session_id=session_id, project_id=project.id, objective=objective,
                                      model=self.settings.model.executor.name)
        return session_id

    async def start(self, session_id: str, project: Project) -> None:
        async with self.db.session() as db:
            row = await session_repo.get(db, session_id)
        if row is None:
            raise KeyError(session_id)
        if row.status != "CREATED":
            raise SessionError(f"session is {row.status}; only a new session can be started")
        if any(r.project_id == project.id for r in self.running.values()):
            raise SessionError("this project already has a running session (one browser profile, one session)")
        if len(self.running) >= self.max_concurrent:
            raise SessionError(f"{self.max_concurrent} session(s) already running")

        running = Running(session_id=session_id, project_id=project.id, control=SessionControl())
        self.running[session_id] = running
        async with self.db.session() as db:
            await session_repo.set_status(db, session_id, "RUNNING", started_at=datetime.now(timezone.utc))
        running.task = asyncio.create_task(self._run(running, project, row.objective), name=f"session {session_id}")

    async def pause(self, session_id: str) -> None:
        running = self._get(session_id)
        if running.status != "RUNNING":
            raise SessionError(f"session is {running.status}")
        running.control.pause()
        await self._set_status(running, "PAUSED")
        self.hub.publish(session_id, {"type": "paused"})

    async def resume(self, session_id: str) -> None:
        running = self._get(session_id)
        if running.status != "PAUSED":
            raise SessionError(f"session is {running.status}")
        running.control.resume()
        await self._set_status(running, "RUNNING")
        self.hub.publish(session_id, {"type": "resumed"})

    async def stop(self, session_id: str) -> None:
        running = self._get(session_id)
        if running.confirmation and not running.confirmation.future.done():
            running.confirmation.future.set_result(False)
        running.control.resume()  # a paused agent must reach a point where it can be cancelled
        if running.task:
            running.task.cancel()

    async def confirm(self, session_id: str, confirmation_id: str, allow: bool) -> None:
        running = self._get(session_id)
        pending = running.confirmation
        if pending is None or pending.id != confirmation_id or pending.future.done():
            raise SessionError("no such pending confirmation")
        pending.future.set_result(allow)

    async def recover(self) -> int:
        async with self.db.session() as db:
            return await session_repo.mark_interrupted(db)

    async def shutdown(self) -> None:
        tasks = [r.task for r in self.running.values() if r.task]
        for session_id in list(self.running):
            await self.stop(session_id)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def live(self, session_id: str) -> dict[str, Any] | None:
        running = self.running.get(session_id)
        if running is None:
            return None
        pending = running.confirmation
        return {
            "status": running.status,
            **running.live,
            "pending_confirmation": {"id": pending.id, "action": pending.action} if pending else None,
        }

    # ------------------------------------------------------------------ internals

    async def _run(self, running: Running, project: Project, objective: str) -> None:
        session_id = running.session_id
        storage = SessionStorage(session_id, self.sessions_dir / session_id)
        final = "COMPLETED"
        try:
            await self.runner(
                url=project.target_url,
                objective=objective,
                settings=self.settings,
                project=project.id if project.persistent_profile else None,
                allow_domains=list(project.allowed_domains or []),
                on_event=lambda e: self._on_event(running, e),
                confirm=lambda action: self._ask(running, action),
                storage=storage,
                control=running.control,
                provider_factory=self.provider_factory,
            )
        except asyncio.CancelledError:
            final = "CANCELLED"
        except Exception as e:  # keep the server alive; the session records what happened
            log.exception("session %s failed", session_id)
            final = "FAILED"
            self.hub.publish(session_id, {"type": "session_failed", "error": str(e)})
        finally:
            self.running.pop(session_id, None)
            try:
                async with self.db.session() as db:
                    await session_repo.save_results(db, session_id, storage, final)
            except Exception:
                log.exception("could not save results of %s", session_id)
            self.hub.publish(session_id, {"type": "session_saved", "status": final})
            self.hub.close(session_id)

    def _on_event(self, running: Running, event: dict[str, Any]) -> None:
        live = running.live
        kind = event.get("type")
        if kind in ("plan_created", "replanned"):
            live["steps_planned"] = live["steps_completed"] + len(event["steps"])
        elif kind == "step_started":
            live["current_step"] = {"sequence": event["step"], "goal": event["goal"]}
        elif kind == "step_finished" and event.get("status") == "PASSED":
            live["steps_completed"] += 1
        elif kind == "action_completed":
            live["actions"] += 1
        elif kind == "bug_confirmed":
            live["bugs_found"] += 1
        self.hub.publish(running.session_id, event)

    async def _ask(self, running: Running, action: str) -> bool:
        """Risky action: wait for the user. The time spent waiting does not count against the budget."""
        confirmation = Confirmation(id=secrets.token_hex(4), action=action,
                                    future=asyncio.get_running_loop().create_future())
        running.confirmation = confirmation
        running.control.pause()
        await self._set_status(running, "WAITING_FOR_USER")
        self.hub.publish(running.session_id, {"type": "confirmation_required",
                                              "confirmation_id": confirmation.id, "action": action})
        try:
            allowed = await confirmation.future
        finally:
            running.confirmation = None
            running.control.resume()
        self.hub.publish(running.session_id, {"type": "confirmation_answered", "allowed": allowed})
        await self._set_status(running, "RUNNING")
        return allowed

    async def _set_status(self, running: Running, status: str) -> None:
        running.status = status
        async with self.db.session() as db:
            await session_repo.set_status(db, running.session_id, status)

    def _get(self, session_id: str) -> Running:
        running = self.running.get(session_id)
        if running is None:
            raise SessionError("session is not running")
        return running
