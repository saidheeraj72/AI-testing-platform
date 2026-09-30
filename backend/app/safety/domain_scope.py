"""Which URLs the agent may navigate to, and which requests count as first-party.

Two separate questions:

- allows(url): may the browser navigate a page here? Enforced in code for
  every navigation, whatever the model asks for.
- is_first_party(url): does this request belong to the application under
  test? Used to classify network errors. It is broader than navigation
  scope, e.g. an app on localhost:3000 calling its API on localhost:8000.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from urllib.parse import urlsplit

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})

# Minimal multi-label public suffixes for the first-party heuristic.
# Not a full public-suffix list; extend when a real target needs it.
_TWO_LABEL_SUFFIXES = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au", "co.in", "net.in",
    "org.in", "co.jp", "co.nz", "com.br", "com.cn", "com.mx", "co.za", "com.sg",
})
_NON_HTTP_ALLOWED = frozenset({"about:blank"})


class ScopeError(ValueError):
    pass


@dataclass(frozen=True)
class DomainScope:
    target_url: str
    host: str
    port: int | None
    extra_domains: tuple[str, ...] = field(default=())

    @classmethod
    def from_target(cls, target_url: str, extra_domains: list[str] | tuple[str, ...] = ()) -> DomainScope:
        parts = urlsplit(target_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ScopeError(f"Target must be an http(s) URL with a host, got {target_url!r}")
        return cls(
            target_url=target_url,
            host=_normalize_host(parts.hostname),
            port=_effective_port(parts.scheme, parts.port),
            extra_domains=tuple(_normalize_host(d.removeprefix("*.")) for d in extra_domains),
        )

    @property
    def is_local(self) -> bool:
        return self.host in LOOPBACK_HOSTS or _is_ip(self.host)

    def allows(self, url: str) -> bool:
        if url in _NON_HTTP_ALLOWED:
            return True
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return False
        host = _normalize_host(parts.hostname)
        port = _effective_port(parts.scheme, parts.port)

        if any(_same_or_subdomain(host, d) for d in self.extra_domains):
            return True
        if self.host in LOOPBACK_HOSTS:
            # Other ports on this machine are other apps: keep to the target's.
            return host in LOOPBACK_HOSTS and port == self.port
        if _is_ip(self.host):
            return host == self.host and port == self.port
        return _same_or_subdomain(host, self.host)

    def is_first_party(self, url: str) -> bool:
        parts = urlsplit(url)
        if not parts.hostname:
            return False
        host = _normalize_host(parts.hostname)
        if any(_same_or_subdomain(host, d) for d in self.extra_domains):
            return True
        if self.host in LOOPBACK_HOSTS:
            return host in LOOPBACK_HOSTS
        if _is_ip(self.host):
            return host == self.host
        return _site(host) == _site(self.host)

    def describe(self) -> str:
        if self.host in LOOPBACK_HOSTS:
            base = f"localhost:{self.port}"
        elif _is_ip(self.host):
            base = f"{self.host}:{self.port}"
        else:
            base = f"{self.host} and *.{self.host}"
        extras = "".join(f", {d} and *.{d}" for d in self.extra_domains)
        return base + extras


def _normalize_host(host: str) -> str:
    return host.strip().lower().rstrip(".").strip("[]")


def _effective_port(scheme: str, port: int | None) -> int | None:
    if port is not None:
        return port
    return {"http": 80, "https": 443}.get(scheme)


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _same_or_subdomain(host: str, base: str) -> bool:
    return host == base or host.endswith("." + base)


def _site(host: str) -> str:
    """Approximate registrable domain: example.co.uk, company.com."""
    if _is_ip(host):
        return host
    labels = host.split(".")
    n = 3 if ".".join(labels[-2:]) in _TWO_LABEL_SUFFIXES else 2
    return ".".join(labels[-n:])
