from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, Request

from app.config import Settings
from app.db.database import Database
from app.db.models import Project
from app.db.repositories import projects as project_repo
from app.services.events import EventHub
from app.services.session_manager import SessionManager


@dataclass
class AppContext:
    settings: Settings
    db: Database
    hub: EventHub
    manager: SessionManager
    sessions_dir: Path
    token: str
    extra_hosts: frozenset[str]


def ctx(request: Request) -> AppContext:
    return request.app.state.ctx


async def get_project_or_404(context: AppContext, project_id: str) -> Project:
    async with context.db.session() as db:
        project = await project_repo.get(db, project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    return project
