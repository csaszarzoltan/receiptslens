"""Pre-development INTEGRATION tests for the SSRF-safe URL contract.

Scope of THIS file (kanban task t_46c0456b, plan Task 7)
--------------------------------------------------------
The unit-level acceptance for ``app.security`` / ``app.ssrf_guard``
(``validate_image_url`` + ``fetch_image_bytes``) is already covered by sibling
pre-tester files (``tests/test_security.py`` for the validator,
``tests/test_fetch_image_bytes.py`` for the fetcher). This file adds the layer
that those files do NOT cover: the **end-to-end endpoint contract** that the
SSRF guard is actually wired into ``app.api``.

Enforced acceptance criteria (from the plan):
  * ``POST /v1/parse-receipt`` with ``image_url=http://169.254.169.254/``
    (cloud metadata endpoint) -> 400, and the rejection must happen via the
    hardened validator BEFORE any network fetch (no 500, no real egress).
  * ``POST /v1/parse-receipt`` with ``image_url=file:///etc/passwd``
    (non-http scheme) -> 400 via the scheme check (not a network error).
  * ``POST /v1/parse-receipts`` (batch) with one SSRF URL -> 200 with a
    per-item ``error`` (NOT a 500), ``summary.failed == 1``.

Interface tests (GREEN now, prove the contract surface is importable):
  * ``app.security`` re-exports ``validate_image_url`` and ``fetch_image_bytes``.
  * Their signatures match the documented contract
    (``validate_image_url(url: str) -> None``,
     ``fetch_image_bytes(url: str, ...) -> bytes``).

Determinism / no real network
-----------------------------
Every test monkeypatches ``httpx.Client`` so that a *real* network send raises
``httpx.ConnectError``. This guarantees:
  * The unwired (current) code path fails deterministically and fast, and the
    test is RED for the RIGHT reason (the hardened validator was never invoked)
    rather than passing by accident because a real socket error happened to
    produce a 400.
  * The wired (target) code path rejects at ``validate_url`` *before* any
    ``httpx.Client`` is used, so the no-network patch never fires there.

RED -> GREEN transition
-----------------------
Each behavioral test asserts BOTH the documented status code AND that the
hardened validator (``app.ssrf_guard.validate_url``) was actually exercised.
That makes the test RED until ``app.api`` replaces ``_bytes_from_url`` with
``fetch_image_bytes`` (plan Task 3), then GREEN once wiring lands.

Run with:
    pytest tests/test_security_integration.py -v
"""
from __future__ import annotations

import asyncio
import inspect

import httpx
import pytest
from httpx import ASGITransport

from app import api
from app import ssrf_guard

# ---------------------------------------------------------------------------
# Guarded imports: the interface under test must exist for these to be GREEN.
# ---------------------------------------------------------------------------

try:  # pragma: no cover - import guard for pre-development state
    from app.security import fetch_image_bytes, validate_image_url
except Exception:  # noqa: BLE001 - fall back so collection stays granular
    fetch_image_bytes = None
    validate_image_url = None


@pytest.fixture(autouse=True)
def _require_security_module():
    """Fail clearly (red) if app/security no longer exposes the contract."""
    if fetch_image_bytes is None or validate_image_url is None:
        pytest.fail(
            "app.security does not expose validate_image_url/fetch_image_bytes "
            "(expected: re-export shim over app.ssrf_guard)"
        )


# ---------------------------------------------------------------------------
# No-network harness: a real httpx.Client send must never reach the wire.
# ---------------------------------------------------------------------------


class _NoNetworkClient:
    """httpx.Client stand-in that fails any real send with ConnectError.

    Used so the unwired endpoint path (which still calls ``_bytes_from_url``
    -> ``httpx.Client.get``) fails deterministically instead of touching the
    network. The wired path rejects at ``validate_url`` before constructing a
    client, so this never fires in the GREEN state.
    """

    def __init__(self, *args, **kwargs):
        pass

    def get(self, *args, **kwargs):
        raise httpx.ConnectError("network blocked by test harness")

    def stream(self, *args, **kwargs):
        raise httpx.ConnectError("network blocked by test harness")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def no_real_network(monkeypatch):
    """Prevent any real egress; real sends raise httpx.ConnectError."""
    monkeypatch.setattr(httpx, "Client", _NoNetworkClient)


@pytest.fixture
def validator_spy(monkeypatch):
    """Record whether the hardened validator actually ran in the endpoint.

    The fetcher (``app.security.fetch_image_bytes``) calls
    ``app.ssrf_guard.validate_url`` before any fetch. Spying there lets us
    assert the rejection came from the SSRF guard, not a downstream network
    error. This is the deterministic RED->GREEN discriminator.
    """
    state = {"invoked": False}
    real = ssrf_guard.validate_url

    def _spy(url, *args, **kwargs):
        state["invoked"] = True
        return real(url, *args, **kwargs)

    monkeypatch.setattr(ssrf_guard, "validate_url", _spy)
    return state


