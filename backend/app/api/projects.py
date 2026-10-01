from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import AppContext, ctx, get_project_or_404
from app.db.repositories import projects as project_repo
from app.schemas.api import ProjectCreate, ProjectOut, ProjectUpdate
from app.services.session_manager import SessionError

router = APIRouter(prefix="/api/projects", tags=["projects"])
Ctx = Annotated[AppContext, Depends(ctx)]


@router.post("", status_code=201)
async def create_project(body: ProjectCreate, c: Ctx) -> ProjectOut:
    async with c.db.session() as db:
        project = await project_repo.create(db, **body.model_dump())
    return ProjectOut.model_validate(project)


@router.get("")
async def list_projects(c: Ctx) -> list[ProjectOut]:
    async with c.db.session() as db:
        return [ProjectOut.model_validate(p) for p in await project_repo.list_all(db)]


@router.get("/{project_id}")
async def get_project(project_id: str, c: Ctx) -> ProjectOut:
    return ProjectOut.model_validate(await get_project_or_404(c, project_id))


@router.patch("/{project_id}")
async def update_project(project_id: str, body: ProjectUpdate, c: Ctx) -> ProjectOut:
    async with c.db.session() as db:
        project = await project_repo.get(db, project_id)
        if project is None:
            raise HTTPException(404, "project not found")
        project = await project_repo.update(db, project, **body.model_dump(exclude_none=True))
    return ProjectOut.model_validate(project)


@router.get("/{project_id}/login")
async def login_status(project_id: str, c: Ctx) -> dict:
    await get_project_or_404(c, project_id)
    return {"open": project_id in c.manager.login_setups}


@router.post("/{project_id}/login")
async def open_login(project_id: str, c: Ctx) -> dict:
    """Opens Chrome with the project's profile at its URL. Log in there, then call /login/finish."""
    project = await get_project_or_404(c, project_id)
    try:
        await c.manager.open_login(project)
    except SessionError as e:
        raise HTTPException(409, str(e)) from None
    return {"open": True}


@router.post("/{project_id}/login/finish")
async def finish_login(project_id: str, c: Ctx) -> dict:
    """Closes the login browser; the profile keeps the session for later tests."""
    try:
        await c.manager.finish_login(project_id)
    except SessionError as e:
        raise HTTPException(409, str(e)) from None
    return {"open": False}
