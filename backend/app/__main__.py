"""Start the local API server: python -m app

The token printed at startup (also written to data/.api-token, readable only
by this user) must be sent as X-AI-Tester-Token on every request.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys

import uvicorn

from app.api.security import new_token, write_token_file
from app.config import ConfigError, data_dir, load_settings
from app.main import create_app


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET) as s:
        return s.connect_ex((host, port)) == 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app", description=__doc__)
    parser.add_argument("--port", type=int, help="override [server] port")
    args = parser.parse_args()
    try:
        settings = load_settings()
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.port:
        settings.server.port = args.port

    host, port = settings.server.host, settings.server.port
    if port_in_use(host, port):
        # Checked before writing the token file, so a running server's token is not overwritten.
        print(f"error: {host}:{port} is in use. Is AI Tester already running?", file=sys.stderr)
        return 1

    token = os.environ.get("AI_TESTER_TOKEN") or new_token()
    token_file = data_dir() / ".api-token"
    write_token_file(token_file, token)
    print(f"AI Tester API on http://{host}:{port}\nToken written to {token_file}")

    uvicorn.run(create_app(settings, token=token), host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
