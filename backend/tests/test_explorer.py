"""Exploration mode end to end: crawl, broken links and server errors, proposed workflows."""

import pytest

from app.config import load_settings
from app.run import run_session
from app.storage.manager import SessionStorage
from test_agent_loop import ScriptedProvider, act, plan

pytestmark = pytest.mark.browser

ANALYSIS = {"is_bug": True, "title": "Analyzed", "severity": "medium", "category": "functional",
            "summary": "s", "expected": "e", "actual": "a"}


async def test_explore_crawls_finds_errors_and_runs_workflows(site, tmp_path):
    settings = load_settings()
    settings.browser.headless = True
    settings.agent.explore_max_pages = 10
    settings.agent.explore_workflows = 1
    script = [
        {"workflows": [{"title": "Submit the form", "objective": "Submit the form with the name Ada"}]},
        plan(("Submit the form with name Ada", [{"type": "text_visible", "value": "Submitted Ada"}])),
        act("type", 'textbox "Name"', text="Ada", submit=True),
        act("verify"),  # the workflow's text check is the model's guess: the agent has to say it is done
        ANALYSIS, ANALYSIS,  # one per bug candidate
    ]
    provider = ScriptedProvider(settings.model.executor, script)
    events = []
    storage, report = await run_session(
        url=site, objective="", settings=settings, mode="explore", on_event=events.append,
        storage=SessionStorage.create(tmp_path / "sessions"), provider_factory=lambda cfg: provider,
    )

    exploration = report["exploration"]
    visited = {p["url"].removeprefix(site) for p in exploration["pages"]}
    assert {"/", "/page2", "/missing-page", "/api/fail"} <= visited
    assert "/logout" not in visited                      # never followed
    assert all(p["url"].startswith(site) for p in exploration["pages"])  # the off-site redirect stayed blocked
    assert (exploration["workflows_attempted"], exploration["workflows_completed"]) == (1, 1)

    signals = [s for b in report["bugs"] for s in b["signals"]]
    assert any("Broken link" in s and "/missing-page" in s for s in signals)
    assert any("/api/fail returned HTTP 500" in s for s in signals)
    assert report["outcome"] == "BUGS_FOUND"
    assert [s["workflow"] for s in report["steps"]] == ["Submit the form"]
    assert any(e["type"] == "page_explored" for e in events)


async def test_checks_in_model_written_workflows_never_become_bugs(site, tmp_path):
    settings = load_settings()
    settings.browser.headless = True
    settings.agent.explore_max_pages = 1
    settings.agent.explore_workflows = 1
    settings.agent.max_replans = 0
    settings.agent.max_analyzer_calls = 0
    script = [
        # The model quotes a label in its workflow; it is still the model's guess, not the user's expectation.
        {"workflows": [{"title": "Welcome", "objective": 'Submit the form and verify "Welcome Ada" is shown'}]},
        plan(("Submit and verify", [{"type": "text_visible", "value": "Welcome Ada"}])),
        act("type", 'textbox "Name"', text="Ada", submit=True),
        act("verify"), act("verify"),
    ]
    provider = ScriptedProvider(settings.model.executor, script)
    _, report = await run_session(
        url=site, objective="", settings=settings, mode="explore",
        storage=SessionStorage.create(tmp_path / "s"), provider_factory=lambda cfg: provider,
    )
    step = report["steps"][0]
    assert step["status"] == "COULD_NOT_VERIFY"
    assert not step["criteria"][0]["grounded"]
    assert not any("Welcome" in b["title"] for b in report["bugs"])
