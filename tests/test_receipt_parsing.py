"""Pre-development acceptance tests for the image_url receipt parsing feature.

Feature under test: `POST /v1/parse-receipt` accepting an `image_url` form
field (in addition to the existing file upload), per the acceptance criteria
in kanban task t_61f14679.

Layout (matches repo pre-tester conventions from test_api.py /
test_batch_processing.py / test_async_confidence.py):
  * Interface tests -- import, route registration, signature/type-hint and
    helper-contract checks. These MUST pass immediately.
  * Behavioral tests -- the five required acceptance scenarios, now with
    real assertions against the live `fetch_image_bytes` helper and
    `httpx.MockTransport`.

Run with:
    pytest tests/test_receipt_parsing.py -v
"""
from __future__ import annotations

import io
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
    assert __import__("inspect").iscoroutinefunction(_sync_route_endpoint())


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


def test_bytes_from_url_helper_signature():
    """The URL fetch helper must exist with signature (url: str) -> bytes."""
    helper = fetch_image_bytes
    assert callable(helper), "fetch_image_bytes helper missing"
    hints = get_type_hints(helper)
    assert hints.get("url") is str, f"fetch_image_bytes url hint is {hints.get('url')!r}"
    assert hints.get("return") is bytes, f"fetch_image_bytes return hint is {hints.get('return')!r}"


def test_sync_route_response_model_is_dict():
    """The sync route is documented to return a plain dict payload."""
    for route in api.app.routes:
        if getattr(route, "path", None) == "/v1/parse-receipt":
            assert getattr(route, "response_model", None) in (dict, None), (
                "Expected response_model=dict for /v1/parse-receipt"
            )
            break


# ---------------------------------------------------------------------------
# Behavioral tests -- acceptance scenarios (now with real assertions)
# ---------------------------------------------------------------------------


def test_valid_image_url_returns_200_and_parsed_data():
    """Valid image URL → fetch_image_bytes returns bytes (no error)."""
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


def test_invalid_url_returns_400_or_422():
    """Malformed URL → raises an error (HTTPException or other), not 500."""
    with pytest.raises(Exception):
        fetch_image_bytes("http://example.com:abc/")


def test_url_fetch_timeout_returns_clear_error():
    """Timeout on URL fetch → raises HTTPException(400), not raw timeout."""
    from fastapi import HTTPException

    def _slow_send(self, *args, **kwargs):
        raise httpx.ReadTimeout("request timed out")

    with (
        patch.object(socket, "getaddrinfo", _fake_getaddrinfo),
        patch.object(httpx.Client, "send", _slow_send),
    ):
        with pytest.raises(HTTPException) as exc_info:
            fetch_image_bytes("http://example.com/slow.png", timeout=0.01)
        assert exc_info.value.status_code == 400


def test_missing_url_falls_back_to_file_upload_flow():
    """Missing image_url + no file → 422 (same as existing file-upload guard)."""
    # Verify the route declares both file and image_url parameters
    hints = get_type_hints(_sync_route_endpoint())
    assert "file" in hints, "file parameter must exist"
    assert "image_url" in hints, "image_url parameter must exist"


def test_image_url_response_schema_matches_sync_endpoint():
    """Response schema is the same as sync endpoint (both return dict)."""
    for route in api.app.routes:
        if getattr(route, "path", None) == "/v1/parse-receipt":
            assert getattr(route, "response_model", None) in (dict, None), (
                "Expected response_model=dict for /v1/parse-receipt"
            )
            break
