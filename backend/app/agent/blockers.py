"""Pages the agent must hand to a human: CAPTCHAs, MFA codes, and logins it has no credentials for.

Detected in code from the page snapshot, so a model can never decide to
"solve" a CAPTCHA or guess a one-time code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.browser.snapshot import Node
from app.safety.secrets import is_secret_field

EDITABLE = ("textbox", "searchbox", "spinbutton")

_CAPTCHA = re.compile(
    r"captcha|recaptcha|hcaptcha|turnstile|i'?m not a robot|verify (that )?you are (a )?human|"
    r"are you a robot|human verification|checking (if the site connection is secure|your browser)",
    re.I,
)
_MFA_FIELD = re.compile(
    r"one[- ]time|verification code|security code|authentication code|auth code|2fa|two[- ]factor|"
    r"\botp\b|passcode|authenticator",
    re.I,
)
_MFA_TEXT = re.compile(
    r"two[- ]factor|2-step verification|enter the (\d+[- ]digit )?code|we (sent|texted|emailed) (you )?a code|"
    r"authenticator app",
    re.I,
)
_LOGIN_WORDS = re.compile(r"\b(sign ?in|log ?in|login|log on)\b", re.I)
_CREDENTIALS_IN_OBJECTIVE = re.compile(r"password|passwd|credentials|\bpwd\b", re.I)


@dataclass(frozen=True)
class Blocker:
    kind: str  # captcha | mfa | login_required
    message: str


def detect_blocker(nodes: list[Node], objective: str) -> Blocker | None:
    """The first thing on this page that needs a human, if any."""
    every = [n for root in nodes for n in root.walk()]
    texts = " ".join(filter(None, (t for n in every for t in (n.name, n.text))))

    if _CAPTCHA.search(texts) or any(n.role == "iframe" and _CAPTCHA.search(n.name) for n in every):
        return Blocker("captcha", "The page shows a CAPTCHA. Solve it in the Chrome window, then click Continue.")

    fields = [n for n in every if n.role in EDITABLE]
    if any(_MFA_FIELD.search(n.name) for n in fields) or (_MFA_TEXT.search(texts) and fields):
        return Blocker("mfa", "The site asks for a verification code. Enter it in the Chrome window, "
                              "then click Continue.")

    has_password = any(n.role == "textbox" and is_secret_field(n.name) for n in fields)
    login_page = any(
        (n.role in ("button", "link", "heading") and _LOGIN_WORDS.search(n.name or n.text or ""))
        for n in every
    )
    if has_password and login_page and not _CREDENTIALS_IN_OBJECTIVE.search(objective):
        return Blocker("login_required", "The site asks you to log in and the objective gives no credentials. "
                                         "Log in in the Chrome window, then click Continue.")
    return None
