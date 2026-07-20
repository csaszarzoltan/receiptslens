"""Pre-development interface + behavioral tests for the ReceiptLens API."""
from __future__ import annotations

import asyncio
import inspect
from typing import get_type_hints

import pytest
from fastapi import FastAPI

from app import api


# --------------------------------------------------------------------------
# Interface tests -- must pass immediately
# --------------------------------------------------------------------------
def test_api_importable():
    assert api is not None


def test_app_is_fastapi():
    assert isinstance(api.app, FastAPI)


def test_parse_receipt_route_registered():
    routes = {getattr(r, "path", None) for r in api.app.routes}
    assert "/v1/parse-receipt" in routes


def test_parse_receipt_endpoint_signature():
    func = api.parse_receipt_endpoint
    # ``from __future__ import annotations`` stores annotations as strings.
    # Convert them back so we can check the runtime shape.
    hints = get_type_hints(func)
    assert hints.get("return") is dict, f"return annotation is {hints.get('return')}"
    assert "file" in hints
    assert hints["file"] is bytes


def test_parse_receipt_endpoint_is_async():
    assert inspect.iscoroutinefunction(api.parse_receipt_endpoint)


# --------------------------------------------------------------------------
# Behavioral tests -- actual endpoint behavior
# --------------------------------------------------------------------------
def test_parse_receipt_rejects_empty_payload():
    with pytest.raises(Exception):  # HTTPException raised inside async func
        asyncio.run(api.parse_receipt_endpoint(file=b""))


def test_parse_receipt_accepts_valid_image_bytes():
    """With a real image, the endpoint returns the expected schema."""
    from PIL import Image

    image = Image.new("RGB", (200, 100), color="white")
    buf = __import__("io").BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)
    image_bytes = buf.read()

    result = asyncio.run(api.parse_receipt_endpoint(file=image_bytes))
    assert isinstance(result, dict)
    for key in ("vendor", "total", "date", "tax", "currency", "line_items"):
        assert key in result, f"missing key {key!r} in {result}"
    assert isinstance(result["line_items"], list)
