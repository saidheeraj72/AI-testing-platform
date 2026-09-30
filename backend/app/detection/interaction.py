"""Signals from the agent's checks: a grounded expectation that the application did not meet.

HTTP errors that failed a request check are left to the HTTP detector, which
has the better evidence; this covers wrong state (missing text, wrong URL,
wrong total, accepted invalid input).
"""

from __future__ import annotations

from app.schemas.bug import Signal
from app.schemas.plan import CheckResult, Step, StepStatus


def assertion_signals(steps: list[Step]) -> list[tuple[Signal, Step, CheckResult]]:
    out = []
    for step in steps:
        if step.status != StepStatus.FAILED:
            continue
        for check in step.checks:
            if check.passed or check.app_error or check.inconclusive or not check.criterion.grounded:
                continue
            c = check.criterion
            sig = Signal(
                kind="assertion_failure",
                strong=True,
                summary=f"Expected {c.describe()}; {check.detail}",
                signature="check " + "|".join(str(v) for v in c.key),
                action_seq=step.last_action or 0,
            )
            out.append((sig, step, check))
    return out
