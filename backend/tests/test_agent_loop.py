"""The agent loop end to end, with a scripted fake model and the local fixture site.

Each script entry sees the whole conversation (which contains the page outline)
and returns the model's JSON reply, so refs can be looked up by label.
"""

import json
import re
from collections.abc import Callable

import pytest

from app.agent.agent import TestAgent
from app.agent.budgets import SessionBudget
from app.agent.test_data import TestData
from app.browser.session import BrowserConfig, BrowserLaunchError, BrowserSession
from app.config import Settings, load_settings
from app.model.client import StructuredModel
from app.model.provider import Completion, Message, ModelProvider
from app.safety.domain_scope import DomainScope
from app.schemas.plan import StepStatus
from app.storage.manager import SessionStorage

pytestmark = pytest.mark.browser
Reply = dict | Callable[[str], dict]


class ScriptedProvider(ModelProvider):
    def __init__(self, settings, script: list[Reply]):
        super().__init__(settings)
        self.script = list(script)
        self.prompts: list[str] = []

    async def complete(self, messages: list[Message], schema, *, temperature=None) -> Completion:
        prompt = "\n".join(m.content for m in messages)  # a repair turn has the page in an earlier message
        self.prompts.append(prompt)
        if not self.script:
            raise AssertionError(f"model script exhausted; last prompt:\n{prompt}")
        reply = self.script.pop(0)
        return Completion(text=json.dumps(reply(prompt) if callable(reply) else reply))


def ref(label: str) -> Callable[[str], str]:
    """Find the ref of e.g. 'textbox "Name"' in the prompt's page outline."""
    def find(prompt: str) -> str:
        m = re.search(r"\[((?:f\d+)?e\d+)\] " + re.escape(label), prompt)
        assert m, f"{label} not in page:\n{prompt}"
        return m[1]
    return find


def act(action: str, label: str | None = None, **fields) -> Callable[[str], dict]:
    return lambda prompt: {"reasoning": "scripted", "action": action,
                           **({"ref": ref(label)(prompt)} if label else {}), **fields}


def plan(*steps: tuple[str, list[dict]]) -> dict:
    return {"steps": [{"goal": g, "criteria": c} for g, c in steps]}


def settings(**agent) -> Settings:
    s = load_settings()
    s.agent = s.agent.model_copy(update={"max_replans": 1, **agent})
    s.safety.risky_actions = "block"
    return s


async def run_agent(site, tmp_path, script: list[Reply], objective="Test the form", **agent_settings):
    cfg = settings(**agent_settings)
    storage = SessionStorage.create(tmp_path / "sessions")
    provider = ScriptedProvider(cfg.model.executor, script)
    browser = BrowserSession(DomainScope.from_target(site), storage, BrowserConfig(headless=True))
    try:
        await browser.start()
    except BrowserLaunchError as e:
        pytest.skip(str(e))
    budget = SessionBudget(cfg.agent)
    model = StructuredModel(provider, storage, before_call=budget.before_model_call)
    agent = TestAgent(objective=objective, browser=browser, planner_model=model, executor_model=model,
                      settings=cfg, budget=budget, test_data=TestData.generate("qa1"))
    try:
        result = await agent.run()
    finally:
        await browser.stop()
    return result, provider, storage


async def test_step_passes_on_automatic_check_without_verify(site, tmp_path):
    result, provider, _ = await run_agent(site, tmp_path, [
        plan(("Submit the form with name John", [{"type": "text_visible", "value": "Submitted John"}])),
        act("type", 'textbox "Name"', text="John", submit=True),
    ])
    assert result.outcome == "PASS"
    assert result.steps[0].status == StepStatus.PASSED
    assert len(provider.prompts) == 2  # plan + one action; the check ran without asking the model


async def test_app_error_fails_the_step_with_evidence(site, tmp_path):
    result, _, storage = await run_agent(site, tmp_path, [
        plan(("Call the API", [{"type": "request_succeeded", "value": "/api/fail", "method": "POST"}]),
             ("Never reached", [{"type": "url_contains", "value": "/"}])),
        act("click", 'button "Call failing API"'),
    ])
    assert result.outcome == "BUGS_FOUND"
    first, second = result.steps
    assert first.status == StepStatus.FAILED and "500" in first.reason
    assert first.screenshot and (storage.root / first.screenshot).exists()
    assert second.status == StepStatus.SKIPPED


async def test_grounded_check_failing_after_verifies_is_a_bug(site, tmp_path):
    result, _, _ = await run_agent(site, tmp_path, [
        plan(("Submit Test User-qa1", [{"type": "text_visible", "value": "Welcome Test User-qa1"}])),
        act("type", 'textbox "Name"', text="Test User-qa1", submit=True),
        act("verify"),
        act("verify"),
    ], objective="Submit Test User-qa1 and check that 'Welcome Test User-qa1' is shown")
    step = result.steps[0]
    assert step.status == StepStatus.FAILED
    assert step.checks[0].criterion.grounded
    assert result.outcome == "BUGS_FOUND"


async def test_guessed_check_failing_is_could_not_verify(site, tmp_path):
    result, _, _ = await run_agent(site, tmp_path, [
        plan(("Submit", [{"type": "text_visible", "value": "Thank you"}])),
        act("type", 'textbox "Name"', text="x", submit=True),
        act("verify"),
        act("verify"),
    ], max_replans=0)
    assert result.steps[0].status == StepStatus.COULD_NOT_VERIFY
    assert result.outcome == "COULD_NOT_VERIFY"


