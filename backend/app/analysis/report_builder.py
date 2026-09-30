"""Builds report.json for a session.

For now bugs come only from FAILED steps: a grounded check that failed, or
the application returning an error on the step's own request. Each carries
the network and console evidence from that step. The Phase 3 bug engine
(detectors, baseline, AI analysis, deduplication) will replace this.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from app.agent.agent import AgentResult
from app.browser.session import BrowserSession
from app.schemas.plan import Step, StepStatus


def build_report(
    *,
    session_id: str,
    objective: str,
    result: AgentResult,
    browser: BrowserSession,
    extra: dict[str, Any],
) -> dict[str, Any]:
    steps = [s for s in result.steps]
    counted = [s for s in steps if s.status != StepStatus.REPLANNED]
    bugs = [_bug(s, steps, browser) for s in counted if s.status == StepStatus.FAILED]
    return {
        "session_id": session_id,
        "objective": objective,
        "target_url": browser.scope.target_url,
        "outcome": result.outcome,
        "reason": result.reason,
        "summary": {
            "steps_planned": len(counted),
            "steps_passed": sum(s.status == StepStatus.PASSED for s in counted),
            "steps_failed": sum(s.status == StepStatus.FAILED for s in counted),
            "steps_could_not_verify": sum(s.status == StepStatus.COULD_NOT_VERIFY for s in counted),
            "steps_blocked": sum(s.status == StepStatus.BLOCKED for s in counted),
            "steps_skipped": sum(s.status == StepStatus.SKIPPED for s in counted),
            "replans": sum(s.status == StepStatus.REPLANNED for s in steps),
            "actions": len(result.actions),
            "pages_visited": len({urlsplit(a.url_after).path for a in result.actions} |
                                 {urlsplit(a.url_before).path for a in result.actions}),
            "bugs": len(bugs),
        },
        "steps": [s.model_dump(mode="json") for s in steps],
        "bugs": bugs,
        **extra,
    }


def _bug(step: Step, steps: list[Step], browser: BrowserSession) -> dict[str, Any]:
    failed = next((c for c in step.checks if not c.passed), None)
    lo, hi = step.first_action or 0, step.last_action or 0
    in_step = lambda e: lo <= e.action_seq <= hi  # noqa: E731

    network = [
        {"method": e.method, "url": e.url, "status": e.status, "failure": e.failure,
         "response_body": e.response_body}
        for e in browser.network.events
        if in_step(e) and e.first_party and e.is_error and e.resource_type in ("fetch", "xhr", "document")
    ]
    # Console errors already present before this step are page noise, not evidence.
    earlier = {e.text for e in browser.console.events if e.action_seq < lo}
    console = [e.text for e in browser.console.events if in_step(e) and e.level == "error" and e.text not in earlier]

    reproduce = [s.goal for s in steps if s.sequence < step.sequence and s.status == StepStatus.PASSED]
    return {
        "title": f"{step.goal}: {failed.criterion.describe() if failed else step.reason}",
        "severity": "high" if failed and failed.app_error else "medium",
        "category": "functional",
        "summary": step.reason,
        "expected": failed.criterion.describe() if failed else "",
        "actual": failed.detail if failed else step.reason,
        "url": step.end_url,
        "step": step.sequence,
        "steps_to_reproduce": [*reproduce, step.goal],
        "evidence": {"network": network, "console": console, "screenshot": step.screenshot},
    }
