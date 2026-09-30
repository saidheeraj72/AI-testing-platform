"""Run benchmark scenarios end to end and score them.

    uv run python -m benchmark.run                      # every scenario
    uv run python -m benchmark.run customer-create cart-total --repeat 3

Start the seeded app first (npm run dev, or npm run dev:clean). For each run the
app is reset, the active bugs are read from /__bench/config, the agent runs the
scenario's objective, and the session is scored against expected-results.yaml.
Risky actions are allowed and the browser is headless unless --headed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.request
from pathlib import Path

from app.config import ConfigError, load_settings
from app.run import run_session
from benchmark.evaluator.cli import _print_run, _print_summary
from benchmark.evaluator.scoring import RunResult, score_session, summarize
from benchmark.evaluator.spec import SpecError, load_spec


def _bench(app_url: str, path: str, method: str = "GET"):
    with urllib.request.urlopen(urllib.request.Request(f"{app_url}{path}", method=method), timeout=10) as r:
        body = r.read()
    return json.loads(body) if body else None


def _quiet(event: dict) -> None:
    if event["type"] == "step_finished":
        print(f"    step {event['step']}: {event['status']} {event['reason']}")
    elif event["type"] == "replanned":
        print(f"    replanned after step {event['after_step']}")


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m benchmark.run", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenarios", nargs="*", help="scenario ids (default: all)")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--app-url", help="seeded app URL (default: from expected-results.yaml)")
    parser.add_argument("--config", type=Path, help="settings file (default: ai-tester.toml)")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--json", type=Path, help="also write the scores to this file")
    args = parser.parse_args(argv)

    try:
        spec = load_spec()
        settings = load_settings(args.config)
        scenarios = [spec.scenario(s) for s in args.scenarios] or list(spec.scenarios.values())
    except (SpecError, ConfigError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    app_url = args.app_url or spec.app_url
    settings.browser.headless = not args.headed
    settings.safety.risky_actions = "allow"

    try:
        active = _bench(app_url, "/__bench/config")["bugs"]
    except OSError as e:
        print(f"error: seeded app not reachable at {app_url} ({e}). Start it with npm run dev.", file=sys.stderr)
        return 2
    print(f"Model: {settings.model.executor.name} | active bugs: {', '.join(active) or 'none'}\n")

    results: list[RunResult] = []
    for scenario in scenarios:
        for i in range(args.repeat):
            _bench(app_url, "/__bench/reset", "POST")
            print(f"▶ {scenario.id}" + (f" (run {i + 1}/{args.repeat})" if args.repeat > 1 else ""))
            storage, _ = await run_session(
                url=app_url, objective=scenario.objective, settings=settings, on_event=_quiet,
                manifest_extra={"benchmark": {"scenario": scenario.id, "active_bugs": active}},
            )
            result = score_session(storage.root, spec)
            results.append(result)
            _print_run(result)

    summary = summarize(results)
    _print_summary(summary)
    if args.json:
        args.json.write_text(json.dumps({"summary": summary, "runs": [r.to_dict() for r in results]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
