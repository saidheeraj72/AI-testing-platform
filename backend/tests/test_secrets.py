import yaml

from app.browser.observation import build_observation
from app.browser.snapshot import parse_snapshot
from app.safety.secrets import MASK, is_secret_field, mask_snapshot
from app.schemas.observation import Viewport

RAW = (
    "- generic [ref=e1]:\n"
    '  - textbox "Email" [ref=e2]: demo@example.com\n'
    '  - textbox "Password" [active] [ref=e3]: hunter2\n'
    "  - 'textbox \"Confirm password: again\" [ref=e4]': hunter2\n"
    '  - textbox "One-time code" [ref=e5]: "123456"\n'
)


def test_secret_field_names():
    assert all(map(is_secret_field, ["Password", "Confirm password", "OTP", "Card number", "PIN", "API token"]))
    assert not any(map(is_secret_field, ["Email", "Passenger name", "Spinner", "Pinned notes"]))


def test_observation_masks_secret_values():
    obs = build_observation(parse_snapshot(RAW), sequence=1, url="u", title="t", viewport=Viewport(width=1, height=1))
    values = {e.name: e.value for e in obs.elements}
    assert values["Email"] == "demo@example.com"
    assert values["Password"] == values["Confirm password: again"] == values["One-time code"] == MASK
    assert "hunter2" not in obs.text and "123456" not in obs.text


def test_raw_snapshot_masked_and_still_valid_yaml():
    masked = mask_snapshot(RAW)
    assert "hunter2" not in masked and "123456" not in masked
    assert "demo@example.com" in masked
    assert yaml.safe_load(masked)
