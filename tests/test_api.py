"""Pre-development interface + behavioral tests for the ReceiptLens API.

Contract under test (app/api.py):
  - app: FastAPI instance
  - parse_receipt_endpoint(file: bytes) -> dict   (async)
  - POST /v1/parse-receipt

Interface tests PASS immediately (import + signature / route inspection).
The behavioral test is marked xfail(strict=True, raises=NotImplementedError):
it encodes the INTENDED behavior (returning a dict) and so it FAILS now
because the stub raises NotImplementedError -- the expected pre-dev failure.
pytest still exits 0 (xfail, not a hard failure). Once the endpoint is wired
to the OCR pipeline, the test will XPASS and strict=True will turn that into a
failure, forcing the developer to drop the xfail marker.
"""
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
    sig = inspect.signature(func)
    assert sig.return_annotation is dict
    hints = get_type_hints(func)
    assert "file" in hints


def test_parse_receipt_endpoint_is_async():
    assert inspect.iscoroutinefunction(api.parse_receipt_endpoint)


# --------------------------------------------------------------------------
# Behavioral test -- expected failure (NotImplementedError) until implemented
# --------------------------------------------------------------------------
@pytest.mark.xfail(strict=True, raises=NotImplementedError, reason="parse-receipt endpoint not implemented yet")
def test_parse_receipt_endpoint_not_implemented():
    asyncio.run(api.parse_receipt_endpoint(file=b""))
