"""Pre-development ACCEPTANCE tests for the hardened image fetcher.

Function under test
-------------------
``app.ssrf_guard.fetch_image_bytes(url, *, max_bytes=20_000_000, timeout=30.0) -> bytes``

This module encodes the P0-2 acceptance criteria from
``analysis/analysis-brief.md`` (section 4 P0-2, section 5 P0-2, section 7).

Source contract (the brief, verbatim where it matters)
------------------------------------------------------
- Calls ``validate_image_url`` first; a rejected URL (``file://``, private host,
  non-http scheme) -> ``HTTPException(400)``.
- Enforces ``max_bytes``: body larger than cap ->
  ``HTTPException(400, "Image exceeds maximum allowed size")``.
- Validates ``Content-Type``: non-``image/*`` response ->
  ``HTTPException(400, "URL did not return an image")``.
- Redirects capped (<=5) and EACH redirect target re-validated; a redirect to a
  private host -> ``HTTPException(400)``.
- Enforces overall ``timeout``; network errors ->
  ``HTTPException(400, "Failed to fetch image from URL: ...")``.
- Returns the decoded ``bytes`` of a valid small ``image/png``.

No real network is touched. DNS is stubbed (``socket.getaddrinfo`` monkeypatched)
and every HTTP interaction goes through an ``httpx.MockTransport`` (already in the
installed ``httpx`` dependency -- no new dependency added, ``pyproject.toml``
unchanged) injected by monkeypatching ``httpx.Client``. This exercises the
fetcher's *real* streaming / redirect / size logic against deterministic
in-process responses.

Pre-development state & SPEC DEVIATIONS OBSERVED IN-REPO (flagged to analyst)
----------------------------------------------------------------------------
This module pins the BRIEF (analysis-brief.md section 5 P0-2) as the source of
truth. At authoring time a sibling worker was concurrently implementing P0-2 in
``app/ssrf_guard.fetch_image_bytes``; five of these eleven tests already PASS
against that partial implementation (signature + ``timeout`` kwarg, ``file://``
-> 400, private host -> 400, redirect-cap -> 400, redirect-to-private -> 400).
The remaining six are RED and encode genuine, actionable gaps between the brief
and the current implementation:

  * Network errors are NOT wrapped: a ``httpx.ReadTimeout`` / ``ConnectError``
    propagates out of ``fetch_image_bytes`` instead of becoming
    ``HTTPException(400, "Failed to fetch image from URL: ...")``
    (brief requirement). -> ``test_timeout_...`` / ``test_network_connect...``.
  * Oversize message has a trailing period and does not match the brief's exact
    string: impl returns ``"Image exceeds maximum allowed size."`` vs brief
    ``"Image exceeds maximum allowed size"`` (status 400 is correct).
    -> ``test_oversize_body_raises_400_max_size``.
  * Non-image message likewise has a trailing period: impl returns
    ``"URL did not return an image."`` vs brief ``"URL did not return an image"``
    (status 400 is correct). -> ``test_non_image_content_type_raises_400``.
  * Latent double-read bug: the fetcher iterates the raw stream to enforce
    ``max_bytes`` (``response.iter_raw``) AND then calls ``response.read()``
    (app/ssrf_guard.py:188 + 199), which raises ``httpx.StreamConsumed`` on the
    second access. A valid single image and a redirect chain therefore fail in
    production too. -> ``test_valid_small_png_returns_bytes`` /
    ``test_redirect_response_without_image_content_type_is_followed``.

The developer must converge the implementation to this contract, or the analyst
must revise the brief. The divergence is recorded here and in the task handoff
so it is not silently lost.
"""

from __future__ import annotations

import inspect
import socket

import httpx
import pytest
from fastapi import HTTPException

# Guarded import: until a spec-compliant ``fetch_image_bytes`` exists, keep the
# module collectable so each test reports a clear, granular RED failure instead
# of one opaque collection error. The autouse fixture turns the missing symbol
# into an explicit message.
try:  # pragma: no cover - import guard for pre-development state
    from app.ssrf_guard import fetch_image_bytes
except Exception:  # noqa: BLE001 - we deliberately fall back to None
    fetch_image_bytes = None


@pytest.fixture(autouse=True)
def _require_fetcher():
    """Fail clearly (red) until a spec-compliant fetcher is importable."""
    if fetch_image_bytes is None:
        pytest.fail(
            "app.ssrf_guard.fetch_image_bytes is not importable/implemented yet "
            "(expected pre-development RED state)"
        )


# ---------------------------------------------------------------------------
# Fixtures: deterministic, network-free httpx via injected MockTransport
# ---------------------------------------------------------------------------


