# Spec: Batch Receipt Duplicate Detection (v0.4.0)

## Goal
Add a duplicate-detection layer on top of parsed receipts. This is the
single highest-impact missing capability for ReceiptLens in 2026:
market research shows 0.8-2% of receipts/invoices are submitted or paid
twice, and vendors like Klippa, Tabscanner, Mindee, and DocSumo now
advertise duplicate detection as a standard feature. The endpoint
accepts either a prepopulated batch result or a JSON array of parsed
receipts and returns duplicate groups with match evidence.

## Public API Changes

### New endpoint: `POST /v1/check-duplicates`

Accepts a JSON body with `receipts: list[dict]` and returns duplicate
groups with matched fields and confidence.

**Request:**
```json
{
  "receipts": [
    {
      "vendor": "STARBUCKS",
      "total": 5.75,
      "date": "2025-03-14",
      "tax": 0.50,
      "currency": "USD",
      "line_items": [{"name": "COFFEE", "price": 5.75}]
    },
    {
      "vendor": "STARBUCKS COFFEE",
      "total": 5.75,
      "date": "2025-03-14",
      "tax": 0.50,
      "currency": "USD",
      "line_items": [{"name": "LATTE", "price": 5.75}]
    }
  ]
}
```

**Response:**
```json
{
  "duplicate_groups": [
    {
      "group_id": 1,
      "indices": [0, 1],
      "confidence": 0.92,
      "match_evidence": {
        "total_match": true,
        "vendor_similarity": 0.88,
        "date_match": true
      }
    }
  ],
  "summary": {
    "total": 2,
    "unique": 1,
    "duplicate_groups": 1
  }
}
```

**HTTP status codes:**
- `200` — normal response with `duplicate_groups`
- `422` — empty `receipts` list or invalid body shape
- `500` — internal fuzzy-match error (unexpected)

### Validation rules
- `receipts` must be a non-empty JSON array (`length >= 1`).
- Each receipt dict must have numeric `total` and string `vendor`.
- Missing `date` is allowed but reduces match quality.
- Maximum 200 receipts per request to bound computation.

## Data Model Changes

Add to `app/ocr.py`:

```python
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class DuplicateGroup:
    group_id: int
    indices: list[int]
    confidence: float
    match_evidence: dict[str, Any]

@dataclass(frozen=True)
class DuplicateResult:
    duplicate_groups: list[DuplicateGroup]
    summary: dict[str, Any]
```

Add to `app/api.py`:
- `_group_duplicates(receipts: list[dict]) -> DuplicateResult`
- `POST /v1/check-duplicates` route.

## Matching Rules

A pair of receipts `(a, b)` is flagged as a potential duplicate when ALL
of the following hold:

1. **Exact total match**: `a["total"] == b["total"]` (floats compared
   after canonicalizing to 2 decimal places).
2. **Vendor similarity >= 70**: fuzzy ratio on uppercase vendor strings.
3. **Date match**: `a["date"] == b["date"]` OR both dates are within
   `±3` calendar days.

Duplicate groups are transitive: if 1~2 and 2~3 match, all three are
placed in one group.

Confidence is the average of per-field match scores:
- total_match: 1.0 if exact match, 0.0 otherwise
- date_match: 1.0 if exact date match, 0.5 if within ±3 days, 0.0 otherwise
- vendor_similarity: fuzzy ratio (0.0-1.0)

Group confidence = `(total_match + date_score + vendor_similarity) / 3`.

## Implementation Notes

- Use Python stdlib `difflib.SequenceMatcher` for fuzzy vendor
  matching to avoid adding a new binary dependency. It is slower than
  RapidFuzz but sufficient for the expected batch sizes and avoids
  wheel/build friction.
- The endpoint lives in `app/api.py` and reuses the existing FastAPI
  app instance.
- No changes to existing `/v1/parse-receipt*` or `/v1/jobs/*`
  endpoints.
- Bump FastAPI app version to `"0.4.0"`.
- Update `pyproject.toml` `version` to `"0.4.0"`.

## Tests

### Pre-development acceptance tests (`tests/test_duplicate_detection.py`)

Must cover:

**Interface tests:**
- Endpoint registered at `/v1/check-duplicates`
- Endpoint accepts JSON body
- Rejects empty `receipts` list with 422
- Rejects missing `receipts` key with 422
- Rejects receipts without numeric `total` with 422

**Behavioral tests:**
- Identical receipts return one duplicate group with confidence 1.0
- Same vendor, same total, same date → flagged as duplicate
- Same vendor, same total, different date (>3 days) → not flagged
- Different vendor (ratio < 70%), same total, same date → not flagged
- Exact total mismatch → not flagged
- Vendor None/empty is normalized to empty string, not flagged
- Three receipts forming a transitive duplicate chain are grouped together
- Missing date reduces confidence but may still flag if total + vendor match well
- Summary counts unique and duplicate_groups correctly
- Batch of 1 unique receipt returns empty duplicate_groups

**Regression tests:**
- All existing tests in `test_ocr.py`, `test_api.py`,
  `test_async_confidence.py`, `test_batch_processing.py` must still pass.

## Non-functional
- No breaking changes to existing endpoints.
- `ruff check .` must pass clean.
- README/docs/CHANGELOG updated in the same cycle.
