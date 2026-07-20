"""Pre-development interface + behavioral tests for the ReceiptLens OCR pipeline.

Contract under test (app/ocr.py):
  - extract_text(image_bytes: bytes) -> str
  - parse_receipt(image_bytes: bytes) -> ParsedReceipt

Interface tests PASS immediately (they only inspect signatures / type hints).
Behavioral tests are marked xfail(strict=True, raises=NotImplementedError):
they encode the INTENDED behavior (returning a str / ParsedReceipt) and so
they FAIL now because the stub raises NotImplementedError -- the expected
pre-dev failure. pytest still exits 0 (xfail, not a hard failure). Once a
real implementation lands, these tests will XPASS and strict=True will turn
that into a failure, forcing the developer to drop the xfail marker.
"""
import inspect
from typing import get_type_hints

import pytest

from app import ocr

SAMPLE_BYTES = b"\x89PNG\r\n\x1a\n(fake receipt image bytes)"


# --------------------------------------------------------------------------
# Interface tests -- must pass immediately
# --------------------------------------------------------------------------
def test_ocr_importable():
    assert ocr is not None


def test_extract_text_signature():
    sig = inspect.signature(ocr.extract_text)
    params = list(sig.parameters)
    assert params == ["image_bytes"], f"extract_text params changed: {params}"
    assert sig.parameters["image_bytes"].annotation is bytes


def test_extract_text_return_type_hint():
    hints = get_type_hints(ocr.extract_text)
    assert hints.get("return") is str, "extract_text must be annotated -> str"


def test_parse_receipt_signature():
    sig = inspect.signature(ocr.parse_receipt)
    params = list(sig.parameters)
    assert params == ["image_bytes"], f"parse_receipt params changed: {params}"
    assert sig.parameters["image_bytes"].annotation is bytes


def test_parse_receipt_return_type_hint():
    hints = get_type_hints(ocr.parse_receipt)
    assert hints.get("return") is ocr.ParsedReceipt


def test_dataclasses_defined():
    assert hasattr(ocr, "ReceiptItem")
    assert hasattr(ocr, "ParsedReceipt")


# --------------------------------------------------------------------------
# Behavioral tests -- expected failures (NotImplementedError) until implemented
# --------------------------------------------------------------------------
@pytest.mark.xfail(strict=True, raises=NotImplementedError, reason="OCR pipeline not implemented yet")
def test_extract_text_returns_str():
    result = ocr.extract_text(SAMPLE_BYTES)
    assert isinstance(result, str)


@pytest.mark.xfail(strict=True, raises=NotImplementedError, reason="OCR pipeline not implemented yet")
def test_parse_receipt_returns_parsed():
    result = ocr.parse_receipt(SAMPLE_BYTES)
    assert isinstance(result, ocr.ParsedReceipt)
