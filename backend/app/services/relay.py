"""CDP relay between the Chrome extension (attached to one user tab) and Playwright.

Playwright connects to /api/relay/{id}/cdp as if to a browser. The extension
connects to /api/relay/{id}/extension and runs commands on its tab with
chrome.debugger. The relay plays the browser-level part of the protocol
(version, auto-attach to the single tab) and forwards everything else.

Commands that would close the user's browser or tab are answered here and
never forwarded.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)

EXTENSION_TIMEOUT = 30.0
TAB_SESSION = "tab-1"
NEVER_FORWARD = frozenset({"Browser.close", "Browser.crash", "Target.closeTarget", "Target.disposeBrowserContext"})


class RelayError(Exception):
    pass


class CDPRelay:
    def __init__(self, relay_id: str):
        self.relay_id = relay_id
        self.extension_connected = asyncio.Event()
        self._extension: WebSocket | None = None
        self._playwright: WebSocket | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._target_info: dict[str, Any] | None = None
        self.tab_url: str | None = None

    # ------------------------------------------------------------------ extension side

    async def serve_extension(self, ws: WebSocket) -> None:
        if self._extension is not None:
            await ws.close(code=1008)
            return
        self._extension = ws
        self.extension_connected.set()
        try:
            while True:
                message = json.loads(await ws.receive_text())
                if "id" in message:
                    future = self._pending.pop(message["id"], None)
                    if future and not future.done():
                        if "error" in message:
                            future.set_exception(RelayError(message["error"].get("message", "extension error")))
                        else:
                            future.set_result(message.get("result") or {})
                elif message.get("method") == "forwardCDPEvent":
                    params = message["params"]
                    await self._to_playwright({
                        "method": params["method"],
                        "params": params.get("params") or {},
                        "sessionId": params.get("sessionId") or TAB_SESSION,
                    })
        except WebSocketDisconnect:
            pass
        finally:
            self._extension = None
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(RelayError("the extension disconnected"))
            self._pending.clear()
            if self._playwright is not None:
                with contextlib.suppress(Exception):
                    await self._playwright.close()

    async def _call_extension(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._extension is None:
            raise RelayError("the extension is not connected")
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[self._next_id] = future
        await self._extension.send_text(json.dumps({"id": self._next_id, "method": method, "params": params}))
        return await asyncio.wait_for(future, EXTENSION_TIMEOUT)

    # ------------------------------------------------------------------ Playwright side

    async def serve_playwright(self, ws: WebSocket) -> None:
        self._playwright = ws
        tasks: set[asyncio.Task] = set()
        try:
            while True:
                command = json.loads(await ws.receive_text())
                # Each command in its own task; sends to the extension still happen in arrival order.
                task = asyncio.create_task(self._answer(command))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        except WebSocketDisconnect:
            pass
        finally:
            self._playwright = None
            for task in tasks:
                task.cancel()

    async def _answer(self, command: dict[str, Any]) -> None:
        reply: dict[str, Any] = {"id": command["id"]}
        if command.get("sessionId"):
            reply["sessionId"] = command["sessionId"]
        try:
            reply["result"] = await self._handle(command)
        except Exception as e:  # report to Playwright like a CDP error
            reply["error"] = {"code": -32000, "message": str(e)}
        await self._to_playwright(reply)

    async def _handle(self, command: dict[str, Any]) -> dict[str, Any]:
        method = command["method"]
        params = command.get("params") or {}
        session = command.get("sessionId")

        if method in NEVER_FORWARD:
            return {}
        if session is None:
            if method == "Browser.getVersion":
                return {"protocolVersion": "1.3", "product": "Chrome/AI-Tester-Relay", "revision": "",
                        "userAgent": "AI-Tester-Relay", "jsVersion": ""}
            if method == "Browser.setDownloadBehavior":
                return {}
            if method == "Target.setAutoAttach":
                result = await self._call_extension("attachToTab", {})
                self._target_info = result["targetInfo"]
                self.tab_url = self._target_info.get("url")
                await self._to_playwright({"method": "Target.attachedToTarget", "params": {
                    "sessionId": TAB_SESSION,
                    "targetInfo": {**self._target_info, "attached": True},
                    "waitingForDebugger": False,
                }})
                return {}
            if method == "Target.getTargetInfo":
                return {"targetInfo": self._target_info or {}}
        forward_session = None if session in (None, TAB_SESSION) else session
        return await self._call_extension("forwardCDPCommand", {
            "method": method, "params": params, "sessionId": forward_session,
        })

    async def _to_playwright(self, message: dict[str, Any]) -> None:
        if self._playwright is not None:
            with contextlib.suppress(Exception):
                await self._playwright.send_text(json.dumps(message))

    async def close(self) -> None:
        for ws in (self._extension, self._playwright):
            if ws is not None:
                with contextlib.suppress(Exception):
                    await ws.close()
