"""Process-wide settings, read from the environment."""

from __future__ import annotations

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """Root for SQLite, session folders and browser profiles.

    Defaults to <repo>/data when running from source. A packaged build sets
    AI_TESTER_DATA_DIR to a per-user application directory.
    """
    return Path(os.environ.get("AI_TESTER_DATA_DIR", _REPO_ROOT / "data")).resolve()


def sessions_dir() -> Path:
    return data_dir() / "sessions"


def profiles_dir() -> Path:
    return data_dir() / "browser_profiles"
