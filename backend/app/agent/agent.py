"""TestAgent: planner + a single executor, one step at a time.

Python owns the state. Each model call sees the objective, the plan, the
current step, this step's recent actions and the current page, never a
growing transcript. Steps pass only when code-evaluated checks pass.

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

from app.agent.assertions import PageState, VerifyArgs, evaluate, needs_verify_args
from app.agent.blockers import detect_blocker
from app.agent.budgets import BudgetExceeded, SessionBudget
from app.agent.control import SessionControl
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
from app.schemas.observation import Observation
from app.schemas.plan import CheckResult, Step, StepStatus

log = logging.getLogger(__name__)

EventSink = Callable[[dict[str, Any]], None]
ConfirmFn = Callable[[str], Awaitable[bool]]
AskUserFn = Callable[[str, str], Awaitable[bool]]  # (kind, message) -> continue?
RECENT_ACTIONS = 6


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

        self.steps: list[Step] = []
        self.actions: list[ActionResult] = []
        self.replans_left = settings.agent.max_replans
        self.loops = LoopDetector()
        self._url_typed_by_agent: str | None = None  # page the agent reached with its own navigate
        self._typed_fields: set[str] = set()  # fields the agent typed into since the page last loaded

    # ------------------------------------------------------------------ session

    async def run(self) -> AgentResult:
        try:
            await self.browser.navigate(self.browser.scope.target_url)
            observation = await self.browser.observe()
            self.steps = await self.planner.plan(self.objective, self.test_data, observation)
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
        verify_failures = 0
        feedback = ""
        checked = bool(step.criteria)
        needs_args = any(needs_verify_args(c) for c in step.criteria)
        already_true: bool | None = None  # were all checks true before this step did anything?

        while True:
            await self.control.checkpoint()
            self.budget.check_time()
            observation = await self.browser.observe()
            if reason := self.loops.record_page(observation.fingerprint):
                await self._unfinished(step, f"stuck: {reason}", observation)

            # Checks run after every action, so the model needn't spend a call to say "done".
            # Checks that were already true before the step started prove nothing on their own;
            # then only an explicit "verify" from the executor can complete the step.
            if checked and not needs_args and already_true is None:
                already_true = all(r.passed for r in self._check(step))
            if checked and acted:
                results = self._check(step)
                if not needs_args and not already_true and all(r.passed for r in results):
                    self._end(step, StepStatus.PASSED, results, "all checks passed")
                if errors := [r for r in results if r.app_error]:
                    self._end(step, StepStatus.FAILED, results, errors[0].detail)

            # CAPTCHAs, MFA codes and logins without credentials go to a person, before the model sees them.
            if blocker := detect_blocker(self.browser.last_nodes, self.objective):
                await self._hand_to_user(step, blocker.kind, blocker.message)
                continue

            if acted >= cfg.max_actions_per_step:
                await self._unfinished(step, f"not done after {acted} actions", observation)

            decision = await self._decide(step, observation, feedback)
            feedback = ""

            if decision.action == "verify" and not checked:
                # The planner gave no usable check for this step; the executor's word is all there is.
                if acted:
                    self._end(step, StepStatus.PASSED, [], "done according to the agent (no automatic check)")
                feedback = "Do the step first; nothing has been done in this step yet."
                continue

            if decision.action == "verify":
                results = self._check(step, VerifyArgs(decision.parts or [], decision.total))
                if all(r.passed for r in results):
                    self._end(step, StepStatus.PASSED, results, "all checks passed")
                failed = [r for r in results if not r.passed]
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
                feedback = "The checks did not pass: " + _details(failed)
                continue

            if decision.action == "give_up":
                await self._unfinished(step, f"agent gave up: {decision.reasoning}", observation)

            if decision.action == "ask_user":
                await self._hand_to_user(step, "agent_request", decision.reasoning)
                continue

            feedback = await self._act(step, decision, observation)
            acted += 1
            if reason := self.loops.record_action(decision.signature()):
                await self._app_ignored(step, self.actions[-1])
                await self._unfinished(step, f"stuck: {reason}", observation)

    async def _decide(self, step: Step, observation: Observation, feedback: str) -> Decision:
        progress = "\n".join(
            f"  {s.sequence}. [{'current' if s is step else s.status.lower()}] {s.goal}"
            for s in self.steps if s.status != StepStatus.REPLANNED
        )
        checks = "\n".join(f"  - {c.describe()}" for c in step.criteria) or "  - (no automatic check: reply verify when done)"
        recent = [a for a in self.actions if a.sequence >= (step.first_action or 0)][-RECENT_ACTIONS:]
        history = "\n".join(_describe_action(a) for a in recent) or "  (none yet)"
        user = (
            f"OBJECTIVE: {self.objective}\nTEST DATA (for new records only):\n{self.test_data.lines()}\n\n"
            f"PLAN:\n{progress}\n\n"
            f"CURRENT STEP: {step.goal}\nThe step is complete when:\n{checks}\n\n"
            f"ACTIONS IN THIS STEP:\n{history}\n"
            + (f"\nFEEDBACK: {feedback}\n" if feedback else "")
            + f"\n{page_block(observation)}\n\nReply with the next action."
        )
        decision = await self.executor.generate(
            Decision,
            [Message("system", EXECUTOR_SYSTEM), Message("user", user)],
            purpose="execute",
            step=step.sequence,
            validate=validator_for(observation),
        )
        self.emit({"type": "decision", "step": step.sequence, "action": decision.action,
                   "reasoning": decision.reasoning})
        return decision

    async def _act(self, step: Step, d: Decision, observation: Observation) -> str:
        """Run one browser action. Returns feedback for the next decision ("" when none)."""
        element = next((e for e in observation.elements if e.ref == d.ref), None)
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
            case "click":
                result = await b.click(d.ref)
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
        return "" if result.ok else f"Your last action failed: {result.error}: {result.message}"

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
                self.objective, self.test_data, done, step, self.steps[index + 1:], observation,
            )
            self.steps[index + 1:] = new_steps
            self.emit({"type": "replanned", "after_step": step.sequence, "reason": reason,
                       "steps": [s.model_dump(mode="json") for s in new_steps]})
            self._end(step, StepStatus.REPLANNED, results, reason)
        self._end(step, StepStatus.COULD_NOT_VERIFY, results, reason)


def _matches_goal(label: str, goal: str) -> bool:
    """Every word of the element's label appears in the step goal ("Log out" in "Log out of the app")."""
    words = re.findall(r"[a-z0-9]+", label.casefold())
    goal_words = set(re.findall(r"[a-z0-9]+", goal.casefold()))
    return bool(words) and all(w in goal_words for w in words)


def _is_verification(step: Step) -> bool:
    return step.goal.lower().startswith(("verify", "check", "confirm", "ensure", "make sure", "see that"))


def _details(results: list[CheckResult]) -> str:
    return "; ".join(f"{r.criterion.describe()} -> {r.detail}" for r in results)


def _describe_action(a: ActionResult) -> str:
    target = f' {a.target.role} "{a.target.name}"' if a.target else ""
    args = {k: v for k, v in a.arguments.items() if k != "ref" and v not in (None, False)}
    outcome = "ok" if a.ok else f"FAILED {a.error}: {a.message}"
    moved = f" (now at {a.url_after})" if a.navigated else ""
    return f"  #{a.sequence} {a.action}{target} {args or ''} -> {outcome}{moved}"


def _action_event(a: ActionResult) -> dict[str, Any]:
    return {
        "sequence": a.sequence, "action": a.action, "ok": a.ok,
        "target": a.target.model_dump() if a.target else None,
        "error": a.error, "message": a.message, "url": a.url_after,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
