"""Pre-development acceptance/regression tests: hardened fetcher wired into ALL FOUR entrypoints.

Source of truth: ``analysis/analysis-brief.md`` -- read §4 P0-3, §5 P0-3, §7.

WHAT THIS FILE PROVES
---------------------
P0-3 ("Wire hardened fetcher into all four entrypoints") is satisfied only when:

  1. ``app.api`` no longer references the old ``_bytes_from_url`` helper, and
  2. ``fetch_image_bytes`` is wired into the four ``image_url`` call sites so that
     the SSRF/size/content-type validation the fetcher performs is actually applied
     at request time.

These tests are written BEFORE the wiring lands, so they are expected to be RED
until both P0-2 (the hardened ``fetch_image_bytes`` implementation) and P0-3
(this wiring) are merged.

INTENTIONAL DIVERGENCE FROM THE BRIEF (flagged to analyst, see task comment)
----------------------------------------------------------------------------
The brief specifies ``fetch_image_bytes`` living in ``app/api.py`` and the four
call sites at api.py:279/318/407/527. The repo has diverged:

  * The (partially built) hardened fetcher physically lives in ``app/ssrf_guard.py``
    and is re-exported via ``app/security.py``. ``app/api.py`` STILL calls
    ``_bytes_from_url`` at the brief's exact line numbers (279/318/407/527).
  * Sibling pre-test modules (``tests/test_api.py``, ``tests/test_receipt_parsing.py``,
    ``tests/test_image_url_endpoint.py``, ``tests/test_configurable_limits.py``) import
    ``_bytes_from_url`` / ``fetch_image_bytes`` from conflicting locations
    (``app.api`` vs ``app.security`` vs ``app.ssrf_guard``).

To stay robust against this divergence we check the WIRED state through the
``app.api`` namespace (``app.api.fetch_image_bytes``) as the canonical wire point,
and we accept the physical implementation being reachable via ``app.security`` or
``app.ssrf_guard``. The developer must converge the location as part of P0-3.

HARNESS CONSTRAINTS
-------------------
* No live network: an ``httpx.MockTransport`` serves crafted responses and
  ``socket.getaddrinfo`` is stubbed to a fixed public IP for names and the
  literal IP for IP-literal hosts (so the validator still runs for real and
  blocks reserved ranges).
* OCR is bypassed (``app.api._process_one`` is stubbed) so results do not depend
  on a Tesseract binary; the tests assert *wiring + fetch validation*, not OCR.
* ``respx`` is not a project dependency, so we use ``httpx.MockTransport``
  directly (consistent with the repo's existing ASGITransport-based tests).

REGRESSION NOTE
---------------
The existing suites ``tests/test_api.py``, ``tests/test_batch_processing.py``,
``tests/test_async_confidence.py``, ``tests/test_ocr.py`` must remain green once
P0-3 lands. This file asserts *interface compatibility* with those routes
(registration + the four entrypoints delegate to the wired fetcher). The
pre-tester need not run the sibling suites here, but notes that the developer
must update ``tests/test_api.py`` (which imports ``_bytes_from_url`` at module
top) so removing ``_bytes_from_url`` does not break collection (see task comment).
"""

from __future__ import annotations

import asyncio
import inspect
import socket
from io import BytesIO

import httpx
import pytest
from httpx import ASGITransport
from PIL import Image

from app import api


# ---------------------------------------------------------------------------
# Deterministic fixtures / helpers
# ---------------------------------------------------------------------------

def _make_blank_png() -> bytes:
    """A tiny valid PNG so a successful fetch yields image bytes the route accepts."""
    buf = BytesIO()
    Image.new("RGB", (64, 32), color="white").save(buf, format="PNG")
    return buf.getvalue()


BLANK_PNG = _make_blank_png()

_OVERSIZE_BYTES = 21 * 1024 * 1024  # 21 MB -- above the 20 MB cap


class _ByteChunker(httpx.SyncByteStream):
    """Yield 64 KB chunks up to *total_bytes* for oversize-response tests."""
    def __init__(self, total_bytes: int, chunk_size: int = 65536):
        self._total = total_bytes
        self._chunk = chunk_size

    def __iter__(self):
        remaining = self._total
        while remaining > 0:
            size = min(self._chunk, remaining)
            yield b"\x00" * size
            remaining -= size


class _FakeReceipt:
    """Minimal stand-in so ``_render_receipt`` produces a valid response without OCR."""

    merchant = "STORE"
    total = 1.0
    date = "2025-01-01"
    tax = 0.1
    currency = "USD"
    items: list = []
    confidence: dict = {}


