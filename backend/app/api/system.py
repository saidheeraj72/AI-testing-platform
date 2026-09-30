from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import AppContext, ctx

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/ping")
async def ping() -> dict:
    """Unauthenticated liveness check for the launcher."""
    return {"ok": True}


@router.get("/info")
async def info(c: Annotated[AppContext, Depends(ctx)]) -> dict:
    s = c.settings
    return {
        "model": {"provider": s.model.executor.provider, "planner": s.model.planner.name,
                  "executor": s.model.executor.name, "analyzer": s.model.analyzer.name},
        "browser": s.browser.model_dump(),
        "safety": s.safety.model_dump(),
        "running_sessions": list(c.manager.running),
        "max_concurrent_sessions": c.manager.max_concurrent,
    }
