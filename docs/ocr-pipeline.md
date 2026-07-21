# OCR Pipeline

The OCR pipeline lives in `app.ocr` and `app.preprocessing` and is responsible for turning raw image bytes into structured receipt data.

## Architecture

```
image_bytes
    │
    ▼
┌─────────────────────────────┐
│  validate_magic_bytes()     │  Reject non-image files (JPEG/PNG/TIFF/BMP/WEBP/GIF)
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│  preprocess_image()         │  7-stage image enhancement pipeline
│  ┌───────────────────────┐  │
│  │ 1. EXIF orientation   │  │  Auto-rotate based on EXIF tag 274
│  │ 2. Grayscale           │  │  Convert to mode "L"
│  │ 3. Deskew              │  │  Projection-profile skew detection (±5°)
│  │ 4. Upscale 1.5x        │  │  LANCZOS resampling
│  │ 5. Adaptive threshold  │  │  Block-based (15px, C=10) or global fallback
│  │ 6. Contrast enhance    │  │  2.0x via ImageEnhance
│  │ 7. Sharpen             │  │  ImageFilter.SHARPEN
│  └───────────────────────┘  │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│  pytesseract.image_to_*     │  Tesseract 5 (--oem 3 --psm 6)
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│  Regex parsing              │  Merchant, date, items, total, tax, currency
└─────────────┬───────────────┘
              │
              ▼
        ParsedReceipt
```

The pipeline is split across two modules:

| Module | Purpose |
|---|---|
| `app.preprocessing` | Image decode, validation, and enhancement. Returns `PIL.Image.Image`. |
| `app.ocr` | Tesseract invocation, regex parsing, data model, confidence scoring. |

## Preprocessing stages

### 1. EXIF orientation correction

Reads EXIF tag 274 (Orientation) and applies the corresponding transpose so the image appears upright. Handles all 8 EXIF orientation values. Returns the image unchanged if no tag is present.

### 2. Grayscale conversion

Converts the image to mode `L` (8-bit grayscale). Required for deskew and thresholding.

### 3. Deskew (optional, enabled by default)

Detects skew angle using horizontal projection profiles:

1. Binarizes the image at threshold 128.
2. Rotates from -5° to +5° in 0.5° steps.
3. For each angle, computes the variance of the horizontal projection profile.
4. Picks the angle with the highest variance (sharpest text baselines).
5. Rotates by the detected angle if > 0.2°.

Disable with `deskew=False`:

```python
from app.preprocessing import preprocess_image

image = preprocess_image(image_bytes, deskew=False)
```

### 4. Upscale 1.5x

Resizes the image to 1.5x its original dimensions using LANCZOS resampling. Larger text produces more accurate OCR results.

### 5. Adaptive thresholding

For each pixel, compares it to the local mean of its 15x15 neighborhood. Pixels darker than (mean - 10) become black, others become white.

**Large image fallback**: Images with >2M pixels skip adaptive thresholding and use a global threshold of 128 instead, to avoid excessive compute time.

### 6. Contrast enhancement

Applies 2.0x contrast enhancement via `PIL.ImageEnhance.Contrast`. Improves readability of faded thermal receipts.

### 7. Sharpen

Applies `PIL.ImageFilter.SHARPEN` to sharpen edges after upscaling.

## Magic byte validation

Before any image processing, `validate_magic_bytes()` checks the file header against known signatures:

| Format | Signature(s) |
|---|---|
| JPEG | `\xff\xd8\xff` |
| PNG | `\x89PNG\r\n\x1a\n` |
| TIFF | `II*\x00` (little-endian), `MM\x00*` (big-endian) |
| BMP | `BM` |
| WEBP | `RIFF` + bytes 8-12 = `WEBP` |
| GIF | `GIF87a`, `GIF89a` |

Files that don't match any signature are rejected with `ValueError` before PIL attempts to decode them.

```python
from app.preprocessing import validate_magic_bytes

validate_magic_bytes(open("receipt.jpg", "rb").read())  # OK
validate_magic_bytes(b"<html>not an image</html>")       # raises ValueError
```

