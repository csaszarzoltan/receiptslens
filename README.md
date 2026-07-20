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
- **Flexible input** — accepts a multipart `file` upload or an `image_url` form field.
- **FastAPI service** — async endpoint with `/health`, OpenAPI docs, and strict type hints.
- **Tested** — 16 pytest tests, ruff linted, type-checked dataclasses.

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

### Extract from a file

```bash
curl -X POST "http://localhost:8000/v1/parse-receipt" \
  -F "file=@/path/to/receipt.jpg"
```

### Extract from a URL

```bash
curl -X POST "http://localhost:8000/v1/parse-receipt" \
  -F "image_url=https://example.com/receipt.jpg"
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
  ]
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
