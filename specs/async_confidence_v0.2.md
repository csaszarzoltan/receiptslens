# Spec: Confidence Scores + Async Processing (v0.2.0)

## Goal
Enable per-field confidence scoring and asynchronous receipt processing via job IDs and optional webhooks.

## Public API Changes

### 1. Extend response schema
Add optional `confidence` object to the existing `/v1/parse-receipt` response:

```json
{
  "vendor": "STORE NAME",
  "total": 42.50,
  "date": "2025-03-14",
  "tax": 3.40,
  "currency": "USD",
  "line_items": [
    { "name": "Milk", "price": 1.20, "confidence": 0.92 }
  ],
  "confidence": {
    "vendor": 0.88,
    "total": 0.95,
    "date": 0.80,
    "tax": 0.70,
    "currency": 0.99,
    "line_items": 0.85
  }
}
```

### 2. Add async endpoint
`POST /v1/parse-receipt/async`
- Accepts same inputs as `/v1/parse-receipt` plus optional `webhook_url` (str).
- Returns immediately:
```json
{ "job_id": "uuid", "status": "queued", "webhook_url": "https://..." }
```
- Store job in memory dict with status `queued`, then transition to `processing` then `completed`/`failed`.
- Optional: if `webhook_url` provided, POST result JSON to it on completion/failure.

### 3. Add job status endpoint
`GET /v1/jobs/{job_id}`
- Returns:
```json
{ "job_id": "uuid", "status": "completed", "result": { ... } }
```

## Data Model Changes
- Add `ConfidenceReceipt` dataclass with `confidence` dict.
- Keep existing `ParsedReceipt` unchanged for backward compatibility.
- Add `job_id`, `job_status`, `job_webhook_url` fields.

## Implementation Notes
- Confidence heuristic: use Tesseract `image_to_data` to derive per-character accuracy metrics, map to fields heuristically.
- Async runner: `asyncio.Queue` + background task in FastAPI `lifespan`.
- Threading: sync `pytesseract` calls must run in threadpool to avoid blocking event loop.
- FastAPI app version bump to `"0.2.0"`.

## Tests
- Interface: test new endpoints registered, signatures, async property.
- Behavioral: test async job flows, confidence score shapes, webhook delivery, polling.
- Regression: all existing tests must pass unchanged.

## Non-functional
- No breaking changes to existing `/v1/parse-receipt`.
- ruff clean.
