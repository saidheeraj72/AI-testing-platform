"""Command line entry point: python -m benchmark.evaluator ..."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from benchmark.evaluator.scoring import RunResult, score_session, summarize
from benchmark.evaluator.spec import DEFAULT_SPEC_PATH, SpecError, load_spec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m benchmark.evaluator", description=__doc__)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC_PATH, help="expected-results.yaml path")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("scenarios", help="list benchmark scenarios and their objectives")

    score = sub.add_parser("score", help="score one or more session folders")
    score.add_argument("sessions", nargs="+", type=Path, help="data/sessions/<session_id> folders")
    score.add_argument("--scenario", help="scenario id (default: manifest.json benchmark.scenario)")
    score.add_argument(
        "--active",
        help="seeded bugs active in the app: all | none | BUG-001,BUG-003 "
        "(default: manifest.json benchmark.active_bugs, else all)",
    )
    score.add_argument("--json", action="store_true", help="print machine-readable JSON")

    args = parser.parse_args(argv)
    try:
        spec = load_spec(args.spec)
        if args.command == "scenarios":
            for s in spec.scenarios.values():
                print(f"{s.id}  [{', '.join(s.bugs) or 'control'}]\n  {s.objective}\n")
            return 0

        results = [
            score_session(path, spec, scenario_id=args.scenario, active=args.active)
            for path in args.sessions
        ]
    except SpecError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    summary = summarize(results)
    if args.json:
        print(json.dumps({"summary": summary, "runs": [r.to_dict() for r in results]}, indent=2))
    else:
        for r in results:
            _print_run(r)
        _print_summary(summary)
    return 0


def _print_run(r: RunResult) -> None:
    mark = "ok " if r.outcome_correct and not r.missed and not r.false_positives else "FAIL"
    print(f"[{mark}] {r.scenario}  ({r.session})")
    print(f"       outcome   {r.outcome} (expected {r.expected_outcome})")
    print(f"       found     {', '.join(r.found) or '-'}")
    print(f"       missed    {', '.join(r.missed) or '-'}")
    for bug in r.reported:
        if not bug.label.startswith("expected:"):
            print(f"       reported  {bug.label:<22} {bug.title}")
    m = r.metrics
    extras = [f"steps={m['steps_total']}"]
    if m.get("duration_ms") is not None:
        extras.append(f"duration={m['duration_ms'] / 1000:.1f}s")
    if "model_calls" in m:
        extras.append(f"model_calls={m['model_calls']} invalid={m['invalid_model_calls']}")
    print(f"       metrics   {' '.join(extras)}\n")


def _print_summary(s: dict) -> None:
    def fmt(v: object) -> str:
        return "-" if v is None else str(v)

    print("SUMMARY")
    print(f"  runs               {s['runs']}")
    print(f"  outcome accuracy   {fmt(s['outcome_accuracy'])}")
    print(f"  recall             {fmt(s['recall'])}  (found {s['true_positives']}, missed {s['false_negatives']})")
    print(f"  precision          {fmt(s['precision'])}  (false positives {s['false_positives']})")
    print(f"  duplicates         {s['duplicates']}")
    print(f"  model calls        {fmt(s['model_calls'])}  (invalid rate {fmt(s['invalid_model_call_rate'])})")
    print(f"  mean duration ms   {fmt(s['mean_duration_ms'])}")
