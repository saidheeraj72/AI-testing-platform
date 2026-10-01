"""Turns a parsed aria snapshot into an Observation: elements + a compact outline.

The outline is what the model reads. It keeps page structure (landmarks,
headings, lists, dialogs) as context, gives every actionable element a
[ref], collapses table rows onto one line, and drops meaningless wrappers.
When the page is too large it keeps what is in or near the viewport and
marks the rest as omitted.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from app.browser.snapshot import Node
from app.safety.secrets import MASK, is_secret_field
from app.schemas.observation import Box, Element, Observation, Viewport

INTERACTIVE_ROLES = frozenset({
    "button", "link", "textbox", "searchbox", "checkbox", "radio", "combobox", "listbox",
    "option", "spinbutton", "slider", "switch", "tab", "menuitem", "menuitemcheckbox",
    "menuitemradio", "treeitem",
})
CONTAINER_ROLES = frozenset({
    "banner", "navigation", "main", "contentinfo", "complementary", "region", "form", "search",
    "dialog", "alertdialog", "list", "listitem", "table", "grid", "row", "group", "radiogroup",
    "tablist", "tabpanel", "menu", "menubar", "toolbar", "tree", "article", "iframe",
})
ALWAYS_KEEP_ROLES = frozenset({
    "banner", "navigation", "main", "contentinfo", "dialog", "alertdialog", "alert", "status",
    "heading", "form", "iframe",
})
LIVE_ROLES = frozenset({"alert", "status"})
TRANSPARENT_ROLES = frozenset({"generic", "none", "presentation", "rowgroup"})
FIELD_ROLES = frozenset({"textbox", "searchbox", "combobox", "spinbutton", "listbox", "slider"})
LABEL_ROLES = frozenset({"generic", "text", "paragraph", "LabelText", "label", "strong"})
MAX_FIELD_LABEL = 60
STATE_ATTRS = ("disabled", "checked", "selected", "expanded", "pressed", "active", "required", "readonly")

_REF_TOKEN = re.compile(r"\[(?:f\d+)?e\d+\] ")
_FOCUS = re.compile(r"(?:, )?\bfocused\b(?:, )?")
MAX_LABEL = 60
MAX_TEXT_LINE = 200


@dataclass(frozen=True)
class ObservationLimits:
    max_chars: int = 12_000
    max_elements: int = 200


@dataclass
class _Line:
    indent: int
    text: str
    order: int
    keep: bool
    distance: float  # px outside the viewport; 0 when visible
    in_dialog: bool
    is_element: bool


@dataclass
class _Walk:
    viewport: Viewport
    lines: list[_Line] = field(default_factory=list)
    elements: list[Element] = field(default_factory=list)
    iframes: int = 0


def build_observation(
    nodes: list[Node],
    *,
    sequence: int,
    url: str,
    title: str,
    viewport: Viewport,
    limits: ObservationLimits = ObservationLimits(),
    notices: list[str] | None = None,
) -> Observation:
    walk = _Walk(viewport=viewport)
    _label_fields(nodes)
    for node in nodes:
        _visit(node, walk, indent=0, context=[], frame="main", offset=(0.0, 0.0), parent_distance=0.0,
               parent_clickable=False, in_dialog=False, sibling_names=frozenset())

    text, omitted = _render(walk.lines, limits)
    return Observation(
        sequence=sequence,
        url=url,
        title=title,
        viewport=viewport,
        elements=walk.elements,
        text=text,
        omitted_lines=omitted,
        notices=notices or [],
        fingerprint=_fingerprint(text),
    )


def _visit(
    node: Node,
    walk: _Walk,
    *,
    indent: int,
    context: list[str],
    frame: str,
    offset: tuple[float, float],
    parent_distance: float,
    parent_clickable: bool,
    in_dialog: bool,
    sibling_names: frozenset[str],
) -> None:
    box = _shift(node.box, offset)
    distance = _distance(box, walk.viewport) if box and (box.width or box.height) else parent_distance
    clickable = node.attrs.get("cursor") == "pointer"
    in_dialog = in_dialog or node.role in ("dialog", "alertdialog")

    def emit(text: str, *, keep: bool = False, is_element: bool = False) -> None:
        walk.lines.append(_Line(indent, _clip(text, MAX_TEXT_LINE) if not is_element else text,
                                len(walk.lines), keep, distance, in_dialog, is_element))

    def children(extra_indent: int, ctx: list[str], child_frame: str = frame, child_offset=offset) -> None:
        # Visible labels usually repeat the name of the control next to them.
        names = frozenset(n for c in node.children if c.ref for n in (c.name, c.attrs.get("label")) if n)
        for child in node.children:
            _visit(child, walk, indent=indent + extra_indent, context=ctx, frame=child_frame,
                   offset=child_offset, parent_distance=distance,
                   parent_clickable=clickable or parent_clickable, in_dialog=in_dialog,
                   sibling_names=names)

    role = node.role
    is_element = node.ref is not None and (
        role in INTERACTIVE_ROLES or (clickable and not parent_clickable and role not in CONTAINER_ROLES)
    )

    if is_element:
        card = role not in INTERACTIVE_ROLES and bool(node.children)
        # A clickable card (a div with an onclick) is named by its text, like a button would be.
        element = _element(node, box, distance, context, frame,
                           name=_clip(_first_text(node) or _text_of(node), MAX_LABEL) if card and not node.name else None)
        walk.elements.append(element)
        emit(_element_line(element, node), is_element=True)
        # Descend into composite widgets whose parts are separately actionable, and into cards:
        # their headings and buttons ("Open Module") must stay visible to the model.
        if role in ("listbox", "menu", "tablist", "radiogroup"):
            children(1, context + [_label(node)])
        elif card:
            children(1, context + [f'{role} "{element.name}"' if element.name else role])
        return

    if node.ref is None and role in INTERACTIVE_ROLES and node.attrs.get("disabled") and (node.name or node.text):
        # Playwright gives disabled controls no ref. The model must still see them: a disabled
        # "Create" button means a required field is missing.
        emit(f'{role} "{_clip(node.name or node.text or "", MAX_LABEL)}" (disabled, fill the required fields first)')
        return
    if role == "text":
        if node.text and node.text.strip() not in sibling_names:
            emit(f"text: {node.text}")
        return
    if role == "heading":
        level = node.attrs.get("level")
        emit(f'heading "{node.name or _text_of(node)}"' + (f" (h{level})" if level else ""), keep=True)
        return
    if role in LIVE_ROLES:
        emit(f"{role}: {_text_of(node)}", keep=True)
        return
    if role == "row" and not _has_element(node):
        cells = [_text_of(c) for c in node.children if c.role != "text" or c.text]
        emit("row: " + " | ".join(c for c in cells if c))
        return
    if role == "iframe":
        walk.iframes += 1
        emit("iframe", keep=True)
        frame_offset = (box.x, box.y) if box else offset
        children(1, context + ["iframe"], child_frame=f"iframe-{walk.iframes}", child_offset=frame_offset)
        return
    if role in CONTAINER_ROLES:
        label = _label(node)
        emit(label, keep=role in ALWAYS_KEEP_ROLES)
        children(1, context + [label])
        return
    if role == "img":
        if node.name:
            emit(f'img "{node.name}"')
        return
    if role in TRANSPARENT_ROLES or not (node.name or node.text):
        if node.text and not node.children and node.text.strip() not in sibling_names:
            emit(f"text: {node.text}")
        children(0, context)
        return

    # paragraph, cell, strong, code, label, ...: show as text
    emit(f"text: {_text_of(node)}")


def _fingerprint(text: str) -> str:
    """Identifies what the page shows. Refs and focus are left out: focusing a button changes nothing visible."""
    text = _FOCUS.sub("", _REF_TOKEN.sub("", text)).replace(" ()", "")
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def _element(node: Node, box: Box | None, distance: float, context: list[str], frame: str,
             name: str | None = None) -> Element:
    states = [a for a in STATE_ATTRS if node.attrs.get(a) is True or node.attrs.get(a) == "true"]
    label = node.attrs.get("label")
    if isinstance(label, str) and label:
        if label.rstrip().endswith("*") and "required" not in states:
            states.append("required")
        name = label.rstrip(" *:")
    options = [c.name for c in node.children if c.role == "option"]
    value = node.text
    if value and is_secret_field(node.name):
        value = MASK
    if node.role == "combobox" and options:
        selected = [c.name for c in node.children if c.role == "option" and c.attrs.get("selected")]
        value = selected[0] if selected else value
    elif node.role == "combobox" and not value and node.children:
        value = _text_of(node)  # a custom dropdown shows its choice (or "Select a client") as text
    return Element(
        ref=node.ref or "",
        role=node.role,
        name=name or node.name or (node.text if node.role not in ("textbox", "searchbox", "spinbutton", "combobox")
                                   else "") or "",
        value=value if node.role not in ("button", "link", "tab", "menuitem", "generic") else None,
        url=node.props.get("url"),
        states=states,
        options=options,
        context=context[-3:],
        frame=frame,
        in_viewport=distance == 0,
        box=box,
    )


def _element_line(element: Element, node: Node) -> str:
    parts = [f"[{element.ref}] {element.role}"]
    if element.name:
        parts.append(f'"{_clip(element.name, MAX_TEXT_LINE)}"')
    if element.states:
        parts.append("(" + ", ".join("focused" if s == "active" else s for s in element.states) + ")")
    if element.value is not None and element.value != "":
        parts.append(f'= "{_clip(element.value, MAX_TEXT_LINE)}"')
    if node.attrs.get("label") and node.name and node.name != element.name:
        parts.append(f'placeholder "{_clip(node.name, MAX_LABEL)}"')
    if element.options:
        parts.append("options: " + ", ".join(element.options[:20]) + (" …" if len(element.options) > 20 else ""))
    if element.url:
        parts.append(f"-> {element.url}")
    return " ".join(parts)


def _render(lines: list[_Line], limits: ObservationLimits) -> tuple[str, int]:
    kept: set[int] = set()
    chars = 0
    elements = 0
    # Priority: structural lines, dialog content, then by distance from the viewport.
    for line in sorted(lines, key=lambda l: (not (l.keep or l.in_dialog), l.distance, l.order)):
        cost = len(line.text) + 2 * line.indent + 1
        if not (line.keep or line.in_dialog):
            if chars + cost > limits.max_chars:
                continue
            if line.is_element and elements >= limits.max_elements:
                continue
        kept.add(line.order)
        chars += cost
        elements += line.is_element

    out: list[str] = []
    omitted_run = 0
    omitted_total = 0
    for line in lines:
        if line.order in kept:
            if omitted_run:
                out.append(f"{'  ' * line.indent}… {omitted_run} more lines (scroll to see them)")
                omitted_run = 0
            out.append(f"{'  ' * line.indent}{line.text}")
        else:
            omitted_run += 1
            omitted_total += 1
    if omitted_run:
        out.append(f"… {omitted_run} more lines (scroll to see them)")
    return "\n".join(out), omitted_total


def _label_fields(nodes: list[Node]) -> None:
    """Name fields after the label next to them.

    Many forms put the label in a sibling element rather than a <label>:
    <div>Client *</div><button role="combobox">Select a client</button>. The
    accessibility name is then empty or the placeholder ("e.g. Workforce
    Strategy Review"). The label is stored on the node, so checks that look
    fields up by name find it too.
    """
    for root in nodes:
        for node in root.walk():
            for before, field_node in zip(node.children, node.children[1:]):
                if field_node.role in FIELD_ROLES and "label" not in field_node.attrs:
                    text = _plain_text(before)
                    if text and _norm_label(text) != _norm_label(field_node.name):
                        field_node.attrs["label"] = text


def _plain_text(node: Node) -> str:
    """The text of a label-like node: no controls inside, short."""
    if node.role not in LABEL_ROLES or node.ref and node.attrs.get("cursor") == "pointer":
        return ""
    if any(n.role in INTERACTIVE_ROLES for n in node.walk()):
        return ""
    text = " ".join(t for n in node.walk() for t in (n.text, n.name if n.role == "text" else None) if t).strip()
    return text if 0 < len(text) <= MAX_FIELD_LABEL else ""


def _norm_label(text: str) -> str:
    return " ".join(text.split()).rstrip(" *:").casefold()


def _label(node: Node) -> str:
    """Short human label for a container, used as element context."""
    name = node.name
    if not name and node.role in ("listitem", "row", "article", "group"):
        name = _first_text(node)
    return f'{node.role} "{_clip(name, MAX_LABEL)}"' if name else node.role


def _first_text(node: Node) -> str:
    for n in node.walk():
        if n is node:
            continue
        if n.role in ("heading", "cell", "columnheader", "rowheader") and (n.name or n.text):
            return n.name or n.text or ""
        if n.role == "text" and n.text:
            return n.text
        if n.role not in INTERACTIVE_ROLES and n.text:
            return n.text
    return ""


def _text_of(node: Node, limit: int = MAX_TEXT_LINE) -> str:
    if node.name:
        return _clip(node.name, limit)
    if node.text:
        return _clip(node.text, limit)
    parts = [n.name or n.text or "" for n in node.walk() if n is not node and (n.text or (n.name and n.role != "generic"))]
    return _clip(" ".join(p for p in parts if p), limit)


def _has_element(node: Node) -> bool:
    return any(n is not node and n.ref and n.role in INTERACTIVE_ROLES for n in node.walk())


def _shift(box: Box | None, offset: tuple[float, float]) -> Box | None:
    if box is None or offset == (0.0, 0.0):
        return box
    return Box(x=box.x + offset[0], y=box.y + offset[1], width=box.width, height=box.height)


def _distance(box: Box, viewport: Viewport) -> float:
    if box.y + box.height < 0:
        return -(box.y + box.height)
    if box.y > viewport.height:
        return box.y - viewport.height
    return 0.0


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
