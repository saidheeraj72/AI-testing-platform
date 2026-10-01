"""The executor's reply: exactly one action, validated before anything runs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.observation import Observation

ActionName = Literal[
    # change the page
    "click", "hover", "type", "select", "press", "scroll", "navigate", "go_back", "wait",
    # look without changing anything
    "find", "read_page", "get_page_text", "screenshot", "read_console", "read_network",
    # end or hand over
    "verify", "give_up", "ask_user",
]
LOOK_ACTIONS = frozenset({"find", "read_page", "get_page_text", "screenshot", "read_console", "read_network"})


class Decision(BaseModel):
    reasoning: str = Field("", description="one short sentence")
    action: ActionName
    ref: str | None = None
    x: float | None = Field(None, description="click: viewport x in screenshot pixels, only when there is no ref")
    y: float | None = None
    text: str | None = None
    submit: bool | None = None
    key: str | None = None
    direction: Literal["up", "down", "top", "bottom"] | None = None
    url: str | None = None
    query: str | None = Field(None, description="find: what to look for")
    parts: list[str] | None = None
    total: str | None = None

    @property
    def looks(self) -> bool:
        return self.action in LOOK_ACTIONS

    def signature(self) -> tuple:
        point = f"{round(self.x or 0, -1)},{round(self.y or 0, -1)}" if self.x is not None else ""
        target = self.ref or point or self.url or self.key or self.direction or self.query or ""
        return (self.action, target, self.text or "")


def validator_for(observation: Observation):
    """Rejects decisions the browser could not execute, with a message the model can act on."""
    refs = {e.ref for e in observation.elements}

    def validate(d: Decision) -> None:
        needs_ref = {"click", "hover", "type", "select"}
        if d.action == "click" and not d.ref and d.x is not None and d.y is not None:
            pass  # a click on screenshot coordinates
        elif d.action in needs_ref:
            if not d.ref:
                raise ValueError(f"'{d.action}' needs 'ref'" + (" (or 'x' and 'y')" if d.action == "click" else ""))
            if d.ref not in refs:
                raise ValueError(f"ref {d.ref!r} is not on the current page; use a ref shown in PAGE like [e12]")
        if d.action in ("type", "select") and d.text is None:
            raise ValueError(f"'{d.action}' needs 'text'")
        if d.action == "press" and not d.key:
            raise ValueError("'press' needs 'key'")
        if d.action == "scroll" and not d.direction:
            raise ValueError("'scroll' needs 'direction'")
        if d.action == "navigate" and not d.url:
            raise ValueError("'navigate' needs 'url'")
        if d.action == "find" and not (d.query or "").strip():
            raise ValueError("'find' needs 'query', e.g. \"Open Module button on the Proposals card\"")
        if d.action == "ask_user" and not d.reasoning.strip():
            raise ValueError("'ask_user' needs 'reasoning' saying what the person should do")

    return validate
