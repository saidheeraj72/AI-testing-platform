"""FastAPI application: the local API around the test engine."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import projects, relay, sessions, system, websocket
from app.api.deps import AppContext
from app.api.security import TOKEN_HEADER, LocalOnlyMiddleware
from app.config import ModelSettings, Settings, database_path, sessions_dir
from app.db.database import Database
from app.model.provider import ModelProvider, create_provider
from app.run import run_session
from app.services.events import EventHub
from app.services.session_manager import SessionManager


def create_app(
    settings: Settings,
    *,
    token: str,
    db_path: Path | None = None,
    session_root: Path | None = None,
    runner: Callable[..., Any] = run_session,
    provider_factory: Callable[[ModelSettings], ModelProvider] = create_provider,
    extra_hosts: frozenset[str] = frozenset(),
    login_headless: bool = False,
) -> FastAPI:
    db = Database(db_path or database_path())
    hub = EventHub()
    root = session_root or sessions_dir()
    root.mkdir(parents=True, exist_ok=True)
    manager = SessionManager(db=db, settings=settings, hub=hub, sessions_dir=root,
                             max_concurrent=settings.server.max_concurrent_sessions,
                             runner=runner, provider_factory=provider_factory, login_headless=login_headless,
                             relay_base=f"ws://{settings.server.host}:{settings.server.port}", relay_token=token)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await db.migrate()
        await manager.recover()
        yield
        await manager.shutdown()
        await db.close()

    app = FastAPI(title="AI Tester", version="0.1.0", lifespan=lifespan)
    app.state.ctx = AppContext(settings=settings, db=db, hub=hub, manager=manager, sessions_dir=root,
                               token=token, extra_hosts=extra_hosts)
    # Order matters: CORS must wrap the local-only check so preflight responses get CORS headers.
    app.add_middleware(LocalOnlyMiddleware, token=token, allowed_origins=settings.server.allowed_origins,
                       extra_hosts=extra_hosts, allow_extension=settings.server.allow_extension)
    app.add_middleware(CORSMiddleware, allow_origins=settings.server.allowed_origins,
                       allow_origin_regex=r"chrome-extension://[a-p]{32}" if settings.server.allow_extension else None,
                       allow_methods=["GET", "POST", "PATCH"], allow_headers=["Content-Type", TOKEN_HEADER])
    for router in (system.router, projects.router, sessions.router, websocket.router, relay.router):
        app.include_router(router)
    return app
