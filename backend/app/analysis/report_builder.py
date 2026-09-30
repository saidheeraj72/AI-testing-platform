"""Turns a finished session into report.json: bugs, unconfirmed signals, and what was (not) verified."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from app.agent.agent import AgentResult
from app.analysis.bug_analyzer import BugAnalyzer
from app.analysis.deduplicator import deduplicate
from app.browser.session import BrowserSession
from app.detection.candidates import detect
from app.schemas.bug import Bug, Candidate
from app.schemas.plan import Step, StepStatus


async def find_bugs(
    *,
    result: AgentResult,
    browser: BrowserSession,
    analyzer: BugAnalyzer,
    on_event: Callable[[dict[str, Any]], None] = lambda e: None,
) -> tuple[list[Bug], list[dict[str, Any]], int]:
    """Returns (bugs, unconfirmed signals, number of baseline signatures ignored)."""
    candidates, baseline = detect(
        network=browser.network.events,
        console=browser.console.events,
        steps=result.steps,
        actions=result.actions,
        scope=browser.scope,
    )
    analyzed = []
    unconfirmed = []
    for candidate in candidates:
        on_event({"type": "bug_candidate", "step": candidate.step, "signals": [s.summary for s in candidate.signals]})
        analysis, described_by = await analyzer.analyze(candidate)
        if analysis is None:
            unconfirmed.append({"step": candidate.step, "url": candidate.url, "action": candidate.action,
                                "signals": [s.summary for s in candidate.signals], "decided_by": described_by})
            continue
        analyzed.append((candidate, analysis, described_by, _reproduce(candidate, result.steps)))

    bugs = deduplicate(analyzed)
    for bug in bugs:
        on_event({"type": "bug_confirmed", "id": bug.id, "title": bug.title, "severity": bug.severity})
    return bugs, unconfirmed, len(baseline.signatures)


def build_report(
    *,
    session_id: str,
    objective: str,
    result: AgentResult,
    browser: BrowserSession,
    bugs: list[Bug],
    unconfirmed: list[dict[str, Any]],
    baseline_ignored: int,
    extra: dict[str, Any],
) -> dict[str, Any]:
    counted = [s for s in result.steps if s.status != StepStatus.REPLANNED]
    trace = browser.storage.relative(browser.storage.paths.trace) if browser.storage.paths.trace.exists() else None
    return {
        "session_id": session_id,
        "objective": objective,
        "target_url": browser.scope.target_url,
        "outcome": outcome(result, bugs),
        "reason": result.reason,
        "summary": {
            "steps_planned": len(counted),
            "steps_passed": _count(counted, StepStatus.PASSED),
            "steps_failed": _count(counted, StepStatus.FAILED),
            "steps_could_not_verify": _count(counted, StepStatus.COULD_NOT_VERIFY),
            "steps_blocked": _count(counted, StepStatus.BLOCKED),
            "steps_skipped": _count(counted, StepStatus.SKIPPED),
            "replans": _count(result.steps, StepStatus.REPLANNED),
            "actions": len(result.actions),
            "pages_visited": len({urlsplit(a.url_after).path for a in result.actions}),
            "bugs": len(bugs),
            "unconfirmed_signals": len(unconfirmed),
            "baseline_errors_ignored": baseline_ignored,
        },
        "bugs": [_bug_json(b, trace) for b in bugs],
        "unconfirmed": unconfirmed,
        "could_not_verify": [{"step": s.sequence, "goal": s.goal, "reason": s.reason}
                             for s in counted if s.status == StepStatus.COULD_NOT_VERIFY],
        "not_tested": [{"step": s.sequence, "goal": s.goal, "status": s.status, "reason": s.reason}
                       for s in counted if s.status in (StepStatus.SKIPPED, StepStatus.BLOCKED)],
        "steps": [s.model_dump(mode="json") for s in result.steps],
        "trace": trace,
        **extra,
    }


def outcome(result: AgentResult, bugs: list[Bug]) -> str:
    if result.outcome in ("CANCELLED", "FAILED") and not bugs:
        return result.outcome
    if bugs:
        return "BUGS_FOUND"
    # A step failed but no evidence survived detection: do not claim a bug.
    return "COULD_NOT_VERIFY" if result.outcome == "BUGS_FOUND" else result.outcome


def _reproduce(candidate: Candidate, steps: list[Step]) -> list[str]:
    if candidate.step is None:
        return [f"Open {candidate.url}"] + ([candidate.action] if candidate.action else [])
    before = [s.goal for s in steps if s.sequence < candidate.step and s.status == StepStatus.PASSED]
    last = candidate.step_goal or ""
    if candidate.action:
        last = f"{last} ({candidate.action})" if last else candidate.action
    return [*before, last]


def _bug_json(bug: Bug, trace: str | None) -> dict[str, Any]:
    data = bug.model_dump(mode="json", exclude={"network", "console"})
    data["evidence"] = {
        "network": [n.model_dump(mode="json") for n in bug.network],
        "console": bug.console,
        "screenshots": [o.screenshot for o in bug.occurrences if o.screenshot],
        "trace": trace,
    }
    return data


def _count(steps: list[Step], status: StepStatus) -> int:
    return sum(s.status == status for s in steps)
