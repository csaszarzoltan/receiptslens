"""Pre-development interface + behavioral tests for the ReceiptLens OCR pipeline."""
from __future__ import annotations

import inspect
from typing import get_type_hints

import pytest
from PIL import Image

from app import ocr

SAMPLE_BYTES = b"\x89PNG\r\n\x1a\n(fake receipt image bytes)"


# --------------------------------------------------------------------------
# Interface tests -- must pass immediately
# --------------------------------------------------------------------------
def test_ocr_importable():
    assert ocr is not None


def test_extract_text_signature():
    func = ocr.extract_text
    sig = inspect.signature(func)
    params = list(sig.parameters)
    assert params == ["image_bytes"], f"extract_text params changed: {params}"
    hints = get_type_hints(func)
    assert hints.get("image_bytes") is bytes


def test_extract_text_return_type_hint():
    hints = get_type_hints(ocr.extract_text)
    assert hints.get("return") is str, f"extract_text return hint is {hints.get('return')}"


def test_parse_receipt_signature():
    func = ocr.parse_receipt
    sig = inspect.signature(func)
    params = list(sig.parameters)
    assert params == ["image_bytes"], f"parse_receipt params changed: {params}"
    hints = get_type_hints(func)
    assert hints.get("image_bytes") is bytes


def test_parse_receipt_return_type_hint():
    hints = get_type_hints(ocr.parse_receipt)
    assert hints.get("return") is ocr.ParsedReceipt


def test_dataclasses_defined():
    assert hasattr(ocr, "ReceiptItem")
    assert hasattr(ocr, "ParsedReceipt")


# --------------------------------------------------------------------------
# Behavioral tests -- real OCR behavior
# --------------------------------------------------------------------------
def test_extract_text_returns_str_for_valid_image():
    image = Image.new("RGB", (200, 100), color="white")
    buf = __import__("io").BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)
    result = ocr.extract_text(buf.getvalue())
    assert isinstance(result, str)


def test_parse_receipt_returns_parsed_for_valid_image():
    image = Image.new("RGB", (200, 100), color="white")
    buf = __import__("io").BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)
    result = ocr.parse_receipt(buf.getvalue())
    assert isinstance(result, ocr.ParsedReceipt)
    assert isinstance(result.items, list)
    assert isinstance(result.raw_text, str)


def test_parse_receipt_rejects_empty_bytes():
    with pytest.raises(ValueError, match="image_bytes must not be empty"):
        ocr.parse_receipt(b"")
