"""Read-only tools the executor can call between actions, as Claude in Chrome does.

They answer the model's questions about the page without changing it:

  find           elements matching a description ("Open Module on the Proposals card")
  read_page      the whole page outline, also the parts beyond the viewport
  get_page_text  all visible text, for reading content
  read_console   console errors and uncaught exceptions during this step
  read_network   requests made during this step, with their status

`find` is lexical, not a model call: it scores every element by how many of
the query's words appear in its label, its card or section, and the text
right around it. That is cheap, deterministic, and good enough for the
labels people put on buttons.
"""

from __future__ import annotations

import re

from app.browser.console import ConsoleEvent
from app.browser.network import NetworkEvent
from app.browser.snapshot import Node
from app.schemas.observation import Element, Observation

FIND_LIMIT = 8
_STOP = frozenset("a an the to of on in for and or with button link field page click open card".split())


def find(query: str, observation: Observation, nodes: list[Node]) -> str:
    words = [w for w in _words(query) if w not in _STOP] or _words(query)
    if not words:
        return "Give a description to search for, like 'Save button in the Billing form'."
    nearby = _text_near_elements(nodes)
    scored = []
    for order, e in enumerate(observation.elements):
        label = set(_words(e.name))
        around = set(_words(" ".join(e.context))) | set(_words(nearby.get(e.ref, "")))
        score = sum(2 for w in words if w in label) + sum(1 for w in words if w in around and w not in label)
        if score:
            scored.append((score, e.in_viewport, -order, e))
    if not scored:
        return f"No element matches {query!r}. Try other words, scroll, or take a screenshot."
    scored.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
    return "\n".join(_describe(e) for *_, e in scored[:FIND_LIMIT])


def console_report(events: list[ConsoleEvent]) -> str:
    if not events:
        return "No console errors or warnings during this step."
    lines = [f"{'UNCAUGHT ' if e.kind == 'pageerror' else ''}{e.level}: {_clip(e.text, 300)}" for e in events[-15:]]
    return "\n".join(lines)


def network_report(events: list[NetworkEvent]) -> str:
    calls = [e for e in events if e.resource_type in ("fetch", "xhr", "document")]
    if not calls:
        return "No page loads or API requests during this step."
    lines = []
    for e in calls[-20:]:
        result = f"HTTP {e.status}" if e.status is not None else f"failed ({e.failure})"
        party = "" if e.first_party else " [third-party]"
        lines.append(f"{e.method} {_clip(e.url, 140)} -> {result}{party}")
    return "\n".join(lines)


def _describe(e: Element) -> str:
    where = " > ".join(e.context[-2:])
    position = "visible" if e.in_viewport else "off-screen (scroll or act on the ref directly)"
    name = f' "{e.name}"' if e.name else ""
    return f"[{e.ref}] {e.role}{name}" + (f" in {where}" if where else "") + f" - {position}"


def _text_near_elements(nodes: list[Node]) -> dict[str, str]:
    """For each ref, the text of the closest container that holds a heading or text: its card or row."""
    found: dict[str, str] = {}

    def visit(node: Node, ancestors: list[Node]) -> None:
        if node.ref and node.ref not in found:
            for parent in reversed(ancestors[-4:]):
                text = " ".join(n.name or n.text or "" for n in parent.walk() if n.role in ("heading", "text", "paragraph"))
                if text.strip():
                    found[node.ref] = text[:400]
                    break
        for child in node.children:
            visit(child, ancestors + [node])

    for root in nodes:
        visit(root, [])
    return found


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").casefold())


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
