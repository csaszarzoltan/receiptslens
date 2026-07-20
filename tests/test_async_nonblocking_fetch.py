"""Pre-development ACCEPTANCE tests for P1-2: async routes use non-blocking fetch.

Feature under test
-------------------
``POST /v1/parse-receipt/async``  (app/api.py: ``parse_receipt_async_route``)
``POST /v1/parse-receipts/async`` (app/api.py: ``parse_receipts_async_route``)

Per analysis-brief.md section 4 P1-2 and section 5 P1-2, the URL fetch currently
happens SYNCHRONOUSLY inside the request handler (api.py:316-318 and api.py:505-527)
BEFORE the job is queued. The developer must move URL fetching into the background
job (``_process_job`` / ``_process_batch_job``) so the request returns
``queued`` immediately.

Acceptance criteria encoded here (brief section 5 P1-2)
-------------------------------------------------------
1. ``POST /v1/parse-receipt/async`` with a valid ``image_url`` returns
   ``{"job_id":..., "status":"queued"}`` WITHOUT performing the network fetch in
   the request coroutine. A fetch that is SLOW only inside the job must not delay
   the response.
2. Polling ``GET /v1/jobs/{job_id}`` for a bad URL eventually shows
   ``status:"failed"`` with an ``error`` field -- NOT a 500 at request time.
3. ``POST /v1/parse-receipts/async`` with a mix of valid + private-host URLs
   returns ``queued`` immediately and the job result flags the private one with
   ``error``.

How these tests are constructed (no real network)
--------------------------------------------------
The fetcher is ``app.api._bytes_from_url`` (sync, used to build ``image_bytes``
before the job is queued). We monkeypatch THAT helper (the real pre-P1-2 code
path) so:

  * A SLOW fetch is simulated with a ``time.sleep`` only inside the fetch helper.
    The async route must return ``queued`` well before the fetch would have
    finished. Because the current code calls the helper synchronously in the
    handler, the response is delayed -> those tests are RED until P1-2 lands.

  * A BAD URL raises inside the fetch helper (exactly how the live code surfaces
    a fetch failure). After P1-2, that raise happens inside the background
    executor, so the job record becomes ``{"status":"failed", "error":...}``.
    Before P1-2, the raise escapes the handler as a 500, so the job is never
    created and polling 404s -> RED.

  * A PRIVATE host URL is likewise rejected inside the fetch helper; for the
    batch route the private URL's per-item ``error`` must survive into the final
    job result, not abort the whole request.

To let the background job actually run to completion deterministically (so we can
assert the ``failed``/``error`` state), OCR is stubbed to a fast no-op. This keeps
the suite green-independent-of-Tesseract while still exercising the real
queue -> executor -> job-store pipeline, which is exactly the path P1-2 changes.

We deliberately do NOT import or call any not-yet-existing ``fetch_image_bytes``
symbol here -- this is about the ROUTE contract, not the fetcher internals (the
fetcher internals are covered by tests/test_fetch_image_bytes.py, task t_e70303ae).
If the developer refactors the helper name as part of P1-2, they must also update
the ``monkeypatch`` target in this file (clearly marked below).

Pre-development state
---------------------
At authoring time the fetch is synchronous, so:
  * ``test_single_async_returns_queued_without_blocking_on_fetch``      -> RED (slow fetch delays response)
  * ``test_single_async_bad_url_polls_failed_with_error``              -> RED (500 at request time, job never created)
  * ``test_batch_async_mixed_valid_and_private_host_queued``           -> RED (private URL raises 500 in handler; no job created)
  * Interface tests and ``test_*_returns_queued_immediately`` (fast path) -> GREEN now.
The developer must converge the implementation; then all of the above go GREEN.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest
from httpx import ASGITransport

from app import api
from app.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ocr_stub(monkeypatch):
    """Replace OCR with a fast deterministic stub so background jobs complete.

    Stubbing ``api._process_one`` keeps the queue -> executor -> JobStore path
    fully real (that is what P1-2 changes) without depending on Tesseract or a
    real image. The private/bad URL failures are injected at the FETCH layer
    (below), so OCR is never reached for those items.
    """

    def _fake_process_one(image_bytes: bytes) -> dict[str, Any]:
        return {
            "vendor": "STUB",
            "total": 0.0,
            "date": None,
            "tax": 0.0,
            "currency": None,
            "line_items": [],
            "confidence": {
                "vendor": 0.9,
                "total": 0.9,
                "date": 0.9,
                "tax": 0.9,
                "currency": 0.9,
                "line_items": 0.9,
            },
        }

    monkeypatch.setattr(api, "_process_one", _fake_process_one)


def _async_client():
    transport = ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _poll_until(client, job_id, *, timeout=5.0, interval=0.02):
    """Poll GET /v1/jobs/{job_id} until status leaves 'processing'/'queued'."""
    deadline = time.monotonic() + timeout
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        resp = await client.get(f"/v1/jobs/{job_id}")
        last = resp.json()
        if last["status"] not in ("queued", "processing"):
            return last
        await asyncio.sleep(interval)
    return last


# ---------------------------------------------------------------------------
# Interface tests -- MUST pass immediately
# ---------------------------------------------------------------------------


def test_async_single_route_registered():
    routes = {getattr(r, "path", None) for r in api.app.routes}
    assert "/v1/parse-receipt/async" in routes


def test_async_batch_route_registered():
    routes = {getattr(r, "path", None) for r in api.app.routes}
    assert "/v1/parse-receipts/async" in routes


def test_jobs_status_route_registered():
    routes = {getattr(r, "path", None) for r in api.app.routes}
    assert "/v1/jobs/{job_id}" in routes


def test_async_single_route_is_async():
    import inspect

    for route in api.app.routes:
        if getattr(route, "path", None) == "/v1/parse-receipt/async":
            assert inspect.iscoroutinefunction(route.endpoint)
            break
    else:  # pragma: no cover - route always present
        pytest.fail("Route /v1/parse-receipt/async not found")


def test_async_batch_route_has_image_urls_param():
    from typing import get_type_hints

    for route in api.app.routes:
        if getattr(route, "path", None) == "/v1/parse-receipts/async":
            hints = get_type_hints(route.endpoint)
            assert "image_urls" in hints, "image_urls param missing on /v1/parse-receipts/async"
            break
    else:  # pragma: no cover - route always present
        pytest.fail("Route /v1/parse-receipts/async not found")


# ---------------------------------------------------------------------------
# Behavioral tests -- encode P1-2 acceptance criteria; RED until impl
# ---------------------------------------------------------------------------


def test_single_async_returns_queued_immediately_for_valid_url(ocr_stub, monkeypatch):
    """A valid image_url returns {"job_id", "status":"queued"} promptly.

    Fast-path check: even with the current (synchronous) code the response is
    near-instant, so this passes now and stays passing after P1-2.
    """
    monkeypatch.setattr(api, "fetch_image_bytes", lambda url: b"fake-image-bytes")

    client = _async_client()

    async def do() -> None:
        resp = await client.post(
            "/v1/parse-receipt/async",
            data={"image_url": "https://example.com/receipt.png"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "queued"
        assert "job_id" in body and body["job_id"]

    asyncio.run(do())


def test_single_async_returns_queued_without_blocking_on_fetch(ocr_stub, monkeypatch):
    """P1-2 core: a SLOW fetch must NOT delay the 'queued' response.

    The fetch helper is patched to sleep 2s before returning. If the route calls
    the fetch synchronously (the current pre-P1-2 behaviour), the response takes
    >=2s and the test fails the promptness assertion. After P1-2 the fetch moves
    into the background job, so the response returns in well under the fetch time.
    """
    SLOW = 2.0

    def _slow_fetch_helper(url: str) -> bytes:
        time.sleep(SLOW)
        return b"fake-image-bytes"

    monkeypatch.setattr(api, "fetch_image_bytes", _slow_fetch_helper)

    client = _async_client()

    async def do() -> None:
        start = time.monotonic()
        resp = await client.post(
            "/v1/parse-receipt/async",
            data={"image_url": "https://example.com/slow.png"},
        )
        elapsed = time.monotonic() - start
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "queued"
        # The response must return long before the (slow) fetch would finish.
        assert elapsed < SLOW / 2, (
            f"response blocked on fetch: took {elapsed:.2f}s, "
            f"fetch takes {SLOW}s -- P1-2 non-blocking contract violated"
        )

    asyncio.run(do())


def test_single_async_bad_url_polls_failed_with_error(ocr_stub, monkeypatch):
    """P1-2: a bad URL -> job status 'failed' with 'error', NOT a 500 at request.

    The fetch helper raises HTTPException(400) for the bad URL (modelling the live
    fetch failure). After P1-2 that raise happens inside the executor, so the job
    record ends as {"status":"failed", "error":...}. Before P1-2 the raise escapes
    the handler as a 500, the job is never created, and polling 404s -> RED.
    """
    BAD = "https://example.com/does-not-exist.png"

    def _fetch_helper(url: str) -> bytes:
        if url == BAD:
            from fastapi import HTTPException

            raise HTTPException(status_code=400, detail=f"Failed to fetch image from URL: {url}")
        return b"fake-image-bytes"

    monkeypatch.setattr(api, "fetch_image_bytes", _fetch_helper)

    client = _async_client()

    async def do() -> None:
        # Request itself must NOT 500; it returns queued (fetch deferred to job).
        resp = await client.post("/v1/parse-receipt/async", data={"image_url": BAD})
        assert resp.status_code == 200, f"request 500'd on bad URL: {resp.status_code} {resp.text}"
        body = resp.json()
        assert body["status"] == "queued"
        job_id = body["job_id"]

        final = await _poll_until(client, job_id, timeout=5.0)
        assert final["status"] == "failed", f"expected 'failed', got {final['status']!r}"
        assert final.get("error") is not None, "failed job must carry an 'error' field"
        assert "Failed to fetch" in final["error"]

    asyncio.run(do())


def test_batch_async_mixed_valid_and_private_host_queued(ocr_stub, monkeypatch):
    """P1-2 batch: mix of valid + private-host URLs returns queued immediately,
    and the final job result flags the private one with an 'error'.

    The private URL is rejected inside the fetch helper (HTTPException). After P1-2
    the per-item failure is captured into an ``error`` field in the batch result,
    exactly as the synchronous batch route already does for upload/parse errors --
    the difference P1-2 introduces is that this capture happens inside the
    background job rather than before the job is created. Before P1-2 the private
    URL raises in the handler as a 500 and no job is created -> RED.
    """
    VALID = "https://example.com/valid.png"
    PRIVATE = "http://127.0.0.1/secret.png"

    def _fetch_helper(url: str) -> bytes:
        if url == PRIVATE:
            from fastapi import HTTPException

            raise HTTPException(status_code=400, detail=f"Failed to fetch image from URL: {url}")
        return b"fake-image-bytes"

    monkeypatch.setattr(api, "fetch_image_bytes", _fetch_helper)

    client = _async_client()

    async def do() -> None:
        resp = await client.post(
            "/v1/parse-receipts/async",
            data={"image_urls": f'["{VALID}", "{PRIVATE}"]'},
        )
        assert resp.status_code == 200, f"request 500'd: {resp.status_code} {resp.text}"
        body = resp.json()
        assert body["status"] == "queued"
        job_id = body["job_id"]

        final = await _poll_until(client, job_id, timeout=5.0)
        assert final["status"] == "completed", f"expected 'completed', got {final['status']!r}"
        result = final["result"]
        assert "results" in result and "summary" in result
        assert len(result["results"]) == 2

        private_result = next(r for r in result["results"] if r["index"] == 1)
        assert private_result.get("error") is not None, (
            "private-host URL must be flagged with an 'error' in the batch result"
        )
        assert "Failed to fetch" in private_result["error"]

        valid_result = next(r for r in result["results"] if r["index"] == 0)
        assert valid_result.get("error") is None, "valid URL must not be flagged"

        summary = result["summary"]
        assert summary["total"] == 2
        assert summary["successful"] == 1
        assert summary["failed"] == 1

    asyncio.run(do())