def _post(route: str, data: dict) -> httpx.Response:
    """POST to the ASGI app synchronously via asyncio.run."""
    transport = ASGITransport(app=api.app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")

    async def _do():
        return await client.post(route, data=data)

    return asyncio.run(_do())


# ---------------------------------------------------------------------------
# Interface tests -- MUST pass immediately
# ---------------------------------------------------------------------------


def test_security_module_exports_contract_symbols():
    """app.security re-exports validate_image_url + fetch_image_bytes."""
    assert callable(validate_image_url)
    assert callable(fetch_image_bytes)


def test_validate_image_url_signature():
    """validate_image_url(url: str) -> None."""
    assert callable(validate_image_url)
    sig = inspect.signature(validate_image_url)
    params = sig.parameters
    assert "url" in params, "validate_image_url missing 'url' parameter"
    assert params["url"].annotation in (str, "str"), (
        f"validate_image_url 'url' must be str, got {params['url'].annotation!r}"
    )
    assert sig.return_annotation in (None, "None"), (
        f"validate_image_url must return None, got {sig.return_annotation!r}"
    )


def test_fetch_image_bytes_signature():
    """fetch_image_bytes(url: str, ...) -> bytes."""
    assert callable(fetch_image_bytes)
    sig = inspect.signature(fetch_image_bytes)
    params = sig.parameters
    assert "url" in params, "fetch_image_bytes missing 'url' parameter"
    assert sig.return_annotation in (bytes, "bytes"), (
        f"fetch_image_bytes must return bytes, got {sig.return_annotation!r}"
    )


# ---------------------------------------------------------------------------
# Behavioral integration tests -- RED until app.api is wired (plan Task 3)
# ---------------------------------------------------------------------------


def test_endpoint_rejects_ssrf_metadata_url(
    no_real_network, validator_spy
):
    """POST /v1/parse-receipt with image_url=http://169.254.169.254/ -> 400.

    The cloud metadata endpoint must be blocked by the SSRF validator before
    any network call. RED until the hardened fetcher replaces ``_bytes_from_url``.
    """
    resp = _post(
        "/v1/parse-receipt",
        data={"image_url": "http://169.254.169.254/latest/meta-data/"},
    )
    assert resp.status_code == 400, (
        f"expected 400 for SSRF metadata URL, got {resp.status_code}: "
        f"{resp.text!r}"
    )
    assert validator_spy["invoked"], (
        "endpoint rejected the SSRF URL without invoking the hardened "
        "validator (app.api not yet wired to fetch_image_bytes)"
    )


def test_endpoint_rejects_file_scheme(
    no_real_network, validator_spy
):
    """POST /v1/parse-receipt with image_url=file:///etc/passwd -> 400.

    A non-http(s) scheme must be rejected by the scheme check, not by a
    downstream network error. RED until wiring lands.
    """
    resp = _post(
        "/v1/parse-receipt",
        data={"image_url": "file:///etc/passwd"},
    )
    assert resp.status_code == 400, (
        f"expected 400 for file:// scheme, got {resp.status_code}: "
        f"{resp.text!r}"
    )
    assert validator_spy["invoked"], (
        "endpoint rejected the file:// URL without invoking the hardened "
        "validator (app.api not yet wired to fetch_image_bytes)"
    )


def test_batch_url_ssrf_blocked_returns_error_item(
    no_real_network, validator_spy
):
    """Batch with one SSRF URL returns 200 + per-item error (not 500).

    POST /v1/parse-receipts with ``image_urls=['http://169.254.169.254/x.jpg']``
    must return 200, a per-item ``error``, and ``summary.failed == 1``. RED
    until the batch path is wired to ``fetch_image_bytes``.
    """
    resp = _post(
        "/v1/parse-receipts",
        data={"image_urls": '["http://169.254.169.254/x.jpg"]'},
    )
    assert resp.status_code == 200, (
        f"batch SSRF URL must not 500, got {resp.status_code}: {resp.text!r}"
    )
    data = resp.json()
    results = data.get("results", [])
    assert len(results) == 1, f"expected 1 result, got {results!r}"
    assert results[0].get("error") is not None, (
        f"SSRF batch item must carry a per-item error, got {results[0]!r}"
    )
    assert data.get("summary", {}).get("failed") == 1, (
        f"summary.failed must be 1, got {data.get('summary')!r}"
    )
    assert validator_spy["invoked"], (
        "batch endpoint rejected the SSRF URL without invoking the hardened "
        "validator (app.api batch path not yet wired to fetch_image_bytes)"
    )