## Configuration options

### `preprocess_image(image_bytes, *, deskew=True)`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `image_bytes` | `bytes` | — | Raw image file bytes. Raises `ValueError` if empty. |
| `deskew` | `bool` | `True` | Enable/disable skew correction. |

### Pipeline constants (hardcoded)

| Constant | Value | Location |
|---|---|---|
| Upscale factor | 1.5x | `preprocessing.py:243` |
| Contrast factor | 2.0x | `preprocessing.py:252` |
| Adaptive threshold block size | 15 | `preprocessing.py:158` |
| Adaptive threshold C | 10 | `preprocessing.py:159` |
| Large image threshold | 2,000,000 px | `preprocessing.py:152` |
| Deskew angle range | ±5° | `preprocessing.py:65` |
| Deskew step | 0.5° | `preprocessing.py:65` |
| Min deskew angle | 0.2° | `preprocessing.py:131` |
| Tesseract config | `--oem 3 --psm 6` | `ocr.py:443` |

## Error handling

The pipeline uses a typed exception hierarchy in `app.exceptions`:

```
Exception
└── OCRError
    ├── InvalidImageError (also inherits ValueError)
    ├── UnsupportedImageFormatError
    └── CorruptImageError
```

### When exceptions are raised

| Exception | When | HTTP status |
|---|---|---|
| `InvalidImageError` | Empty bytes, non-image bytes, magic byte mismatch, PIL decode failure | 400 |
| `UnsupportedImageError` | Image format recognized but not supported (reserved for future use) | 415 |
| `CorruptImageError` | Image is partially decoded but structurally corrupt (reserved for future use) | 422 |
| `ValueError` | Empty `image_bytes` in `preprocess_image` | — |

### Catching exceptions

```python
from app.ocr import extract_text, parse_receipt
from app.exceptions import InvalidImageError, OCRError

try:
    receipt = parse_receipt(image_bytes)
except InvalidImageError as e:
    # Bad input — tell the user their file isn't a valid image
    print(f"Invalid image: {e}")
except OCRError as e:
    # Other pipeline errors
    print(f"OCR failed: {e}")
except ValueError as e:
    # Preprocessing-level errors (decode, empty bytes)
    print(f"Preprocessing error: {e}")
```

`InvalidImageError` inherits from both `OCRError` and `ValueError`, so existing code catching `ValueError` continues to work.

## Tips for best OCR results on receipts

1. **Use well-lit images.** Even illumination across the receipt avoids shadow-induced threshold artifacts.

2. **Keep the receipt flat.** Wrinkles and folds break text baselines. Press the receipt flat before photographing.

3. **Scan at 300+ DPI.** Higher resolution gives Tesseract more pixels per character. The 1.5x upscale helps, but starting with a high-res image is better.

4. **Disable deskew for already-straight images.** If the receipt is already aligned, `deskew=False` avoids unnecessary computation:

   ```python
   image = preprocess_image(image_bytes, deskew=False)
   ```

5. **Check confidence scores.** Use `parse_receipt_with_confidence()` to identify fields where OCR struggled. Low confidence on `total` or `date` often means the text was partially occluded or faded.

6. **Supported formats.** JPEG and PNG work best. TIFF is reliable. BMP and GIF are supported but less common for receipts. Avoid converting to exotic formats — stick to what the camera produces.

7. **Currency detection.** The parser recognizes common symbols (`$`, `€`, `£`, `¥`, `₹`, `₽`) and ISO codes (`USD`, `EUR`, `GBP`, `JPY`, `INR`, `RUB`, `CZK`, `HUF`, `RON`, `BGN`, `PLN`, `SEK`, `NDK`, `DKK`). If your receipts use a different currency, the field will be `None`.

8. **Date format.** The parser handles `DD.MM.YYYY`, `DD/MM/YYYY`, `DD-MM-YYYY`, and 2-digit year variants. Other formats (US `MM/DD/YYYY`) may not be recognized.
