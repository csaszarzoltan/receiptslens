"""Pre-development tests for the OCR exception hierarchy.

Interface tests must pass immediately (class definitions exist + correct hierarchy).
Behavioral tests must fail clearly (current code raises ValueError, not typed exceptions).

Covers P1 gap identified in analysis brief t_9bfd006a.
"""
from __future__ import annotations

import io
import pytest
from PIL import Image, ImageDraw, ImageFont

from app.exceptions import (
    OCRError,
    InvalidImageError,
    UnsupportedImageFormatError,
    CorruptImageError,
)
from app import ocr


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_receipt_bytes() -> bytes:
    """Create a minimal synthetic receipt image."""
    img = Image.new("RGB", (400, 300), color="white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20
        )
    except (OSError, IOError):
        font = ImageFont.load_default()
    draw.text((100, 20), "GROCERY STORE", fill="black", font=font)
    draw.text((10, 80), "Date: 21/07/2026", fill="black", font=font)
    draw.text((10, 120), "Milk         $1.20", fill="black", font=font)
    draw.text((10, 150), "Bread        $2.50", fill="black", font=font)
    draw.text((10, 200), "Total:      $3.70", fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Interface tests — must PASS immediately
# ---------------------------------------------------------------------------

class TestExceptionHierarchyInterface:
    """Verify exception classes exist and have correct inheritance."""

    def test_ocr_error_importable(self):
        assert OCRError is not None

    def test_invalid_image_error_importable(self):
        assert InvalidImageError is not None

    def test_unsupported_format_error_importable(self):
        assert UnsupportedImageFormatError is not None

    def test_corrupt_image_error_importable(self):
        assert CorruptImageError is not None

    def test_ocr_error_inherits_exception(self):
        assert issubclass(OCRError, Exception)

    def test_invalid_image_inherits_ocr_error(self):
        assert issubclass(InvalidImageError, OCRError)

    def test_unsupported_format_inherits_ocr_error(self):
        assert issubclass(UnsupportedImageFormatError, OCRError)

    def test_corrupt_image_inherits_ocr_error(self):
        assert issubclass(CorruptImageError, OCRError)

    def test_invalid_image_inherits_exception(self):
        """Transitive: InvalidImageError → OCRError → Exception."""
        assert issubclass(InvalidImageError, Exception)

    def test_unsupported_format_inherits_exception(self):
        assert issubclass(UnsupportedImageFormatError, Exception)

    def test_corrupt_image_inherits_exception(self):
        assert issubclass(CorruptImageError, Exception)

    def test_ocr_error_is_base_for_all_three(self):
        """OCRError should be the common base (not ValueError)."""
        for exc_cls in (InvalidImageError, UnsupportedImageFormatError, CorruptImageError):
            assert issubclass(exc_cls, OCRError)

    def test_exceptions_can_be_raised_and_caught(self):
        """All exceptions should be raiseable and catchable as OCRError."""
        for exc_cls in (InvalidImageError, UnsupportedImageFormatError, CorruptImageError):
            with pytest.raises(OCRError):
                raise exc_cls("test message")

    def test_exceptions_carry_message(self):
        exc = InvalidImageError("bad image")
        assert str(exc) == "bad image"


# ---------------------------------------------------------------------------
# Behavioral tests — must FAIL (current code raises ValueError, not typed)
# ---------------------------------------------------------------------------

class TestExceptionBehavior:
    """Verify that ocr.py functions raise typed exceptions instead of ValueError.

    These tests define the desired behavior after P1 implementation.
    They should FAIL until ValueError raises are replaced with typed exceptions.
    """

    def test_extract_text_empty_raises_invalid_image(self):
        """Empty bytes should raise InvalidImageError, not ValueError."""
        with pytest.raises(InvalidImageError):
            ocr.extract_text(b"")

    def test_extract_text_non_image_raises_invalid_image(self):
        """Non-image bytes should raise InvalidImageError, not ValueError."""
        with pytest.raises(InvalidImageError):
            ocr.extract_text(b"this is not an image")

    def test_extract_text_html_raises_invalid_image(self):
        """HTML content should raise InvalidImageError."""
        with pytest.raises(InvalidImageError):
            ocr.extract_text(b"<html><body>not an image</body></html>")

    def test_parse_receipt_empty_raises_invalid_image(self):
        """Empty bytes to parse_receipt should raise InvalidImageError."""
        with pytest.raises(InvalidImageError):
            ocr.parse_receipt(b"")

    def test_parse_receipt_non_image_raises_invalid_image(self):
        """Non-image bytes to parse_receipt should raise InvalidImageError."""
        with pytest.raises(InvalidImageError):
            ocr.parse_receipt(b"definitely not image data")

    def test_extract_text_valid_receipt_returns_str(self):
        """Valid receipt image should return a non-empty string."""
        result = ocr.extract_text(_make_receipt_bytes())
        assert isinstance(result, str)
        assert len(result) > 0

    def test_parse_receipt_valid_returns_parsed_receipt(self):
        """Valid receipt image should return a ParsedReceipt."""
        result = ocr.parse_receipt(_make_receipt_bytes())
        assert isinstance(result, ocr.ParsedReceipt)
        assert isinstance(result.items, list)
        assert isinstance(result.raw_text, str)
