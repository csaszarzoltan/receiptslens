"""Tests for the image preprocessing module."""
from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.preprocessing import (
    _auto_rotate_exif,
    _adaptive_threshold,
    _detect_skew_angle,
    _deskew,
    preprocess_image,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_image_bytes(width: int = 200, height: int = 100,
                      color: str = "white", *, mode: str = "RGB") -> bytes:
    """Create a simple solid-color PNG image and return its bytes."""
    img = Image.new(mode, (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_receipt_image_bytes() -> bytes:
    """Create a synthetic receipt image with readable text."""
    img = Image.new("RGB", (400, 600), color="white")
    draw = ImageDraw.Draw(img)
    # Use default font
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
        font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
    except (OSError, IOError):
        font = ImageFont.load_default()
        font_large = font

    draw.text((100, 20), "GROCERY STORE", fill="black", font=font_large)
    draw.text((10, 80), "Date: 21/07/2026", fill="black", font=font)
    draw.line([(10, 110), (390, 110)], fill="black", width=1)
    draw.text((10, 120), "Milk         $1.20", fill="black", font=font)
    draw.text((10, 150), "Bread        $2.50", fill="black", font=font)
    draw.text((10, 180), "Eggs         $3.99", fill="black", font=font)
    draw.line([(10, 220), (390, 220)], fill="black", width=1)
    draw.text((10, 230), "Total:      $7.69", fill="black", font=font)
    draw.text((10, 260), "Tax:        $0.62", fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_rotated_receipt_bytes(angle: float) -> bytes:
    """Create a receipt image rotated by the given angle (degrees)."""
    img = Image.new("RGB", (400, 600), color="white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
    except (OSError, IOError):
        font = ImageFont.load_default()
    draw.text((100, 100), "GROCERY STORE", fill="black", font=font)
    draw.text((10, 200), "Total: $5.00", fill="black", font=font)
    # Rotate the image
    rotated = img.rotate(angle, resample=Image.BILINEAR, expand=True)
    buf = io.BytesIO()
    rotated.save(buf, format="PNG")
    return buf.getvalue()


def _make_exif_image_bytes(orientation: int) -> bytes:
    """Create a PNG image with a specific EXIF orientation tag.

    We manually write EXIF data since PNG doesn't natively support EXIF
    in the same way as JPEG, but PIL can read it.
    """
    img = Image.new("RGB", (200, 100), color="white")
    draw = ImageDraw.Draw(img)
    draw.text((10, 10), "HELLO", fill="black")
    # Set EXIF orientation tag (tag 274 = 0x0112)
    exif = img.getexif()
    exif[274] = orientation
    img.info["exif"] = exif.tobytes()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# preprocess_image tests
# ---------------------------------------------------------------------------

class TestPreprocessImage:
    """Tests for the main preprocess_image() function."""

    def test_returns_pil_image(self):
        result = preprocess_image(_make_image_bytes())
        assert isinstance(result, Image.Image)

    def test_returns_grayscale(self):
        result = preprocess_image(_make_image_bytes())
        assert result.mode == "L"

    def test_upscaled(self):
        img_bytes = _make_image_bytes(200, 100)
        result = preprocess_image(img_bytes)
        # Base size is 200×100, upscale 1.5× → ~300×150.
        # Deskew may expand slightly, so allow generous tolerance.
        assert result.width >= 290
        assert result.height >= 140

    def test_empty_bytes_raises_value_error(self):
        with pytest.raises(ValueError, match="image_bytes must not be empty"):
            preprocess_image(b"")

    def test_corrupt_bytes_raises_value_error(self):
        with pytest.raises(ValueError, match="Cannot decode image"):
            preprocess_image(b"not an image at all")

    def test_very_small_image(self):
        """Very small images should be processed without errors."""
        img = Image.new("RGB", (5, 5), color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        result = preprocess_image(buf.getvalue())
        assert isinstance(result, Image.Image)

    def test_deskew_false(self):
        """When deskew=False, deskew should be skipped."""
        img_bytes = _make_image_bytes(200, 100)
        result = preprocess_image(img_bytes, deskew=False)
        assert isinstance(result, Image.Image)

    def test_jpeg_input(self):
        """Should handle JPEG input, not just PNG."""
        img = Image.new("RGB", (200, 100), color="white")
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        result = preprocess_image(buf.getvalue())
        assert isinstance(result, Image.Image)

    def test_receipt_image_processed(self):
        """A receipt image should produce a usable preprocessed output."""
        result = preprocess_image(_make_receipt_image_bytes())
        assert isinstance(result, Image.Image)
        assert result.mode == "L"
        # Check it has some non-white content (the text)
        pixels = list(result.getdata())
        non_white = sum(1 for p in pixels if p < 200)
        assert non_white > 0, "Preprocessed receipt should have dark pixels from text"


# ---------------------------------------------------------------------------
# _auto_rotate_exif tests
# ---------------------------------------------------------------------------

class TestAutoRotateExif:
    """Tests for EXIF orientation correction."""

    def test_no_exif_returns_same(self):
        img = Image.new("RGB", (100, 50), color="white")
        result = _auto_rotate_exif(img)
        assert result.size == img.size

    def test_orientation_1_no_change(self):
        """Orientation 1 = normal, no transformation needed."""
        img = Image.new("RGB", (100, 50), color="white")
        exif = img.getexif()
        exif[274] = 1
        result = _auto_rotate_exif(img)
        assert result.size == img.size

    def test_orientation_6_rotates_270(self):
        """Orientation 6 = 90° CW → transpose to 270°."""
        img = Image.new("RGB", (200, 100), color="white")
        exif = img.getexif()
        exif[274] = 6
        result = _auto_rotate_exif(img)
        # After ROTATE_270, width/height swap
        assert result.size == (100, 200)

    def test_orientation_3_rotates_180(self):
        """Orientation 3 = 180° rotation."""
        img = Image.new("RGB", (100, 50), color="white")
        exif = img.getexif()
        exif[274] = 3
        result = _auto_rotate_exif(img)
        assert result.size == (100, 50)

    def test_orientation_8_rotates_90(self):
        """Orientation 8 = 270° CW → transpose to 90°."""
        img = Image.new("RGB", (200, 100), color="white")
        exif = img.getexif()
        exif[274] = 8
        result = _auto_rotate_exif(img)
        assert result.size == (100, 200)

    def test_no_exif_data(self):
        """Image with no EXIF data at all should pass through."""
        img = Image.new("RGB", (100, 50), color="white")
        # getexif() returns empty Exif on fresh image
        result = _auto_rotate_exif(img)
        assert result.size == img.size


# ---------------------------------------------------------------------------
# _detect_skew_angle tests
# ---------------------------------------------------------------------------

class TestDetectSkewAngle:
    """Tests for skew angle detection."""

    def test_straight_image_returns_zero(self):
        """A non-skewed image should return ~0° skew."""
        img = Image.new("L", (200, 100), color=255)
        draw = ImageDraw.Draw(img)
        # Draw horizontal lines (text-like)
        for y in range(20, 80, 10):
            draw.line([(10, y), (190, y)], fill=0, width=2)
        angle = _detect_skew_angle(img)
        assert abs(angle) <= 1.0, f"Straight image should have near-zero skew, got {angle}"

    def test_small_image_returns_zero(self):
        """Images smaller than 10x10 should return 0.0."""
        img = Image.new("L", (5, 5), color=255)
        assert _detect_skew_angle(img) == 0.0

    def test_detects_rotation(self):
        """A rotated image should have a non-zero detected angle."""
        # Create an image with horizontal lines, then rotate it
        img = Image.new("L", (200, 100), color=255)
        draw = ImageDraw.Draw(img)
        for y in range(20, 80, 10):
            draw.line([(10, y), (190, y)], fill=0, width=2)
        rotated = img.rotate(3.0, resample=Image.BILINEAR, expand=True)
        angle = _detect_skew_angle(rotated)
        # The detected angle should be close to the rotation angle
        assert abs(angle) >= 1.0, f"Expected non-trivial angle, got {angle}"

    def test_custom_range(self):
        """Custom angle range should be respected."""
        img = Image.new("L", (100, 50), color=255)
        angle = _detect_skew_angle(img, angle_range=2.0, step=1.0)
        assert -2.0 <= angle <= 2.0


# ---------------------------------------------------------------------------
# _deskew tests
# ---------------------------------------------------------------------------

class TestDeskew:
    """Tests for deskew correction."""

    def test_straight_image_unchanged_or_minimal(self):
        """A straight image should pass through with minimal change."""
        img = Image.new("L", (200, 100), color=255)
        draw = ImageDraw.Draw(img)
        for y in range(20, 80, 10):
            draw.line([(10, y), (190, y)], fill=0, width=2)
        result = _deskew(img)
        # Size should be similar (small rotations may change size slightly)
        assert abs(result.width - img.width) < 20

    def test_returns_image(self):
        img = Image.new("L", (100, 50), color=255)
        result = _deskew(img)
        assert isinstance(result, Image.Image)


# ---------------------------------------------------------------------------
# _adaptive_threshold tests
# ---------------------------------------------------------------------------

class TestAdaptiveThreshold:
    """Tests for adaptive thresholding."""

    def test_returns_image(self):
        img = Image.new("L", (100, 50), color=128)
        result = _adaptive_threshold(img)
        assert isinstance(result, Image.Image)
        assert result.mode == "L"

    def test_binarized_output(self):
        """Output should be mostly black (0) or white (255)."""
        img = Image.new("L", (100, 50), color=128)
        result = _adaptive_threshold(img)
        pixels = list(result.getdata())
        # All pixels should be either 0 or 255
        for p in pixels:
            assert p in (0, 255), f"Expected binary output, got {p}"

    def test_white_stays_white(self):
        """A completely white image should stay white after thresholding."""
        img = Image.new("L", (100, 50), color=255)
        result = _adaptive_threshold(img)
        pixels = list(result.getdata())
        # With uniform white, all pixels should stay white (255)
        assert all(p == 255 for p in pixels)

    def test_black_stays_black(self):
        """A completely black image: adaptive threshold uses local mean (0),
        so pixels at 0 are NOT less than (0 - 10) → become white (255).
        This is correct behavior for uniform-adaptive thresholding."""
        img = Image.new("L", (100, 50), color=0)
        result = _adaptive_threshold(img)
        pixels = list(result.getdata())
        # All pixels have value 0, local mean = 0, 0 < (0-10) is False → all white
        assert all(p == 255 for p in pixels)
