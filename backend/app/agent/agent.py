"""TestAgent: planner + a single executor, one step at a time.

The executor works like Claude in Chrome: within a step it holds a short
conversation of tool calls and their results (click, type, find, read_page,
read_network, ...), and each turn it sees the current page as an outline
with [refs] plus a screenshot with the same refs drawn on it. Python still
owns the state: the plan, budgets, loop detection, risk policy and, above
all, the checks. Steps pass only when code-evaluated checks pass.

A step ends as:
  PASSED            every check passed
  FAILED            the app returned an error on the step's request, or a
                    grounded check still failed after repeated verification
  COULD_NOT_VERIFY  the agent could not get there (after replanning)
  BLOCKED           a risky action was refused
After a step that did not pass, the remaining steps are SKIPPED.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.agent.assertions import NO_FIELD, PageState, VerifyArgs, evaluate, needs_verify_args
from app.agent.blockers import detect_blocker
from app.agent.budgets import BudgetExceeded, SessionBudget
from app.agent.control import SessionControl
from app.agent import tools
from app.agent.decision import Decision, validator_for
from app.agent.loops import LoopDetector
from app.agent.planner import Planner
from app.agent.prompts import EXECUTOR_SYSTEM, page_block
from app.agent.test_data import TestData
from app.browser.session import BrowserClosedError, BrowserSession
from app.config import Settings
from app.model.client import InvalidModelOutput, StructuredModel
from app.model.provider import Message, ModelError
from app.safety.risk import risky_reason
from app.schemas.action import ActionResult
from app.schemas.observation import Element, Observation
from app.schemas.plan import CheckResult, Step, StepStatus

log = logging.getLogger(__name__)

EventSink = Callable[[dict[str, Any]], None]
ConfirmFn = Callable[[str], Awaitable[bool]]
AskUserFn = Callable[[str, str], Awaitable[bool]]  # (kind, message) -> continue?
RECENT_TURNS = 10  # tool calls of this step the executor sees in full; older ones are left out
FULL_RESULTS = 2  # the latest results are shown in full, earlier ones shortened


class _StepEnded(Exception):
    """Internal: the current step reached a final status."""


@dataclass
class AgentResult:
    outcome: str
    reason: str
    steps: list[Step]
    actions: list[ActionResult] = field(default_factory=list)


class TestAgent:
    __test__ = False  # not a pytest class

    def __init__(
        self,
        *,
        objective: str,
        browser: BrowserSession,
        planner_model: StructuredModel,
        executor_model: StructuredModel,
        settings: Settings,
        budget: SessionBudget,
        test_data: TestData,
        on_event: EventSink = lambda e: None,
        confirm: ConfirmFn | None = None,
        control: SessionControl | None = None,
        ask_user: AskUserFn | None = None,
        first_sequence: int = 1,
        workflow: str | None = None,
        stated: str | None = None,
    ):
        self.objective = objective
        self.browser = browser
        self.executor = executor_model
        self.planner = Planner(planner_model, max_steps=settings.agent.max_plan_steps)
        self.settings = settings
        self.budget = budget
        self.test_data = test_data
        self.emit = on_event
        self.confirm = confirm
        self.ask_user = ask_user
        self.control = control or SessionControl()

        self.first_sequence = first_sequence  # exploration runs several agents in one session
        self.workflow = workflow
        self.stated = stated  # what the user wrote; grounds checks. None: the objective itself
        self.steps: list[Step] = []
        self.actions: list[ActionResult] = []
        self.replans_left = settings.agent.max_replans
        self.loops = LoopDetector()
        self._url_typed_by_agent: str | None = None  # page the agent reached with its own navigate
        self._typed_fields: set[str] = set()  # fields the agent typed into since the page last loaded
        self._turns: list[tuple[str, str]] = []  # this step's (tool call JSON, result) conversation
        self._want_screenshot = False

    # ------------------------------------------------------------------ session

    async def run(self) -> AgentResult:
        try:
            await self.browser.navigate(self.browser.scope.target_url)
            observation = await self.browser.observe()
            self.steps = await self.planner.plan(self.objective, self.test_data, observation, self.first_sequence,
                                                 stated=self.stated, image=await self._planner_image())
            for step in self.steps:
                step.workflow = self.workflow
            self.emit({"type": "plan_created", "steps": [s.model_dump(mode="json") for s in self.steps]})

            index = 0
            while index < len(self.steps):
                step = self.steps[index]
                await self._run_step(step)
                if step.status == StepStatus.REPLANNED:
                    index += 1  # the new steps were inserted right after it
                    continue
                if step.status != StepStatus.PASSED:
                    self._skip_rest(index + 1)
                    break
                index += 1
        except BudgetExceeded as e:
            self.abort(StepStatus.COULD_NOT_VERIFY, f"session budget: {e}")
            return self._result(f"stopped: {e}")
        except (ModelError, InvalidModelOutput) as e:
            self.abort(StepStatus.COULD_NOT_VERIFY, f"model problem: {e}")
            return self._result(f"model problem: {e}", failed=True)
        except BrowserClosedError:
            self.abort(StepStatus.COULD_NOT_VERIFY, "the browser was closed")
            return self._result("the browser was closed", failed=True)
        return self._result("")

    def _result(self, reason: str, *, failed: bool = False) -> AgentResult:
        statuses = [s.status for s in self.steps if s.status != StepStatus.REPLANNED]
        if failed and not any(s == StepStatus.FAILED for s in statuses):
            outcome = "FAILED"
        elif any(s == StepStatus.FAILED for s in statuses):
            outcome = "BUGS_FOUND"
        elif statuses and all(s == StepStatus.PASSED for s in statuses):
            outcome = "PASS"
        elif any(s == StepStatus.BLOCKED for s in statuses):
            outcome = "BLOCKED"
        else:
            outcome = "COULD_NOT_VERIFY"
        return AgentResult(outcome=outcome, reason=reason, steps=self.steps, actions=self.actions)

    def _skip_rest(self, start: int) -> None:
        for step in self.steps[start:]:
            step.status, step.reason = StepStatus.SKIPPED, "an earlier step did not pass"

    def abort(self, status: StepStatus, reason: str) -> None:
        """End the running (or next pending) step with `status` and skip the rest."""
        for i, step in enumerate(self.steps):
            if step.status in (StepStatus.RUNNING, StepStatus.PENDING):
                step.status, step.reason = status, reason
                step.completed_at = _now()
                self._skip_rest(i + 1)
                return

    # ------------------------------------------------------------------ step

    async def _run_step(self, step: Step) -> None:
        step.status, step.started_at = StepStatus.RUNNING, _now()
        step.first_action = self.browser.action_count + 1
        self.loops.reset()
        self.emit({"type": "step_started", "step": step.sequence, "goal": step.goal,
                   "criteria": [c.describe() for c in step.criteria]})
        try:
            await self._step_loop(step)
        except _StepEnded:
            pass
        step.last_action = self.browser.action_count
        step.completed_at = _now()
        step.end_url = self.browser.page.url
        if step.status in (StepStatus.FAILED, StepStatus.COULD_NOT_VERIFY, StepStatus.BLOCKED):
            step.screenshot = await self.browser.screenshot()
        self.emit({"type": "step_finished", "step": step.sequence, "status": step.status, "reason": step.reason})

    async def _step_loop(self, step: Step) -> None:
        cfg = self.settings.agent
        acted = 0
        looked = 0
        verify_failures = 0
        checked = bool(step.criteria)
        needs_args = any(needs_verify_args(c) for c in step.criteria)
        already_true: bool | None = None  # were all checks true before this step did anything?
        self._turns = []
        self._want_screenshot = cfg.vision != "off"  # every step starts by looking
        previous: str | None = None  # page fingerprint before the latest action
        after_action = False
        last_verify: list[CheckResult] | None = None  # results of the executor's latest "verify"

        while True:
            await self.control.checkpoint()
            self.budget.check_time()
            observation = await self.browser.observe()
            if after_action:
                if reason := self.loops.record_page(observation.fingerprint):
                    await self._unfinished(step, f"stuck: {reason}", observation)
                if observation.fingerprint == previous:
                    self._note("Nothing on the page changed.")
            previous, after_action = observation.fingerprint, False

            # Checks run after every action, so the model needn't spend a call to say "done".
            # Checks that were already true before the step started prove nothing on their own;
            # then only an explicit "verify" from the executor can complete the step.
            if checked and not needs_args and already_true is None:
                already_true = _passes(self._check(step))
            if checked and acted:
                results = self._check(step)
                if not needs_args and not already_true and _passes(results):
                    self._end(step, StepStatus.PASSED, results, "all checks passed")
                if errors := [r for r in results if r.app_error]:
                    self._end(step, StepStatus.FAILED, results, errors[0].detail)

            # CAPTCHAs, MFA codes, single sign-on and logins without credentials go to a person,
            # before the model sees them.
            if blocker := detect_blocker(self.browser.last_nodes, self.objective, self.browser.page.url):
                await self._hand_to_user(step, blocker.kind, blocker.message)
                continue

            if acted >= cfg.max_actions_per_step:
                await self._unfinished(step, f"not done after {acted} actions", observation)
            if looked >= cfg.max_actions_per_step:
                await self._unfinished(step, f"looked at the page {looked} times without getting it done", observation)

            decision = await self._decide(step, observation)

            if decision.looks:
                looked += 1
                self._record(decision, await self._look(step, decision, observation))
                if reason := self.loops.record_action(decision.signature()):
                    await self._unfinished(step, f"stuck: {reason}", observation)
                continue

            if decision.action == "verify" and not checked:
                # The planner gave no usable check for this step; the executor's word is all there is.
                if acted:
                    self._end(step, StepStatus.PASSED, [], "done according to the agent (no automatic check)")
                self._record(decision, "Do the step first; nothing has been done in this step yet.")
                continue

            if decision.action == "verify":
                results = self._check(step, VerifyArgs(decision.parts or [], decision.total))
                last_verify = results
                if _passes(results):
                    self._end(step, StepStatus.PASSED, results, "all checks passed")
                failed = [r for r in results if not r.passed and not _missing_field(r)]
                if any(r.app_error for r in failed):
                    self._end(step, StepStatus.FAILED, results, next(r.detail for r in failed if r.app_error))
                verify_failures += 1
                if verify_failures >= cfg.max_verify_failures:
                    # Without an action, a failed check only means something in a pure verification step.
                    grounded = [r for r in failed if r.criterion.grounded and not r.inconclusive]
                    if grounded and (acted or needs_args or _is_verification(step)):
                        self._end(step, StepStatus.FAILED, results,
                                  f"{grounded[0].criterion.describe()}: {grounded[0].detail}")
                    await self._unfinished(step, "checks did not pass: " + _details(failed), observation)
                self._record(decision, "The checks did not pass: " + _details(failed))
                continue

            if decision.action == "give_up":
                # The model's conclusion is not evidence, but the checks are: when a check the user asked
                # for fails as the agent gives up, the step failed exactly as if it had verified twice.
                results = last_verify if needs_args else self._check(step)
                grounded = [r for r in results or [] if not r.passed and r.criterion.grounded and not r.inconclusive]
                if grounded and (acted or needs_args or _is_verification(step)):
                    self._end(step, StepStatus.FAILED, results or [],
                              f"{grounded[0].criterion.describe()}: {grounded[0].detail}")
                await self._unfinished(step, f"agent gave up: {decision.reasoning}", observation)

            if decision.action == "ask_user":
                await self._hand_to_user(step, "agent_request", decision.reasoning)
                self._record(decision, "The person is done. Look at the page again before you continue.")
                continue

            self._record(decision, await self._act(step, decision, observation))
            acted += 1
            after_action = True
            if reason := self.loops.record_action(decision.signature()):
                await self._app_ignored(step, self.actions[-1])
                await self._unfinished(step, f"stuck: {reason}", observation)

    async def _decide(self, step: Step, observation: Observation) -> Decision:
        progress = "\n".join(
            f"  {s.sequence}. [{'current' if s is step else s.status.lower()}] {s.goal}"
            for s in self.steps if s.status != StepStatus.REPLANNED
        )
        checks = "\n".join(f"  - {c.describe()}" for c in step.criteria) or "  - (no automatic check: reply verify when done)"
        header = (
            f"OBJECTIVE: {self.objective}\nTEST DATA (for new records only):\n{self.test_data.lines()}\n\n"
            f"PLAN:\n{progress}\n\n"
            f"CURRENT STEP: {step.goal}\nThe step is complete when:\n{checks}"
        )
        images = []
        if self.settings.agent.vision == "always" or self._want_screenshot:
            images = [await self.browser.model_screenshot()]
            self._want_screenshot = False
        shown = "\nSCREENSHOT: attached; it shows the viewport with the refs drawn on it." if images else ""
        page = f"\n\n{page_block(observation)}{shown}\n\nReply with the next tool call."

        turns = self._turns[-RECENT_TURNS:]
        messages = [Message("system", EXECUTOR_SYSTEM)]
        if not turns:
            messages.append(Message("user", header + page, images))
        else:
            hidden = len(self._turns) - len(turns)
            note = f"\n({hidden} earlier tool calls in this step are not shown.)" if hidden else ""
            messages.append(Message("user", header + note + "\n\nStart the step."))
            for i, (call, result) in enumerate(turns):
                last = i == len(turns) - 1
                if i < len(turns) - FULL_RESULTS:
                    result = _clip(result, 300)
                messages.append(Message("assistant", call))
                messages.append(Message("user", f"RESULT: {result}" + (page if last else ""), images if last else []))

        decision = await self.executor.generate(
            Decision, messages, purpose="execute", step=step.sequence, validate=validator_for(observation),
        )
        self.emit({"type": "decision", "step": step.sequence, "action": decision.action,
                   "reasoning": decision.reasoning})
        return decision

    async def _look(self, step: Step, d: Decision, observation: Observation) -> str:
        """Run a tool that only reads the page. Returns its result for the model."""
        b = self.browser
        since = step.first_action or 0
        match d.action:
            case "find":
                result = tools.find(d.query or "", observation, b.last_nodes)
            case "read_page":
                result = b.full_outline()
            case "get_page_text":
                result = await b.page_text()
            case "screenshot":
                if self.settings.agent.vision == "off":
                    result = "Screenshots are turned off in the settings; use read_page or find."
                else:
                    self._want_screenshot = True
                    result = "A fresh screenshot is attached below."
            case "read_console":
                result = tools.console_report(b.console.since(since))
            case _:
                result = tools.network_report(b.network.since(since))
        self.emit({"type": "looked", "step": step.sequence, "action": d.action, "query": d.query,
                   "result": _clip(result, 400)})
        return result

    def _record(self, decision: Decision, result: str) -> None:
        self._turns.append((decision.model_dump_json(exclude_none=True), result))

    def _note(self, text: str) -> None:
        """Add an observation to the latest tool result, e.g. that the page did not change."""
        if self._turns:
            call, result = self._turns[-1]
            self._turns[-1] = (call, f"{result} {text}")

    async def _planner_image(self):
        return await self.browser.model_screenshot() if self.settings.agent.vision != "off" else None

    async def _act(self, step: Step, d: Decision, observation: Observation) -> str:
        """Run one browser action. Returns its result for the model."""
        element = next((e for e in observation.elements if e.ref == d.ref), None)
        if d.action == "click" and not d.ref and d.x is not None and d.y is not None:
            target = await self.browser.element_at(d.x, d.y)
            element = Element(ref="", role=target.role, name=target.name) if target else None
        if reason := risky_reason(d.action, element):
            policy = self.settings.safety.risky_actions
            allowed = policy == "allow"
            if policy == "confirm":
                # The confirm callback owns the user-facing event (the API adds a confirmation id).
                allowed = await self.confirm(reason) if self.confirm else False
            if not allowed:
                self._end(step, StepStatus.BLOCKED, [], f"risky action not allowed: {reason}")

        b = self.browser
        match d.action:
            case "click" if d.ref:
                result = await b.click(d.ref)
            case "click":
                result = await b.click_at(d.x or 0, d.y or 0)
            case "hover":
                result = await b.hover(d.ref)
            case "type":
                result = await b.type(d.ref, d.text or "", submit=bool(d.submit))
            case "select":
                result = await b.select(d.ref, d.text or "")
            case "press":
                result = await b.press(d.key or "Enter", d.ref)
            case "scroll":
                result = await b.scroll(d.direction or "down")
            case "navigate":
                result = await b.navigate(d.url or "/")
            case "go_back":
                result = await b.go_back()
            case _:
                result = await b.wait(1.0)
        self.actions.append(result)
        if result.navigated or d.action in ("navigate", "go_back"):
            self._typed_fields.clear()
        if result.ok and d.action in ("type", "select") and result.target:
            self._typed_fields.add(result.target.name.casefold())
        if result.ok and d.action == "navigate":
            self._url_typed_by_agent = result.url_after
        elif result.navigated:
            self._url_typed_by_agent = None
        self.emit({"type": "action_completed", "step": step.sequence, **_action_event(result)})
        return _action_report(result, self.browser.network.since(result.sequence))

    def _check(self, step: Step, args: VerifyArgs | None = None) -> list[CheckResult]:
        page = PageState(
            url=self.browser.page.url,
            nodes=self.browser.last_nodes,
            network=self.browser.network.since(step.first_action or 0),
        )
        results = [self._credit(evaluate(c, page, args)) for c in step.criteria]
        self.emit({"type": "checks", "step": step.sequence,
                   "results": [{"check": r.criterion.describe(), "passed": r.passed, "detail": r.detail}
                               for r in results]})
        return results

    def _credit(self, result: CheckResult) -> CheckResult:
        """Only credit what the application did, not what the agent did.

        - A field value the agent typed on this page is inconclusive until the page reloads.
        - A grounded URL check fails if the agent opened that URL itself; otherwise
          "logging out returns to the login page" would pass whenever the agent
          gives up waiting and opens /login.
        """
        c = result.criterion
        if c.type == "field_value" and any(
            (c.name or "").casefold() in typed for typed in self._typed_fields
        ):
            # A field still showing what the agent typed says nothing about what the app saved.
            return result.model_copy(update={
                "passed": False,
                "inconclusive": True,
                "detail": f"{result.detail}, but that is what the agent typed; reload the page "
                          "(navigate to the same URL) to see the saved value",
            })
        if (result.passed and c.grounded and c.type == "url_contains" and not c.negate
                and self._url_typed_by_agent == self.browser.page.url):
            return result.model_copy(update={
                "passed": False,
                "detail": f"{result.detail}, but the agent opened this URL itself; the application did not go there",
            })
        return result

    def _end(self, step: Step, status: StepStatus, results: list[CheckResult], reason: str) -> None:
        step.status, step.reason = status, reason
        if results:
            step.checks = results
        raise _StepEnded

    async def _hand_to_user(self, step: Step, kind: str, message: str) -> None:
        """Wait for a person to do something in the browser. Returns when they continue; ends the step if not."""
        if self.ask_user is None:
            self._end(step, StepStatus.BLOCKED, [], f"needs a person ({kind}): {message}")
        self.emit({"type": "waiting_for_user", "step": step.sequence, "kind": kind, "message": message})
        if not await self.ask_user(kind, message):
            self._end(step, StepStatus.BLOCKED, [], f"skipped by the user ({kind}): {message}")
        self.loops.reset()  # the person changed the page; earlier repetition no longer counts

    async def _app_ignored(self, step: Step, repeated: ActionResult) -> None:
        """The agent kept doing exactly what the step says, it worked each time, and nothing happened.

        Example: clicking "Log out" three times without being taken to /login.
        Then the grounded expectation failing is the application's fault, not the
        agent's. Repeating an unrelated action is just the agent being stuck.
        """
        if not (repeated.ok and repeated.target and _matches_goal(repeated.target.name, step.goal)):
            return
        page = PageState(self.browser.page.url, self.browser.last_nodes,
                         self.browser.network.since(step.first_action or 0))
        results = [self._credit(evaluate(c, page)) for c in step.criteria]
        failed = [r for r in results if not r.passed and r.criterion.grounded and not r.inconclusive]
        if failed:
            target = f'{repeated.target.role} "{repeated.target.name}"'
            self._end(step, StepStatus.FAILED, results,
                      f"the application did not react to {repeated.action} {target}: "
                      f"{failed[0].criterion.describe()} -> {failed[0].detail}")

    async def _unfinished(self, step: Step, reason: str, observation: Observation) -> None:
        """The agent could not complete the step: record an app error if the checks show one, else replan."""
        results = [self._credit(evaluate(c, PageState(self.browser.page.url, self.browser.last_nodes,
                                                      self.browser.network.since(step.first_action or 0))))
                   for c in step.criteria]
        if errors := [r for r in results if r.app_error]:
            self._end(step, StepStatus.FAILED, results, errors[0].detail)

        step.reason = reason
        if self.replans_left > 0:
            self.replans_left -= 1
            index = self.steps.index(step)
            done = [s for s in self.steps[:index] if s.status == StepStatus.PASSED]
            new_steps = await self.planner.replan(
                self.objective, self.test_data, done, step, self.steps[index + 1:], observation, stated=self.stated,
                image=await self._planner_image(),
            )
            for new_step in new_steps:
                new_step.workflow = self.workflow
            self.steps[index + 1:] = new_steps
            self.emit({"type": "replanned", "after_step": step.sequence, "reason": reason,
                       "steps": [s.model_dump(mode="json") for s in new_steps]})
            self._end(step, StepStatus.REPLANNED, results, reason)
        self._end(step, StepStatus.COULD_NOT_VERIFY, results, reason)


def _missing_field(r: CheckResult) -> bool:
    """A field check for a field this page does not have, e.g. "First Name" on an order confirmation."""
    return r.inconclusive and r.criterion.type == "field_value" and r.detail.startswith(NO_FIELD)


def _passes(results: list[CheckResult]) -> bool:
    """All checks pass. A check on a field the page does not have cannot be evaluated here; it does not
    block a step that the other checks prove. Alone, it proves nothing."""
    applicable = [r for r in results if not _missing_field(r)] or results
    return all(r.passed for r in applicable)


def _matches_goal(label: str, goal: str) -> bool:
    """Every word of the element's label appears in the step goal ("Log out" in "Log out of the app")."""
    words = re.findall(r"[a-z0-9]+", label.casefold())
    goal_words = set(re.findall(r"[a-z0-9]+", goal.casefold()))
    return bool(words) and all(w in goal_words for w in words)


