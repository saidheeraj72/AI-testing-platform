"""Re-resolves an element ref from an older observation against the current page.

Refs come from Playwright's aria snapshot. The same DOM node keeps its ref
across snapshots, and a re-rendered node gets a new one, so:

1. The ref still exists with the same role, name and context: use it.
2. It exists but the name changed while role and context did not: same node
   whose label changed (e.g. "Save" -> "Saving…"); use it.
3. Otherwise look for the element by role + name + context. One match: use
   it. Several: pick by position only when the group has the same size as
   before, else refuse (AMBIGUOUS). None: try role + name in the same frame,
   ignoring context, and accept a single unique match.

Refusing is deliberate. Clicking the wrong row in a re-rendered list is worse
than reporting that the page changed and re-observing.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.action import ActionError
from app.schemas.observation import Element


class ResolutionError(Exception):
    def __init__(self, code: ActionError, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Resolution:
    ref: str
    element: Element
    how: str  # same | renamed | by_identity | by_position | by_name


def _key(e: Element) -> tuple:
    return (e.role, e.name, tuple(e.context), e.frame)


def resolve(target: Element, source: list[Element], fresh: list[Element]) -> Resolution:
    """Find `target` (taken from observation `source`) in the `fresh` element list."""
    same = next((e for e in fresh if e.ref == target.ref), None)
    if same is not None:
        if _key(same) == _key(target):
            return Resolution(same.ref, same, "same")
        if (same.role, tuple(same.context), same.frame) == (target.role, tuple(target.context), target.frame):
            return Resolution(same.ref, same, "renamed")

    group_before = [e for e in source if _key(e) == _key(target)]
    group_now = [e for e in fresh if _key(e) == _key(target)]
    if len(group_now) == 1:
        return Resolution(group_now[0].ref, group_now[0], "by_identity")
    if len(group_now) > 1:
        if len(group_now) == len(group_before) and target in group_before:
            match = group_now[group_before.index(target)]
            return Resolution(match.ref, match, "by_position")
        raise ResolutionError(
            ActionError.AMBIGUOUS_ELEMENT,
            f"{target.describe()} now matches {len(group_now)} elements; observe the page again and pick one.",
        )

    by_name = [e for e in fresh if (e.role, e.name, e.frame) == (target.role, target.name, target.frame)]
    if len(by_name) == 1 and target.name:
        return Resolution(by_name[0].ref, by_name[0], "by_name")

    raise ResolutionError(
        ActionError.ELEMENT_NOT_FOUND,
        f"{target.describe()} is no longer on the page; observe the page again.",
    )
