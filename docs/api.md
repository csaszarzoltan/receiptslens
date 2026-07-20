# API Reference

The FastAPI application is exposed as `app.api.app`.

```bash
uvicorn app.main:app --reload
```

## Endpoints

### `GET /health`

Health-check probe.

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

### `POST /v1/parse-receipt`

Parse a receipt image and return structured JSON with per-field confidence scores.

Send either:
- `file` (multipart upload), or
- `image_url` (form field pointing to a public image URL)

Do not send both at the same time.

#### Request (file upload)

```bash
curl -X POST "http://localhost:8000/v1/parse-receipt" \
  -F "file=@/path/to/receipt.jpg"
```

#### Request (URL)

```bash
curl -X POST "http://localhost:8000/v1/parse-receipt" \
  -F "image_url=https://example.com/receipt.jpg"
```

#### Response

```json
{
  "vendor": "STORE NAME",
  "total": 42.50,
  "date": "2025-03-14",
  "tax": 3.40,
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
}
```

### `POST /v1/parse-receipt/async`

Queue an async OCR job and return immediately with a `job_id`.

Parameters:

- `file` (multipart upload)
- `image_url` (form field)
- `webhook_url` (optional form field) — URL to POST the result to when processing completes or fails.

```bash
curl -X POST "http://localhost:8000/v1/parse-receipt/async" \
  -F "file=@/path/to/receipt.jpg"
```

Response:

```json
{
  "job_id": "uuid",
  "status": "queued",
  "webhook_url": null
}
```

### `GET /v1/jobs/{job_id}`

Poll the status and result of an async OCR job.

```bash
curl "http://localhost:8000/v1/jobs/abc123"
```

Response:

```json
{
  "job_id": "abc123",
  "status": "completed",
  "result": { ... same shape as /v1/parse-receipt ... },
  "error": null
}
```

## Verified quickstart

```bash
python -c "
from PIL import Image
from app.ocr import parse_receipt
import io
img = Image.new('RGB', (200, 100), color='white')
buf = io.BytesIO()
img.save(buf, format='PNG')
buf.seek(0)
r = parse_receipt(buf.getvalue())
print(r)
"
```

```bash
curl -s http://localhost:8000/health
curl -s -X POST http://localhost:8000/v1/parse-receipt \
  -F 'file=@<(python -c "
from PIL import Image
import io, sys
img = Image.new(\"RGB\", (200, 100), color=\"white\")
buf = io.BytesIO()
img.save(buf, format=\"PNG\")
buf.seek(0)
sys.stdout.buffer.write(buf.read())
")' | python -m json.tool
```
