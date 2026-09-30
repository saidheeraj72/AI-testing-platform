"""Plans, steps and success criteria.

The planner writes success criteria before execution; the executor can only
ask for them to be checked. Criteria are evaluated by code
(app.agent.assertions), never judged by the model.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

CriterionType = Literal[
    "url_contains", "text_visible", "element_present", "field_value", "request_succeeded", "sum_equals",
]


class PlannedCriterion(BaseModel):
    """What the planner model writes. Unused fields are null."""

    type: CriterionType
    value: str | None = Field(None, description="text / URL fragment / expected field value")
    within: str | None = Field(None, description="text_visible: name of the container, e.g. a table")
    role: str | None = Field(None, description="element_present: ARIA role, e.g. button, alert, row")
    name: str | None = Field(None, description="element_present / field_value: accessible name (label)")
    method: str | None = Field(None, description="request_succeeded: HTTP method")
    negate: bool = Field(False, description="true = must NOT be true (text absent, value different, ...)")


class PlannedStep(BaseModel):
    goal: str = Field(description="one short user-level action, e.g. 'Open the Customers page'")
    criteria: list[PlannedCriterion] = Field(description="0-3 checks that prove the step succeeded")


class PlannerOutput(BaseModel):
    steps: list[PlannedStep]


class Criterion(PlannedCriterion):
    """A criterion as the agent keeps it."""

    grounded: bool = Field(
        False,
        description="Derived from the objective or generated test data, not guessed from the UI. "
        "Only a grounded mismatch can be reported as a bug.",
    )

    def describe(self) -> str:
        neg = "NOT " if self.negate else ""
        match self.type:
            case "url_contains":
                return f"URL {neg}contains {self.value!r}"
            case "text_visible":
                where = f" within {self.within!r}" if self.within else ""
                return f"text {self.value!r} is {neg}visible{where}"
            case "element_present":
                what = f"{self.role or 'element'}" + (f" {self.name!r}" if self.name else "")
                return f"{what} is {neg}present"
            case "field_value":
                return f"field {self.name!r} value is {neg}{self.value!r}"
            case "request_succeeded":
                return f"{self.method or 'any'} request to {self.value!r} succeeded"
            case "sum_equals":
                return "the listed amounts add up to the total (give parts and total when you verify)"
        return self.type

    @property
    def key(self) -> tuple:
        return (self.type, self.value, self.within, self.role, self.name, self.method, self.negate)


class StepStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"                      # grounded check failed or the app returned an error
    COULD_NOT_VERIFY = "COULD_NOT_VERIFY"  # the agent could not establish the expected state
    BLOCKED = "BLOCKED"                    # safety policy or user refused an action
    SKIPPED = "SKIPPED"                    # not attempted because an earlier step did not pass
    REPLANNED = "REPLANNED"                # replaced by a new plan for the remaining work


class CheckResult(BaseModel):
    criterion: Criterion
    passed: bool
    detail: str
    app_error: bool = Field(False, description="failed because the application returned an error")


class Step(BaseModel):
    sequence: int
    goal: str
    criteria: list[Criterion]
    status: StepStatus = StepStatus.PENDING
    reason: str = ""
    checks: list[CheckResult] = Field(default_factory=list)
    first_action: int | None = None
    last_action: int | None = None
    end_url: str | None = None
    screenshot: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
