import pytest

from app.safety.domain_scope import DomainScope, ScopeError


@pytest.mark.parametrize("url, allowed", [
    ("https://staging.company.com/customers", True),
    ("https://api.staging.company.com/x", True),
    ("http://staging.company.com:8443/", True),
    ("https://company.com/", False),
    ("https://evilstaging.company.com/", False),
    ("https://staging.company.com.evil.io/", False),
    ("https://gmail.com/", False),
    ("javascript:alert(1)", False),
    ("file:///etc/passwd", False),
    ("about:blank", True),
])
def test_domain_target(url, allowed):
    assert DomainScope.from_target("https://staging.company.com").allows(url) is allowed


@pytest.mark.parametrize("url, allowed", [
    ("http://localhost:3000/cart", True),
    ("http://127.0.0.1:3000/", True),
    ("http://localhost:8000/", False),  # another local app
    ("http://localhost/", False),
    ("https://example.com/", False),
])
def test_localhost_target_keeps_port(url, allowed):
    assert DomainScope.from_target("http://localhost:3000").allows(url) is allowed


def test_private_ip_target_is_exact():
    scope = DomainScope.from_target("http://192.168.1.20:8080")
    assert scope.allows("http://192.168.1.20:8080/a")
    assert not scope.allows("http://192.168.1.21:8080/a")
    assert not scope.allows("http://192.168.1.20:9090/a")


def test_extra_domains_for_sso():
    scope = DomainScope.from_target("https://app.company.com", ["*.okta.com"])
    assert scope.allows("https://company.okta.com/login")
    assert not scope.allows("https://okta.com.evil.io/")


def test_first_party_is_broader_than_navigation():
    scope = DomainScope.from_target("https://app.company.com")
    assert scope.is_first_party("https://api.company.com/v1/customers")
    assert not scope.allows("https://api.company.com/v1/customers")
    assert not scope.is_first_party("https://www.google-analytics.com/collect")

    local = DomainScope.from_target("http://localhost:3000")
    assert local.is_first_party("http://localhost:8000/api")
    assert not local.is_first_party("https://analytics.seeded-app.invalid/tracker.js")


def test_multi_label_suffix():
    scope = DomainScope.from_target("https://shop.example.co.uk")
    assert scope.is_first_party("https://api.example.co.uk/x")
    assert not scope.is_first_party("https://other.co.uk/x")


@pytest.mark.parametrize("bad", ["localhost:3000", "ftp://x.com", "", "https://"])
def test_invalid_target(bad):
    with pytest.raises(ScopeError):
        DomainScope.from_target(bad)
