from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import AppContext, ctx, get_project_or_404
from app.db.repositories import projects as project_repo
from app.schemas.api import ProjectCreate, ProjectOut

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
