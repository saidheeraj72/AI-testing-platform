"""BrowserSession: the only way the agent touches the browser.

It owns the Playwright lifecycle and centralises what every action needs:
domain scope, element re-resolution, settling, evidence capture and logging.
The agent never receives a Playwright Page.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import (
    Browser,
    BrowserContext,
    Dialog,
    Error as PlaywrightError,
    Frame,
    Locator,
    Page,
    Playwright,
    Route,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from app.browser.console import ConsoleRecorder
from app.browser.elements import ResolutionError, resolve
from app.browser.network import NetworkRecorder
from app.browser.observation import ObservationLimits, build_observation
from app.browser.profile import ProfileLock
from app.browser.snapshot import Node, parse_snapshot
from app.safety.domain_scope import DomainScope
from app.safety.secrets import REDACTED, is_secret_field, mask_snapshot
from app.schemas.action import ActionError, ActionResult, Target
from app.schemas.observation import Element, Observation, Viewport
from app.storage.manager import SessionStorage

log = logging.getLogger(__name__)

MAX_WAIT_SECONDS = 10


class BrowserLaunchError(RuntimeError):
    pass


class BrowserClosedError(RuntimeError):
    pass


@dataclass(frozen=True)
class BrowserConfig:
    headless: bool = False
    channel: str | None = "chrome"  # installed Chrome; falls back to Playwright's Chromium
    profile_dir: Path | None = None  # persistent profile; None = fresh throwaway profile
    viewport_width: int = 1280
    viewport_height: int = 800
    action_timeout_ms: int = 10_000
    navigation_timeout_ms: int = 30_000
    settle_timeout_ms: int = 5_000
    settle_quiet_ms: int = 400
    trace: bool = True
    limits: ObservationLimits = field(default_factory=ObservationLimits)


class _Failure(Exception):
    def __init__(self, code: ActionError, message: str):
        super().__init__(message)
        self.code = code


class BrowserSession:
    def __init__(self, scope: DomainScope, storage: SessionStorage, config: BrowserConfig = BrowserConfig()):
        self.scope = scope
        self.storage = storage
        self.config = config
        self.page: Page | None = None
        self.browser_channel: str | None = None
        self.blocked_navigations: list[str] = []
        self._last_in_scope_url: str | None = None

        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._lock: ProfileLock | None = None
        self._attached: set[Page] = set()
        self._tracing = False
        self._started = False
        self._stopped = False
        self._context_closed = False

        self._action_seq = 0
        self._observation_seq = 0
        self._screenshot_seq = 0
        self._last_observation: Observation | None = None
        self.last_nodes: list[Node] = []  # parsed snapshot behind the latest observation
        self._elements_by_ref: dict[str, Element] = {}
        self._notices: list[str] = []

        self.network = NetworkRecorder(scope, storage, lambda: self._action_seq)
        self.console = ConsoleRecorder(storage, lambda: self._action_seq)

    # ------------------------------------------------------------------ lifecycle

    async def __aenter__(self) -> BrowserSession:
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.stop()

    async def start(self) -> None:
        if self._started:
            raise RuntimeError("BrowserSession already started")
        self._started = True
        try:
            self._playwright = await async_playwright().start()
            await self._launch()
            ctx = self._context
            assert ctx is not None
            ctx.set_default_timeout(self.config.action_timeout_ms)
            ctx.set_default_navigation_timeout(self.config.navigation_timeout_ms)
            ctx.on("close", lambda _: setattr(self, "_context_closed", True))
            await ctx.route(lambda url: not self.scope.allows(url), self._guard_navigation)
            self.network.attach(ctx)
            if self.config.trace:
                await ctx.tracing.start(screenshots=True, snapshots=True, sources=False)
                self._tracing = True

            self.page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            self._attach_page(self.page)
            ctx.on("page", self._on_new_page)
        except BaseException:
            await self.stop()
            raise

        self.storage.update_manifest(
            target_url=self.scope.target_url,
            scope=self.scope.describe(),
            browser={
                "channel": self.browser_channel,
                "headless": self.config.headless,
                "persistent_profile": str(self.config.profile_dir) if self.config.profile_dir else None,
            },
            started_at=_now(),
        )

    async def stop(self) -> None:
        """Save evidence and close everything. Safe to call more than once and after failures."""
        if self._stopped:
            return
        self._stopped = True
        steps: list[tuple[str, Callable[[], Awaitable[None]]]] = [
            ("trace", self._stop_trace),
            ("network", self.network.close),
            ("context", self._close_context),
            ("playwright", self._stop_playwright),
        ]
        for name, step in steps:
            try:
                await step()
            except Exception as e:
                log.warning("stop step %s failed: %s", name, e)
        if self._lock:
            self._lock.release()
        self.storage.update_manifest(
            finished_at=_now(),
            trace=self.storage.relative(self.storage.paths.trace) if self.storage.paths.trace.exists() else None,
        )

    async def _launch(self) -> None:
        assert self._playwright is not None
        cfg = self.config
        viewport = {"width": cfg.viewport_width, "height": cfg.viewport_height}
        if cfg.profile_dir:
            cfg.profile_dir.mkdir(parents=True, exist_ok=True)
            self._lock = ProfileLock(cfg.profile_dir)
            self._lock.acquire()

        errors: list[str] = []
        for channel in [cfg.channel, None] if cfg.channel else [None]:
            try:
                if cfg.profile_dir:
                    self._context = await self._playwright.chromium.launch_persistent_context(
                        str(cfg.profile_dir), channel=channel, headless=cfg.headless, viewport=viewport,
                    )
                else:
                    self._browser = await self._playwright.chromium.launch(channel=channel, headless=cfg.headless)
                    self._context = await self._browser.new_context(viewport=viewport)
                self.browser_channel = channel or "chromium"
                return
            except PlaywrightError as e:
                errors.append(f"{channel or 'bundled chromium'}: {_first_line(e)}")
        raise BrowserLaunchError(
            "Could not start a browser (" + "; ".join(errors) + "). "
            "Install Google Chrome, or run: uv run playwright install chromium"
        )

    async def _stop_trace(self) -> None:
        if self._tracing and self._context and not self._context_closed:
            self._tracing = False
            await self._context.tracing.stop(path=str(self.storage.paths.trace))

    async def _close_context(self) -> None:
        if self._context and not self._context_closed:
            await self._context.close()
        if self._browser:
            await self._browser.close()

    async def _stop_playwright(self) -> None:
        if self._playwright:
            await self._playwright.stop()

    # ------------------------------------------------------------------ observation

    async def observe(self, *, screenshot: bool = False) -> Observation:
        self._require_running()
        if not self._on_allowed_page():
            await self._return_to_scope()
        self._observation_seq += 1
        notices, self._notices = self._notices, []
        observation, raw, self.last_nodes = await self._snapshot(self._observation_seq, notices)
        if screenshot:
            observation.screenshot_path = await self.screenshot()
        self._last_observation = observation
        self._elements_by_ref = {e.ref: e for e in observation.elements}

        path = self.storage.paths.observation(observation.sequence)
        self.storage.write_json(path, observation)
        path.with_suffix(".snapshot.yaml").write_text(mask_snapshot(raw))
        return observation

    async def screenshot(self, *, full_page: bool = False) -> str:
        self._require_running()
        self._screenshot_seq += 1
        path = self.storage.paths.screenshot(self._screenshot_seq)
        await self.page.screenshot(path=str(path), full_page=full_page)
        return self.storage.relative(path)

    @property
    def action_count(self) -> int:
        return self._action_seq

    async def _snapshot(self, sequence: int, notices: list[str]) -> tuple[Observation, str, list[Node]]:
        page = self.page
        last_error: Exception | None = None
        for _ in range(3):
            try:
                raw = await page.aria_snapshot(mode="ai", boxes=True)
                metrics = await page.evaluate(
                    "() => ({w: innerWidth, h: innerHeight, y: Math.round(scrollY),"
                    " ph: document.documentElement.scrollHeight})"
                )
                nodes = parse_snapshot(raw)
                observation = build_observation(
                    nodes,
                    sequence=sequence,
                    url=page.url,
                    title=await page.title(),
                    viewport=Viewport(width=metrics["w"], height=metrics["h"],
                                      scroll_y=metrics["y"], page_height=metrics["ph"]),
                    limits=self.config.limits,
                    notices=notices,
                )
                return observation, raw, nodes
            except PlaywrightError as e:  # usually "execution context destroyed" mid-navigation
                last_error = e
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=5_000)
                except PlaywrightError:
                    await asyncio.sleep(0.3)
        raise last_error  # type: ignore[misc]

    # ------------------------------------------------------------------ actions

    async def navigate(self, url: str) -> ActionResult:
        async def body(result: ActionResult) -> None:
            base = self.page.url if self.page.url.startswith("http") else self.scope.target_url
            absolute = urljoin(base, url)
            if not self.scope.allows(absolute):
                self._blocked(absolute)
                raise _Failure(ActionError.BLOCKED_NAVIGATION, self._blocked_message(absolute))
            try:
                response = await self.page.goto(absolute, wait_until="domcontentloaded")
            except PlaywrightTimeoutError:
                raise
            except PlaywrightError as e:
                raise _Failure(ActionError.NAVIGATION_FAILED, _first_line(e)) from None
            result.http_status = response.status if response else None

        return await self._run("navigate", {"url": url}, body)

    async def click(self, ref: str) -> ActionResult:
        async def body(result: ActionResult) -> None:
            await (await self._locate(ref, result)).click()

        return await self._run("click", {"ref": ref}, body)

    async def type(self, ref: str, text: str, *, clear: bool = True, submit: bool = False) -> ActionResult:
        target = self._elements_by_ref.get(ref)
        secret = target is not None and is_secret_field(target.name)
        logged = {"ref": ref, "text": REDACTED if secret else text, "clear": clear, "submit": submit}

        async def body(result: ActionResult) -> None:
            locator = await self._locate(ref, result)
            if clear:
                await locator.fill(text)
            else:
                await locator.press_sequentially(text)
            if submit:
                await locator.press("Enter")

        return await self._run("type", logged, body)

    async def select(self, ref: str, option: str) -> ActionResult:
        async def body(result: ActionResult) -> None:
            locator = await self._locate(ref, result)
            try:
                await locator.select_option(label=option)
            except PlaywrightError:
                await locator.select_option(value=option)

        return await self._run("select", {"ref": ref, "option": option}, body)

    async def press(self, key: str, ref: str | None = None) -> ActionResult:
        async def body(result: ActionResult) -> None:
            if ref:
                await (await self._locate(ref, result)).press(key)
            else:
                await self.page.keyboard.press(key)

        return await self._run("press", {"key": key, "ref": ref}, body)

    async def scroll(self, direction: str = "down", ref: str | None = None) -> ActionResult:
        async def body(result: ActionResult) -> None:
            if ref:
                await (await self._locate(ref, result)).scroll_into_view_if_needed()
                return
            if direction not in ("up", "down", "top", "bottom"):
                raise _Failure(ActionError.INVALID_ARGUMENT, "direction must be up, down, top or bottom")
            if direction in ("top", "bottom"):
                await self.page.evaluate(
                    "(top) => window.scrollTo(0, top ? 0 : document.documentElement.scrollHeight)",
                    direction == "top",
                )
                return
            await self.page.mouse.move(self.config.viewport_width / 2, self.config.viewport_height / 2)
            delta = self.config.viewport_height * 0.8
            await self.page.mouse.wheel(0, delta if direction == "down" else -delta)

        return await self._run("scroll", {"direction": direction, "ref": ref}, body)

    async def go_back(self) -> ActionResult:
        async def body(result: ActionResult) -> None:
            response = await self.page.go_back(wait_until="domcontentloaded")
            if response is None and result.url_before == self.page.url:
                raise _Failure(ActionError.NAVIGATION_FAILED, "There is no previous page in history.")

        return await self._run("go_back", {}, body)

    async def wait(self, seconds: float = 1.0) -> ActionResult:
        async def body(result: ActionResult) -> None:
            if not 0 <= seconds <= MAX_WAIT_SECONDS:
                raise _Failure(ActionError.INVALID_ARGUMENT, f"seconds must be between 0 and {MAX_WAIT_SECONDS}")
            await asyncio.sleep(seconds)

        return await self._run("wait", {"seconds": seconds}, body)

    async def _run(self, action: str, arguments: dict, body: Callable[[ActionResult], Awaitable[None]]) -> ActionResult:
        self._require_running()
        self._action_seq += 1
        started = time.monotonic()
        blocked_before = len(self.blocked_navigations)
        result = ActionResult(
            sequence=self._action_seq, action=action, arguments=arguments, ok=True,
            url_before=self.page.url, url_after=self.page.url,
        )
        try:
            await body(result)
        except (_Failure, ResolutionError) as e:
            result.ok, result.error, result.message = False, e.code, str(e)
        except PlaywrightTimeoutError as e:
            result.ok, result.error, result.message = False, ActionError.TIMEOUT, _first_line(e)
        except PlaywrightError as e:
            if self._context_closed:
                raise BrowserClosedError("The browser was closed") from None
            result.ok, result.error, result.message = False, ActionError.ACTION_FAILED, _first_line(e)

        result.settled = await self._settle()
        blocked = self.blocked_navigations[blocked_before:]
        if blocked:  # also covers a goto that failed because its redirect was blocked
            result.ok, result.error = False, ActionError.BLOCKED_NAVIGATION
            result.message = self._blocked_message(blocked[0])
            await self._return_to_scope()
        result.url_after = self.page.url
        result.duration_ms = int((time.monotonic() - started) * 1000)
        self.storage.append_jsonl(self.storage.paths.actions, result)
        return result

    async def _locate(self, ref: str, result: ActionResult) -> Locator:
        if self._last_observation is None:
            raise _Failure(ActionError.UNKNOWN_REF, "Observe the page before acting on elements.")
        target = self._elements_by_ref.get(ref)
        if target is None:
            raise _Failure(ActionError.UNKNOWN_REF, f"{ref} is not in the latest observation.")
        result.target = Target(role=target.role, name=target.name)

        fresh, _, _ = await self._snapshot(self._observation_seq, [])
        resolution = resolve(target, self._last_observation.elements, fresh.elements)
        result.resolved_ref = resolution.ref
        return self.page.locator(f"aria-ref={resolution.ref}")

    async def _settle(self) -> bool:
        """Wait for navigation and first-party requests to go quiet. False if the timeout hit first."""
        if self._context_closed:
            return False
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.config.settle_timeout_ms / 1000
        quiet = self.config.settle_quiet_ms / 1000
        await asyncio.sleep(0.1)
        try:
            await self.page.wait_for_load_state(
                "domcontentloaded", timeout=max(1, (deadline - loop.time()) * 1000)
            )
        except PlaywrightError:
            pass
        quiet_since: float | None = None
        while loop.time() < deadline:
            if self.network.inflight == 0:
                quiet_since = quiet_since or loop.time()
                if loop.time() - quiet_since >= quiet:
                    return True
            else:
                quiet_since = None
            await asyncio.sleep(0.05)
        return False

    # ------------------------------------------------------------------ browser events

    async def _guard_navigation(self, route: Route) -> None:
        """Called only for out-of-scope URLs. Blocks top-level navigations, lets subresources through."""
        request = route.request
        if request.is_navigation_request() and request.frame.parent_frame is None:
            self._blocked(request.url)
            await route.abort("blockedbyclient")
            page = request.frame.page
            if page is not self.page and page.url == "about:blank":
                await page.close()  # a popup that only existed to leave the scope
        else:
            await route.continue_()

    def _on_frame_navigated(self, frame: Frame) -> None:
        """Second line of defence: server redirects are not routed, so catch them after commit.

        The redirected request has already been sent at this point; the page
        is taken back before the agent can observe or act on it.
        """
        if frame.parent_frame is not None or frame.url.startswith("chrome-error://"):
            return
        if self.scope.allows(frame.url):
            if frame.url != "about:blank":
                self._last_in_scope_url = frame.url
        elif frame.url not in self.blocked_navigations[-1:]:
            self._blocked(frame.url)

    def _on_allowed_page(self) -> bool:
        url = self.page.url
        return self.scope.allows(url) and not url.startswith("chrome-error://")

    async def _return_to_scope(self) -> None:
        """After a blocked navigation: go back, or reload the last in-scope URL."""
        if self._on_allowed_page():
            return
        try:
            await self.page.go_back(wait_until="domcontentloaded")
        except PlaywrightError:
            pass
        if not self._on_allowed_page():
            await self.page.goto(self._last_in_scope_url or "about:blank", wait_until="domcontentloaded")

    def _blocked(self, url: str) -> None:
        self.blocked_navigations.append(url)
        self._notices.append(self._blocked_message(url))
        self._browser_event("navigation_blocked", url=url)

    def _blocked_message(self, url: str) -> str:
        return f"Navigation to {url} was blocked: outside the test scope ({self.scope.describe()})."

    def _attach_page(self, page: Page) -> None:
        if page in self._attached:
            return
        self._attached.add(page)
        self.console.attach(page)
        page.on("framenavigated", self._on_frame_navigated)
        page.on("dialog", self._on_dialog)
        page.on("close", self._on_page_close)

    def _on_new_page(self, page: Page) -> None:
        if page in self._attached:
            return
        self._attach_page(page)
        self.page = page
        self._notices.append("A new tab opened and is now the active page.")
        self._browser_event("tab_opened", url=page.url)

    def _on_page_close(self, page: Page) -> None:
        self._attached.discard(page)
        if page is self.page and self._context and self._context.pages:
            self.page = self._context.pages[-1]
            self._notices.append("The active tab closed; switched to the previous tab.")
        self._browser_event("tab_closed", url=page.url)

    async def _on_dialog(self, dialog: Dialog) -> None:
        # Never auto-confirm: a confirm() dialog may guard a destructive action.
        self._browser_event("dialog", type=dialog.type, message=dialog.message)
        if dialog.type == "beforeunload":
            await dialog.accept()
            return
        self._notices.append(f'A browser {dialog.type} dialog said "{dialog.message}". It was dismissed.')
        await dialog.dismiss()

    def _browser_event(self, kind: str, **data) -> None:
        self.storage.append_jsonl(
            self.storage.paths.browser_events,
            {"kind": kind, "action_seq": self._action_seq, "at": time.time(), **data},
        )

    def _require_running(self) -> None:
        if not self._started or self._stopped:
            raise RuntimeError("BrowserSession is not running")
        if self._context_closed:
            raise BrowserClosedError("The browser was closed")


def _first_line(error: Exception) -> str:
    return str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