async def test_unchecked_step_needs_an_action_then_verify(site, tmp_path):
    result, _, _ = await run_agent(site, tmp_path, [
        plan(("Type a name", []), ("Submit", [{"type": "text_visible", "value": "Submitted Ada"}])),
        act("verify"),  # refused: nothing done yet
        act("type", 'textbox "Name"', text="Ada"),
        act("verify"),
        act("click", 'button "Submit form"'),
    ])
    assert [s.status for s in result.steps] == [StepStatus.PASSED, StepStatus.PASSED]
    assert "no automatic check" in result.steps[0].reason


async def test_risky_action_blocked_by_policy(site, tmp_path):
    result, _, _ = await run_agent(site, tmp_path, [
        plan(("Delete everything", [{"type": "text_visible", "value": "Deleted"}])),
        act("click", 'button "Delete everything"'),
    ])
    assert result.steps[0].status == StepStatus.BLOCKED
    assert result.outcome == "BLOCKED"


async def test_give_up_replans_remaining_work(site, tmp_path):
    result, _, _ = await run_agent(site, tmp_path, [
        plan(("Open the reports page", [{"type": "url_contains", "value": "/reports"}])),
        act("give_up"),
        plan(("Open page two instead", [{"type": "url_contains", "value": "/page2"}])),
        act("click", 'link "Page two"'),
    ])
    assert [s.status for s in result.steps] == [StepStatus.REPLANNED, StepStatus.PASSED]
    assert result.outcome == "PASS"


async def test_repeated_action_is_detected_as_a_loop(site, tmp_path):
    result, _, _ = await run_agent(site, tmp_path, [
        plan(("Reach page three", [{"type": "url_contains", "value": "/page3"}])),
        *[act("click", 'button "Save"') for _ in range(3)],
    ], max_replans=0)
    assert result.steps[0].status == StepStatus.COULD_NOT_VERIFY
    assert "repeated" in result.steps[0].reason


async def test_invalid_reply_is_repaired(site, tmp_path):
    result, provider, storage = await run_agent(site, tmp_path, [
        plan(("Go to page two", [{"type": "url_contains", "value": "/page2"}])),
        {"reasoning": "bad ref", "action": "click", "ref": "e999"},
        act("click", 'link "Page two"'),
    ])
    assert result.outcome == "PASS"
    assert "not on the current page" in provider.prompts[2]
    calls = [json.loads(line) for line in storage.paths.model_calls.read_text().splitlines()]
    assert [c["validation_error"] is None for c in calls] == [True, False, True]


async def test_model_call_budget_stops_the_session(site, tmp_path):
    result, _, _ = await run_agent(site, tmp_path, [
        plan(("Reach page three", [{"type": "url_contains", "value": "/page3"}])),
        act("scroll", direction="down"),
    ], max_model_calls=2)
    assert result.outcome == "COULD_NOT_VERIFY"
    assert "model call limit" in result.reason


async def test_agent_typing_the_url_does_not_satisfy_a_grounded_url_check(site, tmp_path):
    result, _, _ = await run_agent(site, tmp_path, [
        plan(("Verify the app goes to page two", [{"type": "url_contains", "value": "/page2"}])),
        act("navigate", url="/page2"),
        act("verify"),
        act("verify"),
    ], objective="Submit the form and verify the app takes the user to /page2")
    step = result.steps[0]
    assert step.status == StepStatus.FAILED
    assert "opened this URL itself" in step.reason


async def test_agent_navigation_is_fine_for_guessed_url_checks(site, tmp_path):
    result, _, _ = await run_agent(site, tmp_path, [
        plan(("Open page two", [{"type": "url_contains", "value": "/page2"}])),
        act("navigate", url="/page2"),
    ])
    assert result.outcome == "PASS"


async def test_checks_already_true_at_step_start_do_not_pass_it_automatically(site, tmp_path):
    result, provider, _ = await run_agent(site, tmp_path, [
        plan(("Stay home", [{"type": "text_visible", "value": "Fixture home"}])),
        act("type", 'textbox "Name"', text="x"),
        act("verify"),
    ])
    assert result.outcome == "PASS"
    assert len(provider.prompts) == 3  # needed the explicit verify


async def test_pure_verification_step_can_fail_without_actions(site, tmp_path):
    result, _, _ = await run_agent(site, tmp_path, [
        plan(("Verify the welcome text for Test User-qa1 is shown",
              [{"type": "text_visible", "value": "Welcome Test User-qa1"}])),
        act("verify"),
        act("verify"),
    ], objective="Verify 'Welcome Test User-qa1' is shown")
    assert result.steps[0].status == StepStatus.FAILED


async def test_doing_step_is_not_failed_when_the_agent_only_verified(site, tmp_path):
    result, _, _ = await run_agent(site, tmp_path, [
        plan(("Create the user Test User-qa1", [{"type": "text_visible", "value": "Welcome Test User-qa1"}])),
        act("verify"),
        act("verify"),
    ], objective="Create 'Welcome Test User-qa1'", max_replans=0)
    assert result.steps[0].status == StepStatus.COULD_NOT_VERIFY
