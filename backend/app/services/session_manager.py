"""Owns running sessions: start, pause, resume, stop, risky-action confirmation, persistence.

One session per project at a time (a browser profile can only be open once),
and at most `max_concurrent` sessions overall.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.agent.control import SessionControl
from app.agent.explorer import DEFAULT_OBJECTIVE
from app.browser.profile import profile_dir_for
from app.browser.session import BrowserConfig, BrowserSession
from app.config import ModelSettings, Settings
from app.db.database import Database
from app.db.models import Project
from app.db.repositories import sessions as session_repo
from app.model.provider import ModelProvider, create_provider
from app.run import run_session
from app.safety.domain_scope import DomainScope
from app.services.events import EventHub
from app.storage.manager import SessionStorage
from app.storage.paths import new_session_id

log = logging.getLogger(__name__)


class SessionError(Exception):
    """A request that cannot be done in the session's current state (HTTP 409)."""


@dataclass
class Confirmation:
    """Something the user must answer: a risky action (allow?) or a hand-over (continue?)."""

    id: str
    kind: str  # risky_action | login_required | mfa | captcha | agent_request
    message: str
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
        login_headless: bool = False,
    ):
        self.db = db
        self.settings = settings
        self.hub = hub
        self.sessions_dir = sessions_dir
        self.max_concurrent = max_concurrent
        self.runner = runner
        self.provider_factory = provider_factory
        self.login_headless = login_headless  # tests only: a real login needs a visible browser
        self.running: dict[str, Running] = {}
        self.login_setups: dict[str, asyncio.Task] = {}  # project id -> open login browser
        self._login_done: dict[str, asyncio.Event] = {}

    # ------------------------------------------------------------------ lifecycle

    async def create(self, project: Project, objective: str, mode: str = "objective") -> str:
        session_id = new_session_id()
        SessionStorage.create(self.sessions_dir, session_id)
        if mode == "explore" and not objective.strip():
            objective = DEFAULT_OBJECTIVE
        async with self.db.session() as db:
            await session_repo.create(db, session_id=session_id, project_id=project.id, objective=objective,
                                      model=self.settings.model.executor.name, mode=mode)
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
        if project.id in self.login_setups:
            raise SessionError("the login browser for this project is still open; click Done first")
        if len(self.running) >= self.max_concurrent:
            raise SessionError(f"{self.max_concurrent} session(s) already running")

        running = Running(session_id=session_id, project_id=project.id, control=SessionControl())
        self.running[session_id] = running
        async with self.db.session() as db:
            await session_repo.set_status(db, session_id, "RUNNING", started_at=datetime.now(timezone.utc))
        running.task = asyncio.create_task(self._run(running, project, row.objective, row.mode),
                                           name=f"session {session_id}")

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
        for project_id in list(self.login_setups):
            await self.finish_login(project_id)
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
            "pending_confirmation": (
                {"id": pending.id, "kind": pending.kind, "action": pending.message} if pending else None
            ),
        }

    # ------------------------------------------------------------------ login setup

    async def open_login(self, project: Project) -> None:
        """Open a visible browser with the project's profile so the user can log in once."""
        if not project.persistent_profile:
            raise SessionError("this project does not keep logins between tests")
        if project.id in self.login_setups:
            raise SessionError("the login browser is already open")
        if any(r.project_id == project.id for r in self.running.values()):
            raise SessionError("a test is running for this project")
        done = asyncio.Event()
        self._login_done[project.id] = done
        self.login_setups[project.id] = asyncio.create_task(self._login(project, done))

    async def finish_login(self, project_id: str) -> None:
        task = self.login_setups.get(project_id)
        if task is None:
            raise SessionError("no login browser is open for this project")
        self._login_done[project_id].set()
        await asyncio.gather(task, return_exceptions=True)

    async def _login(self, project: Project, done: asyncio.Event) -> None:
        storage = SessionStorage.create(self.sessions_dir.parent / "login-setup")
        browser = BrowserSession(
            DomainScope.from_target(project.target_url, list(project.allowed_domains or [])),
            storage,
            BrowserConfig(headless=self.login_headless,
                          channel="chrome" if self.settings.browser.channel == "chrome" else None,
                          profile_dir=profile_dir_for(project.id), trace=False),
        )
        try:
            await browser.start()
            await browser.navigate(project.target_url)
            while not done.is_set() and not browser.closed:
                try:
                    await asyncio.wait_for(done.wait(), timeout=1)
                except TimeoutError:
                    pass
        finally:
            await browser.stop()
            shutil.rmtree(storage.root, ignore_errors=True)  # nothing to keep; the profile holds the login
            self.login_setups.pop(project.id, None)
            self._login_done.pop(project.id, None)

    # ------------------------------------------------------------------ internals

    async def _run(self, running: Running, project: Project, objective: str, mode: str) -> None:
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
                confirm=lambda action: self._ask(running, "risky_action", action),
                ask_user=lambda kind, message: self._ask(running, kind, message),
                storage=storage,
                control=running.control,
                provider_factory=self.provider_factory,
                mode=mode,
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
        elif kind == "page_explored":
            live["pages_explored"] = event["count"]
        self.hub.publish(running.session_id, event)

    async def _ask(self, running: Running, kind: str, message: str) -> bool:
        """Wait for the user (risky action or hand-over). Waiting time does not count against the budget."""
        confirmation = Confirmation(id=secrets.token_hex(4), kind=kind, message=message,
                                    future=asyncio.get_running_loop().create_future())
        running.confirmation = confirmation
        running.control.pause()
        await self._set_status(running, "WAITING_FOR_USER")
        self.hub.publish(running.session_id, {"type": "confirmation_required", "confirmation_id": confirmation.id,
                                              "kind": kind, "action": message})
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
