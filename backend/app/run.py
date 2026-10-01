"""Run one test session from the command line.

    uv run python -m app.run --url http://localhost:3000 \\
        --objective "Create a customer and verify it appears in the customer list"

The model is configured in ai-tester.toml. Results go to data/sessions/<id>/.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.agent.agent import AgentResult, AskUserFn, ConfirmFn, EventSink, TestAgent
from app.agent.budgets import SessionBudget
from app.agent.test_data import TestData
from app.agent.budgets import BudgetExceeded
from app.analysis.bug_analyzer import BugAnalyzer
from app.analysis.report_builder import build_report, find_bugs
from app.browser.observation import ObservationLimits
from app.browser.profile import ProfileInUseError, profile_dir_for
from app.browser.session import BrowserConfig, BrowserLaunchError, BrowserSession
from app.agent.control import SessionControl
from app.agent.explorer import DEFAULT_OBJECTIVE, Explorer
from app.config import ConfigError, ModelSettings, Settings, load_settings
from app.model.client import StructuredModel
from app.model.provider import ModelProvider, create_provider
from app.safety.domain_scope import DomainScope, ScopeError
from app.schemas.plan import Criterion, StepStatus
from app.storage.manager import SessionStorage


async def run_session(
    *,
    url: str,
    objective: str,
    settings: Settings,
    project: str | None = None,
    allow_domains: list[str] | tuple[str, ...] = (),
    sessions_dir: Path | None = None,
    on_event: EventSink = lambda e: None,
    confirm: ConfirmFn | None = None,
    ask_user: AskUserFn | None = None,
    manifest_extra: dict[str, Any] | None = None,
    storage: SessionStorage | None = None,
    control: SessionControl | None = None,
    mode: str = "objective",
    provider_factory: Callable[[ModelSettings], ModelProvider] = create_provider,
) -> tuple[SessionStorage, dict[str, Any]]:
    """Run a whole session. Always writes report.json and the trace, even when cancelled or failing.

    `storage` lets a caller (the API) create the session folder first; `provider_factory`
    lets tests substitute the model.
    """
    scope = DomainScope.from_target(url, allow_domains)
    storage = storage or SessionStorage.create(sessions_dir)
    control = control or SessionControl()
    test_data = TestData.generate()
    budget = SessionBudget(settings.agent, paused_seconds=control.paused_seconds)
    started = time.monotonic()
    storage.update_manifest(
        objective=objective,
        project=project,
        mode=mode,
        model={"planner": settings.model.planner.name, "executor": settings.model.executor.name},
        test_data_tag=test_data.tag,
        **(manifest_extra or {}),
    )

    providers = _providers(settings, provider_factory)
    planner_provider, executor_provider, analyzer_provider = (
        providers["planner"], providers["executor"], providers["analyzer"]
    )
    browser = BrowserSession(scope, storage, BrowserConfig(
        headless=settings.browser.headless,
        channel="chrome" if settings.browser.channel == "chrome" else None,
        profile_dir=profile_dir_for(project) if project else None,
        limits=ObservationLimits(max_chars=settings.agent.observation_max_chars),
    ))
    agent = TestAgent(
        objective=objective,
        browser=browser,
        planner_model=StructuredModel(planner_provider, storage, before_call=budget.before_model_call),
        executor_model=StructuredModel(executor_provider, storage, before_call=budget.before_model_call),
        settings=settings,
        budget=budget,
        test_data=test_data,
        on_event=on_event,
        confirm=confirm,
        control=control,
        ask_user=ask_user,
    )

    explorer = Explorer(
        notes=objective if objective.strip() != DEFAULT_OBJECTIVE else "", browser=browser,
        planner_model=agent.planner.model, executor_model=agent.executor, settings=settings, budget=budget,
        test_data=test_data, on_event=on_event, confirm=confirm, ask_user=ask_user, control=control,
    ) if mode == "explore" else None
    runner = explorer or agent

    on_event({"type": "session_started", "session_id": storage.session_id, "url": url, "objective": objective,
              "mode": mode})
    result = AgentResult(outcome="FAILED", reason="did not start", steps=[])
    exploration_extra: dict[str, Any] = {}
    cancelled = False
    try:
        await browser.start()
        if explorer:
            exploration = await explorer.run()
            result, exploration_extra = exploration.agent, exploration.report_extra()
        else:
            result = await agent.run()
    except (BrowserLaunchError, ProfileInUseError) as e:
        result = AgentResult(outcome="FAILED", reason=str(e), steps=[])
    except asyncio.CancelledError:
        cancelled = True
        runner.abort(StepStatus.COULD_NOT_VERIFY, "cancelled by the user")
        steps = [s for a in explorer.agents for s in a.steps] if explorer else agent.steps
        actions = [x for a in explorer.agents for x in a.actions] if explorer else agent.actions
        result = AgentResult(outcome="CANCELLED", reason="cancelled by the user", steps=steps, actions=actions)
        raise
    finally:
        await browser.stop()
        # A cancelled session is described by rules only, so stopping stays fast.
        analyzer_model = None
        if not cancelled and settings.agent.max_analyzer_calls:
            analyzer_model = StructuredModel(analyzer_provider, storage,
                                             before_call=_call_limit(settings.agent.max_analyzer_calls))
        bugs, unconfirmed, baseline_ignored = await find_bugs(
            result=result, browser=browser, analyzer=BugAnalyzer(analyzer_model, objective), on_event=on_event,
        )
        for provider in set(providers.values()):
            await provider.close()
        duration_ms = int((time.monotonic() - started) * 1000)
        report = build_report(
            session_id=storage.session_id, objective=objective, result=result, browser=browser,
            bugs=bugs, unconfirmed=unconfirmed, baseline_ignored=baseline_ignored,
            extra={"mode": mode, "test_data": test_data.model_dump(), "model_calls": budget.model_calls,
                   "duration_ms": duration_ms, **exploration_extra},
        )
        storage.write_json(storage.paths.report, report)
        storage.update_manifest(outcome=report["outcome"], duration_ms=duration_ms,
                                model_calls=budget.model_calls, bugs=len(report["bugs"]))
        on_event({"type": "session_completed", "outcome": report["outcome"], "reason": result.reason,
                  "report": str(storage.paths.report)})
    return storage, report


def _providers(settings: Settings, factory: Callable[[ModelSettings], ModelProvider]) -> dict[str, ModelProvider]:
    """One provider per distinct model configuration, shared by roles that use the same one."""
    by_config: dict[str, ModelProvider] = {}
    roles = {}
    for role in ("planner", "executor", "analyzer"):
        cfg = getattr(settings.model, role)
        key = cfg.model_dump_json()
        if key not in by_config:
            by_config[key] = factory(cfg)
        roles[role] = by_config[key]
    return roles


def _call_limit(limit: int):
    calls = 0

    def before_call() -> None:
        nonlocal calls
        if calls >= limit:
            raise BudgetExceeded(f"analyzer call limit of {limit} reached")
        calls += 1

    return before_call


# ---------------------------------------------------------------------- CLI output


def print_event(event: dict[str, Any]) -> None:
    kind = event["type"]
    if kind == "session_started":
        print(f"Session {event['session_id']}\nTarget  {event['url']}\nObjective  {event['objective']}\n")
    elif kind in ("plan_created", "replanned"):
        if kind == "replanned":
            print(f"\n  ↻ Replanning after step {event['after_step']}: {event['reason']}")
        print("Plan:")
        for s in event["steps"]:
            checks = "; ".join(_criterion_text(c) for c in s["criteria"]) or "(no automatic check)"
            print(f"  {s['sequence']}. {s['goal']}\n       ✓ when: {checks}")
        print()
    elif kind == "step_started":
        print(f"▶ Step {event['step']}: {event['goal']}")
    elif kind == "decision" and event["action"] not in ("verify",):
        print(f"    · {event['reasoning']}")
    elif kind == "action_completed":
        target = f" {event['target']['role']} \"{event['target']['name']}\"" if event["target"] else ""
        mark = "✓" if event["ok"] else "✗"
        err = f"  {event['error']}: {event['message']}" if not event["ok"] else ""
        print(f"    {mark} {event['action']}{target}{err}")
    elif kind == "step_finished":
        print(f"  = {event['status']}: {event['reason']}\n")
    elif kind == "page_explored":
        print(f"  explored {event['count']}: {event['url']} ({event['title']})")
    elif kind == "workflow_started":
        print(f"\n=== Workflow: {event['title']}\n    {event['objective']}")
    elif kind == "waiting_for_user":
        print(f"    ⏸ {event['message']}")
    elif kind == "bug_confirmed":
        print(f"  ! {event['id']} [{event['severity']}] {event['title']}")
    elif kind == "session_completed":
        print(f"RESULT: {event['outcome']}" + (f" ({event['reason']})" if event["reason"] else ""))
        print(f"Report: {event['report']}")


def _criterion_text(c: dict[str, Any]) -> str:
    crit = Criterion(**c)
    return crit.describe() + ("" if crit.grounded else " [guess]")


async def terminal_ask_user(kind: str, message: str) -> bool:
    if not sys.stdin.isatty():
        print(f"    ! Needs a person ({kind}), but there is no terminal to ask: {message}")
        return False
    answer = await asyncio.to_thread(input, f"    ? {message}\n      Press Enter to continue, or type 'skip': ")
    return answer.strip().lower() != "skip"


async def terminal_confirm(action: str) -> bool:
    if not sys.stdin.isatty():
        print(f"    ! Risky action refused (no terminal to confirm): {action}")
        return False
    answer = await asyncio.to_thread(input, f"    ? Allow risky action: {action}? [y/N] ")
    return answer.strip().lower() in ("y", "yes")


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.run", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", required=True)
    parser.add_argument("--objective", help="what to test (with --explore: optional notes, e.g. credentials)")
    parser.add_argument("--explore", action="store_true", help="explore the site and find bugs on its own")
    parser.add_argument("--project", help="persistent browser profile id (keeps logins)")
    parser.add_argument("--allow-domain", action="append", default=[], help="extra allowed domain, e.g. SSO")
    parser.add_argument("--config", type=Path, help="settings file (default: ai-tester.toml)")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--allow-risky", action="store_true", help="do not ask before risky actions")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(args.config)
        DomainScope.from_target(args.url)
    except (ConfigError, ScopeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.headless:
        settings.browser.headless = True
    if args.allow_risky:
        settings.safety.risky_actions = "allow"
    if not args.objective and not args.explore:
        parser.error("--objective is required unless --explore is given")

    _, report = await run_session(
        url=args.url, objective=args.objective or DEFAULT_OBJECTIVE, settings=settings, project=args.project,
        mode="explore" if args.explore else "objective",
        allow_domains=args.allow_domain, on_event=print_event, confirm=terminal_confirm,
        ask_user=terminal_ask_user,
    )
    for bug in report["bugs"]:
        print(f"\n{bug['id']} [{bug['severity']}] {bug['title']}\n  expected: {bug['expected']}\n"
              f"  actual:   {bug['actual']}\n  occurrences: {len(bug['occurrences'])}")
    return 0 if report["outcome"] in ("PASS", "BUGS_FOUND") else 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(130)
