"""HTTP API: security, projects, session lifecycle, confirmation, WebSocket, persistence."""

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import load_settings
from app.main import create_app
from app.run import run_session
from app.storage.manager import SessionStorage

TOKEN = "test-token"
AUTH = {"X-AI-Tester-Token": TOKEN}
ORIGIN = "http://localhost:5173"


async def fake_runner(*, url, objective, settings, project, allow_domains, on_event, confirm, storage: SessionStorage,
                      control, provider_factory, ask_user, mode="objective"):
    """Stands in for run_session: emits events, honours pause/confirm/cancel, writes a report."""
    on_event({"type": "session_started", "session_id": storage.session_id, "url": url, "objective": objective})
    on_event({"type": "plan_created", "steps": [{"sequence": 1}, {"sequence": 2}]})
    on_event({"type": "step_started", "step": 1, "goal": "Do it", "criteria": []})
    await control.checkpoint()
    allowed = None
    if "risky" in objective:
        allowed = await confirm('clicking button "Delete everything"')
    if "login wall" in objective:
        allowed = await ask_user("login_required", "Log in in the Chrome window, then click Continue.")
    try:
        if "slow" in objective:
            for _ in range(200):
                await control.checkpoint()
                await asyncio.sleep(0.05)
        on_event({"type": "action_completed", "step": 1, "sequence": 1, "action": "click", "ok": True,
                  "target": None, "error": None, "message": "", "url": url})
        on_event({"type": "step_finished", "step": 1, "status": "PASSED", "reason": ""})
        outcome = "BLOCKED" if allowed is False else "BUGS_FOUND"
    except asyncio.CancelledError:
        outcome = "CANCELLED"
        _write(storage, url, objective, outcome, bugs=False)
        raise
    _write(storage, url, objective, outcome, bugs=outcome == "BUGS_FOUND")
    return storage, {}


def _write(storage, url, objective, outcome, bugs):
    storage.append_jsonl(storage.paths.actions, {
        "sequence": 1, "action": "click", "arguments": {"ref": "e3"}, "ok": True, "error": None, "message": "",
        "target": {"role": "button", "name": "Save"}, "url_before": url, "url_after": url, "duration_ms": 5,
        "screenshot": None,
    })
    (storage.paths.screenshots / "000001.png").write_bytes(b"png")
    bug = {
        "id": "BUG-001", "title": "Saving fails with HTTP 500", "severity": "high", "category": "functional",
        "summary": "s", "expected": "e", "actual": "a", "url": url, "signature": "http POST /api/x 500",
        "described_by": "rules", "steps_to_reproduce": ["Do it"],
        "occurrences": [{"step": 1, "action_seq": 1, "url": url, "screenshot": "screenshots/000001.png"}],
        "evidence": {"network": [{"method": "POST", "url": f"{url}/api/x", "status": 500}], "console": ["boom"],
                     "screenshots": ["screenshots/000001.png"], "trace": None},
    }
    storage.write_json(storage.paths.report, {
        "outcome": outcome, "reason": "", "duration_ms": 1234,
        "summary": {"steps_planned": 2, "steps_passed": 1, "steps_failed": 1, "pages_visited": 1,
                    "bugs": 1 if bugs else 0},
        "steps": [
            {"sequence": 1, "goal": "Do it", "status": "PASSED", "reason": "ok", "criteria": [], "checks": []},
            {"sequence": 2, "goal": "Check it", "status": "FAILED", "reason": "500", "criteria": [], "checks": []},
        ],
        "bugs": [bug] if bugs else [],
        "could_not_verify": [], "not_tested": [{"step": 3, "goal": "x", "status": "SKIPPED", "reason": "r"}],
        "trace": None,
    })
    storage.update_manifest(finished_at="2026-10-01T00:00:00+00:00")


@pytest.fixture
def make_client(tmp_path):
    clients = []

    def make(runner=fake_runner, **kwargs):
        app = create_app(load_settings(), token=TOKEN, db_path=tmp_path / "app.db", session_root=tmp_path / "sessions",
                         runner=runner, extra_hosts=frozenset({"testserver"}), login_headless=True, **kwargs)
        client = TestClient(app, headers=AUTH)
        client.__enter__()
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client):
    return make_client()


