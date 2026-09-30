"""Writes one session's artifacts to data/sessions/{session_id}/."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.config import sessions_dir
from app.storage.paths import SessionPaths, new_session_id


class SessionStorage:
    def __init__(self, session_id: str, root: Path):
        self.session_id = session_id
        self.paths = SessionPaths(root)

    @classmethod
    def create(cls, base_dir: Path | None = None, session_id: str | None = None) -> SessionStorage:
        session_id = session_id or new_session_id()
        root = (base_dir or sessions_dir()) / session_id
        root.mkdir(parents=True, exist_ok=False)
        storage = cls(session_id, root)
        for d in (storage.paths.screenshots, storage.paths.observations,
                  storage.paths.network_events.parent, storage.paths.console_events.parent,
                  storage.paths.browser_events.parent):
            d.mkdir(parents=True, exist_ok=True)
        storage.update_manifest(session_id=session_id, created_at=_now())
        return storage

    @property
    def root(self) -> Path:
        return self.paths.root

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def write_json(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(_plain(data), indent=2, ensure_ascii=False))
        tmp.replace(path)

    def append_jsonl(self, path: Path, record: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(_plain(record), ensure_ascii=False) + "\n")

    def read_manifest(self) -> dict[str, Any]:
        if not self.paths.manifest.exists():
            return {}
        return json.loads(self.paths.manifest.read_text())

    def update_manifest(self, **fields: Any) -> dict[str, Any]:
        manifest = self.read_manifest() | fields
        self.write_json(self.paths.manifest, manifest)
        return manifest


def _plain(data: Any) -> Any:
    if isinstance(data, BaseModel):
        return data.model_dump(mode="json")
    return data


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
