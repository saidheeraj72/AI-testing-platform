"""Evaluates success criteria against the real page. The model never decides a pass.

Text checks read the aria snapshot (what the model also sees), but ignore the
contents of editable fields: text typed into an input is not evidence that
the application saved or displayed it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.browser.network import NetworkEvent
from app.browser.observation import CONTAINER_ROLES
from app.browser.snapshot import Node
from app.schemas.plan import CheckResult, Criterion

EDITABLE_ROLES = frozenset({"textbox", "searchbox", "spinbutton", "combobox", "slider"})
_NUMBER = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?")


@dataclass
class PageState:
    url: str
    nodes: list[Node]
    network: list[NetworkEvent] = field(default_factory=list)  # since the step started


@dataclass
class VerifyArgs:
    """Values the executor supplies when it asks for a check (sum_equals only)."""

    parts: list[str] = field(default_factory=list)
    total: str | None = None


def needs_verify_args(criterion: Criterion) -> bool:
    return criterion.type == "sum_equals"


def evaluate(criterion: Criterion, page: PageState, args: VerifyArgs | None = None) -> CheckResult:
    passed, detail, app_error = _CHECKS[criterion.type](criterion, page, args or VerifyArgs())
    if criterion.negate and criterion.type not in ("request_succeeded", "sum_equals"):
        passed = not passed
    return CheckResult(criterion=criterion, passed=passed, detail=detail, app_error=app_error)


def _url_contains(c: Criterion, page: PageState, _) -> tuple[bool, str, bool]:
    return (c.value or "") in page.url, f"current URL is {page.url}", False


def _text_visible(c: Criterion, page: PageState, _) -> tuple[bool, str, bool]:
    wanted = _norm(c.value or "")
    roots, note = _scope(page.nodes, c.within)
    pieces = _texts(roots)
    found = any(wanted in p for p in pieces) or wanted in " ".join(pieces)
    where = f" within {c.within!r}" if c.within and not note else ""
    return found, f"{c.value!r} {'found' if found else 'not found'}{where}{note}", False


def _element_present(c: Criterion, page: PageState, _) -> tuple[bool, str, bool]:
    name = _norm(c.name or "")
    matches = [
        n for root in page.nodes for n in root.walk()
        if n.role != "text" and (not c.role or n.role == c.role)
        and (not name or name in _norm(n.name) or name in " ".join(_texts([n])))  # alerts carry text, not a name
    ]
    what = f"{c.role or 'element'}" + (f" {c.name!r}" if c.name else "")
    return bool(matches), f"{len(matches)} {what} found", False


def _field_value(c: Criterion, page: PageState, _) -> tuple[bool, str, bool]:
    name = _norm(c.name or "")
    fields = [n for root in page.nodes for n in root.walk() if n.role in EDITABLE_ROLES and name in _norm(n.name)]
    if not fields:
        return False, f"no field named {c.name!r}", False
    value = fields[0].text or ""
    return _norm(value) == _norm(c.value or ""), f"field {fields[0].name!r} has value {value!r}", False


def _request_succeeded(c: Criterion, page: PageState, _) -> tuple[bool, str, bool]:
    matching = [
        e for e in page.network
        if (c.value or "") in e.url and (not c.method or e.method.upper() == c.method.upper())
        and e.resource_type in ("fetch", "xhr", "document")
    ]
    if not matching:
        return False, f"no {c.method or ''} request to {c.value!r} was made during this step".replace("  ", " "), False
    failed = [e for e in matching if e.is_error]
    if failed:
        e = failed[-1]
        return False, f"{e.method} {e.url} returned {e.status or e.failure}", True
    last = matching[-1]
    return True, f"{last.method} {last.url} returned {last.status}", False


def _sum_equals(c: Criterion, page: PageState, args: VerifyArgs) -> tuple[bool, str, bool]:
    if not args.parts or not args.total:
        return False, "give the amounts ('parts') and the 'total' exactly as shown on the page", False
    pieces = _texts(page.nodes)
    missing = [s for s in [*args.parts, args.total] if not any(_norm(s) in p for p in pieces)]
    if missing:
        return False, f"not visible on the page: {missing}", False
    try:
        parts = [_number(p) for p in args.parts]
        total = _number(args.total)
    except ValueError as e:
        return False, str(e), False
    expected = round(sum(parts), 2)
    ok = abs(expected - total) < 0.005
    return ok, f"parts {args.parts} add up to {expected:g}; the page shows total {args.total}", False


_CHECKS = {
    "url_contains": _url_contains,
    "text_visible": _text_visible,
    "element_present": _element_present,
    "field_value": _field_value,
    "request_succeeded": _request_succeeded,
    "sum_equals": _sum_equals,
}


def _scope(nodes: list[Node], within: str | None) -> tuple[list[Node], str]:
    if not within:
        return nodes, ""
    wanted = _norm(within)
    containers = [
        n for root in nodes for n in root.walk()
        if n.role in CONTAINER_ROLES and wanted in _norm(n.name)
    ]
    if containers:
        return containers, ""
    return nodes, f" (no container named {within!r}; searched the whole page)"


def _texts(roots: list[Node]) -> list[str]:
    out: list[str] = []

    def visit(node: Node) -> None:
        if node.role in EDITABLE_ROLES:
            return
        for piece in (node.name, node.text):
            if piece:
                out.append(_norm(piece))
        for child in node.children:
            visit(child)

    for root in roots:
        visit(root)
    return out


def _number(text: str) -> float:
    match = _NUMBER.search(text.replace("−", "-"))
    if not match:
        raise ValueError(f"no number in {text!r}")
    return float(match[0].replace(",", ""))


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()
