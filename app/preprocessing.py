"""Image preprocessing for ReceiptLens OCR pipeline.

Handles deskew, EXIF orientation correction, adaptive thresholding,
and the standard grayscale → upscale → contrast → sharpen pipeline.
"""
from __future__ import annotations

import io

from PIL import Image, ImageEnhance, ImageFilter

# Pillow >=9.1 exposes resampling filters under ``Image.Resampling``; provide a
# compatibility alias so the rest of the module can use ``RESAMPLING`` without
# version checks.
try:
    _RESAMPLING = Image.Resampling.LANCZOS  # type: ignore[attr-defined]
except AttributeError:
    _RESAMPLING = Image.LANCZOS  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# EXIF orientation handling
# ---------------------------------------------------------------------------

def _auto_rotate_exif(image: Image.Image) -> Image.Image:
    """Apply EXIF orientation tag so the image appears upright.

    Returns the image unchanged if no EXIF orientation tag is present or
    if the tag value is already 1 (normal).
    """
    try:
        exif = image.getexif()
    except Exception:
        return image

    if not exif:
        return image

    # EXIF tag 274 (0x0112) is Orientation
    orientation = exif.get(274)
    if orientation is None:
        return image

    # Pillow EXIF orientation values and their corresponding operations.
    _ORIENTATION_TRANSFORMS = {
        2: Image.FLIP_LEFT_RIGHT,
        3: Image.ROTATE_180,
        4: Image.FLIP_TOP_BOTTOM,
        5: Image.TRANSPOSE,
        6: Image.ROTATE_270,
        7: Image.TRANSVERSE,
        8: Image.ROTATE_90,
    }

    transform = _ORIENTATION_TRANSFORMS.get(orientation)
    if transform is not None:
        image = image.transpose(transform)
    return image


# ---------------------------------------------------------------------------
# Deskew via projection profiles
# ---------------------------------------------------------------------------

def _detect_skew_angle(image: Image.Image, *, angle_range: float = 5.0,
                       step: float = 0.5) -> float:
    """Detect the skew angle of a binarized image using projection profiles.

    Tries rotations from ``-angle_range`` to ``+angle_range`` in ``step``
    increments and picks the angle whose horizontal projection profile has
    the highest variance (sharpest text baselines).

    Parameters
    ----------
    image:
        A grayscale (mode ``L``) image.
    angle_range:
        Maximum rotation angle to test (degrees). Default ±5°.
    step:
        Rotation step size (degrees). Default 0.5°.

    Returns
    -------
    float
        Detected skew angle in degrees, positive = clockwise.
        Returns 0.0 if the image is too small or detection fails.
    """
    width, height = image.size
    if width < 10 or height < 10:
        return 0.0

    best_angle = 0.0
    best_score = -1.0

    # Try each candidate angle
    angle = -angle_range
    while angle <= angle_range + 0.001:  # tiny epsilon for float comparison
        # rotate() only supports NEAREST/BILINEAR/BICUBIC, not LANCZOS
        rotated = image.rotate(angle, resample=Image.BILINEAR, expand=False)
        # Compute horizontal projection profile
        pixels = list(rotated.getdata())
        row_sum = [0] * rotated.height
        for y in range(rotated.height):
            start = y * rotated.width
            end = start + rotated.width
            row_sum[y] = sum(pixels[start:end])

        # Score = variance of projection profile (higher = more aligned text)
        n = len(row_sum)
        if n == 0:
            angle += step
            continue
        mean = sum(row_sum) / n
        variance = sum((v - mean) ** 2 for v in row_sum) / n

        if variance > best_score:
            best_score = variance
            best_angle = angle

        angle += step

    return round(best_angle, 2)


def _deskew(image: Image.Image) -> Image.Image:
    """Rotate the image to correct detected skew.

    Only rotates if the detected angle is non-trivial (> 0.2°).
    """
    angle = _detect_skew_angle(image)
    if abs(angle) > 0.2:
        image = image.rotate(angle, resample=Image.BILINEAR, expand=True)
    return image


# ---------------------------------------------------------------------------
# Adaptive thresholding
# ---------------------------------------------------------------------------

