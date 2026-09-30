"""Start the API server and the UI together, sharing a fresh token.

    uv run python scripts/dev.py

Then open http://127.0.0.1:5173. Ctrl+C stops both.
"""

from __future__ import annotations

import os
import secrets
import signal
import subprocess
import sys
from pathlib import Path

from app.__main__ import port_in_use
from app.config import ConfigError, load_settings

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
UI_PORT = 5173


def _stop(signum, frame):
    raise KeyboardInterrupt


def main() -> int:
    # Handle Ctrl+C and termination even if started with SIGINT ignored (e.g. from a script with &).
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    try:
        settings = load_settings()
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    token = secrets.token_urlsafe(32)
    api_url = f"http://{settings.server.host}:{settings.server.port}"
    for port in (settings.server.port, UI_PORT):
        if port_in_use(settings.server.host, port):
            print(f"error: port {port} is in use. Is AI Tester already running?", file=sys.stderr)
            return 1

    if not (FRONTEND / "node_modules").exists():
        print("Installing UI dependencies…")
        subprocess.run(["npm", "install"], cwd=FRONTEND, check=True)

    procs = [
        subprocess.Popen([sys.executable, "-m", "app"], cwd=ROOT, env={**os.environ, "AI_TESTER_TOKEN": token}),
        subprocess.Popen(["npm", "run", "dev"], cwd=FRONTEND,
                         env={**os.environ, "VITE_API_TOKEN": token, "VITE_API_URL": api_url}),
    ]
    print(f"\n  AI Tester UI:  http://127.0.0.1:5173\n  API:           {api_url}\n  Ctrl+C to stop\n")
    try:
        while all(p.poll() is None for p in procs):
            try:
                procs[0].wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
    except KeyboardInterrupt:
        pass
    finally:
        # `uv run` forwards Ctrl+C too; a second interrupt must not abort the cleanup.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
