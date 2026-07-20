# OCR Pipeline

The OCR pipeline lives in `app.ocr` and is responsible for turning raw image bytes
into structured receipt data.

## Entry points

### `extract_text(image_bytes: bytes) -> str`

Runs Tesseract 5 on the image after pre-processing and returns the raw recognized text.

```bash
python -c "
from app.ocr import extract_text
with open('/path/to/receipt.jpg', 'rb') as f:
    print(extract_text(f.read()))
"
```

### `parse_receipt(image_bytes: bytes) -> ParsedReceipt`

Extracts structured fields from the raw OCR text and returns a `ParsedReceipt`.

```bash
python -c "
from app.ocr import parse_receipt
with open('/path/to/receipt.jpg', 'rb') as f:
    r = parse_receipt(f.read())
    print('vendor      :', r.merchant)
    print('date        :', r.date)
    print('currency    :', r.currency)
    print('tax         :', r.tax)
    print('total       :', r.total)
    print('line_items  :')
    for item in r.items:
        print('  -', item.name, ':', item.price)
"
```

## Data model

```python
from app.ocr import ReceiptItem, ParsedReceipt

class ReceiptItem:
    name: str
    price: float

class ParsedReceipt:
    merchant: str | None
    date: str | None
    items: list[ReceiptItem]
    total: float | None
    tax: float | None
    currency: str | None
    raw_text: str
```

## Tuning tips

- Supported image formats: anything Pillow can open (PNG, JPEG, BMP, TIFF, ...).
- The pipeline upscales by 1.5x, converts to grayscale, boosts contrast, and sharpens before OCR.
- `lang` and `config` parameters to `pytesseract.image_to_string` can be tweaked in `extract_text`.
