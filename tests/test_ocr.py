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


# --------------------------------------------------------------------------
# Regression tests -- guard against the IndexError in _parse_line_items
# (m.group(2) crashed when _LINE_ITEM_RE had no capturing group for price).
# --------------------------------------------------------------------------
def test_parse_line_items_uses_price_group():
    """A line `NAME <amount>` must yield a ReceiptItem with the parsed price.

    This is the exact shape that previously raised ``IndexError: no such group``
    because the price group was non-capturing.
    """
    text = "Milk        1.20\nBread       2.50\nTOTAL       7.48"
    items = ocr._parse_line_items(text)
    # TOTAL is filtered out by the keyword guard; the two goods remain.
    names = {item.name for item in items}
    assert "Milk" in names and "Bread" in names
    milk = next(i for i in items if i.name == "Milk")
    assert milk.price == 1.2


def test_parse_line_items_handles_decimal_separators():
    # European-style thousands/decimal separation must not break parsing.
    text = "Wine        1.234,56\nWater       0,99"
    items = ocr._parse_line_items(text)
    prices = {item.name: item.price for item in items}
    assert prices.get("Wine") == 1234.56
    assert prices.get("Water") == 0.99