class _ByteStream(httpx.SyncByteStream):
    """A re-iterable byte stream backed by an in-memory bytes object.

    httpx's ``Response`` requires ``stream`` to be a ``SyncByteStream`` and will
    *not* cache it (so ``iter_raw`` + ``read`` can both run -- which the fetcher
    does). Each iteration yields the full body, so the stream is reusable.
    """

    def __init__(self, body: bytes):
        self._body = body

    def __iter__(self):
        yield self._body


def _response(status_code: int, content_type: str, body: bytes) -> httpx.Response:
    """Build a mock response carrying a single Content-Type header and a body."""
    return httpx.Response(
        status_code,
        headers={"Content-Type": content_type},
        stream=_ByteStream(body),
    )


def _redirect_response(location: str, body: bytes = b"") -> httpx.Response:
    """Build a 302 redirect response (carries image/png so it reaches the branch)."""
    return httpx.Response(
        302,
        headers={"Location": location, "Content-Type": "image/png"},
        stream=_ByteStream(body),
    )


class _TransportHolder:
    """Holds the per-test MockTransport so the monkeypatched Client uses it."""

    transport: httpx.MockTransport | None = None


@pytest.fixture
def httpx_mock(monkeypatch) -> _TransportHolder:
    """Monkeypatch ``httpx.Client`` to route through the test's MockTransport.

    The fetcher constructs ``httpx.Client(timeout=...)`` internally; this swaps
    in a real ``httpx.Client`` that uses the handler the test installs on the
    returned holder, so the fetcher's own streaming/redirect/size logic runs
    against deterministic in-process responses. No real socket is touched.
    """
    holder = _TransportHolder()
    _orig_client = httpx.Client

    def _client(*args, **kwargs):
        if holder.transport is not None:
            kwargs["transport"] = holder.transport
        return _orig_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", _client)
    return holder


@pytest.fixture
def dns_stub(monkeypatch):
    """Stub DNS resolution via ``socket.getaddrinfo``: no real network.

    Public-looking hosts resolve to a clearly global address; hosts whose name
    looks private (127.0.0.1, localhost, internal, local, 169.254) resolve to a
    reserved address so the validator rejects them. Patching ``getaddrinfo`` --
    the stable stdlib boundary -- keeps the fixture immune to internal symbol
    renames inside ``app.ssrf_guard``.
    """
    def _fake_getaddrinfo(host, port, *_args, **_kwargs):
        lowered = (host or "").lower()
        if any(s in lowered for s in ("127.0.0.1", "localhost", "internal", "local", "169.254")):
            addr = "10.0.0.5"  # reserved -> must be rejected
        else:
            addr = "93.184.216.34"  # global -> accepted
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, port or 80))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)


# ---------------------------------------------------------------------------
# Expected contract messages (from analysis-brief.md section 5 P0-2)
# ---------------------------------------------------------------------------

MSG_OVERSIZE = "Image exceeds maximum allowed size"
MSG_NOT_IMAGE = "URL did not return an image"
MSG_FETCH_PREFIX = "Failed to fetch image from URL"


# ---------------------------------------------------------------------------
# Interface tests -- MUST pass once a spec-compliant impl exists
# ---------------------------------------------------------------------------


def test_function_signature_matches_spec():
    """Brief: ``fetch_image_bytes(url, *, max_bytes=20_000_000, timeout=30.0)``."""
    sig = inspect.signature(fetch_image_bytes)
    params = sig.parameters
    assert "url" in params, "missing 'url' parameter"
    assert "max_bytes" in params, "missing 'max_bytes' parameter"
    assert params["max_bytes"].default == 20_000_000, "max_bytes default must be 20_000_000"
    assert "timeout" in params, "missing 'timeout' parameter (required by brief)"
    assert params["timeout"].default == 30.0, "timeout default must be 30.0"


# ---------------------------------------------------------------------------
# Behavioral tests -- encode the P0-2 acceptance criteria; RED until impl
# ---------------------------------------------------------------------------


def test_rejected_non_http_scheme_raises_400_without_network(httpx_mock):
    """A non-http(s) scheme must be rejected with 400 BEFORE any network call."""

    def handler(request):
        raise AssertionError("network was contacted for a rejected URL")

    httpx_mock.transport = httpx.MockTransport(handler)
    with pytest.raises(HTTPException) as exc:
        fetch_image_bytes("file:///etc/passwd")
    assert exc.value.status_code == 400


def test_rejected_private_host_raises_400_without_network(httpx_mock, dns_stub):
    """A private/loopback host must be rejected with 400 BEFORE any network call."""

    def handler(request):
        raise AssertionError("network was contacted for a rejected URL")

    httpx_mock.transport = httpx.MockTransport(handler)
    with pytest.raises(HTTPException) as exc:
        fetch_image_bytes("http://127.0.0.1/secret")
    assert exc.value.status_code == 400