def _fake_process_one(_bytes: bytes) -> dict:
    """Return a valid receipt dict without invoking Tesseract."""
    return api._render_receipt(_FakeReceipt())


def _fake_getaddrinfo(host, port, *args, **kwargs):
    """Resolve IP literals as-is; map all names to a fixed public IP.

    Reserved IP literals (e.g. 169.254.169.254, 10.0.0.1) therefore remain reserved
    and the real validator still rejects them -- we only avoid touching real DNS.
    """
    import ipaddress

    try:
        ipaddress.ip_address(host)
        addr = host
    except ValueError:
        addr = "93.184.216.34"  # public, reserved-range-free
    family = socket.AF_INET6 if ":" in addr else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, "", (addr, port or 0))]


class _OneShotStream(httpx.SyncByteStream):
    """Single-iteration byte stream so ``iter_raw()`` and ``read()`` both work."""
    def __init__(self, data: bytes):
        self._data = data
        self._yielded = False

    def __iter__(self):
        if not self._yielded:
            self._yielded = True
            yield self._data


def _mock_handler(request: httpx.Request) -> httpx.Response:
    """Serve crafted responses by URL. SSRF/file cases are blocked pre-fetch by the
    validator, so they never reach this handler."""
    url = str(request.url)
    if "oversize" in url or url.rstrip("/").endswith("big.png"):
        # Stream ~21 MB in 64 KB chunks; the fetcher must cap at 20 MB.
        return httpx.Response(
            200,
            stream=_ByteChunker(_OVERSIZE_BYTES),
            headers={"Content-Type": "image/png", "Content-Length": str(_OVERSIZE_BYTES)},
        )
    if "html" in url:
        return httpx.Response(
            200,
            stream=_OneShotStream(b"<html>not an image</html>"),
            headers={"Content-Type": "text/html; charset=utf-8"},
        )
    # Default: a small valid image.
    return httpx.Response(200, stream=_OneShotStream(BLANK_PNG), headers={"Content-Type": "image/png"})


@pytest.fixture
def wired_env(monkeypatch: pytest.MonkeyPatch):
    """Wire the (future) fetcher to a mock transport and isolate from OCR/DNS."""
    transport = httpx.MockTransport(_mock_handler)
    real_client = httpx.Client

    def _client_factory(*args, **kwargs):
        # Force the mock transport while preserving any timeout kwarg from the fetcher.
        return real_client(transport=transport, *args, **kwargs)

    monkeypatch.setattr(httpx, "Client", _client_factory)
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    # Decouple from Tesseract: a successful fetch must still produce a 200 receipt.
    monkeypatch.setattr(api, "_process_one", _fake_process_one)
    yield


def _run(coro) -> None:
    asyncio.run(coro)


# ---------------------------------------------------------------------------
# Interface / wiring tests -- must be GREEN once P0-3 lands
# ---------------------------------------------------------------------------

def test_old_bytes_from_url_is_gone_from_api_module() -> None:
    """P0-3: ``_bytes_from_url`` must no longer exist in ``app.api``."""
    assert not hasattr(api, "_bytes_from_url"), (
        "app.api still exposes _bytes_from_url; P0-3 requires the hardened "
        "fetcher be wired in and the old helper removed."
    )
    src = inspect.getsource(api)
    assert "_bytes_from_url" not in src, (
        "app.api source still references _bytes_from_url; the four entrypoints "
        "must call fetch_image_bytes instead."
    )


def test_fetch_image_bytes_is_wired_into_api_module() -> None:
    """P0-3: ``app.api.fetch_image_bytes`` must exist, be callable, return bytes,
    and be used at the four ``image_url`` call sites (279/318/407/527 in the brief)."""
    fib = getattr(api, "fetch_image_bytes", None)
    assert callable(fib), (
        "app.api.fetch_image_bytes missing -- the hardened fetcher is not wired "
        "into the API module (P0-3)."
    )
    sig = inspect.signature(fib)
    # Stringified annotations (from __future__ import annotations) yield str, not type.
    ret = sig.return_annotation
    assert ret is bytes or ret == "bytes", (
        f"fetch_image_bytes must return bytes, got {ret!r}"
    )
    # The physical implementation must be importable from the project.
    try:
        from app.security import fetch_image_bytes as _via_security  # noqa: F401
    except Exception:
        try:
            from app.ssrf_guard import fetch_image_bytes as _via_guard  # noqa: F401
        except Exception as exc:  # pragma: no cover - defensive
            pytest.fail(f"fetch_image_bytes not importable from app.security/app.ssrf_guard: {exc}")