def _adaptive_threshold(image: Image.Image) -> Image.Image:
    """Apply adaptive thresholding to handle noisy receipt images.

    Uses a block-based approach: for each pixel, compare it to the local
    mean of its neighborhood. Pixels darker than (mean - C) become black,
    others become white. C=10 is a common choice for receipts.

    Skips for very large images (>2M pixels) to avoid excessive compute —
    falls back to a global Otsu-style threshold instead.
    """
    width, height = image.size
    # For large images, skip adaptive and use global threshold
    if width * height > 2_000_000:
        threshold = 128
        return image.point(lambda p: 0 if p < threshold else 255)

    pixels = list(image.getdata())

    block_size = 15  # must be odd
    c = 10
    half = block_size // 2

    # Build integral image (summed area table) for O(1) local mean queries
    integral = [0] * (width * height)
    for y in range(height):
        row_sum = 0
        for x in range(width):
            row_sum += pixels[y * width + x]
            above = integral[(y - 1) * width + x] if y > 0 else 0
            integral[y * width + x] = above + row_sum

    result = [0] * (width * height)
    for y in range(height):
        for x in range(width):
            y_start = max(0, y - half)
            y_end = min(height - 1, y + half)
            x_start = max(0, x - half)
            x_end = min(width - 1, x + half)

            # Sum using integral image corners
            total = integral[y_end * width + x_end]
            if x_start > 0:
                total -= integral[y_end * width + (x_start - 1)]
            if y_start > 0:
                total -= integral[(y_start - 1) * width + x_end]
            if x_start > 0 and y_start > 0:
                total += integral[(y_start - 1) * width + (x_start - 1)]

            count = (y_end - y_start + 1) * (x_end - x_start + 1)
            mean = total / count if count > 0 else 128
            idx = y * width + x
            result[idx] = 0 if pixels[idx] < (mean - c) else 255

    out = Image.new("L", (width, height))
    out.putdata(result)
    return out


# ---------------------------------------------------------------------------
# Main preprocessing pipeline
# ---------------------------------------------------------------------------

def preprocess_image(image_bytes: bytes, *, deskew: bool = True) -> Image.Image:
    """Full preprocessing pipeline for receipt images.

    Steps:
    1. Decode bytes → PIL Image
    2. Auto-rotate via EXIF orientation tag
    3. Convert to grayscale
    4. Deskew (optional, enabled by default)
    5. Upscale 1.5× (LANCZOS)
    6. Adaptive thresholding for noisy receipts
    7. Contrast enhancement (2.0×)
    8. Sharpen

    Parameters
    ----------
    image_bytes:
        Raw image file bytes (PNG, JPEG, TIFF, etc.).
    deskew:
        Whether to detect and correct skew. Default ``True``.

    Returns
    -------
    PIL.Image.Image
        Preprocessed grayscale image ready for Tesseract OCR.

    Raises
    ------
    ValueError
        If ``image_bytes`` is empty.
    """
    if not image_bytes:
        raise ValueError("image_bytes must not be empty")

    try:
        validate_magic_bytes(image_bytes)
        image = Image.open(io.BytesIO(image_bytes))
    except Exception as exc:
        raise ValueError(f"Cannot decode image: {exc}") from exc

    # 1. EXIF orientation
    image = _auto_rotate_exif(image)

    # 2. Grayscale
    image = image.convert("L")

    # 3. Deskew
    if deskew:
        image = _deskew(image)

    # 4. Upscale 1.5×
    image = image.resize(
        (int(image.width * 1.5), int(image.height * 1.5)),
        _RESAMPLING,
    )

    # 5. Adaptive threshold
    image = _adaptive_threshold(image)

    # 6. Contrast enhancement
    enhancer = ImageEnhance.Contrast(image)
    image = enhancer.enhance(2.0)

    # 7. Sharpen
    image = image.filter(ImageFilter.SHARPEN)

    return image


# ---------------------------------------------------------------------------
# Magic byte validation (SSRF hardening)
# ---------------------------------------------------------------------------

# Supported image formats and their magic byte signatures.
_IMAGE_SIGNATURES: dict[str, list[bytes]] = {
    "JPEG": [b"\xff\xd8\xff"],
    "PNG":  [b"\x89PNG\r\n\x1a\n"],
    "TIFF": [b"II*\x00", b"MM\x00*"],
    "BMP":  [b"BM"],
    "WEBP": [b"RIFF"],  # also check bytes 8-12 == b"WEBP"
    "GIF":  [b"GIF87a", b"GIF89a"],
}


def validate_magic_bytes(data: bytes) -> None:
    """Validate that image bytes start with a recognized magic signature.

    Parameters
    ----------
    data : bytes
        Raw file content to validate.

    Raises
    ------
    ValueError
        If data is empty or does not match any known image signature.
    """
    if not data:
        raise ValueError("data must not be empty")

    for fmt, signatures in _IMAGE_SIGNATURES.items():
        for sig in signatures:
            if data[: len(sig)] == sig:
                # WEBP requires an additional check: bytes 8-12 must spell "WEBP"
                if fmt == "WEBP":
                    if len(data) >= 12 and data[8:12] == b"WEBP":
                        return
                    # If RIFF header matched but not WEBP tag, keep looking
                    continue
                return

    raise ValueError(f"not an image: unrecognized format {data[:8]!r}")
