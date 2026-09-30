"""Async SQLite engine and migrations. Migrations run at startup, so a packaged app upgrades itself."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

MIGRATIONS = Path(__file__).parent / "migrations"


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        event.listen(self.engine.sync_engine, "connect", _sqlite_pragmas)
        self._sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def migrate(self) -> None:
        await asyncio.to_thread(command.upgrade, alembic_config(f"sqlite:///{self.path}"), "head")

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._sessions() as session:
            yield session

    async def close(self) -> None:
        await self.engine.dispose()


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _sqlite_pragmas(dbapi_connection, _record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()
