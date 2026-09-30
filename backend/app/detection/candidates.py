"""Detection pipeline: recorded events -> baseline filter -> detectors -> candidates.

Signals raised by the same action form one candidate (incident): a click
that triggers a 500, the app's console error about it and the failed check
are one problem with three pieces of evidence, not three bugs.
"""

from __future__ import annotations

from collections import defaultdict

from app.browser.console import ConsoleEvent
from app.browser.network import NetworkEvent
from app.detection.baseline import Baseline
from app.detection.console import console_signals
from app.detection.http import http_signals
from app.detection.interaction import assertion_signals
from app.safety.domain_scope import DomainScope
from app.schemas.action import ActionResult
from app.schemas.bug import Candidate, NetworkEvidence, Signal
from app.schemas.plan import Step


def detect(
    *,
    network: list[NetworkEvent],
    console: list[ConsoleEvent],
    steps: list[Step],
    actions: list[ActionResult],
    scope: DomainScope,
) -> tuple[list[Candidate], Baseline]:
    by_seq = {a.sequence: a for a in actions}

    http = [(s, e) for s, e in http_signals(network) if not _agent_typed_url(s, e, by_seq)]
    logs = console_signals(console, scope)
    checks = assertion_signals(steps)

    signals: list[Signal] = [s for s, _ in http] + [s for s, _ in logs] + [s for s, *_ in checks]
    baseline = Baseline.from_signals(signals)
    kept = {id(s) for s in baseline.filter(signals)}

    incidents: dict[int, list[Signal]] = defaultdict(list)
    for s in signals:
        if id(s) in kept:
            incidents[s.action_seq].append(s)

    candidates = []
    for seq, group in sorted(incidents.items()):
        action = by_seq.get(seq)
        step = _step_for(seq, steps)
        failed_check = next(((st, c) for s, st, c in checks if s in group), None)
        candidates.append(Candidate(
            action_seq=seq,
            step=step.sequence if step else None,
            step_goal=step.goal if step else None,
            url=(action.url_before if action else step.end_url if step else None) or scope.target_url,
            action=_describe(action),
            signals=group,
            network=[NetworkEvidence(method=e.method, url=e.url, status=e.status, failure=e.failure,
                                     response_body=e.response_body)
                     for s, e in http if s in group],
            console=[e.text for s, e in logs if s in group],
            failed_check=failed_check[1].criterion.describe() if failed_check else None,
            check_detail=failed_check[1].detail if failed_check else None,
            screenshot=(action.screenshot if action else None) or (failed_check[0].screenshot if failed_check else None),
        ))
    return candidates, baseline


def _agent_typed_url(signal: Signal, event: NetworkEvent, actions: dict[int, ActionResult]) -> bool:
    """A missing page the agent reached by typing a guessed URL is not an application bug."""
    action = actions.get(event.action_seq)
    return (
        event.resource_type == "document" and signal.kind == "http_error" and not signal.strong
        and action is not None and action.action == "navigate"
    )


def _step_for(seq: int, steps: list[Step]) -> Step | None:
    for step in steps:
        if step.first_action is not None and step.last_action is not None and step.first_action <= seq <= step.last_action:
            return step
    return None


def _describe(action: ActionResult | None) -> str | None:
    if action is None:
        return None
    if action.target:
        return f'{action.action} {action.target.role} "{action.target.name}"'
    args = ", ".join(f"{k}={v}" for k, v in action.arguments.items() if v not in (None, False, ""))
    return f"{action.action} {args}".strip()
