# ReceiptLens

<p align="center">
  <img alt="ReceiptLens" src="docs/assets/logo.svg" width="120" />
</p>

**ReceiptLens** extracts structured data from receipt images using Tesseract OCR.

Send an image (file upload or public URL) to `POST /v1/parse-receipt` and get back JSON with `vendor`, `total`, `date`, `tax`, `currency`, and `line_items[]`.

---

## Features

- **Receipt OCR** — runs Tesseract 5 on uploaded images with automatic pre-processing (grayscale, upscale, contrast, sharpen).
- **Structured output** — parses merchant, date, line items, subtotal, tax, and total from raw OCR text with regex heuristics.
- **Confidence scores** — every field includes a `confidence` float between 0.0 and 1.0, derived from Tesseract `image_to_data` accuracy metrics.
- **Async processing** — queue long-running OCR jobs with `POST /v1/parse-receipt/async`, poll with `GET /v1/jobs/{job_id}`, and receive a webhook callback on completion.
- **Flexible input** — accepts a multipart `file` upload or an `image_url` form field.
- **FastAPI service** — async endpoint with `/health`, OpenAPI docs, and strict type hints.
- **Tested** — 29 pytest tests, ruff linted, type-checked dataclasses.

---

## Getting Started

### Prerequisites

- Python 3.11+
- Tesseract OCR 5.x with the English language pack

### Installation

```bash
cp .env.example .env   # if present
pip install -e .
```

### Running

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Visit `http://localhost:8000/docs` for the interactive OpenAPI playground.

---

## Endpoints

### Sync parsing

```bash
curl -X POST "http://localhost:8000/v1/parse-receipt" \
  -F "file=@/path/to/receipt.jpg"
```

```bash
curl -X POST "http://localhost:8000/v1/parse-receipt" \
  -F "image_url=https://example.com/receipt.jpg"
```

### Async parsing

```bash
curl -X POST "http://localhost:8000/v1/parse-receipt/async" \
  -F "file=@/path/to/receipt.jpg"
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

## Tests

```bash
pytest
ruff check .
```

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

---

## License

MIT — see [LICENSE](LICENSE).
