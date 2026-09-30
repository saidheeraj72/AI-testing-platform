"""Ground-truth check: each seeded bug shows up in the real UI exactly when enabled.

Opt-in. Start the seeded app first, then:

    SEEDED_APP_URL=http://localhost:3000 uv run pytest -m seeded_app

The tests read the active bugs from /__bench/config and assert either the buggy
or the correct behaviour, so they pass for `npm run dev` and `npm run dev:clean`.
They drive the installed Chrome headless (channel="chrome").
"""

import json
import os
import re
import urllib.request

import pytest

pytestmark = pytest.mark.seeded_app

APP_URL = os.environ.get("SEEDED_APP_URL")
if not APP_URL:
    pytest.skip("SEEDED_APP_URL not set", allow_module_level=True)

from playwright.sync_api import Page, expect, sync_playwright  # noqa: E402


def _bench(path: str, method: str = "GET"):
    req = urllib.request.Request(f"{APP_URL}{path}", method=method)
    with urllib.request.urlopen(req) as res:
        body = res.read()
    return json.loads(body) if body else None


ACTIVE = set(_bench("/__bench/config")["bugs"])


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    _bench("/__bench/reset", "POST")
    context = browser.new_context(base_url=APP_URL)
    page = context.new_page()
    page.goto("/login")
    page.get_by_label("Email").fill("demo@example.com")
    page.get_by_label("Password").fill("password123")
    page.get_by_role("button", name="Sign in").click()
    expect(page).to_have_url(re.compile(r"/dashboard$"))
    yield page
    context.close()


def test_bug_001_customer_create(page: Page):
    page.goto("/customers/new")
    page.get_by_label("Name").fill("John-e2e")
    page.get_by_label("Email").fill("john-e2e@example.com")
    with page.expect_response("**/api/customers") as response:
        page.get_by_role("button", name="Create customer").click()

    if "BUG-001" in ACTIVE:
        assert response.value.status == 500
        expect(page.get_by_role("alert")).to_contain_text("Could not create customer")
    else:
        assert response.value.status == 201
        expect(page).to_have_url(re.compile(r"/customers$"))
        expect(page.get_by_role("table", name="Customers")).to_contain_text("John-e2e")


def test_bug_002_cart_total(page: Page):
    page.goto("/shop")
    page.get_by_label("Quantity for Wireless Mouse").fill("2")
    page.get_by_role("button", name="Add Wireless Mouse to cart").click()
    expect(page.get_by_role("status")).to_contain_text("Wireless Mouse")
    page.get_by_role("button", name="Add USB-C Hub to cart").click()
    expect(page.get_by_role("status")).to_contain_text("USB-C Hub")
    page.goto("/cart")

    # 2 x 24.99 + 1 x 34.50 = 84.48; the bug ignores quantity: 24.99 + 34.50 = 59.49
    expected = "$59.49" if "BUG-002" in ACTIVE else "$84.48"
    expect(page.get_by_test_id("cart-total")).to_have_text(expected)


def test_bug_003_settings_email_validation(page: Page):
    page.goto("/settings")
    page.get_by_label("Email", exact=True).fill("not-an-email")
    page.get_by_role("button", name="Save settings").click()

    if "BUG-003" in ACTIVE:
        expect(page.get_by_role("status")).to_have_text("Settings saved.")
        page.reload()
        expect(page.get_by_label("Email", exact=True)).to_have_value("not-an-email")
    else:
        expect(page.get_by_role("alert")).to_have_text("Enter a valid email address")
        page.reload()
        expect(page.get_by_label("Email", exact=True)).to_have_value("demo@example.com")


def test_bug_004_logout_redirect(page: Page):
    with page.expect_response("**/api/logout"):
        page.get_by_role("button", name="Log out").click()

    if "BUG-004" in ACTIVE:
        page.wait_for_timeout(500)
        expect(page).to_have_url(re.compile(r"/dashboard$"))
    else:
        expect(page).to_have_url(re.compile(r"/login$"))
        expect(page.get_by_role("status")).to_have_text("You have been logged out.")


def test_checkout_control_has_no_bug(page: Page):
    page.goto("/shop")
    page.get_by_role("button", name="Add Mechanical Keyboard to cart").click()
    expect(page.get_by_role("status")).to_contain_text("Mechanical Keyboard")
    page.goto("/checkout")
    expect(page.get_by_text("Order total:")).to_contain_text("$89.99")
    page.get_by_label("Shipping address").fill("1 Test Street")
    page.get_by_role("button", name="Place order").click()
    expect(page.get_by_role("heading", name="Order confirmed")).to_be_visible()
    expect(page.get_by_text("Order number:")).to_contain_text("ORD-1001")


def test_noise_is_present(page: Page):
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.reload()
    expect(page.get_by_role("heading", name=re.compile("Welcome back"))).to_be_visible()
    assert any("legacy-widget" in e for e in errors), errors