def _is_verification(step: Step) -> bool:
    return step.goal.lower().startswith(("verify", "check", "confirm", "ensure", "make sure", "see that"))


def _details(results: list[CheckResult]) -> str:
    return "; ".join(f"{r.criterion.describe()} -> {r.detail}" for r in results)


def _action_report(a: ActionResult, network: list) -> str:
    """What the model learns from its action: did it work, where is the browser now, did the app complain."""
    target = f' {a.target.role} "{a.target.name}"' if a.target else ""
    if not a.ok:
        return f"{a.action}{target} FAILED: {a.message} Try something different."
    parts = [f"{a.action}{target}: done."]
    if a.navigated:
        parts.append(f"The browser is now at {a.url_after}.")
    errors = [e for e in network if e.first_party and e.is_error and e.resource_type in ("fetch", "xhr", "document")]
    for e in errors[:3]:
        status = f"HTTP {e.status}" if e.status is not None else e.failure
        parts.append(f"The application answered {e.method} {e.url} with {status}.")
    if errors:
        parts.append('If this was not expected, reply "verify" so the checks record it.')
    return " ".join(parts)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _action_event(a: ActionResult) -> dict[str, Any]:
    return {
        "sequence": a.sequence, "action": a.action, "ok": a.ok,
        "target": a.target.model_dump() if a.target else None,
        "error": a.error, "message": a.message, "url": a.url_after,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
