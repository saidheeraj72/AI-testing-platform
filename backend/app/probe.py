"""Drive a BrowserSession by hand, with the same actions the agent will use.

    uv run python -m app.probe http://localhost:3000
    uv run python -m app.probe https://staging.example.com --project crm-staging

--project uses a persistent profile (data/browser_profiles/<project>/), so a
login done by hand in the visible browser is kept for later sessions.
Everything is recorded to data/sessions/<id>/ like a real run.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.browser.profile import ProfileInUseError, profile_dir_for
from app.browser.session import BrowserClosedError, BrowserConfig, BrowserLaunchError, BrowserSession
from app.safety.domain_scope import DomainScope, ScopeError
from app.schemas.action import ActionResult
from app.storage.manager import SessionStorage

HELP = """\
  o                         observe (print the page outline)
  goto <url>                navigate (absolute or relative to the current page)
  click <ref>               click an element, e.g. click e12
  type <ref> <text>         replace the field's text
  enter <ref> <text>        type, then press Enter
  select <ref> <option>     choose an option by label or value
  press <key> [ref]         e.g. press Escape
  scroll up|down|top|bottom | scroll <ref>
  back                      browser back
  wait <seconds>
  shot                      save a screenshot
  net                       failed / error requests so far
  console                   console errors and page exceptions so far
  help | quit"""


async def repl(session: BrowserSession) -> None:
    print(f"Session folder: {session.storage.root}\nScope: {session.scope.describe()}\nType 'help' for commands.\n")
    await session.navigate(session.scope.target_url)
    print_observation(await session.observe())

    while True:
        try:
            line = (await asyncio.to_thread(input, "> ")).strip()
        except EOFError:
            return
        if not line:
            continue
        cmd, _, rest = line.partition(" ")
        args = rest.split(maxsplit=1)
        try:
            if cmd in ("quit", "exit", "q"):
                return
            if cmd == "help":
                print(HELP)
                continue
            if cmd == "o":
                print_observation(await session.observe())
                continue
            if cmd == "shot":
                print(f"saved {await session.screenshot()}")
                continue
            if cmd == "net":
                for e in session.network.events:
                    if e.is_error:
                        status = e.status or e.failure
                        print(f"  #{e.action_seq:<3} {e.method} {e.url} -> {status}{'' if e.first_party else '  (third-party)'}")
                continue
            if cmd == "console":
                for e in session.console.events:
                    print(f"  #{e.action_seq:<3} [{e.kind}/{e.level}] {e.text}")
                continue

            result = await run_action(session, cmd, args)
            if result is None:
                print("Unknown or incomplete command. Type 'help'.")
                continue
            print_result(result)
            print_observation(await session.observe())
        except BrowserClosedError:
            print("The browser was closed.")
            return


async def run_action(session: BrowserSession, cmd: str, args: list[str]) -> ActionResult | None:
    match cmd, args:
        case "goto", [url, *_]:
            return await session.navigate(url)
        case "click", [ref]:
            return await session.click(ref)
        case "type", [ref, text]:
            return await session.type(ref, text)
        case "enter", [ref, text]:
            return await session.type(ref, text, submit=True)
        case "select", [ref, option]:
            return await session.select(ref, option)
        case "press", [key]:
            return await session.press(key)
        case "press", [key, ref]:
            return await session.press(key, ref)
        case "scroll", [where]:
            if where in ("up", "down", "top", "bottom"):
                return await session.scroll(where)
            return await session.scroll(ref=where)
        case "back", []:
            return await session.go_back()
        case "wait", [seconds]:
            return await session.wait(float(seconds))
    return None


def print_result(r: ActionResult) -> None:
    target = f" {r.target.role} \"{r.target.name}\"" if r.target else ""
    status = "ok" if r.ok else f"FAILED {r.error}: {r.message}"
    extras = []
    if r.navigated:
        extras.append(f"-> {r.url_after}")
    if r.http_status:
        extras.append(f"HTTP {r.http_status}")
    if not r.settled:
        extras.append("(page still busy)")
    print(f"#{r.sequence} {r.action}{target}: {status} {' '.join(extras)} [{r.duration_ms} ms]")


def print_observation(obs) -> None:
    print(f"\n--- {obs.title} | {obs.url} | scroll {obs.viewport.scroll_y}/{obs.viewport.page_height}px")
    for notice in obs.notices:
        print(f"!!! {notice}")
    print(obs.text)
    print()


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.probe", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url", help="target URL; also defines the navigation scope")
    parser.add_argument("--project", help="use a persistent browser profile for this project id")
    parser.add_argument("--allow-domain", action="append", default=[], help="extra allowed domain, e.g. SSO")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--bundled-chromium", action="store_true", help="use Playwright's Chromium, not Chrome")
    args = parser.parse_args(argv)

    try:
        scope = DomainScope.from_target(args.url, args.allow_domain)
    except ScopeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    config = BrowserConfig(
        headless=args.headless,
        channel=None if args.bundled_chromium else "chrome",
        profile_dir=profile_dir_for(args.project) if args.project else None,
    )
    session = BrowserSession(scope, SessionStorage.create(), config)
    try:
        await session.start()
        await repl(session)
    except (BrowserLaunchError, ProfileInUseError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        await session.stop()
        print(f"Saved to {session.storage.root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
