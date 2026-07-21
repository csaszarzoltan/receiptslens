# ReceiptLens

<p align="center">
  <img alt="ReceiptLens" src="docs/assets/logo.svg" width="120" />
</p>

**ReceiptLens** extracts structured data from receipt images using Tesseract OCR.

Send an image (file upload, public URL, or batch of either) to `POST /v1/parse-receipt` or `POST /v1/parse-receipts` and get back JSON with `vendor`, `total`, `date`, `tax`, `currency`, and `line_items[]`.

---

## Features

- **Receipt OCR** — runs Tesseract 5 on uploaded images with automatic pre-processing (grayscale, upscale, contrast, sharpen).
- **Structured output** — parses merchant, date, line items, subtotal, tax, and total from raw OCR text with regex heuristics.
- **Confidence scores** — every field includes a `confidence` float between 0.0 and 1.0, derived from Tesseract `image_to_data` accuracy metrics.
- **OCR pipeline library** — use `app.ocr` and `app.preprocessing` as a Python library, no server required. See [Library Usage](#library-usage).
- **Image preprocessing** — automatic EXIF orientation correction, deskew via projection profiles, adaptive thresholding, and contrast/sharpen enhancements. All stages configurable.
- **Magic byte validation** — rejects non-image files before PIL decode (JPEG, PNG, TIFF, BMP, WEBP, GIF).
- **Typed exceptions** — `InvalidImageError`, `UnsupportedImageFormatError`, and `CorruptImageError` map to HTTP 400/415/422.
- **Async processing** — queue long-running OCR jobs with `POST /v1/parse-receipt/async`, poll with `GET /v1/jobs/{job_id}`, and receive a webhook callback on completion.
- **Batch processing** — parse multiple receipts in one call with `POST /v1/parse-receipts` for file uploads or `image_urls`, plus async batch jobs via `POST /v1/parse-receipts/async`.
- **Flexible input** — accepts a multipart `file` upload or an `image_url` form field, in single or batch mode.
- **FastAPI service** — async endpoint with `/health`, OpenAPI docs, and strict type hints.
- **Tested** — pytest suite plus `ruff` linting.

---

## Getting Started

### Prerequisites

- Python 3.11+
- Tesseract OCR 5.x with the English language pack

### Installation

```bash
# System dependency (Tesseract binary)
# Debian/Ubuntu:
sudo apt install tesseract-ocr tesseract-ocr-eng

# macOS:
brew install tesseract

# Python dependencies
pip install -e .
# Installs: pillow, pytesseract, fastapi, uvicorn, pydantic, httpx, python-multipart
```

### Running the server

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Visit `http://localhost:8000/docs` for the interactive OpenAPI playground.

---

## Library Usage

ReceiptLens works as a Python library without starting the server. All functions accept raw image bytes.

### Basic OCR

```python
from app.ocr import extract_text

with open("receipt.jpg", "rb") as f:
    text = extract_text(f.read())
print(text)
```

### Structured parsing

```python
from app.ocr import parse_receipt

with open("receipt.jpg", "rb") as f:
    receipt = parse_receipt(f.read())

print("vendor :", receipt.merchant)
print("date   :", receipt.date)
print("total  :", receipt.total)
print("tax    :", receipt.tax)
print("items  :", [(i.name, i.price) for i in receipt.items])
```

### With confidence scores

```python
from app.ocr import parse_receipt_with_confidence

with open("receipt.jpg", "rb") as f:
    result = parse_receipt_with_confidence(f.read())

for field, score in result.confidence.items():
    print(f"{field}: {score:.2f}")
```

### Preprocessing only

```python
from app.preprocessing import preprocess_image

with open("receipt.jpg", "rb") as f:
    image = preprocess_image(f.read(), deskew=True)
# Returns a PIL.Image.Image ready for custom processing
```

### Error handling

```python
from app.ocr import extract_text
from app.exceptions import InvalidImageError

try:
    result = extract_text(b"not an image")
except InvalidImageError as e:
    print(f"Bad input: {e}")
except ValueError as e:
    print(f"Decode error: {e}")
```

### Preprocessing options

| Option | Default | Description |
|---|---|---|
| `deskew` | `True` | Detect and correct skew via projection profiles (±5° range, 0.5° steps) |

The preprocessing pipeline runs these stages in order:

1. EXIF orientation correction
2. Grayscale conversion
3. Deskew (optional)
4. Upscale 1.5x (LANCZOS)
5. Adaptive thresholding (block size 15, C=10; global fallback for images >2M pixels)
6. Contrast enhancement (2.0x)
7. Sharpen

### Supported image formats

JPEG, PNG, TIFF (II/MM), BMP, WEBP, GIF — validated by magic bytes before PIL decode.

---

## API Endpoints

### Sync parsing

```bash
curl -X POST "http://localhost:8000/v1/parse-receipt" \
  -F "file=@/path/to/receipt.jpg"
```

```bash
curl -X POST "http://localhost:8000/v1/parse-receipt" \
  -F "image_url=https://example.com/receipt.jpg"
```

### Batch parsing

```bash
curl -X POST "http://localhost:8000/v1/parse-receipts" \
  -F "files=@/path/to/receipt1.jpg" \
  -F "files=@/path/to/receipt2.jpg"
```

```bash
curl -X POST "http://localhost:8000/v1/parse-receipts" \
  -F "image_urls=[\"https://example.com/receipt1.jpg\",\"https://example.com/receipt2.jpg\"]"
```

### Async parsing

```bash
curl -X POST "http://localhost:8000/v1/parse-receipt/async" \
  -F "file=@/path/to/receipt.jpg"
  # returns { "job_id": "uuid", "status": "queued" }
```

### Async batch parsing

```bash
curl -X POST "http://localhost:8000/v1/parse-receipts/async" \
  -F "files=@/path/to/receipt1.jpg" \
  -F "files=@/path/to/receipt2.jpg"
  # returns { "job_id": "uuid", "status": "queued" }
```

Poll for the result:

```bash
curl "http://localhost:8000/v1/jobs/{job_id}"
```

Optional webhook:

```bash
curl -X POST "http://localhost:8000/v1/parse-receipt/async" \
  -F "file=@/path/to/receipt.jpg" \
  -F "webhook_url=https://your-app.com/ocr-callback"
```

---

## Using image_url

ReceiptLens can fetch receipt images directly from a public URL instead of requiring a multipart file upload. This is useful for cloud-hosted images, webhooks, or when you want to avoid transferring large files in the request.

### Single receipt

Send `image_url` as a form field to `POST /v1/parse-receipt`:

```bash
curl -X POST "http://localhost:8000/v1/parse-receipt" \
  -F "image_url=https://example.com/receipt.jpg"
```

### Batch receipts

Send `image_urls` as a JSON-encoded array to `POST /v1/parse-receipts`:

```bash
curl -X POST "http://localhost:8000/v1/parse-receipts" \
  -F "image_urls=[\"https://example.com/receipt1.jpg\",\"https://example.com/receipt2.jpg\"]"
```

### Async receipt

Send `image_url` as a form field to `POST /v1/parse-receipt/async`:

```bash
curl -X POST "http://localhost:8000/v1/parse-receipt/async" \
  -F "image_url=https://example.com/receipt.jpg"
```

### URL constraints

| Constraint | Value |
|---|---|
| **Connect timeout** | 10 seconds |
| **Read timeout** | 30 seconds |
| **Redirects** | Automatically followed |
| **Allowed protocols** | `http://` and `https://` (httpx default) |
| **Maximum inputs** | 1 URL (single endpoint), 1-20 URLs (batch) |
| **Mixed input** | Cannot combine `file` upload + `image_url` in the same request |

### Error behavior

- **Invalid or unreachable URL** — returns `400 Bad Request` with detail: `Failed to fetch image from URL: <error message>`.
- **Both `file` and `image_url` provided** — returns `400 Bad Request`: `Provide either 'file' or 'image_url', not both.`
- **Neither `file` nor `image_url` provided** — returns `422`: `Missing required input: send 'file' or 'image_url'.`
- **Batch: invalid `image_urls` JSON** — returns `422` with JSON decode error details.
- **Batch: more than 20 URLs** — returns `413 Payload Too Large`.

In batch mode, individual URL failures are returned per-item in the `results` array (with an `error` field and null values for all other fields) without failing the entire request. The `summary` block counts `successful` and `failed` items.

---

## Response Schema

```jsonc
{
  "vendor": "STORE NAME",      // merchant / store name
  "total": 42.50,              // receipt total
  "date": "2025-03-14",        // ISO-8601 date
  "tax": 3.40,                 // tax amount (best-effort)
  "currency": "USD",           // ISO-4217 currency code
  "line_items": [              // parsed individual items
    { "name": "ITEM", "price": 9.99 }
  ],
  "confidence": {              // per-field confidence (0.0 - 1.0)
    "vendor": 0.88,
    "total": 0.95,
    "date": 0.80,
    "tax": 0.70,
    "currency": 0.99,
    "line_items": 0.85
  }
}
```

Batch responses wrap individual results in a top-level `results` array with a `summary` block.

## Tests

```bash
pytest
ruff check .
```

---

## Documentation

- [API Reference](docs/api.md) — endpoints, URL fetching contract, SSRF protection, error responses
- [OCR Pipeline](docs/ocr-pipeline.md) — architecture, preprocessing stages, configuration, error handling, tips

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

---

## License

MIT — see [LICENSE](LICENSE).
