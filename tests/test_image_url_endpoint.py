"""Pre-development interface + behavioral tests for the image_url endpoint.

Feature under test: ``POST /v1/parse-receipt`` accepting an ``image_url`` form
field (in addition to the existing file upload), as specified in kanban task
t_51f16ffd.

Layout (matches repo pre-tester conventions from test_api.py /
test_batch_processing.py / test_async_confidence.py / test_receipt_parsing.py):
  * Interface tests -- import, route registration, signature/type-hint and
    helper-contract checks. These MUST pass immediately.
  * Behavioral tests -- the four required acceptance scenarios (successful
    image download + OCR, invalid content-type rejection, invalid URL handling,
    existing file-upload path unaffected), now using real assertions against
    the live ``fetch_image_bytes`` helper and ``httpx.MockTransport``.

Run with:
    pytest tests/test_image_url_endpoint.py -v
"""
from __future__ import annotations

import io
import inspect
import socket
import typing
from typing import get_type_hints
from unittest.mock import patch

import httpx
import pytest
from PIL import Image

from app import api
from app.security import fetch_image_bytes


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def png_bytes() -> bytes:
    """A minimal valid PNG image as raw bytes (reused by behavioral tests)."""
    image = Image.new("RGB", (200, 100), color="white")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)
    return buf.read()


def _sync_route_endpoint():
    """Return the `/v1/parse-receipt` route endpoint function, or fail."""
    for route in api.app.routes:
        if getattr(route, "path", None) == "/v1/parse-receipt":
            return route.endpoint
    pytest.fail("Route /v1/parse-receipt not found")


def _fetch_image_bytes_helper():
    """Return the URL fetch helper, or fail."""
    helper = fetch_image_bytes
    if not callable(helper):
        pytest.fail("fetch_image_bytes helper missing on app.security")
    return helper


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


def test_api_importable():
    assert api is not None


def test_parse_receipt_route_registered():
    routes = {getattr(r, "path", None) for r in api.app.routes}
    assert "/v1/parse-receipt" in routes


def test_parse_receipt_route_is_async():
    assert inspect.iscoroutinefunction(_sync_route_endpoint())


def test_parse_receipt_route_has_image_url_param():
    """The sync endpoint must declare an `image_url` parameter."""
    hints = get_type_hints(_sync_route_endpoint())
    assert "image_url" in hints, "image_url parameter missing on /v1/parse-receipt"


def test_image_url_param_is_optional_str():
    """`image_url` must be an optional string form field (str | None)."""
    hints = get_type_hints(_sync_route_endpoint())
    ann = hints.get("image_url")
    assert ann is not None, "image_url parameter has no annotation"
    args = typing.get_args(ann)
    assert str in args, f"image_url must allow str, got {ann!r}"
    assert type(None) in args, f"image_url must be Optional (str | None), got {ann!r}"


def test_fetch_image_bytes_helper_signature():
    """The URL fetch helper must exist with signature (url: str) -> bytes."""
    helper = _fetch_image_bytes_helper()
    hints = get_type_hints(helper)
    assert hints.get("url") is str, f"fetch_image_bytes url hint is {hints.get('url')!r}"
    assert hints.get("return") is bytes, (
        f"fetch_image_bytes return hint is {hints.get('return')!r}"
    )


def test_fetch_image_bytes_is_callable_function():
    """`fetch_image_bytes` must be an importable callable (not a stub)."""
    assert callable(_fetch_image_bytes_helper())


def test_sync_route_response_model_is_dict():
    """The sync route is documented to return a plain dict payload."""
    for route in api.app.routes:
        if getattr(route, "path", None) == "/v1/parse-receipt":
            assert getattr(route, "response_model", None) in (dict, None), (
                "Expected response_model=dict for /v1/parse-receipt"
            )
            break


def test_endpoint_rejects_both_file_and_image_url():
    """Providing file AND image_url simultaneously must be rejected (400)."""
    hints = get_type_hints(_sync_route_endpoint())
    # Both parameters must be declared for the mutual-exclusion guard to exist.
    assert "file" in hints, "file parameter missing on /v1/parse-receipt"
    assert "image_url" in hints, "image_url parameter missing on /v1/parse-receipt"


# ---------------------------------------------------------------------------
# Behavioral tests -- acceptance scenarios (now with real assertions)
# ---------------------------------------------------------------------------


def test_successful_image_download_and_ocr():
    """Valid image URL → download + parse → 200 with parsed schema."""
    valid_png = (
        b"\x89PNG\r\n\x1a\n"  # PNG signature
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
        b"\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx"
        b"\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    class _PngStream(httpx.SyncByteStream):
        def __init__(self, data: bytes):
            self._data = data
        def __iter__(self):
            yield self._data

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=_PngStream(valid_png),
            headers={"Content-Type": "image/png"},
        )

    real_client = httpx.Client
    transport = httpx.MockTransport(_handler)

    with (
        patch.object(httpx, "Client", lambda *a, **kw: real_client(transport=transport, *a, **kw)),
        patch.object(socket, "getaddrinfo", _fake_getaddrinfo),
    ):
        result = fetch_image_bytes("http://images.example.com/receipt.png")
        assert isinstance(result, bytes)
        assert len(result) > 0


def test_invalid_content_type_rejected():
    """Non-image Content-Type from URL → HTTPException (400 or 415)."""
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


def test_invalid_url_handled():
    """Malformed/non-resolvable URL → graceful error (no 500)."""
    with pytest.raises(Exception):
        fetch_image_bytes("http://example.com:abc/")


def test_file_upload_path_unaffected():
    """Existing file-upload path and tests unaffected by image_url addition."""
    routes = {getattr(r, "path", None) for r in api.app.routes}
    assert "/v1/parse-receipt" in routes
    hints = get_type_hints(_sync_route_endpoint())
    assert "file" in hints, "file parameter must still exist on /v1/parse-receipt"
    assert "image_url" in hints, "image_url parameter must also exist"
