"""The Chrome extension end to end: a real API server, the unpacked extension in Chromium, a user tab.

The extension relays CDP from the tab to the API, and the agent tests the tab the user already has open.
"""

import json
import socket
import threading
import time
import urllib.request
from pathlib import Path

import pytest
import uvicorn
from playwright.async_api import async_playwright

from app.config import load_settings
from app.main import create_app
from app.run import run_session
from test_agent_loop import ScriptedProvider, act, plan

pytestmark = pytest.mark.browser
EXTENSION = Path(__file__).resolve().parents[2] / "extension"
TOKEN = "extension-test-token"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def api_server(tmp_path):
    port = free_port()
    settings = load_settings()
    settings.server.port = port
    script = [
        plan(("Call the API", [{"type": "request_succeeded", "value": "/api/fail", "method": "POST"}])),
        act("click", 'button "Call failing API"'),
        {"is_bug": True, "title": "Calling the API fails with HTTP 500", "severity": "high",
         "category": "functional", "summary": "s", "expected": "e", "actual": "a"},
    ]
    app = create_app(settings, token=TOKEN, db_path=tmp_path / "app.db", session_root=tmp_path / "sessions",
                     runner=run_session, provider_factory=lambda cfg: ScriptedProvider(cfg, script))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


def get(api: str, path: str):
    req = urllib.request.Request(f"{api}{path}", headers={"X-AI-Tester-Token": TOKEN})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


async def test_extension_tests_the_users_tab(api_server, site, tmp_path):
    async with async_playwright() as p:
        try:
            context = await p.chromium.launch_persistent_context(
                str(tmp_path / "user-profile"), channel="chromium", headless=True,
                args=[f"--disable-extensions-except={EXTENSION}", f"--load-extension={EXTENSION}"],
            )
        except Exception as e:  # bundled Chromium not installed
            pytest.skip(f"Chromium with extensions unavailable: {e}")
        worker = context.service_workers[0] if context.service_workers else await context.wait_for_event("serviceworker")
        tab = await context.new_page()
        await tab.goto(site)

        started = await worker.evaluate(
            """async ([api, token, site]) => {
                await chrome.storage.local.set({ apiUrl: api, token });
                const [tab] = await chrome.tabs.query({ url: site + "/*" });
                return await globalThis.aiTester.startTest({ objective: "Call the API", tabId: tab.id });
            }""",
            [api_server, TOKEN, site],
        )
        session_id = started["sessionId"]

        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            s = get(api_server, f"/api/sessions/{session_id}")
            if s["status"] not in ("CREATED", "RUNNING", "PAUSED", "WAITING_FOR_USER"):
                break
            time.sleep(0.3)

        assert (s["status"], s["outcome"], s["browser"]) == ("COMPLETED", "BUGS_FOUND", "tab"), s
        assert s["bugs"][0]["title"] == "Calling the API fails with HTTP 500"
        # The agent worked in the user's own tab, and the tab is still open afterwards.
        assert not tab.is_closed()
        assert "Status 500" in await tab.locator("#out").inner_text()
        assert (await worker.evaluate("globalThis.aiTester.status()"))["running"] is False
        await context.close()
