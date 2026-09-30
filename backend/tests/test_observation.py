from app.browser.observation import ObservationLimits, build_observation
from app.browser.snapshot import parse_snapshot
from app.schemas.observation import Viewport

VIEWPORT = Viewport(width=1280, height=800)


def observe(snapshot: str, limits: ObservationLimits = ObservationLimits()):
    return build_observation(parse_snapshot(snapshot), sequence=1, url="http://x/", title="t",
                             viewport=VIEWPORT, limits=limits)


def test_parse_quoting_attrs_and_props():
    nodes = parse_snapshot(
        "- generic [ref=e1]:\n"
        "  - 'button \"Say \\\"hi\\\": now\" [ref=e2] [box=8,9,94,21]'\n"
        "  - 'link \"Docs: API\" [ref=e3] [cursor=pointer]':\n"
        "    - /url: /docs?a=1\n"
        "  - checkbox \"Remember me\" [checked] [ref=e4]\n"
        "  - heading \"Title\" [level=2] [ref=e5]\n"
    )
    button, link, checkbox, heading = nodes[0].children
    assert (button.role, button.name, button.ref) == ("button", 'Say "hi": now', "e2")
    assert button.box.width == 94
    assert link.props == {"url": "/docs?a=1"}
    assert checkbox.attrs["checked"] is True
    assert heading.attrs["level"] == "2"


def test_outline_structure_and_elements():
    obs = observe(
        "- main [ref=e1]:\n"
        "  - heading \"Customers\" [level=1] [ref=e2]\n"
        "  - generic [ref=e3]:\n"
        "    - text: Email\n"
        "    - textbox \"Email\" [ref=e4]: a@b.c\n"
        "  - combobox \"Country\" [ref=e5]:\n"
        "    - option \"India\"\n"
        "    - option \"Japan\" [selected]\n"
        "  - table \"People\" [ref=e6]:\n"
        "    - rowgroup [ref=e7]:\n"
        "      - row [ref=e8]:\n"
        "        - cell \"Ada\" [ref=e9]\n"
        "        - cell \"ada@x.io\" [ref=e10]\n"
        "  - status [ref=e11]: Saved\n"
    )
    assert obs.text.splitlines() == [
        "main",
        '  heading "Customers" (h1)',
        '  [e4] textbox "Email" = "a@b.c"',
        '  [e5] combobox "Country" = "Japan" options: India, Japan',
        '  table "People"',
        "    row: Ada | ada@x.io",
        "  status: Saved",
    ]
    email = next(e for e in obs.elements if e.ref == "e4")
    assert email.value == "a@b.c"
    assert email.context == ["main"]


def test_list_item_context_labels():
    obs = observe(
        "- list [ref=e1]:\n"
        "  - listitem [ref=e2]:\n"
        "    - text: Alpha\n"
        "    - button \"Edit\" [ref=e3]\n"
        "  - listitem [ref=e4]:\n"
        "    - text: Beta\n"
        "    - button \"Edit\" [ref=e5]\n"
    )
    contexts = [e.context for e in obs.elements]
    assert contexts == [["list", 'listitem "Alpha"'], ["list", 'listitem "Beta"']]


def test_clickable_generic_is_an_element_but_its_children_are_not():
    obs = observe(
        "- generic [ref=e1] [cursor=pointer]: Open menu\n"
        "- link \"Home\" [ref=e2] [cursor=pointer]:\n"
        "  - generic [ref=e3] [cursor=pointer]: Home\n"
    )
    assert [(e.ref, e.role, e.name) for e in obs.elements] == [("e1", "generic", "Open menu"), ("e2", "link", "Home")]


def test_iframe_elements_get_structural_frame_and_offset():
    obs = observe(
        "- iframe [ref=e1] [box=100,500,300,200]:\n"
        "  - button \"Inside\" [ref=f7e2] [box=10,10,50,20]\n"
    )
    inside = obs.elements[0]
    assert inside.frame == "iframe-1"
    assert inside.box.y == 510
    assert inside.context == ["iframe"]


def test_limits_keep_viewport_and_structure_first():
    lines = ["- main [ref=e1]:", '  - heading "Big" [level=1] [ref=e2] [box=0,0,100,20]']
    for i in range(100):
        y = i * 100
        lines.append(f'  - button "Button {i}" [ref=e{i + 10}] [box=0,{y},100,20]')
    obs = observe("\n".join(lines) + "\n", ObservationLimits(max_chars=600, max_elements=200))

    assert 'heading "Big"' in obs.text
    assert '"Button 0"' in obs.text  # in viewport
    assert '"Button 99"' not in obs.text  # far below
    assert obs.omitted_lines > 0
    assert "more lines (scroll to see them)" in obs.text
    assert len(obs.elements) == 100  # every element stays resolvable


def test_fingerprint_ignores_refs():
    a = observe('- button "Save" [ref=e1]\n')
    b = observe('- button "Save" [ref=f3e40]\n')
    c = observe('- button "Cancel" [ref=e1]\n')
    assert a.fingerprint == b.fingerprint != c.fingerprint
