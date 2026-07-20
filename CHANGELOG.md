# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-07-20

### Features

- Initial ReceiptLens OCR API scaffold.
- `POST /v1/parse-receipt` endpoint accepts multipart file upload or `image_url` form field.
- Tesseract 5 OCR pipeline with image pre-processing (grayscale, upscale, contrast, sharpen).
- Regex-based receipt parser extracting vendor, date, line items, tax, total, and currency.
- Async FastAPI application with `/health` endpoint and OpenAPI docs.
- Pydantic-style response schema: `vendor`, `total`, `date`, `tax`, `currency`, `line_items[]`.

### Tests

- 16 pytest tests covering API routes, OCR signatures, and runtime behavior.
- `ruff` linting configured and passing.