def project(client, url="http://localhost:3000") -> str:
    r = client.post("/api/projects", json={"name": "CRM", "target_url": url})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def wait_until(client, session_id, done=lambda s: s["status"] not in ("RUNNING", "PAUSED", "WAITING_FOR_USER"),
               timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s = client.get(f"/api/sessions/{session_id}").json()
        if done(s):
            return s
        time.sleep(0.05)
    raise AssertionError(f"timed out; last state {s}")


# ---------------------------------------------------------------- security

def test_token_is_required(client):
    assert client.get("/api/projects", headers={"X-AI-Tester-Token": ""}).status_code == 401
    assert client.get("/api/projects", headers={"X-AI-Tester-Token": "wrong"}).status_code == 401
    assert client.get("/api/system/ping", headers={"X-AI-Tester-Token": ""}).status_code == 200
    assert client.get("/api/projects").status_code == 200


def test_dns_rebinding_host_is_rejected(client):
    assert client.get("/api/projects", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/api/projects", headers={"Host": "127.0.0.1:8765"}).status_code == 200


def test_extension_origin_is_accepted_but_still_needs_the_token(client):
    ext = {"Origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop"}
    assert client.get("/api/projects", headers=ext).status_code == 200
    assert client.get("/api/projects", headers={**ext, "X-AI-Tester-Token": "wrong"}).status_code == 401
    assert client.get("/api/projects", headers={"Origin": "chrome-extension://not-an-id"}).status_code == 403


def test_tab_session_creates_a_browser_project(client):
    r = client.post("/api/tab-sessions", json={"url": "https://app.example.com/orders?x=1", "objective": "Check it"})
    assert r.status_code == 201
    data = r.json()
    assert data["relay_path"] == f"/api/relay/{data['session_id']}/extension"
    project = client.get(f"/api/projects/{data['project_id']}").json()
    assert (project["target_url"], project["persistent_profile"]) == ("https://app.example.com", False)
    assert client.get(f"/api/sessions/{data['session_id']}").json()["browser"] == "tab"
    client.post(f"/api/sessions/{data['session_id']}/stop")
    wait_until(client, data["session_id"])


def test_foreign_origin_is_rejected(client):
    assert client.get("/api/projects", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/projects", headers={"Origin": ORIGIN}).status_code == 200


@pytest.mark.parametrize("method", ["GET", "POST", "PATCH"])  # every method the UI uses
def test_cors_preflight_for_the_ui(client, method):
    r = client.options("/api/projects/p_x", headers={
        "Origin": ORIGIN, "Access-Control-Request-Method": method,
        "Access-Control-Request-Headers": "x-ai-tester-token,content-type"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == ORIGIN


# ---------------------------------------------------------------- projects

def test_projects(client):
    assert client.post("/api/projects", json={"name": "x", "target_url": "localhost:3000"}).status_code == 422
    pid = project(client)
    assert client.get(f"/api/projects/{pid}").json()["target_url"] == "http://localhost:3000"
    assert [p["id"] for p in client.get("/api/projects").json()] == [pid]
    assert client.get("/api/projects/p_missing").status_code == 404


# ---------------------------------------------------------------- sessions

def test_session_runs_and_results_are_saved(client):
    pid = project(client)
    r = client.post("/api/sessions", json={"project_id": pid, "objective": "Test the thing"})
    assert r.status_code == 201
    sid = r.json()["id"]

    s = wait_until(client, sid)
    assert (s["status"], s["outcome"]) == ("COMPLETED", "BUGS_FOUND")
    assert (s["steps_planned"], s["steps_completed"], s["bugs_found"], s["duration_ms"]) == (2, 1, 1, 1234)
    assert [st["status"] for st in s["steps"]] == ["PASSED", "FAILED"]
    bug = client.get(f"/api/sessions/{sid}/bugs").json()[0]
    assert bug["code"] == "BUG-001" and bug["occurrences"][0]["screenshot_path"] == "screenshots/000001.png"

    coverage = client.get(f"/api/sessions/{sid}/coverage").json()
    assert coverage["steps_completed"] == 1 and coverage["not_tested"][0]["goal"] == "x"
    assert client.get(f"/api/sessions/{sid}/report").json()["outcome"] == "BUGS_FOUND"

    files = {f["path"]: f["type"] for f in client.get(f"/api/sessions/{sid}/evidence").json()}
    assert files["screenshots/000001.png"] == "screenshot"
    assert client.get(f"/api/sessions/{sid}/files/screenshots/000001.png").content == b"png"
    assert client.get(f"/api/sessions/{sid}/files/../../app.db").status_code == 404
    assert client.get(f"/api/sessions/{sid}/files/%2e%2e/%2e%2e/app.db").status_code == 404

    kinds = [e["type"] for e in client.get(f"/api/sessions/{sid}/events").json()]
    assert kinds[:2] == ["session_started", "plan_created"] and kinds[-1] == "session_saved"
    assert [x["id"] for x in client.get("/api/sessions", params={"project_id": pid}).json()] == [sid]


def test_created_session_can_be_started_later_but_only_once(client):
    pid = project(client)
    sid = client.post("/api/sessions", json={"project_id": pid, "objective": "Later", "start": False}).json()["id"]
    assert client.get(f"/api/sessions/{sid}").json()["status"] == "CREATED"
    assert client.post(f"/api/sessions/{sid}/start").status_code == 200
    wait_until(client, sid)
    assert client.post(f"/api/sessions/{sid}/start").status_code == 409


def test_pause_resume_stop(client):
    pid = project(client)
    sid = client.post("/api/sessions", json={"project_id": pid, "objective": "slow run"}).json()["id"]
    assert client.post(f"/api/sessions/{sid}/pause").json()["status"] == "PAUSED"
    assert client.post(f"/api/sessions/{sid}/pause").status_code == 409
    assert client.post(f"/api/sessions/{sid}/resume").json()["status"] == "RUNNING"
    assert client.post(f"/api/sessions/{sid}/stop").status_code == 202

    s = wait_until(client, sid)
    assert (s["status"], s["outcome"]) == ("CANCELLED", "CANCELLED")
    assert client.post(f"/api/sessions/{sid}/resume").status_code == 409


def test_one_running_session_per_project(client):
    pid = project(client)
    first = client.post("/api/sessions", json={"project_id": pid, "objective": "slow run"}).json()["id"]
    r = client.post("/api/sessions", json={"project_id": pid, "objective": "another"})
    assert r.status_code == 409 and "already has a running session" in r.json()["detail"]
    client.post(f"/api/sessions/{first}/stop")
    wait_until(client, first)


@pytest.mark.parametrize("allow, outcome", [(True, "BUGS_FOUND"), (False, "BLOCKED")])
def test_risky_action_waits_for_the_user(client, allow, outcome):
    pid = project(client)
    sid = client.post("/api/sessions", json={"project_id": pid, "objective": "risky run"}).json()["id"]
    s = wait_until(client, sid, done=lambda s: s["status"] == "WAITING_FOR_USER")
    pending = s["live"]["pending_confirmation"]
    assert "Delete everything" in pending["action"] and pending["kind"] == "risky_action"

    assert client.post(f"/api/sessions/{sid}/confirm", json={"confirmation_id": "nope", "allow": True}).status_code == 409
    assert client.post(f"/api/sessions/{sid}/confirm", json={"confirmation_id": pending["id"], "allow": allow}).json()
    assert wait_until(client, sid)["outcome"] == outcome


@pytest.mark.parametrize("cont, outcome", [(True, "BUGS_FOUND"), (False, "BLOCKED")])
def test_hand_over_to_the_user(client, cont, outcome):
    pid = project(client)
    sid = client.post("/api/sessions", json={"project_id": pid, "objective": "login wall run"}).json()["id"]
    s = wait_until(client, sid, done=lambda s: s["status"] == "WAITING_FOR_USER")
    pending = s["live"]["pending_confirmation"]
    assert pending["kind"] == "login_required" and "Log in" in pending["action"]
    client.post(f"/api/sessions/{sid}/confirm", json={"confirmation_id": pending["id"], "allow": cont})
    assert wait_until(client, sid)["outcome"] == outcome


def test_update_project(client):
    pid = project(client)
    r = client.patch(f"/api/projects/{pid}", json={"allowed_domains": ["login.example.com"]})
    assert r.json()["allowed_domains"] == ["login.example.com"]
    assert client.patch("/api/projects/p_none", json={"name": "x"}).status_code == 404


@pytest.mark.browser
def test_login_setup_opens_and_closes_the_profile_browser(client, site):
    pid = project(client, url=site)
    assert client.post(f"/api/projects/{pid}/login").json() == {"open": True}
    assert client.get(f"/api/projects/{pid}/login").json() == {"open": True}
    assert client.post(f"/api/projects/{pid}/login").status_code == 409
    r = client.post("/api/sessions", json={"project_id": pid, "objective": "During login"})
    assert r.status_code == 409 and "login browser" in r.json()["detail"]
    assert client.post(f"/api/projects/{pid}/login/finish").json() == {"open": False}
    assert client.get(f"/api/projects/{pid}/login").json() == {"open": False}
    assert client.post(f"/api/projects/{pid}/login/finish").status_code == 409

    no_profile = client.post("/api/projects", json={"name": "x", "target_url": site, "persistent_profile": False})
    assert client.post(f"/api/projects/{no_profile.json()['id']}/login").status_code == 409


def test_websocket_replays_and_streams_until_the_end(client):
    pid = project(client)
    sid = client.post("/api/sessions", json={"project_id": pid, "objective": "Stream it"}).json()["id"]
    wait_until(client, sid)

    with client.websocket_connect(f"/api/sessions/{sid}/events?token={TOKEN}") as ws:
        events = []
        while (event := ws.receive_json())["type"] != "stream_end":
            events.append(event["type"])
    assert events[0] == "session_started" and "session_saved" in events

    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(f"/api/sessions/{sid}/events?token=wrong") as ws:
            ws.receive_json()
    assert closed.value.code == 1008


def test_sessions_running_at_shutdown_are_marked_interrupted(make_client):
    client = make_client()
    pid = project(client)
    sid = client.post("/api/sessions", json={"project_id": pid, "objective": "Later", "start": False}).json()["id"]

    from app.db.repositories import sessions as repo

    ctx = client.app.state.ctx

    async def mark_running():
        async with ctx.db.session() as db:
            await repo.set_status(db, sid, "RUNNING")

    client.portal.call(mark_running)
    client.__exit__(None, None, None)

    restarted = make_client()
    assert restarted.get(f"/api/sessions/{sid}").json()["status"] == "INTERRUPTED"


# ---------------------------------------------------------------- end to end with a real browser

@pytest.mark.browser
def test_real_session_through_the_api(make_client, site):
    from test_agent_loop import ScriptedProvider, act, plan

    script = [
        plan(("Call the API", [{"type": "request_succeeded", "value": "/api/fail", "method": "POST"}])),
        act("click", 'button "Call failing API"'),
        {"is_bug": True, "title": "Calling the API fails with HTTP 500", "severity": "high",
         "category": "functional", "summary": "The API returns 500.", "expected": "Success", "actual": "HTTP 500"},
    ]
    settings = load_settings()
    settings.browser.headless = True
    client = make_client(runner=run_session, provider_factory=lambda cfg: ScriptedProvider(cfg, script))
    client.app.state.ctx.manager.settings = settings

    r = client.post("/api/projects", json={"name": "Fixture", "target_url": site, "persistent_profile": False})
    sid = client.post("/api/sessions", json={"project_id": r.json()["id"], "objective": "Call the API"}).json()["id"]
    s = wait_until(client, sid, timeout=90)

    assert (s["status"], s["outcome"]) == ("COMPLETED", "BUGS_FOUND"), s
    bug = s["bugs"][0]
    assert bug["title"] == "Calling the API fails with HTTP 500" and bug["described_by"] == "analyzer"
    shot = bug["occurrences"][0]["screenshot_path"]
    assert shot and client.get(f"/api/sessions/{sid}/files/{shot}").status_code == 200
    files = {f["path"] for f in client.get(f"/api/sessions/{sid}/evidence").json()}
    assert {"trace.zip", "report.json", "actions.jsonl"} <= files
    report = client.get(f"/api/sessions/{sid}/report").json()
    assert json.dumps(report)  # serialisable
