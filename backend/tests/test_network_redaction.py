import json

from app.browser.network import BODY_LIMIT, REDACTED, _redact_body


def test_json_secrets_redacted_recursively():
    body = json.dumps({"email": "a@b.c", "password": "hunter2", "card": {"cvv": "123"}, "items": [{"api_key": "k"}]})
    out = json.loads(_redact_body(body, "application/json"))
    assert out["email"] == "a@b.c"
    assert out["password"] == REDACTED
    assert out["card"] == REDACTED
    assert out["items"][0]["api_key"] == REDACTED


def test_form_encoded_redacted():
    out = _redact_body("user=bob&passwd=x&otp_code=1", "application/x-www-form-urlencoded")
    assert out == f"user=bob&passwd={REDACTED.replace('[', '%5B').replace(']', '%5D')}&otp_code=%5Bredacted%5D"


def test_plain_body_truncated():
    out = _redact_body("x" * (BODY_LIMIT + 50), "text/plain")
    assert out.startswith("x" * BODY_LIMIT)
    assert out.endswith("[50 more chars]")


def test_empty():
    assert _redact_body(None, "") is None
