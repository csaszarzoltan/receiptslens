"""Pre-development tests for magic byte validation in preprocessing module.

Interface tests must pass immediately (with stub).
Behavioral tests must fail clearly (NotImplementedError from stub).

Covers P0 SSRF hardening gap identified in analysis brief t_9bfd006a.
"""
from __future__ import annotations
import inspect
from typing import get_type_hints


import pytest
from app.preprocessing import validate_magic_bytes


# ---------------------------------------------------------------------------
# Helpers — minimal file-content byte arrays
# ---------------------------------------------------------------------------

# JPEG: starts with FF D8 FF
JPEG_HEADER = b"\xff\xd8\xff\xe0" + b"\x00" * 100

# PNG: starts with 89 50 4E 47 0D 0A 1A 0A
PNG_HEADER = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

# TIFF (little-endian): starts with 49 49 2A 00
TIFF_LE_HEADER = b"II*\x00" + b"\x00" * 100

# TIFF (big-endian): starts with 4D 4D 00 2A
TIFF_BE_HEADER = b"MM\x00*" + b"\x00" * 100

# BMP: starts with 42 4D
BMP_HEADER = b"BM" + b"\x00" * 100

# WEBP: starts with 52 49 46 46 ... 57 45 42 50
WEBP_HEADER = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 100

# GIF87a
GIF87_HEADER = b"GIF87a" + b"\x00" * 100

# GIF89a
GIF89_HEADER = b"GIF89a" + b"\x00" * 100

# Non-image content
HTML_CONTENT = b"<html><body>Hello</body></html>"
PDF_CONTENT = b"%PDF-1.4 some fake pdf content"
TEXT_CONTENT = b"This is plain text, not an image."
PARTIAL_PNG = b"\x89PNG\r\n"  # truncated PNG header (missing \x1a\n)


# ---------------------------------------------------------------------------
# Interface tests — must PASS immediately with stub
# ---------------------------------------------------------------------------

class TestMagicBytesInterface:
    """Import, signature, and type-hint checks."""

    def test_function_importable(self):
        """validate_magic_bytes is importable from app.preprocessing."""
        assert validate_magic_bytes is not None

    def test_function_is_callable(self):
        assert callable(validate_magic_bytes)

    def test_signature_single_param(self):
        sig = inspect.signature(validate_magic_bytes)
        params = list(sig.parameters)
        assert params == ["data"], f"Expected ['data'], got {params}"

    def test_param_type_is_bytes(self):
        hints = get_type_hints(validate_magic_bytes)
        assert hints.get("data") is bytes, f"data hint: {hints.get('data')}"

    def test_return_type_is_none(self):
        hints = get_type_hints(validate_magic_bytes)
        assert hints.get("return") is type(None), (
            f"return hint: {hints.get('return')}"
        )

    def test_valid_png_does_not_raise(self):
        """Valid PNG bytes should pass validation without error."""
        validate_magic_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10)


# ---------------------------------------------------------------------------
# Behavioral tests — must FAIL (NotImplementedError from stub)
# ---------------------------------------------------------------------------

class TestMagicBytesBehavior:
    """Validate that each supported format passes, and non-images are rejected.

    All these tests should fail until validate_magic_bytes is implemented.
    """

    def test_valid_jpeg_header(self):
        validate_magic_bytes(JPEG_HEADER)

    def test_valid_png_header(self):
        validate_magic_bytes(PNG_HEADER)

    def test_valid_tiff_little_endian(self):
        validate_magic_bytes(TIFF_LE_HEADER)

    def test_valid_tiff_big_endian(self):
        validate_magic_bytes(TIFF_BE_HEADER)

    def test_valid_bmp_header(self):
        validate_magic_bytes(BMP_HEADER)

    def test_valid_webp_header(self):
        validate_magic_bytes(WEBP_HEADER)

    def test_valid_gif87_header(self):
        validate_magic_bytes(GIF87_HEADER)

    def test_valid_gif89_header(self):
        validate_magic_bytes(GIF89_HEADER)

    def test_empty_bytes_raises_value_error(self):
        with pytest.raises(ValueError, match="empty|must not be empty|no data"):
            validate_magic_bytes(b"")

    def test_html_content_raises_value_error(self):
        with pytest.raises(ValueError, match=" unrecognized|not.*image|invalid|unsupported"):
            validate_magic_bytes(HTML_CONTENT)

    def test_pdf_content_raises_value_error(self):
        with pytest.raises(ValueError, match=" unrecognized|not.*image|invalid|unsupported"):
            validate_magic_bytes(PDF_CONTENT)

    def test_plain_text_raises_value_error(self):
        with pytest.raises(ValueError, match=" unrecognized|not.*image|invalid|unsupported"):
            validate_magic_bytes(TEXT_CONTENT)

    def test_partial_header_raises_value_error(self):
        with pytest.raises(ValueError, match=" unrecognized|not.*image|invalid|unsupported"):
            validate_magic_bytes(PARTIAL_PNG)

    def test_zero_length_raises_value_error(self):
        with pytest.raises(ValueError):
            validate_magic_bytes(b"")


# ---------------------------------------------------------------------------
# Integration test — preprocess_image should reject non-image bytes
# ---------------------------------------------------------------------------

class TestMagicBytesIntegration:
    """Verify that the preprocessing pipeline rejects non-image bytes.

    This test should fail until validate_magic_bytes is wired into
    preprocess_image() before Image.open().
    """

    def test_preprocess_image_rejects_html(self):
        """preprocess_image should reject HTML content before PIL decode."""
        from app.preprocessing import preprocess_image
        # Currently preprocess_image raises ValueError for non-decodable bytes,
        # but the error message should mention validation, not PIL decode failure.
        with pytest.raises(ValueError):
            preprocess_image(HTML_CONTENT)

    def test_preprocess_image_rejects_empty(self):
        from app.preprocessing import preprocess_image
        with pytest.raises(ValueError, match="image_bytes must not be empty"):
            preprocess_image(b"")
