"""BrowserSession on the seeded benchmark app: the evidence Phase 3 will need is captured.

    SEEDED_APP_URL=http://localhost:3000 uv run pytest -m seeded_app
"""

import json
import os
import urllib.request

import pytest

from app.browser.session import BrowserConfig, BrowserSession
from app.safety.domain_scope import DomainScope
from app.storage.manager import SessionStorage

pytestmark = pytest.mark.seeded_app
APP_URL = os.environ.get("SEEDED_APP_URL")
if not APP_URL:
    pytest.skip("SEEDED_APP_URL not set", allow_module_level=True)


def ref(obs, role, name):
    return next(e.ref for e in obs.elements if e.role == role and e.name == name)


async def test_customer_creation_evidence(tmp_path):
    urllib.request.urlopen(urllib.request.Request(f"{APP_URL}/__bench/reset", method="POST"))
    active = json.load(urllib.request.urlopen(f"{APP_URL}/__bench/config"))["bugs"]
    storage = SessionStorage.create(tmp_path)

    async with BrowserSession(DomainScope.from_target(APP_URL), storage, BrowserConfig(headless=True)) as b:
        await b.navigate("/login")
        obs = await b.observe()
        await b.type(ref(obs, "textbox", "Email"), "demo@example.com")
        await b.type(ref(obs, "textbox", "Password"), "password123")
        await b.click(ref(obs, "button", "Sign in"))
        await b.navigate("/customers/new")
        obs = await b.observe()
        await b.type(ref(obs, "textbox", "Name"), "John-e2e")
        await b.type(ref(obs, "textbox", "Email"), "john-e2e@example.com")
        submit = await b.click(ref(obs, "button", "Create customer"))

        after = b.network.since(submit.sequence)
        create = next(e for e in after if e.method == "POST" and e.url.endswith("/api/customers"))
        if "BUG-001" in active:
            assert create.status == 500 and create.first_party
            assert any("status 500" in e.text for e in b.console.since(submit.sequence))
            assert "alert: Could not create customer" in (await b.observe()).text
        else:
            assert create.status == 201

        # Noise the Phase 3 filters must remove is visible in the raw evidence.
        assert any("analytics.seeded-app.invalid" in e.url and not e.first_party for e in b.network.events)
        assert any("legacy-widget" in e.text for e in b.console.events)
        assert any(e.url.endswith("/api/me") and e.status == 401 for e in b.network.events)
