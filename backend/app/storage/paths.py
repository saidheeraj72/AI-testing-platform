"""Session ids and on-disk layout of a session folder."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path


def new_session_id() -> str:
    """Sortable and short enough to embed in generated test data: s_20260930T173012_a1b2."""
    return f"s_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}_{secrets.token_hex(2)}"


class SessionPaths:
    def __init__(self, root: Path):
        self.root = root
        self.manifest = root / "manifest.json"
        self.report = root / "report.json"
        self.trace = root / "trace.zip"
        self.actions = root / "actions.jsonl"
        self.model_calls = root / "model_calls.jsonl"
        self.screenshots = root / "screenshots"
        self.observations = root / "observations"
        self.vision = root / "vision"  # screenshots shown to the model, with refs drawn on them
        self.network_events = root / "network" / "events.jsonl"
        self.console_events = root / "console" / "events.jsonl"
        self.browser_events = root / "browser" / "events.jsonl"

    def screenshot(self, sequence: int) -> Path:
        return self.screenshots / f"{sequence:06d}.png"

    def vision_image(self, sequence: int) -> Path:
        return self.vision / f"{sequence:06d}.jpg"

    def observation(self, sequence: int) -> Path:
        return self.observations / f"{sequence:06d}.json"
