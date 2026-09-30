"""Scores one session folder against the benchmark spec.

Session folder contract (fields the evaluator reads; everything else is ignored):

  report.json
    outcome: PASS | BUGS_FOUND | COULD_NOT_VERIFY | BLOCKED | FAILED | CANCELLED
    steps:   [{status: PASSED | FAILED | COULD_NOT_VERIFY | SKIPPED | BLOCKED, ...}]
    bugs:    [{title, summary?, expected?, actual?, url?,
               evidence?: {network?: [{method, url, status}], console?: [str]}}]

  manifest.json (optional)
    duration_ms
    benchmark: {scenario, active_bugs}   # used when not given on the command line

  model_calls.jsonl (optional)
    one JSON object per call; a non-null validation_error counts as invalid
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from benchmark.evaluator.spec import KnownIssue, MatchRule, Scenario, Spec, SpecError


@dataclass
class ReportedBug:
    index: int
    title: str
    label: str  # expected:<id> | duplicate:<id> | incidental:<id> | noise:<id> | inactive:<id> | unmatched


@dataclass
class RunResult:
    session: str
    scenario: str
    active_bugs: list[str]
    expected_bugs: list[str]
    outcome: str | None
    expected_outcome: str
    found: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)
    reported: list[ReportedBug] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def false_positives(self) -> list[ReportedBug]:
        return [
            r for r in self.reported
            if r.label == "unmatched" or r.label.startswith(("noise:", "inactive:"))
        ]

    @property
    def duplicates(self) -> list[ReportedBug]:
        return [r for r in self.reported if r.label.startswith("duplicate:")]

    @property
    def outcome_correct(self) -> bool:
        return self.outcome == self.expected_outcome

    def to_dict(self) -> dict[str, Any]:
        return {
            "session": self.session,
            "scenario": self.scenario,
            "active_bugs": self.active_bugs,
            "expected_bugs": self.expected_bugs,
            "outcome": self.outcome,
            "expected_outcome": self.expected_outcome,
            "outcome_correct": self.outcome_correct,
            "found": self.found,
            "missed": self.missed,
            "false_positives": len(self.false_positives),
            "duplicates": len(self.duplicates),
            "reported": [r.__dict__ for r in self.reported],
            "metrics": self.metrics,
        }


def score_session(
    session_dir: Path,
    spec: Spec,
    *,
    scenario_id: str | None = None,
    active: str | list[str] | None = None,
) -> RunResult:
    session_dir = Path(session_dir)
    report = _read_json(session_dir / "report.json", required=True)
    manifest = _read_json(session_dir / "manifest.json", required=False) or {}
    bench = manifest.get("benchmark") or {}

    scenario_id = scenario_id or bench.get("scenario")
    if not scenario_id:
        raise SpecError(f"{session_dir}: no scenario given and none in manifest.json")
    scenario = spec.scenario(scenario_id)
    active_ids = spec.parse_active(active if active is not None else bench.get("active_bugs"))

    expected = [b for b in scenario.bugs if b in active_ids]
    result = RunResult(
        session=str(session_dir),
        scenario=scenario.id,
        active_bugs=sorted(active_ids),
        expected_bugs=expected,
        outcome=report.get("outcome"),
        expected_outcome="BUGS_FOUND" if expected else "PASS",
    )

    for index, bug in enumerate(report.get("bugs") or []):
        label = _classify(bug, spec, scenario, active_ids, expected, result.found)
        if label.startswith("expected:"):
            result.found.append(label.split(":", 1)[1])
        result.reported.append(ReportedBug(index=index, title=str(bug.get("title", "")), label=label))

    result.missed = [b for b in expected if b not in result.found]
    result.metrics = _metrics(report, manifest, session_dir / "model_calls.jsonl")
    return result


def _classify(
    bug: dict[str, Any],
    spec: Spec,
    scenario: Scenario,
    active_ids: frozenset[str],
    expected: list[str],
    already_found: list[str],
) -> str:
    # Priority: the scenario's own bugs, then noise, then other seeded bugs.
    for bug_id in expected:
        if _matches(bug, spec.bugs[bug_id].rule):
            return f"duplicate:{bug_id}" if bug_id in already_found else f"expected:{bug_id}"
    for noise in spec.noise.values():
        if _matches(bug, noise.rule):
            return f"noise:{noise.id}"
    for other in _other_bugs(spec, scenario):
        if _matches(bug, other.rule):
            return f"incidental:{other.id}" if other.id in active_ids else f"inactive:{other.id}"
    return "unmatched"


def _other_bugs(spec: Spec, scenario: Scenario) -> list[KnownIssue]:
    return [b for b in spec.bugs.values() if b.id not in scenario.bugs]


def _matches(bug: dict[str, Any], rule: MatchRule) -> bool:
    evidence = bug.get("evidence") or {}
    network = [n for n in evidence.get("network") or [] if isinstance(n, dict)]
    console = [str(c) for c in evidence.get("console") or []]

    if rule.network and not any(rule.network.matches(n) for n in network):
        return False
    if rule.url_contains and rule.url_contains not in str(bug.get("url") or ""):
        return False
    if rule.keywords_any:
        text = " ".join(
            [str(bug.get(k) or "") for k in ("title", "summary", "expected", "actual")]
            + console
            + [f"{n.get('method', '')} {n.get('url', '')} {n.get('status', '')}" for n in network]
        ).lower()
        if not any(k in text for k in rule.keywords_any):
            return False
    return True


def _metrics(report: dict[str, Any], manifest: dict[str, Any], model_calls_path: Path) -> dict[str, Any]:
    steps = Counter(str(s.get("status")) for s in report.get("steps") or [])
    metrics: dict[str, Any] = {
        "steps_total": sum(steps.values()),
        "steps_by_status": dict(steps),
        "duration_ms": manifest.get("duration_ms"),
    }
    if model_calls_path.exists():
        calls = [json.loads(line) for line in model_calls_path.read_text().splitlines() if line.strip()]
        metrics["model_calls"] = len(calls)
        metrics["invalid_model_calls"] = sum(1 for c in calls if c.get("validation_error"))
        metrics["model_latency_ms"] = sum(int(c.get("latency_ms") or 0) for c in calls)
    return metrics


def summarize(results: list[RunResult]) -> dict[str, Any]:
    tp = sum(len(r.found) for r in results)
    fn = sum(len(r.missed) for r in results)
    fp = sum(len(r.false_positives) for r in results)
    calls = [r.metrics["model_calls"] for r in results if "model_calls" in r.metrics]
    invalid = sum(r.metrics.get("invalid_model_calls", 0) for r in results)
    durations = [r.metrics["duration_ms"] for r in results if r.metrics.get("duration_ms") is not None]
    return {
        "runs": len(results),
        "outcome_accuracy": _ratio(sum(r.outcome_correct for r in results), len(results)),
        "true_positives": tp,
        "false_negatives": fn,
        "false_positives": fp,
        "duplicates": sum(len(r.duplicates) for r in results),
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
        "model_calls": sum(calls) if calls else None,
        "invalid_model_call_rate": _ratio(invalid, sum(calls)) if calls else None,
        "mean_duration_ms": round(sum(durations) / len(durations)) if durations else None,
    }


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 3) if den else None


def _read_json(path: Path, *, required: bool) -> dict[str, Any] | None:
    if not path.exists():
        if required:
            raise SpecError(f"Missing {path}")
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise SpecError(f"{path} is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise SpecError(f"{path}: expected a JSON object")
    return data
