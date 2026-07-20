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

Parse a receipt image and return structured JSON.

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
  ]
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
