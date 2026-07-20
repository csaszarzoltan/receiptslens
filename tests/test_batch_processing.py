"""Pre-development acceptance tests for batch receipt processing (v0.3.0).

These tests define the expected behavior of `POST /v1/parse-receipts` and
`POST /v1/parse-receipts/async`. They are written before implementation;
pre-tests may fail until the feature is built.
"""
from __future__ import annotations

import io
from typing import get_type_hints

import httpx
import pytest
from httpx import ASGITransport
from PIL import Image

from app import api
from app.main import app


# ---------------------------------------------------------------------------
# Interface tests
# ---------------------------------------------------------------------------


def test_batch_route_is_registered():
    routes = {getattr(r, "path", None) for r in api.app.routes}
    assert "/v1/parse-receipts" in routes


def test_batch_accepts_files_list():
    """The batch endpoint should declare a `files` parameter."""
    for route in api.app.routes:
        if getattr(route, "path", None) == "/v1/parse-receipts":
            hints = get_type_hints(route.endpoint) if hasattr(route, "endpoint") else {}
            assert "files" in hints, "`files` parameter missing on batch endpoint"
            break
    else:
        pytest.fail("Batch endpoint not found")


def test_batch_accepts_image_urls():
    """The batch endpoint should declare an `image_urls` parameter."""
    for route in api.app.routes:
        if getattr(route, "path", None) == "/v1/parse-receipts":
            hints = get_type_hints(route.endpoint) if hasattr(route, "endpoint") else {}
            assert "image_urls" in hints, "`image_urls` parameter missing on batch endpoint"
            break
    else:
        pytest.fail("Batch endpoint not found")


def test_batch_rejects_mixed_inputs():
    """Providing both files and image_urls must return 400."""
    transport = ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")

    async def do() -> None:
        image = Image.new("RGB", (100, 50), color="white")
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        buf.seek(0)

        resp = await client.post(
            "/v1/parse-receipts",
            files=[("files", ("a.png", buf, "image/png"))],
            data={"image_urls": '["https://example.com/r.jpg"]'},
        )
        assert resp.status_code == 400

    import asyncio

    asyncio.run(do())


def test_batch_rejects_more_than_20_files():
    """Providing >20 files must return 413."""
    transport = ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")

    async def do() -> None:
        files = []
        for i in range(21):
            image = Image.new("RGB", (10, 10), color="white")
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            buf.seek(0)
            files.append(("files", (f"f{i}.png", buf, "image/png")))
        resp = await client.post("/v1/parse-receipts", files=files)
        assert resp.status_code == 413

    import asyncio

    asyncio.run(do())


# ---------------------------------------------------------------------------
# Behavioral tests
# ---------------------------------------------------------------------------


def test_empty_request_returns_422():
    transport = ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")

    async def do() -> None:
        resp = await client.post("/v1/parse-receipts", data={})
        assert resp.status_code == 422

    import asyncio

    asyncio.run(do())


def test_single_file_batch_returns_result_list():
    """A batch with one file must return a list of length 1."""
    transport = ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")

    async def do() -> None:
        image = Image.new("RGB", (200, 100), color="white")
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        buf.seek(0)
        resp = await client.post(
            "/v1/parse-receipts",
            files=[("files", ("a.png", buf, "image/png"))],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert "summary" in data
        assert len(data["results"]) == 1

    import asyncio

    asyncio.run(do())


def test_empty_image_returns_successful_result():
    """A blank image should parse successfully with empty line_items."""
    transport = ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")

    async def do() -> None:
        image = Image.new("RGB", (200, 100), color="white")
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        buf.seek(0)
        resp = await client.post(
            "/v1/parse-receipts",
            files=[("files", ("blank.png", buf, "image/png"))],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 1
        r = data["results"][0]
        assert "index" in r
        assert r.get("error") is None
        assert "vendor" in r
        assert "line_items" in r
        assert isinstance(r["line_items"], list)

    import asyncio

    asyncio.run(do())


def test_url_batch_returns_result():
    """A batch with image_urls should fetch and parse each URL."""
    transport = ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")

    async def do() -> None:
        # We'll use a local mock server approach: pass a URL that will
        # actually resolve. Since we cannot guarantee internet access in
        # all test environments, use a 404 URL to test the error path.
        resp = await client.post(
            "/v1/parse-receipts",
            data={"image_urls": '["https://httpbin.org/image/png"]'},
        )
        # Either success or a handled fetch error - both should return 200
        # because the batch endpoint must handle individual failures.
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 1

    import asyncio

    asyncio.run(do())


def test_mixed_input_returns_400():
    transport = ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")

    async def do() -> None:
        image = Image.new("RGB", (100, 50), color="white")
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        buf.seek(0)
        resp = await client.post(
            "/v1/parse-receipts",
            files=[("files", ("a.png", buf, "image/png"))],
            data={"image_urls": '["https://example.com/r.jpg"]'},
        )
        assert resp.status_code == 400

    import asyncio

    asyncio.run(do())


def test_failed_url_returns_error_field():
    """Individual URL failures must produce an `error` field in the result."""
    transport = ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")

    async def do() -> None:
        resp = await client.post(
            "/v1/parse-receipts",
            data={"image_urls": '["https://localhost:1/nonexistent.jpg"]'},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 1
        r = data["results"][0]
        assert r.get("error") is not None
        assert r.get("vendor") is None

    import asyncio

    asyncio.run(do())


def test_summary_counts_are_correct():
    """summary.total, summary.successful, summary.failed must match results."""
    transport = ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")

    async def do() -> None:
        # Multiple URLs that will fail
        resp = await client.post(
            "/v1/parse-receipts",
            data={
                "image_urls": '["https://localhost:1/r1.jpg", "https://localhost:1/r2.jpg"]',
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        summary = data["summary"]
        assert summary["total"] == len(data["results"])
        assert summary["successful"] == sum(1 for r in data["results"] if r.get("error") is None)
        assert summary["failed"] == sum(1 for r in data["results"] if r.get("error") is not None)

    import asyncio

    asyncio.run(do())
