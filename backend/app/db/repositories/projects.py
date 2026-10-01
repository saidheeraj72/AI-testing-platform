from __future__ import annotations

import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Project


def new_project_id() -> str:
    return f"p_{secrets.token_hex(4)}"


async def create(db: AsyncSession, *, name: str, target_url: str, allowed_domains: list[str],
                 persistent_profile: bool) -> Project:
    project = Project(id=new_project_id(), name=name, target_url=target_url,
                      allowed_domains=allowed_domains, persistent_profile=persistent_profile)
    db.add(project)
    await db.commit()
    return project


async def get(db: AsyncSession, project_id: str) -> Project | None:
    return await db.get(Project, project_id)


async def list_all(db: AsyncSession) -> list[Project]:
    return list((await db.scalars(select(Project).order_by(Project.created_at.desc()))).all())


async def update(db: AsyncSession, project: Project, **fields) -> Project:
    for key, value in fields.items():
        setattr(project, key, value)
    await db.commit()
    return project
