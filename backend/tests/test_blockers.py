import pytest

from app.agent.blockers import detect_blocker
from app.browser.snapshot import parse_snapshot

LOGIN = ('- heading "Sign in to Acme" [level=1] [ref=e1]\n- textbox "Email" [ref=e2]\n'
         '- textbox "Password" [ref=e3]\n- button "Sign in" [ref=e4]\n')


def blocker(snapshot: str, objective: str = "Check the dashboard"):
    found = detect_blocker(parse_snapshot(snapshot), objective)
    return found.kind if found else None


def test_login_wall_without_credentials():
    assert blocker(LOGIN) == "login_required"


def test_login_with_credentials_in_objective_is_the_agents_job():
    assert blocker(LOGIN, "Log in with a@b.c / password123 and check the dashboard") is None


def test_a_password_field_alone_is_not_a_login_wall():
    assert blocker('- textbox "Password" [ref=e1]\n- button "Save settings" [ref=e2]\n') is None


@pytest.mark.parametrize("snapshot", [
    '- iframe "reCAPTCHA" [ref=e1]\n',
    '- checkbox "I\'m not a robot" [ref=e1]\n',
    '- heading "Verify you are human" [level=1] [ref=e1]\n',
])
def test_captcha(snapshot):
    assert blocker(snapshot, "Log in with password x") == "captcha"


@pytest.mark.parametrize("snapshot", [
    '- textbox "Verification code" [ref=e1]\n- button "Verify" [ref=e2]\n',
    '- paragraph [ref=e1]: We sent a code to your phone\n- textbox "Code" [ref=e2]\n',
])
def test_mfa(snapshot):
    assert blocker(snapshot, "Log in with password x") == "mfa"


def test_ordinary_page():
    assert blocker('- heading "Customers" [level=1] [ref=e1]\n- button "New customer" [ref=e2]\n') is None
