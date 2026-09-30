"""Persistent browser profiles: one folder per project, one session at a time.

Chrome refuses to open a profile that another process holds, with an unclear
error. Taking our own OS-level lock first gives a clear one, and the lock is
released automatically if the process dies.
"""

from __future__ import annotations

import os
from pathlib import Path

from app.config import profiles_dir

LOCK_NAME = ".ai-tester.lock"


class ProfileInUseError(RuntimeError):
    pass


def profile_dir_for(project_id: str) -> Path:
    return profiles_dir() / project_id / "chrome-profile"


class ProfileLock:
    def __init__(self, profile_dir: Path):
        self.path = profile_dir / LOCK_NAME
        self._fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            _lock(fd)
        except OSError:
            os.close(fd)
            raise ProfileInUseError(
                f"Browser profile {self.path.parent} is in use by another test session. "
                "Stop that session first; one profile can run one session at a time."
            ) from None
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            _unlock(self._fd)
        finally:
            os.close(self._fd)
            self._fd = None


if os.name == "nt":
    import msvcrt

    def _lock(fd: int) -> None:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
