"""Parses Playwright's `aria_snapshot(mode="ai", boxes=True)` output into a tree.

The snapshot is YAML. Each list item is either a bare key or a one-entry
mapping from key to inline text or a child list, where a key looks like:

    button "Add to cart" [disabled] [ref=f2e26] [cursor=pointer] [box=8,9,94,21]

Children whose key starts with "/" (e.g. "/url") are properties of the parent.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from app.schemas.observation import Box

_KEY_RE = re.compile(
    r'^(?P<role>[A-Za-z][\w-]*)'
    r'(?: "(?P<name>(?:[^"\\]|\\.)*)")?'
    r'(?P<attrs>(?:\s*\[[^\]]*\])*)\s*$'
)
_ATTR_RE = re.compile(r"\[([^\]=]+)(?:=([^\]]*))?\]")


@dataclass
class Node:
    role: str
    name: str = ""
    ref: str | None = None
    attrs: dict[str, str | bool] = field(default_factory=dict)
    text: str | None = None
    props: dict[str, str] = field(default_factory=dict)
    children: list[Node] = field(default_factory=list)

    @property
    def box(self) -> Box | None:
        raw = self.attrs.get("box")
        if not isinstance(raw, str):
            return None
        try:
            x, y, w, h = (float(v) for v in raw.split(","))
        except ValueError:
            return None
        return Box(x=x, y=y, width=w, height=h)

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


class SnapshotParseError(ValueError):
    pass


def parse_snapshot(text: str) -> list[Node]:
    try:
        data = yaml.safe_load(text) if text.strip() else []
    except yaml.YAMLError as e:
        raise SnapshotParseError(f"Snapshot is not valid YAML: {e}") from e
    if not isinstance(data, list):
        raise SnapshotParseError("Snapshot root must be a list")
    root = Node(role="root")
    _add_items(root, data)
    return root.children


def _add_items(parent: Node, items: list[Any]) -> None:
    for item in items:
        if isinstance(item, dict):
            for key, value in item.items():
                _add_entry(parent, str(key), value)
        elif item is not None:
            _add_entry(parent, str(item), None)


def _add_entry(parent: Node, key: str, value: Any) -> None:
    if key.startswith("/"):
        parent.props[key[1:]] = _scalar(value)
        return
    if key == "text":
        parent.children.append(Node(role="text", text=_scalar(value)))
        return

    match = _KEY_RE.match(key)
    if not match:
        parent.children.append(Node(role="text", text=key if value is None else f"{key}: {_scalar(value)}"))
        return

    attrs: dict[str, str | bool] = {}
    for name, val in _ATTR_RE.findall(match["attrs"] or ""):
        attrs[name.strip()] = val if val != "" else True
    node = Node(
        role=match["role"],
        name=_unescape(match["name"]) if match["name"] is not None else "",
        ref=attrs.pop("ref", None) or None,  # type: ignore[arg-type]
        attrs=attrs,
    )
    if isinstance(value, list):
        _add_items(node, value)
    elif value is not None:
        node.text = _scalar(value)
    parent.children.append(node)


def _scalar(value: Any) -> str:
    return "" if value is None else str(value)


def _unescape(name: str) -> str:
    try:
        return json.loads(f'"{name}"')
    except json.JSONDecodeError:
        return name
