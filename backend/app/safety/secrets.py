"""One rule for "this field holds a secret", shared by observations and action logs.

The aria snapshot does not expose input types, so the field's accessible
name decides. Values of matching fields never reach the model or disk.
"""

from __future__ import annotations

import re

SECRET_FIELD = re.compile(
    r"\bpass(word|code|phrase|wd)?\b|secret|token|\botp\b|one[- ]time|\bcvv\b|\bcvc\b|card number|\bpin\b",
    re.I,
)
MASK = "••••••••"
REDACTED = "[redacted]"

_SNAPSHOT_VALUE_LINE = re.compile(r'^(?P<head>\s*- \'?(?:textbox|searchbox|spinbutton) "(?P<name>(?:[^"\\]|\\.)*)"[^\n]*?):[ ].*$', re.M)


def is_secret_field(name: str) -> bool:
    return bool(SECRET_FIELD.search(name))


def mask_snapshot(raw: str) -> str:
    """Mask inline values of secret fields in a raw aria snapshot."""
    def replace(m: re.Match) -> str:
        if not is_secret_field(m["name"]):
            return m[0]
        head = m["head"]
        return f"{head}: {MASK}'" if head.lstrip().startswith("- '") else f"{head}: {MASK}"
    return _SNAPSHOT_VALUE_LINE.sub(replace, raw)