def test_valid_small_png_returns_bytes(httpx_mock, dns_stub):
    """A valid small ``image/png`` returns its decoded bytes."""
    body = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    httpx_mock.transport = httpx.MockTransport(
        lambda request: _response(200, "image/png", body)
    )
    result = fetch_image_bytes("https://example.com/receipt.png")
    assert isinstance(result, bytes)
    assert result == body


def test_oversize_body_raises_400_max_size(httpx_mock, dns_stub):
    """A body larger than ``max_bytes`` raises 400 with the brief's message."""
    body = b"x" * 50  # larger than the 10-byte cap used below
    httpx_mock.transport = httpx.MockTransport(
        lambda request: _response(200, "image/png", body)
    )
    with pytest.raises(HTTPException) as exc:
        fetch_image_bytes("https://example.com/big.png", max_bytes=10)
    assert exc.value.status_code == 400
    assert exc.value.detail == MSG_OVERSIZE


def test_non_image_content_type_raises_400(httpx_mock, dns_stub):
    """A non-``image/*`` Content-Type raises 400 with the brief's message."""
    httpx_mock.transport = httpx.MockTransport(
        lambda request: _response(200, "text/html", b"<html></html>")
    )
    with pytest.raises(HTTPException) as exc:
        fetch_image_bytes("https://example.com/page.html")
    assert exc.value.status_code == 400
    assert exc.value.detail == MSG_NOT_IMAGE


def test_redirect_cap_enforced_raises_400(httpx_mock, dns_stub):
    """More than 5 redirects must be capped and rejected with 400.

    NOTE: the 302 responses carry ``Content-Type: image/png`` only to reach the
    redirect branch (the in-repo impl evaluates the content-type gate before the
    redirect branch -- see ``test_redirect_response_without_image_content_type_is_followed``).
    This isolates the redirect-cap / per-hop re-validation behaviour.
    """
    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        return _redirect_response(f"https://example.com/hop{state['n']}")

    httpx_mock.transport = httpx.MockTransport(handler)
    with pytest.raises(HTTPException) as exc:
        fetch_image_bytes("https://example.com/start")
    assert exc.value.status_code == 400


def test_redirect_to_private_host_raises_400(httpx_mock, dns_stub):
    """Each redirect target is re-validated; a private-host redirect is rejected.

    The first hop is a public host (stubbed global); the redirect target
    ``127.0.0.1`` resolves (via the stub) to a reserved address and must be
    rejected with 400. As above, the 302 carries ``Content-Type: image/png`` to
    reach the redirect branch.
    """

    def handler(request):
        if str(request.url).endswith("/start"):
            return _redirect_response("http://127.0.0.1/secret")
        return _response(200, "image/png", b"x")

    httpx_mock.transport = httpx.MockTransport(handler)
    with pytest.raises(HTTPException) as exc:
        fetch_image_bytes("https://example.com/start")
    assert exc.value.status_code == 400


def test_redirect_response_without_image_content_type_is_followed(httpx_mock, dns_stub):
    """A 302 whose content-type is NOT image/* must still be followed.

    Per the brief, redirects are re-validated and followed. A real 302 carries
    no image content-type. This test asserts the redirect target is honoured
    (final hop returns the image). At authoring time the in-repo impl rejects
    the 302 at the content-type gate first, so this is RED and documents that
    ordering limitation for the developer/analyst.
    """
    seen = {"redirect": False}

    def handler(request):
        if not seen["redirect"]:
            seen["redirect"] = True
            return _redirect_response("https://example.com/final.png")
        return _response(200, "image/png", b"img")

    httpx_mock.transport = httpx.MockTransport(handler)
    result = fetch_image_bytes("https://example.com/start")
    assert result == b"img"


def test_timeout_raises_400_with_prefix(httpx_mock, dns_stub):
    """Network errors (e.g. timeout) must map to 400 with the brief's prefix."""

    def handler(request):
        raise httpx.ReadTimeout("read timed out")

    httpx_mock.transport = httpx.MockTransport(handler)
    with pytest.raises(HTTPException) as exc:
        fetch_image_bytes("https://example.com/slow.png")
    assert exc.value.status_code == 400
    assert exc.value.detail.startswith(MSG_FETCH_PREFIX)


def test_network_connect_error_raises_400_with_prefix(httpx_mock, dns_stub):
    """Connect errors must map to 400 with the brief's prefix message."""

    def handler(request):
        raise httpx.ConnectError("connection refused")

    httpx_mock.transport = httpx.MockTransport(handler)
    with pytest.raises(HTTPException) as exc:
        fetch_image_bytes("https://example.com/down.png")
    assert exc.value.status_code == 400
    assert exc.value.detail.startswith(MSG_FETCH_PREFIX)
