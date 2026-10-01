"""Creates the step plan and, when a step cannot be completed, replans the remaining work.

Grounding is decided here, in code: a criterion is grounded when its expected
value is exactly something the user stated (quoted text, a URL path) or the
generated test data. Only grounded checks can turn into bug reports; a failed
guess about the UI means "could not verify".
"""

from __future__ import annotations

import re
from pathlib import Path

from app.agent.prompts import PLANNER_SYSTEM, page_block
from app.agent.test_data import TestData
from app.model.client import StructuredModel
from app.model.provider import Message
from app.schemas.observation import Observation
from app.schemas.plan import Criterion, PlannedCriterion, PlannerOutput, Step

_ERROR_WORDS = ("error", "invalid", "reject", "validation", "warning", "fail")


class Planner:
    def __init__(self, model: StructuredModel, *, max_steps: int):
        self.model = model
        self.max_steps = max_steps

    async def plan(self, objective: str, test_data: TestData, observation: Observation,
                   first_sequence: int = 1, stated: str | None = None, image: Path | None = None) -> list[Step]:
        """`stated`: the text the user actually wrote, which grounds checks (default: the objective).

        In exploration the objective is written by a model, so only the user's notes ground anything.
        """
        messages = [
            Message("system", PLANNER_SYSTEM.format(max_steps=self.max_steps)),
            Message("user", f"OBJECTIVE: {objective}\nTEST DATA:\n{test_data.lines()}\n\n"
                            f"The browser is on the start page{_shown(image)}:\n{page_block(observation)}\n\n"
                            "Write the plan.", _images(image)),
        ]
        output = await self.model.generate(PlannerOutput, messages, purpose="plan", validate=self._validate)
        return self._to_steps(output, objective if stated is None else stated, test_data,
                              first_sequence=first_sequence)

    async def replan(
        self,
        objective: str,
        test_data: TestData,
        done: list[Step],
        failed: Step,
        remaining: list[Step],
        observation: Observation,
        stated: str | None = None,
        image: Path | None = None,
    ) -> list[Step]:
        done_text = "\n".join(f"  {s.sequence}. {s.goal}" for s in done) or "  (none)"
        messages = [
            Message("system", PLANNER_SYSTEM.format(max_steps=self.max_steps)),
            Message("user", (
                f"OBJECTIVE: {objective}\nTEST DATA:\n{test_data.lines()}\n\n"
                f"Already completed (do not repeat):\n{done_text}\n"
                f"This step could not be completed: {failed.goal}\nReason: {failed.reason}\n\n"
                f"The browser is now here{_shown(image)}:\n{page_block(observation)}\n\n"
                "Write a new plan for the remaining work only, starting from this page. "
                "Take a different approach to the failed step."
            ), _images(image)),
        ]
        output = await self.model.generate(PlannerOutput, messages, purpose="replan", validate=self._validate)
        steps = self._to_steps(output, objective if stated is None else stated, test_data,
                               first_sequence=failed.sequence + 1)

        # The objective's grounded checks must survive replanning, or the new plan could quietly skip them.
        kept = {c.key for s in steps for c in s.criteria}
        # Field checks are left out: they belong to one form, and on the next page that field no longer exists.
        dropped = [c for s in [failed, *remaining] for c in s.criteria
                   if c.grounded and c.key not in kept and c.type != "field_value"]
        steps[-1].criteria.extend({c.key: c for c in dropped}.values())
        return steps

    def _validate(self, output: PlannerOutput) -> None:
        """Drop unusable criteria; reject the plan only when the last step is left without a check.

        Small models often write half-filled criteria, and asking them to repair
        the whole plan rarely works. An intermediate step without checks is
        allowed ("unchecked"): it completes when the executor says so. The last
        step verifies the objective, so it must be checked by code.
        """
        if not 1 <= len(output.steps) <= self.max_steps:
            raise ValueError(f"the plan must have 1 to {self.max_steps} steps, it has {len(output.steps)}")
        for i, step in enumerate(output.steps, 1):
            if not step.goal.strip():
                raise ValueError(f"step {i} has an empty goal")
            usable = [c for c in step.criteria if _criterion_problem(c) is None]
            if not usable and i == len(output.steps):
                problems = "; ".join(filter(None, map(_criterion_problem, step.criteria))) or "no criteria"
                raise ValueError(f"the last step needs a usable criterion ({problems})")
            step.criteria = usable[:3]  # an intermediate step may end up unchecked

    def _to_steps(self, output: PlannerOutput, objective: str, test_data: TestData, first_sequence: int) -> list[Step]:
        stated = literals(objective, test_data)
        return [
            Step(
                sequence=first_sequence + i,
                goal=planned.goal.strip(),
                criteria=[
                    Criterion(**c.model_dump(), grounded=is_grounded(c, objective, stated))
                    for c in planned.criteria
                ],
            )
            for i, planned in enumerate(output.steps)
        ]


def _shown(image: Path | None) -> str:
    return " (the SCREENSHOT shows it)" if image else ""


def _images(image: Path | None) -> list[Path]:
    return [image] if image else []


def _criterion_problem(c: PlannedCriterion) -> str | None:
    ok = {
        "url_contains": c.value,
        "text_visible": c.value,
        "element_present": c.role or c.name,
        "field_value": c.name and c.value is not None,
        "request_succeeded": c.value,
        "sum_equals": True,
    }[c.type]
    if ok:
        return None
    fields = {"element_present": "'role' or 'name'", "field_value": "'name' and 'value'"}.get(c.type, "'value'")
    return f"{c.type} needs {fields}"


def literals(objective: str, test_data: TestData) -> set[str]:
    """Exact values the user stated: quoted strings, URL paths, e-mail addresses, plus generated test data."""
    found = set()
    for pattern in (r'"([^"]+)"', r"“([^”]+)”", r"(?<!\w)'([^']+)'(?!\w)"):
        found.update(m.strip() for m in re.findall(pattern, objective))
    # Paths and addresses end where the sentence does: "go back to /login."
    for pattern in (r"(?<![\w/])(/[\w\-./]+)", r"([\w.+-]+@[\w-]+\.[\w.]+)"):
        found.update(m.rstrip(".,;:!?)") for m in re.findall(pattern, objective))
    found.update(v for k, v in test_data.model_dump().items() if k != "tag")
    return {_norm(v) for v in found if v.strip()}


def is_grounded(c: PlannedCriterion, objective: str, stated: set[str]) -> bool:
    """True when the check's expectation is something the user stated exactly.

    Prose is not enough: "verify an order confirmation is shown" does not say
    the page contains the text "Order confirmation". Only exact values (quoted
    text, URL paths, generated test data) can make a mismatch a bug.
    """
    text = objective.casefold()
    if c.type == "request_succeeded":
        # Its URL is a guess. An HTTP error on a matching request is still reported: see CheckResult.app_error.
        return False
    if c.type == "sum_equals":
        return any(w in text for w in ("total", "sum", "add up"))
    if c.type == "element_present" and c.role in ("alert", "status") and not c.name:
        return any(w in text for w in _ERROR_WORDS)
    value = c.name if c.type == "element_present" else c.value
    return bool(value) and _norm(value) in stated


def _norm(value: str) -> str:
    return " ".join(value.split()).casefold()
