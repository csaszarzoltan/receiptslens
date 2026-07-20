"""Pre-development acceptance tests for the SSRF-safe URL validator.

Module under test: ``app/security.py`` -> ``validate_image_url(url: str) -> None``

These tests encode the P0-1 acceptance criteria from
``analysis/analysis-brief.md`` §5. The target module is NOT yet implemented, so
this file is RED until the developer lands ``app/security.py`` with the
``validate_image_url`` function. Each test fails with an explicit
"not implemented" message rather than an opaque collection error.

No real network is touched: ``socket.getaddrinfo`` is monkeypatched in every
test that would otherwise perform DNS resolution.
"""
from __future__ import annotations

import ipaddress
import socket

import pytest

# Guarded import: until app/security.py exists, keep the module collectable so
# every test reports a clear, granular RED failure instead of a single opaque
# collection error. The autouse fixture below turns the missing symbol into an
# explicit message.
try:  # pragma: no cover - import guard for pre-development state
    from app.security import validate_image_url
except Exception:  # noqa: BLE001 - we deliberately fall back to None
    validate_image_url = None


@pytest.fixture(autouse=True)
def _require_security_module():
    """Fail clearly (red) until app/security.py is implemented."""
    if validate_image_url is None:
        pytest.fail(
            "app/security.py:validate_image_url is not implemented yet "
            "(expected pre-development RED state)"
        )


def _fake_getaddrinfo_factory(resolve_to: str):
    """Return a socket.getaddrinfo stub resolving every host to ``resolve_to``."""

    def _fake(host, port, family=0, type=0, proto=0, flags=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (resolve_to, port or 80))]

    return _fake


@pytest.fixture
def stub_dns_public(monkeypatch):
    """Resolve all hostnames to a public IP (no real network)."""
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo_factory(PUBLIC_IP))


@pytest.fixture
def stub_dns_private(monkeypatch):
    """Resolve all hostnames to a private IP (no real network)."""
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo_factory(PRIVATE_IP))


# A clearly public IPv4 address used to stub DNS for the "accept" cases.
PUBLIC_IP = "8.8.8.8"
# A private IPv4 address used to stub DNS for the IP-literal reject cases, so
# the test stays red regardless of whether the implementation validates the
# literal host directly or after resolution.
PRIVATE_IP = "10.0.0.9"


# ---------------------------------------------------------------------------
# Scheme rejection (§5 P0-1: file://, ftp://, gopher://, empty scheme)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",                       # file scheme
        "ftp://example.com/x.jpg",                  # ftp scheme
        "gopher://example.com/x",                   # gopher scheme
        "example.com/receipt.jpg",                  # empty scheme (parsed as path)
        "//example.com/receipt.jpg",                # empty scheme, host present
    ],
)
def test_rejects_non_http_schemes(stub_dns_public, url):
    with pytest.raises(ValueError):
        validate_image_url(url)


# ---------------------------------------------------------------------------
# IP-literal host rejection (§5 P0-1: private/loopback/link-local/reserved/multicast)
# ---------------------------------------------------------------------------


IP_LITERAL_REJECT = [
    ("http://10.0.0.1/x.jpg", "private 10/8"),
    ("http://172.16.0.1/x.jpg", "private 172.16/12"),
    ("http://172.31.255.255/x.jpg", "private 172.16/12 (high)"),
    ("http://192.168.0.1/x.jpg", "private 192.168/16"),
    ("http://127.0.0.1/x.jpg", "loopback 127/8"),
    ("http://127.255.255.254/x.jpg", "loopback 127/8 (high)"),
    ("http://169.254.0.1/x.jpg", "link-local 169.254/16"),
    ("http://0.0.0.0/x.jpg", "reserved 0.0.0.0"),
    ("http://240.0.0.1/x.jpg", "reserved 240/4"),
    ("http://224.0.0.1/x.jpg", "multicast 224/4"),
    ("http://[::1]/x.jpg", "loopback ::1"),
    ("http://[fe80::1]/x.jpg", "link-local fe80::/10"),
    ("http://[ff02::1]/x.jpg", "multicast ff02::/16"),
]


@pytest.mark.parametrize("url,desc", IP_LITERAL_REJECT)
def test_rejects_private_ip_literals(stub_dns_private, url, desc):
    with pytest.raises(ValueError):
        validate_image_url(url)


# ---------------------------------------------------------------------------
# DNS resolution rejection (§5 P0-1: hostname resolving to a blocked range)
# ---------------------------------------------------------------------------


def test_rejects_hostname_resolving_to_private(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo_factory("10.0.0.5"))
    with pytest.raises(ValueError):
        validate_image_url("http://images.internal.example/x.jpg")


def test_rejects_hostname_resolving_to_loopback(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo_factory("127.0.0.1"))
    with pytest.raises(ValueError):
        validate_image_url("http://localhost.example/x.jpg")


# ---------------------------------------------------------------------------
# Missing / empty host rejection (§5 P0-1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https:///receipt.jpg",   # empty host
        "http:///x.jpg",          # empty host
    ],
)
def test_rejects_empty_host(stub_dns_public, url):
    with pytest.raises(ValueError):
        validate_image_url(url)


# ---------------------------------------------------------------------------
# Acceptance of public URLs (§5 P0-1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/receipt.jpg",
        "http://images.example.com/r.png",
    ],
)
def test_accepts_public_urls(stub_dns_public, url):
    # Must not raise.
    validate_image_url(url)


# ---------------------------------------------------------------------------
# Sanity check that the DNS stub returns a genuinely public address
# ---------------------------------------------------------------------------


def test_public_ip_is_global():
    assert ipaddress.ip_address(PUBLIC_IP).is_global
