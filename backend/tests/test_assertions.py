from app.agent.assertions import PageState, VerifyArgs, evaluate
from app.browser.network import NetworkEvent
from app.browser.snapshot import parse_snapshot
from app.schemas.plan import Criterion

PAGE = parse_snapshot(
    "- main [ref=e1]:\n"
    '  - textbox "Name" [ref=e2]: John-typed\n'
    '  - table "Customers" [ref=e3]:\n'
    "    - row [ref=e4]:\n"
    '      - cell "Ada Lovelace" [ref=e5]\n'
    '  - table "Cart" [ref=e6]:\n'
    "    - row [ref=e7]:\n"
    '      - cell "$49.98" [ref=e8]\n'
    '      - cell "$34.50" [ref=e9]\n'
    "    - row [ref=e10]:\n"
    '      - cell "Total" [ref=e11]\n'
    '      - cell "$59.49" [ref=e12]\n'
    '  - alert [ref=e13]: Enter a valid email address\n'
)


def state(url="http://x/customers", network=()):
    return PageState(url=url, nodes=PAGE, network=list(network))


def check(**kw):
    return evaluate(Criterion(**kw), state())


def net(method, url, status=None, failure=None, rtype="fetch"):
    return NetworkEvent(seq=1, action_seq=1, method=method, url=url, resource_type=rtype, first_party=True,
                        main_frame=True, started_at=0, status=status, failure=failure)


def test_text_ignores_what_was_typed_into_fields():
    assert check(type="text_visible", value="Ada Lovelace").passed
    assert not check(type="text_visible", value="John-typed").passed


def test_text_within_container_and_fallback():
    assert check(type="text_visible", value="Ada", within="Customers").passed
    assert not check(type="text_visible", value="Ada", within="Cart").passed
    r = check(type="text_visible", value="Ada", within="Nonexistent")
    assert r.passed and "searched the whole page" in r.detail


def test_negate():
    assert check(type="text_visible", value="Grace", negate=True).passed
    assert not check(type="url_contains", value="/customers", negate=True).passed


def test_element_and_field():
    assert check(type="element_present", role="alert").passed
    assert check(type="element_present", role="alert", name="Enter a valid email").passed
    assert not check(type="element_present", role="alert", name="Saved").passed
    assert check(type="element_present", role="table", name="cart").passed
    assert not check(type="element_present", role="dialog").passed
    assert check(type="field_value", name="Name", value="John-typed").passed
    missing = check(type="field_value", name="Email", value="x")
    assert not missing.passed and missing.inconclusive  # wrong page, not a lost value
    assert check(type="field_value", name="Email", value="x", negate=True).inconclusive


def test_request_succeeded_separates_app_errors_from_missing_requests():
    c = Criterion(type="request_succeeded", value="/api/customers", method="POST")
    assert evaluate(c, state(network=[net("POST", "http://x/api/customers", 201)])).passed

    failed = evaluate(c, state(network=[net("POST", "http://x/api/customers", 500)]))
    assert not failed.passed and failed.app_error

    missing = evaluate(c, state(network=[net("GET", "http://x/api/customers", 200)]))
    assert not missing.passed and not missing.app_error

    aborted = evaluate(c, state(network=[net("POST", "http://x/api/customers", failure="net::ERR_ABORTED")]))
    assert aborted.passed


def test_sum_equals_uses_only_values_on_the_page():
    c = Criterion(type="sum_equals")
    wrong_total = evaluate(c, state(), VerifyArgs(parts=["$49.98", "$34.50"], total="$59.49"))
    assert not wrong_total.passed and "84.48" in wrong_total.detail

    made_up = evaluate(c, state(), VerifyArgs(parts=["$49.98", "$34.50"], total="$84.48"))
    assert not made_up.passed and "not visible" in made_up.detail

    assert not evaluate(c, state()).passed  # no parts supplied
