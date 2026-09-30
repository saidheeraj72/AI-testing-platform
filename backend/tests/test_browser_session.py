"""BrowserSession against a real headless Chrome and the local fixture site."""

import json

import pytest

from app.browser.observation import ObservationLimits
from app.browser.profile import ProfileInUseError
from app.browser.session import BrowserConfig, BrowserLaunchError, BrowserSession
from app.safety.domain_scope import DomainScope
from app.schemas.action import ActionError
from app.storage.manager import SessionStorage

pytestmark = pytest.mark.browser


def make_session(site, tmp_path, **config) -> BrowserSession:
    storage = SessionStorage.create(tmp_path / "sessions")
    return BrowserSession(DomainScope.from_target(site), storage, BrowserConfig(headless=True, **config))


@pytest.fixture
async def browser(site, tmp_path):
    session = make_session(site, tmp_path)
    try:
        await session.start()
    except BrowserLaunchError as e:
        pytest.skip(str(e))
    await session.navigate("/")
    yield session
    await session.stop()


def ref(observation, role, name):
    matches = [e.ref for e in observation.elements if e.role == role and e.name == name]
    assert len(matches) == 1, f"{role} {name!r}: {matches}\n{observation.text}"
    return matches[0]


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


async def test_form_actions(browser):
    obs = await browser.observe()
    assert (await browser.type(ref(obs, "textbox", "Name"), "John")).ok
    assert (await browser.type(ref(obs, "textbox", "Password"), "hunter2")).ok
    assert (await browser.select(ref(obs, "combobox", "Country"), "France")).ok
    assert (await browser.click(ref(obs, "checkbox", "I agree"))).ok
    assert (await browser.click(ref(obs, "button", "Submit form"))).ok

    obs = await browser.observe()
    assert "status: Submitted John / fr / true" in obs.text

    logged = read_jsonl(browser.storage.paths.actions)
    password_action = next(a for a in logged if a["target"] and a["target"]["name"] == "Password")
    assert password_action["arguments"]["text"] == "[redacted]"


async def test_type_with_submit_presses_enter(browser):
    obs = await browser.observe()
    await browser.type(ref(obs, "textbox", "Name"), "Ada", submit=True)
    assert "Submitted Ada" in (await browser.observe()).text


async def test_failed_api_call_is_captured_with_evidence(browser):
    obs = await browser.observe()
    result = await browser.click(ref(obs, "button", "Call failing API"))
    assert result.ok

    failed = [e for e in browser.network.since(result.sequence) if e.status == 500]
    assert len(failed) == 1
    event = failed[0]
    assert event.method == "POST" and event.first_party
    assert json.loads(event.request_body) == {"token": "[redacted]", "query": "x"}
    assert "boom" in event.response_body
    assert event.action_seq == result.sequence


async def test_settle_waits_for_slow_request(browser):
    obs = await browser.observe()
    result = await browser.click(ref(obs, "button", "Call slow API"))
    assert result.settled
    assert result.duration_ms >= 800
    assert "status: Slow done" in (await browser.observe()).text


async def test_uncaught_exception_is_recorded(browser):
    obs = await browser.observe()
    result = await browser.click(ref(obs, "button", "Throw error"))
    errors = [e for e in browser.console.since(result.sequence) if e.kind == "pageerror"]
    assert errors and "Kaboom from fixture" in errors[0].text


async def test_confirm_dialog_is_dismissed_and_reported(browser):
    obs = await browser.observe()
    await browser.click(ref(obs, "button", "Delete everything"))
    obs = await browser.observe()
    assert "Deleted" not in obs.text
    assert any("Delete everything?" in n for n in obs.notices)


async def test_rerendered_element_is_resolved_again(browser):
    obs = await browser.observe()
    save = ref(obs, "button", "Save")
    await browser.click(ref(obs, "button", "Rerender save"))

    result = await browser.click(save)  # old ref, node was replaced
    assert result.ok
    assert result.resolved_ref != save
    assert "status: Saved" in (await browser.observe()).text


async def test_rebuilt_list_clicks_the_same_row_not_the_same_position(browser):
    obs = await browser.observe()
    beta_edit = next(e.ref for e in obs.elements if e.name == "Edit" and 'listitem "Beta"' in e.context)
    await browser.click(ref(obs, "button", "Rebuild rows"))  # now Gamma, Alpha, Beta; all nodes new

    result = await browser.click(beta_edit)
    assert result.ok
    assert "status: Edit Beta" in (await browser.observe()).text


