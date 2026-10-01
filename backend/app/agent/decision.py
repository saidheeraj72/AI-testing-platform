"""The executor's reply: exactly one action, validated before anything runs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.observation import Observation

ActionName = Literal[
    "click", "type", "select", "press", "scroll", "navigate", "go_back", "wait", "verify", "give_up", "ask_user",
]


class Decision(BaseModel):
    reasoning: str = Field(description="one short sentence")
    action: ActionName
    ref: str | None = None
    text: str | None = None
    submit: bool | None = None
    key: str | None = None
    direction: Literal["up", "down", "top", "bottom"] | None = None
    url: str | None = None
    parts: list[str] | None = None
    total: str | None = None

    def signature(self) -> tuple:
        target = self.ref or self.url or self.key or self.direction or ""
        return (self.action, target, self.text or "")


def validator_for(observation: Observation):
    """Rejects decisions the browser could not execute, with a message the model can act on."""
    refs = {e.ref for e in observation.elements}

    def validate(d: Decision) -> None:
        needs_ref = {"click", "type", "select"}
        if d.action in needs_ref:
            if not d.ref:
                raise ValueError(f"'{d.action}' needs 'ref'")
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
        if d.action == "ask_user" and not d.reasoning.strip():
            raise ValueError("'ask_user' needs 'reasoning' saying what the person should do")

    return validate
