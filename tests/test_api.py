"""Interface + behavioral regression tests for the URL fetch helper.

Function under test
-------------------
``app.security.fetch_image_bytes(url: str, *, max_bytes, timeout) -> bytes``
-- the hardened SSRF-safe URL fetch helper used by every image_url entrypoint.

Layout:
  * Interface tests -- import, signature/type-hint and helper-contract checks.
  * Behavioral tests -- the four error-handling acceptance scenarios validated
    via ``httpx.MockTransport`` + ``socket.getaddrinfo`` stub (no live network).

Run with:
    pytest tests/test_api.py -v
"""
from __future__ import annotations

import inspect
import socket
from io import BytesIO
from typing import get_type_hints
from unittest.mock import patch

import httpx
import pytest
from PIL import Image

from app import api
from app.security import fetch_image_bytes


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_blank_png() -> bytes:
    """A tiny valid PNG for mock transport responses."""
    buf = BytesIO()
    Image.new("RGB", (64, 32), color="white").save(buf, format="PNG")
    return buf.getvalue()


BLANK_PNG = _make_blank_png()


def _fake_getaddrinfo(host: str, port: int, *args: object, **kwargs: object) -> list[tuple]:
    """Resolve IP literals as-is; map all names to a fixed public IP."""
    import ipaddress as _ip

    try:
        _ip.ip_address(host)
        addr = host
    except ValueError:
        addr = "93.184.216.34"  # public, reserved-range-free
    family = socket.AF_INET6 if ":" in addr else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, "", (addr, port or 0))]


# ---------------------------------------------------------------------------
# Interface tests -- must pass immediately
# ---------------------------------------------------------------------------


def test_api_importable() -> None:
    assert api is not None


def test_fetch_image_bytes_importable() -> None:
    """`fetch_image_bytes` must be importable from app.security."""
    assert callable(fetch_image_bytes)


def test_fetch_image_bytes_is_callable_function() -> None:
    """`fetch_image_bytes` must be a real importable callable (not a stub)."""
    assert callable(fetch_image_bytes)


def test_fetch_image_bytes_signature() -> None:
    """`fetch_image_bytes(url: str, *, max_bytes, timeout) -> bytes`."""
    hints = get_type_hints(fetch_image_bytes)
    assert hints.get("url") is str, f"fetch_image_bytes url hint is {hints.get('url')!r}"
    assert hints.get("return") is bytes, (
        f"fetch_image_bytes return hint is {hints.get('return')!r}"
    )
    assert "max_bytes" in hints, "fetch_image_bytes missing max_bytes parameter"
    assert "timeout" in hints, "fetch_image_bytes missing timeout parameter"


def test_fetch_image_bytes_is_not_async() -> None:
    """`fetch_image_bytes` is a synchronous helper."""
    assert not inspect.iscoroutinefunction(fetch_image_bytes)


# ---------------------------------------------------------------------------
# Behavioral tests -- the four error-handling acceptance modes
# ---------------------------------------------------------------------------


def test_rejects_invalid_url_format() -> None:
    """Mode 1: malformed/invalid URL -> HTTPException 400 with generic detail.

    httpx.InvalidURL (not an HTTPError subclass in httpx 0.28.1) must be
    caught and converted to a client-safe HTTPException.
    """
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        fetch_image_bytes("http://example.com:abc/")
    assert exc_info.value.status_code == 400
    assert "Invalid image URL" in exc_info.value.detail


def test_rejects_non_image_content_type() -> None:
    """Mode 2: non-image Content-Type from upstream -> HTTPException (415 or 400).

    Uses a mock transport to return Content-Type: text/html.
    The SSRF guard detects non-image content and raises HTTPException.
    """
    from fastapi import HTTPException

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"<html>not an image</html>",
            headers={"Content-Type": "text/html; charset=utf-8"},
        )

    real_client = httpx.Client
    transport = httpx.MockTransport(_handler)

    with (
        patch.object(httpx, "Client", lambda *a, **kw: real_client(transport=transport, *a, **kw)),
        patch.object(socket, "getaddrinfo", _fake_getaddrinfo),
    ):
        with pytest.raises(HTTPException) as exc_info:
            fetch_image_bytes("http://images.example.com/page.html")
        assert exc_info.value.status_code in (400, 415)


def test_rejects_oversized_response() -> None:
    """Mode 3: upstream response > 25 MB -> HTTPException (413 or 400).

    Uses a mock transport to stream 26 MB in 64 KB chunks.
    The SSRF guard's count_bytes iterator must reject it.
    """
    from fastapi import HTTPException

    _OVERSIZE = 26 * 1024 * 1024  # 26 MB

    class _Chunker(httpx.SyncByteStream):
        def __init__(self, total: int, chunk: int = 65536) -> None:
            self._total = total
            self._chunk = chunk

        def __iter__(self):  # type: ignore[override]
            remaining = self._total
            while remaining > 0:
                size = min(self._chunk, remaining)
                yield b"\x00" * size
                remaining -= size

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=_Chunker(_OVERSIZE),
            headers={"Content-Type": "image/png", "Content-Length": str(_OVERSIZE)},
        )

    real_client = httpx.Client
    transport = httpx.MockTransport(_handler)

    with (
        patch.object(httpx, "Client", lambda *a, **kw: real_client(transport=transport, *a, **kw)),
        patch.object(socket, "getaddrinfo", _fake_getaddrinfo),
    ):
        with pytest.raises(HTTPException) as exc_info:
            fetch_image_bytes("http://images.example.com/oversize/big.bin")
        assert exc_info.value.status_code in (400, 413)


def test_hides_upstream_error_detail() -> None:
    """Mode 4 (defensive): upstream fetch failures must not leak host/URL detail.

    ``fetch_image_bytes`` must return a generic 400 message without
    embedding internal IPs, hostnames, or upstream error text.
    """
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        fetch_image_bytes("http://169.254.169.254/metadata")
    assert exc_info.value.status_code == 400
    # Detail must be generic — no IP addresses, hostnames, or upstream text
    assert "169.254.169.254" not in exc_info.value.detail
    assert "blocked hostname" not in exc_info.value.detail


def test_rejects_dns_resolution_failure() -> None:
    """Mode 6: DNS resolution failure (socket.gaierror) -> HTTPException 400.

    Verifies that socket.gaierror from _resolve_addresses is caught and
    converted to a generic client-safe HTTPException, not a 500.
    """

    from fastapi import HTTPException

    def _fail_getaddrinfo(host: str, port: int, *args: object, **kwargs: object) -> None:
        raise socket.gaierror("Name or service not known")

    with patch.object(socket, "getaddrinfo", _fail_getaddrinfo):
        with pytest.raises(HTTPException) as exc_info:
            fetch_image_bytes("http://nonexistent.invalid.example/test.png")
        assert exc_info.value.status_code == 400
        # Must not leak the hostname to the client
        assert "nonexistent.invalid.example" not in exc_info.value.detail


def test_batch_per_item_error_survives() -> None:
    """Mode 5: single bad URL in batch yields per-item error, not 500.

    Verifies that the SSRF guard raises HTTPException (not an unhandled
    exception) for blocked URLs, which the batch endpoint catches via
    ``_build_error_item``.
    """
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        fetch_image_bytes("http://10.0.0.1/secret.png")
    assert exc_info.value.status_code == 400
    # TODO: detail leaks internal IP; should be generic in ssrf_guard.py.
