"""The Claude-in-Chrome style executor pieces: card outlines, find, screenshots, coordinate clicks, sign-on."""

from io import BytesIO

import pytest
from PIL import Image

from app.agent import tools
from app.agent.blockers import detect_blocker
from app.agent.decision import Decision, validator_for
from app.browser.observation import build_observation
from app.browser.snapshot import parse_snapshot
from app.browser.vision import mark
from app.safety.domain_scope import DomainScope
from app.schemas.observation import Viewport

# The module dashboard of a real app: clickable cards (divs with onclick) holding a heading and a button.
DASHBOARD = """\
- button "Modules" [ref=e21] [cursor=pointer] [box=0,97,67,53]
- main [ref=e35] [box=69,37,1400,450]:
  - heading "Your Modules" [level=2] [ref=e40] [box=85,169,1368,32]
  - generic [ref=e42] [cursor=pointer] [box=85,217,672,182]:
    - generic [ref=e43] [box=86,218,670,116]:
      - heading "Aether Customers" [level=3] [ref=e46] [box=166,248,200,24]
      - paragraph [ref=e47] [box=110,284,622,26]: Manage customer relationships
    - button "Open Module" [ref=e49] [box=110,334,622,40]
  - generic [ref=e50] [cursor=pointer] [box=781,217,672,182]:
    - generic [ref=e51] [box=782,218,670,116]:
      - heading "Aether Proposals" [level=3] [ref=e54] [box=862,248,189,24]
      - paragraph [ref=e55] [box=806,284,622,26]: Generate professional RFP responses with AI
    - button "Open Module" [ref=e57] [box=806,334,622,40]
"""


def observe(snapshot: str):
    nodes = parse_snapshot(snapshot)
    return build_observation(nodes, sequence=1, url="https://app.example.com/dashboard", title="t",
                             viewport=Viewport(width=1470, height=835)), nodes


def test_clickable_cards_keep_their_heading_and_buttons():
    o, _ = observe(DASHBOARD)
    assert '[e50] generic "Aether Proposals"' in o.text
    assert 'heading "Aether Proposals" (h3)' in o.text
    proposals_button = o.text.index("[e57] button")
    assert o.text.index("Aether Proposals") < proposals_button
    button = next(e for e in o.elements if e.ref == "e57")
    assert button.context[-1] == 'generic "Aether Proposals"'  # tells the two "Open Module" buttons apart


def test_find_uses_the_card_around_a_button():
    o, nodes = observe(DASHBOARD)
    first = tools.find("Open Module on the Proposals card", o, nodes).splitlines()[0]
    assert first.startswith("[e57] button")
    assert tools.find("billing invoices", o, nodes).startswith("No element matches")


def test_focus_alone_does_not_change_the_page_fingerprint():
    before, _ = observe(DASHBOARD)
    after, _ = observe(DASHBOARD.replace('"Modules" [ref=e21]', '"Modules" [active] [ref=e21]'))
    assert "(focused)" in after.text
    assert before.fingerprint == after.fingerprint


def test_screenshot_marks_visible_elements(tmp_path):
    o, _ = observe(DASHBOARD)
    png = BytesIO()
    Image.new("RGB", (1470, 835), "white").save(png, "PNG")
    out = mark(png.getvalue(), o.elements, tmp_path / "v.jpg")
    image = Image.open(out)
    assert image.size == (1470, 835)
    # The Proposals "Open Module" button has a coloured box around it now.
    assert image.getpixel((806, 360)) != (255, 255, 255)
    assert image.getpixel((1200, 600)) == (255, 255, 255)


def test_click_by_coordinates_or_ref_and_find_needs_a_query():
    o, _ = observe(DASHBOARD)
    validate = validator_for(o)
    validate(Decision(reasoning="r", action="click", x=900, y=350))
    validate(Decision(reasoning="r", action="click", ref="e57"))
    with pytest.raises(ValueError, match="'x' and 'y'"):
        validate(Decision(reasoning="r", action="click"))
    with pytest.raises(ValueError, match="not on the current page"):
        validate(Decision(reasoning="r", action="hover", ref="e999"))
    with pytest.raises(ValueError, match="needs 'query'"):
        validate(Decision(reasoning="r", action="find"))
    assert Decision(reasoning="r", action="find", query="x").looks


def test_single_sign_on_pages_are_reachable_but_handed_to_a_person():
    scope = DomainScope.from_target("https://app.example.com/admin")
    idp = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?client_id=1"
    assert not scope.allows(idp)  # never crawled, never opened by the agent
    assert scope.allows_navigation(idp)  # but the app may send the browser there to sign in
    assert not scope.allows_navigation("https://evil.example.org/")
    found = detect_blocker([], "Log in with a@b.c / password1", idp)
    assert found and found.kind == "login_required" and "login.microsoftonline.com" in found.message
