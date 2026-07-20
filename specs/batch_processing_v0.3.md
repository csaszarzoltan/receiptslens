# Spec: Batch Receipt Processing (v0.3.0)

## Goal
Enable parsing multiple receipt images in a single API call. This is the
single highest-impact missing capability for ReceiptLens: enterprise and
scale use cases need to process bulk receipts, and every major commercial
OCR API comparison in 2025-2026 lists batch processing as a key
differentiator.

## Public API Changes

### New endpoint: `POST /v1/parse-receipts`

Accepts multiple receipt images in one request and returns an array of
individual parsing results.

**Request (multipart form):**
```
POST /v1/parse-receipts
Content-Type: multipart/form-data

files[]: image1.jpg
files[]: image2.jpg
files[]: image3.png
```

**Request (URLs):**
```
POST /v1/parse-receipts
Content-Type: multipart/form-data

image_urls: ["https://example.com/r1.jpg", "https://example.com/r2.jpg"]
```

Mixed `files[]` and `image_urls` is NOT supported in the same request to
keep input validation simple.

**Response:**
```json
{
  "results": [
    {
      "index": 0,
      "vendor": "STORE A",
      "total": 12.50,
      "date": "2025-03-14",
      "tax": 1.00,
      "currency": "USD",
      "line_items": [
        { "name": "ITEM", "price": 9.99 }
      ],
      "confidence": {
        "vendor": 0.88,
        "total": 0.95,
        "date": 0.80,
        "tax": 0.70,
        "currency": 0.99,
        "line_items": 0.85
      }
    },
    {
      "index": 1,
      "error": "Failed to fetch image from URL: 404 Client Error"
    }
  ],
  "summary": {
    "total": 2,
    "successful": 1,
    "failed": 1
  }
}
```

Each result carries an `index` matching the input order. On success, all
standard fields are present. On failure, `error` is a human-readable string
and all other fields are `None` / `[]`.

### Validation rules

- `files[]`: must provide 1-20 files. Each file must be an image
  (`content_type` starts with `image/`).
- `image_urls`: must provide 1-20 URLs. Each URL is fetched with httpx.
- Mixed inputs (`files[]` AND `image_urls`) return `400 Bad Request`.
- Empty request (`files[]` absent AND `image_urls` absent) returns `422`.
- More than 20 inputs returns `413 Payload Too Large`.

### Async batch support

The existing async infrastructure (`JobStore`, `_job_store`, background
tasks) is reused:

- `POST /v1/parse-receipts/async` accepts the same inputs plus optional
  `webhook_url`.
- Returns `job_id` immediately.
- Background task iterates over all inputs, updates percentage-like status
  string (e.g. `"3/10"`) on the job object, and finally sets status to
  `completed` or `failed`.
- `GET /v1/jobs/{job_id}` returns the full result array in `results`.
- If `webhook_url` is provided, the final result array is POSTed as JSON
  on completion or failure.

Internally, async batch reuses the same ThreadPoolExecutor so the event
loop remains responsive.

## Data Model Changes

No new dataclasses are strictly required, but for type clarity add:

```python
@dataclass(frozen=True)
class BatchReceiptResult:
    index: int
    result: ConfidenceReceipt | None
    error: str | None
```

The response dict is built directly in `api.py` to keep the layer
boundary clean.

## Implementation Notes

- Batch endpoint lives in `app/api.py`.
- Parse logic still lives in `app.ocr.parse_receipt_with_confidence`.
- The sync batch endpoint reuses `_bytes_from_upload` and
  `_bytes_from_url` helpers for each item.
- Async batch fetches/runs all items inside the existing
  `_process_job`/`_deliver_webhook` flow. Use a simple for-loop over
  the item list; do not over-engineer with queues.
- Bump FastAPI app version to `"0.3.0"`.
- Update `pyproject.toml` `version` to `"0.3.0"`.

## Tests

### Pre-development acceptance tests (`tests/test_batch_processing.py`)

Must cover:

**Interface tests:**
- Batch endpoint is registered at `/v1/parse-receipts`
- Batch endpoint accepts `files[]` (List[UploadFile])
- Batch endpoint accepts `image_urls` (str Form)
- Rejects mixed `files[]` + `image_urls` with 400
- Rejects >20 files with 413

**Behavioral tests:**
- Empty request returns 422
- Single-file batch returns result array of length 1
- Empty image (white PNG) returns successful result with empty line_items
- URL batch with valid URL returns result
- Mixed inputs returns 400
- Failed URL (404) returns error field in result dict
- Summary counts total/successful/failed are correct

**Regression tests:**
- All existing tests in `test_ocr.py`, `test_api.py`,
  `test_async_confidence.py` must still pass.

## Non-functional

- No breaking changes to existing `/v1/parse-receipt` endpoints.
- `ruff check .` must pass clean.
- README/docs/CHANGELOG updated in the same cycle.