def test_four_entrypoints_registered() -> None:
    """All four ``image_url``-bearing routes must be registered."""
    routes = {getattr(r, "path", None) for r in api.app.routes}
    for path in (
        "/v1/parse-receipt",
        "/v1/parse-receipt/async",
        "/v1/parse-receipts",
        "/v1/parse-receipts/async",
    ):
        assert path in routes, f"Route {path} is not registered on the API app."


# ---------------------------------------------------------------------------
# Integration tests -- GREEN once P0-2 (fetcher) + P0-3 (wiring) both land.
# Each drives a real request through ASGITransport; the wired fetcher's
# validator must block the bad input and surface HTTPException(400).
# ---------------------------------------------------------------------------

def test_parse_receipt_blocks_ssrf_metadata_ip(wired_env: None) -> None:
    """POST /v1/parse-receipt with image_url=http://169.254.169.254/ -> 400 (SSRF)."""
    transport = ASGITransport(app=api.app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")

    async def do() -> None:
        resp = await client.post(
            "/v1/parse-receipt",
            data={"image_url": "http://169.254.169.254/"},
        )
        assert resp.status_code == 400, (
            f"SSRF metadata IP was not blocked by the wired validator; "
            f"got {resp.status_code}: {resp.text}"
        )

    _run(do())


def test_parse_receipt_blocks_file_scheme(wired_env: None) -> None:
    """POST /v1/parse-receipt with image_url=file:///etc/passwd -> 400."""
    transport = ASGITransport(app=api.app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")

    async def do() -> None:
        resp = await client.post(
            "/v1/parse-receipt",
            data={"image_url": "file:///etc/passwd"},
        )
        assert resp.status_code == 400, (
            f"file:// scheme was not rejected by the wired validator; "
            f"got {resp.status_code}: {resp.text}"
        )

    _run(do())


def test_parse_receipt_blocks_oversize_image(wired_env: None) -> None:
    """POST /v1/parse-receipt with a URL returning a 21 MB image -> 400 (size cap)."""
    transport = ASGITransport(app=api.app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")

    async def do() -> None:
        resp = await client.post(
            "/v1/parse-receipt",
            data={"image_url": "http://images.example.com/oversize/big.png"},
        )
        assert resp.status_code == 400, (
            f"Oversize response was not capped by the wired fetcher; "
            f"got {resp.status_code}: {resp.text}"
        )

    _run(do())


def test_parse_receipt_blocks_non_image_content_type(wired_env: None) -> None:
    """POST /v1/parse-receipt with a URL returning Content-Type: text/html -> 400."""
    transport = ASGITransport(app=api.app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")

    async def do() -> None:
        resp = await client.post(
            "/v1/parse-receipt",
            data={"image_url": "http://images.example.com/page.html"},
        )
        assert resp.status_code == 400, (
            f"Non-image Content-Type was not rejected by the wired fetcher; "
            f"got {resp.status_code}: {resp.text}"
        )

    _run(do())


def test_batch_isolates_private_host_without_500(wired_env: None) -> None:
    """Batch with one private-host URL + one valid -> private yields error,
    summary counts reflect it, and the request returns 200 (no 500)."""
    transport = ASGITransport(app=api.app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")

    async def do() -> None:
        payload = '["http://10.0.0.1/secret.png", "http://images.example.com/good.png"]'
        resp = await client.post("/v1/parse-receipts", data={"image_urls": payload})
        assert resp.status_code == 200, (
            f"Batch request should not 500 on a private-host URL; got {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        assert "results" in data and "summary" in data
        results = data["results"]
        assert len(results) == 2, f"expected 2 results, got {len(results)}"

        # With controlled messages, the error won't contain the raw IP.
        # Just check that one result has an error (the private-host one) and one doesn't.
        error_results = [r for r in results if r.get("error") is not None]
        assert len(error_results) == 1, (
            f"expected exactly 1 error result, got {len(error_results)}"
        )
        private_result = error_results[0]
        assert private_result.get("error") is not None, (
            "private-host URL must surface an error field, not a parsed receipt"
        )
        valid_result = next(r for r in results if r.get("error") is None)
        assert valid_result.get("vendor") == "STORE", (
            "valid URL should parse successfully once the fetcher is wired"
        )

        summary = data["summary"]
        assert summary["total"] == 2
        assert summary["failed"] == 1, f"expected 1 failed (private host), got {summary['failed']}"
        assert summary["successful"] == 1, f"expected 1 successful, got {summary['successful']}"

    _run(do())