async def test_unknown_and_removed_elements(browser):
    result = await browser.click("e999")
    assert result.error == ActionError.UNKNOWN_REF

    obs = await browser.observe()
    save = ref(obs, "button", "Save")
    await browser.page.evaluate("document.querySelector('#saver').remove()")
    result = await browser.click(save)
    assert result.error == ActionError.ELEMENT_NOT_FOUND


async def test_iframe_and_shadow_dom(browser):
    obs = await browser.observe()
    assert (await browser.click(ref(obs, "button", "Frame button"))).ok
    assert (await browser.click(ref(obs, "button", "Shadow button"))).ok
    text = (await browser.observe()).text
    assert "Frame clicked" in text
    assert "Shadow clicked" in text


async def test_click_leaving_scope_is_blocked_and_recovers(browser, site, other_site):
    await browser.page.evaluate(
        "(url) => { const a = document.createElement('a'); a.href = url; a.textContent = 'Off site';"
        " document.querySelector('main').append(a); }",
        f"{other_site}/page2",
    )
    obs = await browser.observe()
    result = await browser.click(ref(obs, "link", "Off site"))
    assert result.error == ActionError.BLOCKED_NAVIGATION
    assert result.url_after.startswith(site)
    assert "Fixture home" in (await browser.observe()).text


async def test_redirect_leaving_scope_is_blocked(browser, site):
    obs = await browser.observe()
    result = await browser.click(ref(obs, "link", "Leave site"))
    assert result.error == ActionError.BLOCKED_NAVIGATION
    assert result.url_after.startswith(site)


async def test_navigate_out_of_scope_sends_no_request(browser, other_site):
    result = await browser.navigate(f"{other_site}/page2")
    assert result.error == ActionError.BLOCKED_NAVIGATION
    assert not any(e.url.startswith(other_site) for e in browser.network.events)


async def test_popup_becomes_active_page(browser):
    obs = await browser.observe()
    await browser.click(ref(obs, "button", "Open popup"))
    obs = await browser.observe()
    assert obs.title == "Page two"
    assert any("new tab" in n for n in obs.notices)


async def test_navigation_and_back(browser, site):
    obs = await browser.observe()
    result = await browser.click(ref(obs, "link", "Page two"))
    assert result.navigated and result.url_after == f"{site}/page2"
    result = await browser.go_back()
    assert result.url_after == f"{site}/"

    result = await browser.navigate("/missing")
    assert result.ok and result.http_status == 404


async def test_large_page_is_trimmed_and_scrollable(site, tmp_path):
    async with make_session(site, tmp_path, limits=ObservationLimits(max_chars=1500)) as session:
        await session.navigate("/long")
        obs = await session.observe()
        assert obs.omitted_lines > 0
        assert '"Button 1"' in obs.text
        assert '"Button 200"' not in obs.text
        assert len(obs.elements) == 200

        await session.scroll("bottom")
        obs = await session.observe()
        assert obs.viewport.scroll_y > 0
        assert '"Button 200"' in obs.text


async def test_stop_saves_trace_and_is_idempotent(site, tmp_path):
    session = make_session(site, tmp_path)
    await session.start()
    await session.navigate("/")
    await session.observe(screenshot=True)
    await session.stop()
    await session.stop()

    paths = session.storage.paths
    assert paths.trace.stat().st_size > 0
    assert paths.screenshot(1).exists()
    assert paths.observation(1).exists()
    manifest = json.loads(paths.manifest.read_text())
    assert manifest["trace"] == "trace.zip" and manifest["finished_at"]
    with pytest.raises(RuntimeError):
        await session.navigate("/")


async def test_persistent_profile_keeps_login_and_is_exclusive(site, tmp_path):
    profile = tmp_path / "profile"
    async with make_session(site, tmp_path / "a", profile_dir=profile) as first:
        await first.navigate("/set-cookie")
        with pytest.raises(ProfileInUseError):
            await make_session(site, tmp_path / "b", profile_dir=profile).start()

    async with make_session(site, tmp_path / "c", profile_dir=profile) as later:
        await later.navigate("/whoami")
        assert "remember=yes" in (await later.observe()).text
