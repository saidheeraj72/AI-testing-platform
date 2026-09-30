"""Describes candidates as bugs, and decides the weak ones.

Strong candidates (5xx, uncaught exception, failed grounded check) are bugs
whatever the model says; the analyzer only writes their title and text.
Weak candidates (unexpected 404, console error) become bugs only when the
analyzer confirms them. Without a working model, strong candidates are
described by rules and weak ones stay unconfirmed.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import urlsplit

from app.model.client import InvalidModelOutput, StructuredModel
from app.model.provider import Message, ModelError
from app.agent.budgets import BudgetExceeded
from app.schemas.bug import Analysis, Candidate

log = logging.getLogger(__name__)

SYSTEM = """\
You review problems detected automatically while testing a website. You get the evidence for one incident. \
Decide whether it shows a defect in the application, and describe it for a bug report.

Rules:
- is_bug is false only when the evidence clearly shows expected behaviour (for example a 404 for a page \
the test itself made up).
- title: at most 12 words, says what is broken, e.g. "Creating a customer fails with HTTP 500".
- summary: one or two sentences. expected / actual: one sentence each.
- severity: critical (data loss, security, app unusable), high (a main workflow fails), medium (wrong result \
or state), low (cosmetic, console noise).
- category: functional, validation, ui, performance, security or other.
- Use only the evidence given. Do not invent causes.
Reply with JSON only."""


class BugAnalyzer:
    def __init__(self, model: StructuredModel | None, objective: str):
        self.model = model
        self.objective = objective

    async def analyze(self, candidate: Candidate) -> tuple[Analysis | None, str]:
        """(analysis, "analyzer" | "rules"). None means a weak candidate that is not confirmed."""
        if self.model is not None:
            try:
                analysis = await self.model.generate(
                    Analysis,
                    [Message("system", SYSTEM), Message("user", self._evidence(candidate))],
                    purpose="analyze",
                    step=candidate.step,
                )
                if candidate.strong:
                    return analysis.model_copy(update={"is_bug": True}), "analyzer"
                return (analysis if analysis.is_bug else None), "analyzer"
            except (ModelError, InvalidModelOutput, BudgetExceeded) as e:
                log.warning("analyzer unavailable, using rules: %s", e)
        return (describe_by_rules(candidate) if candidate.strong else None), "rules"

    def _evidence(self, c: Candidate) -> str:
        evidence = {
            "test_objective": self.objective,
            "step": c.step_goal,
            "page": c.url,
            "action": c.action,
            "signals": [s.summary for s in c.signals],
            "network": [n.model_dump(exclude_none=True) for n in c.network],
            "console": c.console[:5],
            "failed_check": c.failed_check,
            "check_result": c.check_detail,
        }
        return json.dumps({k: v for k, v in evidence.items() if v}, indent=1)


def describe_by_rules(c: Candidate) -> Analysis:
    """Deterministic description, used when no model is available."""
    p = c.primary
    where = f" when {c.step_goal[0].lower()}{c.step_goal[1:]}" if c.step_goal else ""
    if p.kind == "http_error" and c.network:
        n = c.network[0]
        path = urlsplit(n.url).path
        return Analysis(
            is_bug=True, title=f"{n.method} {path} returns HTTP {n.status}{where}"[:120],
            severity="high" if (n.status or 0) >= 500 else "medium", category="functional",
            summary=p.summary, expected=f"{n.method} {path} succeeds",
            actual=f"The server responded with HTTP {n.status}" + (f": {n.response_body[:200]}" if n.response_body else ""),
        )
    if p.kind == "network_failure" and c.network:
        n = c.network[0]
        return Analysis(is_bug=True, title=f"{n.method} {urlsplit(n.url).path} fails to complete{where}"[:120],
                        severity="high", category="functional", summary=p.summary,
                        expected="The request completes", actual=f"The request failed: {n.failure}")
    if p.kind == "js_exception":
        return Analysis(is_bug=True, title=f"Uncaught JavaScript error{where}"[:120], severity="high",
                        category="functional", summary=p.summary, expected="No uncaught errors",
                        actual=p.summary)
    if p.kind == "assertion_failure":
        return Analysis(is_bug=True, title=f"{c.step_goal}: check failed"[:120] if c.step_goal else "Check failed",
                        severity="medium", category="functional", summary=p.summary,
                        expected=c.failed_check or "", actual=c.check_detail or "")
    return Analysis(is_bug=True, title=p.summary[:120], severity="low", category="other",
                    summary=p.summary, expected="No errors", actual=p.summary)
