"""Records every request the browser context makes, for evidence and bug detection.

Each finished or failed request becomes one line in network/events.jsonl.
Bodies are kept only for first-party fetch/XHR calls: request bodies with
secrets redacted, and response bodies of error responses. Both are truncated.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode

from playwright.async_api import BrowserContext, Request, Response

from app.safety.domain_scope import DomainScope
from app.storage.manager import SessionStorage

log = logging.getLogger(__name__)

BODY_LIMIT = 2_000
API_TYPES = frozenset({"fetch", "xhr"})
# Requests that keep the page "busy" for settling purposes.
TRACKED_TYPES = frozenset({"fetch", "xhr", "document"})
_SECRET_KEY = re.compile(r"pass|secret|token|auth|otp|cvv|cvc|card|ssn|api[-_]?key", re.I)
REDACTED = "[redacted]"


@dataclass
class NetworkEvent:
    seq: int
    action_seq: int
    method: str
    url: str
    resource_type: str
    first_party: bool
    main_frame: bool
    started_at: float
    duration_ms: int | None = None
    status: int | None = None
    status_text: str | None = None
    failure: str | None = None
    request_body: str | None = None
    response_body: str | None = None

    @property
    def is_error(self) -> bool:
        if self.status is not None:
            return self.status >= 400
        # Chrome also reports ERR_ABORTED for cancelled requests (navigation away, AbortController).
        return self.failure is not None and "ERR_ABORTED" not in self.failure


class NetworkRecorder:
    def __init__(self, scope: DomainScope, storage: SessionStorage, action_seq: Callable[[], int]):
        self._scope = scope
        self._storage = storage
        self._action_seq = action_seq
        self._pending: dict[Request, NetworkEvent] = {}
        self._tasks: set[asyncio.Task] = set()
        self.events: list[NetworkEvent] = []
        self._seq = 0

    def attach(self, context: BrowserContext) -> None:
        context.on("request", self._on_request)
        context.on("requestfinished", self._on_finished)
        context.on("requestfailed", self._on_failed)

    @property
    def inflight(self) -> int:
        """First-party fetch/XHR/document requests still running."""
        return sum(1 for e in self._pending.values() if e.first_party and e.resource_type in TRACKED_TYPES)

    def since(self, action_seq: int) -> list[NetworkEvent]:
        return [e for e in self.events if e.action_seq >= action_seq]

    async def close(self) -> None:
        """Finish pending writes and record requests that never completed."""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        for event in list(self._pending.values()):
            event.failure = "unfinished at session end"
            self._write(event)
        self._pending.clear()

    def _on_request(self, request: Request) -> None:
        self._seq += 1
        first_party = self._scope.is_first_party(request.url)
        event = NetworkEvent(
            seq=self._seq,
            action_seq=self._action_seq(),
            method=request.method,
            url=request.url,
            resource_type=request.resource_type,
            first_party=first_party,
            main_frame=_is_main_frame(request),
            started_at=time.time(),
        )
        if first_party and request.resource_type in API_TYPES:
            event.request_body = _redact_body(request.post_data, request.headers.get("content-type", ""))
        self._pending[request] = event

    def _on_finished(self, request: Request) -> None:
        self._track(self._complete(request, failure=None))

    def _on_failed(self, request: Request) -> None:
        self._track(self._complete(request, failure=request.failure or "failed"))

    def _track(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _complete(self, request: Request, failure: str | None) -> None:
        event = self._pending.get(request)
        if event is None:
            return
        try:
            event.duration_ms = int((time.time() - event.started_at) * 1000)
            event.failure = failure
            # Chrome reports some completed requests as failed (e.g. a 204 fetch as ERR_ABORTED),
            # so look for a response either way.
            response = await request.response()
            if response is not None:
                event.status = response.status
                event.status_text = response.status_text
                if event.first_party and event.resource_type in API_TYPES and response.status >= 400:
                    event.response_body = await _read_body(response)
        except Exception as e:  # the page may be closing; keep what we have
            log.debug("network event incomplete for %s: %s", request.url, e)
        finally:
            if self._pending.pop(request, None) is not None:
                self._write(event)

    def _write(self, event: NetworkEvent) -> None:
        self.events.append(event)
        self._storage.append_jsonl(self._storage.paths.network_events, asdict(event))


def _is_main_frame(request: Request) -> bool:
    try:
        return request.frame.parent_frame is None
    except Exception:  # service-worker requests have no frame
        return False


async def _read_body(response: Response) -> str | None:
    try:
        return _truncate((await response.body()).decode("utf-8", errors="replace"))
    except Exception:
        return None


def _redact_body(body: str | None, content_type: str) -> str | None:
    if not body:
        return None
    if "json" in content_type or body.lstrip().startswith(("{", "[")):
        try:
            return _truncate(json.dumps(_redact_json(json.loads(body))))
        except ValueError:
            pass
    if "x-www-form-urlencoded" in content_type:
        pairs = [(k, REDACTED if _SECRET_KEY.search(k) else v) for k, v in parse_qsl(body, keep_blank_values=True)]
        return _truncate(urlencode(pairs))
    return _truncate(body)


def _redact_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: REDACTED if _SECRET_KEY.search(str(k)) else _redact_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_json(v) for v in value]
    return value


def _truncate(text: str) -> str:
    return text if len(text) <= BODY_LIMIT else text[:BODY_LIMIT] + f"… [{len(text) - BODY_LIMIT} more chars]"
