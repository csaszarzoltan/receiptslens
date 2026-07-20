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

##### URL fetching contract

When `image_url` is provided the server fetches the image server-side before
running OCR. The fetch is governed by the constraints below.

**Accepted schemes**

Only `http` and `https`. Any other scheme (e.g. `file://`, `ftp://`) is
rejected immediately with a `400` before any network call.

**SSRF protection**

The fetcher validates the URL and its resolved IP address to prevent
server-side request forgery.

| Layer | What is checked | Rejection detail |
|---|---|---|
| Scheme | Must be `http` or `https` | `400 — Failed to fetch image from URL.` |
| Hostname blocklist (exact) | `localhost`, `local.host`, `metadata.google.internal`, `metadata.internal`, `169.254.169.254`, `metadata` | `400 — Failed to fetch image from URL.` |
| Hostname blocklist (substring) | Hostnames containing `local`, `internal`, or `localhost` (case-insensitive, including subdomains such as `foo.local`) | `400 — Failed to fetch image from URL.` |
| Resolved IPs — private | `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` | `400 — Failed to fetch image from URL.` |
| Resolved IPs — loopback | `127.0.0.0/8`, `::1/128` | `400 — Failed to fetch image from URL.` |
| Resolved IPs — link-local | `169.254.0.0/16`, `fe80::/10` | `400 — Failed to fetch image from URL.` |
| Resolved IPs — carrier-grade NAT | `100.64.0.0/10` | `400 — Failed to fetch image from URL.` |
| Resolved IPs — multicast/reserved | `224.0.0.0/4`, `240.0.0.0/4`, `0.0.0.0/8`, `::/128`, `fc00::/7` | `400 — Failed to fetch image from URL.` |

DNS resolution happens **before** any HTTP request. If the hostname resolves
to a blocked address, the request is rejected with no network contact.

**Redirect handling**

- Maximum **5** redirects.
- Each redirect target is re-validated (scheme, hostname blocklist, resolved IPs).
- A redirect to a private or blocked host is rejected with `400`.
- Exceeding the redirect cap returns `400 — Failed to fetch image from URL.`

**Response validation**

| Check | Constraint | Error |
|---|---|---|
| Content-Type | Must start with `image/` | `400 — URL did not return an image` |
| Body size | Max **20 MB** (`MAX_IMAGE_BYTES = 20_000_000`) | `400 — Image exceeds maximum allowed size` |

**Timeouts**

| Phase | Limit |
|---|---|
| Connect | 10 s |
| Read | 30 s (`URL_FETCH_TIMEOUT = 30.0`) |

Network errors (timeouts, connection refused, DNS failures) return
`400 — Failed to fetch image from URL.`

**Error responses summary**

| Status | `detail` | Trigger |
|---|---|---|
| `400` | `Invalid image URL.` | Malformed URL (e.g. missing host) |
| `400` | `Failed to fetch image from URL.` | Unsupported scheme, blocked hostname/IP, DNS failure, connection error, timeout, too many redirects |
| `400` | `URL did not return an image` | Response Content-Type is not `image/*` |
| `400` | `Image exceeds maximum allowed size` | Response body exceeds 20 MB |
| `400` | `The provided data is not a recognized image format.` | Downloaded bytes are not a valid image (PIL error) |
| `400` | `Provide either 'file' or 'image_url', not both.` | Both `file` and `image_url` supplied |
| `422` | `Missing required input: send 'file' or 'image_url'.` | Neither `file` nor `image_url` supplied |
| `500` | `OCR processing failed.` | Unexpected OCR error (no internals leaked) |

> **Security note:** All error messages are controlled strings. Raw IP
> addresses, exception messages, and internal paths are never included in
> HTTP responses.

##### Example requests (URL)

```bash
# Valid URL
curl -X POST "http://localhost:8000/v1/parse-receipt" \
  -F "image_url=https://example.com/receipt.jpg"

# Blocked: file:// scheme → 400
curl -s -X POST "http://localhost:8000/v1/parse-receipt" \
  -F "image_url=file:///etc/passwd" | python -m json.tool
# {"detail":"Failed to fetch image from URL."}

# Blocked: metadata IP → 400
curl -s -X POST "http://localhost:8000/v1/parse-receipt" \
  -F "image_url=http://169.254.169.254/latest/meta-data/" | python -m json.tool
# {"detail":"Failed to fetch image from URL."}

# Blocked: non-image response → 400
curl -s -X POST "http://localhost:8000/v1/parse-receipt" \
  -F "image_url=https://example.com/page.html" | python -m json.tool
# {"detail":"URL did not return an image"}

# Blocked: empty input → 422
curl -s -X POST "http://localhost:8000/v1/parse-receipt" | python -m json.tool
# {"detail":"Missing required input: send 'file' or 'image_url'."}
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

### `POST /v1/parse-receipts`

Parse multiple receipt images in a single request. Accepts either multiple
`files` uploads or a JSON `image_urls` array, not both.

#### Request (file uploads)

```bash
curl -X POST "http://localhost:8000/v1/parse-receipts" \
  -F "files=@/path/to/receipt1.jpg" \
  -F "files=@/path/to/receipt2.jpg"
```

#### Request (URLs)

```bash
curl -X POST "http://localhost:8000/v1/parse-receipts" \
  -F 'image_urls=["https://example.com/receipt1.jpg","https://example.com/receipt2.jpg"]'
```

#### Response

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
      },
      "error": null
    },
    {
      "index": 1,
      "vendor": null,
      "total": null,
      "date": null,
      "tax": null,
      "currency": null,
      "line_items": [],
      "confidence": {
        "vendor": null,
        "total": null,
        "date": null,
        "tax": null,
        "currency": null,
        "line_items": null
      },
      "error": "Failed to fetch image from URL: ..."
    }
  ],
  "summary": {
    "total": 2,
    "successful": 1,
    "failed": 1
  }
}
```

### `POST /v1/parse-receipts/async`

Queue an async batch OCR job. Same inputs as `POST /v1/parse-receipts`,
plus optional `webhook_url` for completion callback.

```bash
curl -X POST "http://localhost:8000/v1/parse-receipts/async" \
  -F "files=@/path/to/receipt1.jpg" \
  -F "files=@/path/to/receipt2.jpg"
```

Returns `job_id` immediately. Poll with `GET /v1/jobs/{job_id}`.

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
