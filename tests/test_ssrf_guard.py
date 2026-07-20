"""Unit tests for app.ssrf_guard.

These tests rely on monkeypatching `resolve_addresses` / HTTP calls so they
cover hostname validation and IP rejections without needing external DNS/network.
"""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import socket

from app.ssrf_guard import (
    _SSRFGuardClient,
    count_bytes,
    resolve_and_validate,
    validate_url,
)


class _Response:
    def __init__(self, status_code=200, content=b"", headers=None, is_redirect=False):
        self.status_code = status_code
        self.headers = httpx.Headers(headers or {})
        self.is_redirect = is_redirect
        self.is_closed = False
        self._content = content

    @property
    def content(self):
        return self._content

    def iter_raw(self, chunk_size=None):
        yield self._content

    def read(self):
        return self._content

    def close(self):
        self.is_closed = True


def _mock_response(status_code: int = 200, content: bytes = b"", headers: dict | None = None, is_redirect: bool = False):
    return _Response(status_code=status_code, content=content, headers=headers, is_redirect=is_redirect)


def test_allowed_schemes_http_and_https():
    for scheme in ("http", "https"):
        parsed = resolve_and_validate(f"{scheme}://example.com/image.jpg")
        assert parsed.scheme == scheme


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/image.png", "gopher://evil/x"])
def test_rejects_blocked_schemes(url):
    with pytest.raises(ValueError, match="unsupported url scheme"):
        resolve_and_validate(url)


@pytest.mark.parametrize("url", ["https://localhost/image", "https://local.host/image", "https://service.internal/image", "https://169.254.169.254/latest/meta-data/"])
def test_rejects_blocked_hosts(url):
    with pytest.raises(ValueError, match="(blocked hostname|unsupported url scheme)"):
        resolve_and_validate(url)


@pytest.mark.parametrize("addr", ["127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.169.254", "172.16.0.1", "100.64.0.1", "::1", "fc00::1", "224.0.0.1", "0.0.0.1"])
def test_rejects_reserved_ips(addr):
    with patch("app.ssrf_guard.socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, 80))]):
        with pytest.raises(ValueError, match="reserved ip rejected|unresolvable host"):
            resolve_and_validate("http://example.com/image.jpg")


def test_allows_public_ip():
    public_ip = "93.184.216.34"
    url = f"http://{public_ip}/image.jpg"
    request = validate_url(url)
    assert public_ip in str(str(request.url))


def test_fetch_allows_successful_response():
    request = validate_url("http://example.com/image.jpg")
    client = _SSRFGuardClient()
    response = _mock_response(content=b"ok", headers={"Content-Type": "image/png"})
    with patch("app.ssrf_guard.socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]):
        with patch.object(httpx.Client, "send", return_value=response):
            out = client.fetch(request)
    assert out == b"ok"


def test_response_size_capped():
    request = validate_url("http://example.com/image.jpg")
    client = _SSRFGuardClient()
    with patch("app.ssrf_guard.socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]):
        with patch.object(httpx.Client, "send") as send_mock:
            stream_response = _mock_response(content=b"a" * 15, headers={"Content-Type": "image/png"})
            stream_response.is_redirect = False
            send_mock.return_value = stream_response
            with pytest.raises(ValueError, match="response exceeds max bytes"):
                client.fetch(request, max_bytes=10)


def test_redirect_to_internal_blocked():
    request = validate_url("http://example.com/image.jpg")
    client = _SSRFGuardClient()
    redirect_response = _mock_response(status_code=302, headers={"Location": "http://127.0.0.1/image.jpg"}, is_redirect=True)

    def _smart_getaddrinfo(host, port, *args, **kwargs):
        """Resolve IP literals as-is; map names to a public IP."""
        import ipaddress as _ip
        try:
            _ip.ip_address(host)
            addr = host
        except ValueError:
            addr = "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, port or 80))]

    with patch("app.ssrf_guard.socket.getaddrinfo", side_effect=_smart_getaddrinfo):
        with patch.object(httpx.Client, "send", return_value=redirect_response):
            with pytest.raises(ValueError, match="reserved ip rejected"):
                client.fetch(request)


def test_redirect_to_valid_host_allowed():
    request = validate_url("http://example.com/image.jpg")
    client = _SSRFGuardClient()
    redirect_response = _mock_response(status_code=302, headers={"Location": "http://93.184.216.34/image.jpg"}, is_redirect=True)
    final_response = _mock_response(content=b"ok", headers={"Content-Type": "image/png"})
    with patch("app.ssrf_guard.socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]):
        with patch.object(httpx.Client, "send", side_effect=[redirect_response, final_response]):
            out = client.fetch(request)
    assert out == b"ok"


def test_count_bytes_under_limit():
    assert list(count_bytes([b"hello", b"world"], 10)) == [b"hello", b"world"]


def test_count_bytes_exceed_limit_raises():
    with pytest.raises(ValueError, match="response exceeds max bytes"):
        list(count_bytes([b"a" * 6], 5))
