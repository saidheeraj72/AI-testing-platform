"""Deterministic risk policy for actions (no model involved).

An action is risky when it clicks something whose label says it destroys,
pays, orders, sends or publishes. What happens then is the project's
policy: confirm with the user, allow, or block.
"""

from __future__ import annotations

import re

from app.schemas.observation import Element

_RISKY = re.compile(
    r"\b(delete|remove|destroy|erase|purge|wipe|deactivate|terminate|revoke|"
    r"close (my )?account|cancel (my )?(subscription|account|order|plan)|"
    r"place (the )?order|pay|pay now|purchase|buy|confirm (the )?(payment|order|purchase)|"
    r"submit payment|send|transfer|publish|unsubscribe|reset)\b",
    re.I,
)


def risky_reason(action: str, element: Element | None) -> str | None:
    """Why this action needs the policy check, or None when it is safe."""
    if action != "click" or element is None:
        return None
    label = element.name or ""
    match = _RISKY.search(label)
    return f'clicking {element.role} "{label}" ({match[0].lower()})' if match else None
