"""ReceiptLens OCR pipeline.

Produces structured receipt data from raw image bytes via Tesseract.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

import pytesseract

from app.preprocessing import preprocess_image
from app.exceptions import InvalidImageError

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ReceiptItem:
    name: str
    price: float


@dataclass
class ParsedReceipt:
    merchant: str | None
    date: str | None
    items: list[ReceiptItem]
    total: float | None
    tax: float | None
    currency: str | None
    raw_text: str


@dataclass
class ConfidenceReceipt(ParsedReceipt):
    confidence: dict[str, float | None] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CURRENCY_SYMBOLS = (
    r"(?:[$€£¥₹₽]|USD|EUR|GBP|JPY|INR|RUB|CZK|HUF|RON|BGN|PLN|SEK|NOK|DKK)"
)
_AMOUNT = r"(?:\d{1,3}(?:[.,]\d{3})*[.,]\d{2}|\d+[.,]\d{2}|\d+)"

_TOTAL_RE = re.compile(
    rf"(?:total|amount\s*due|balance|sum|grand\s*total)\s*(?:due)?\s*[:=]?\s*{_CURRENCY_SYMBOLS}?\s*({_AMOUNT})",
    re.IGNORECASE,
)
_SUBTOTAL_RE = re.compile(
    rf"(?:sub[- ]?total|sub\s*amount|sub\s*sum)\s*[:=]?\s*{_CURRENCY_SYMBOLS}?\s*({_AMOUNT})",
    re.IGNORECASE,
)
_TAX_RE = re.compile(
    rf"(?:tax|vat|gst|iva)\s*(?:\d+%)?\s*[:=]?\s*{_CURRENCY_SYMBOLS}?\s*({_AMOUNT})",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r"(?:date|datum|fecha)\s*[:=]?\s*"
    r"(\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4})",
    re.IGNORECASE,
)
_CURRENCY_RE = re.compile(_CURRENCY_SYMBOLS)
_LINE_ITEM_RE = re.compile(
    rf"^(.+?)\s+({_AMOUNT})\s*$"
)

# Headers that indicate a store name are typically at the top of the text.
_MERCHANT_RE = re.compile(
    r"^(?P<name>(?:[A-Z][A-Z\s&.,'-]{2,}))$",
    re.MULTILINE,
)


def _clean_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def _find_first(pattern: re.Pattern, text: str) -> Optional[str]:
    m = pattern.search(text)
    return m.group(1) if m else None


def _parse_float(raw: str | None) -> Optional[float]:
    if not raw:
        return None
    cleaned = re.sub(r"[^\d.,]", "", raw)
    if not cleaned:
        return None
    # Detect decimal separator: last separator wins.
    last_comma = cleaned.rfind(",")
    last_dot = cleaned.rfind(".")
    if last_comma == -1 and last_dot == -1:
        try:
            return float(cleaned)
        except ValueError:
            return None
    if last_comma > last_dot:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _extract_merchant(text: str) -> str | None:
    # Heuristic: first all-caps line of sufficient length is usually the store.
    for line in text.splitlines():
        stripped = line.strip()
        if (
            stripped
            and stripped.isupper()
            and len(stripped) >= 3
            and not re.search(r"\d", stripped)
        ):
            return stripped
    return None


def _extract_currency(text: str) -> str | None:
    m = _CURRENCY_RE.search(text)
    if not m:
        return None
    sym = m.group(0)
    mapping = {
        "$": "USD",
        "€": "EUR",
        "£": "GBP",
        "¥": "JPY",
        "₹": "INR",
        "₽": "RUB",
        "USD": "USD",
        "EUR": "EUR",
        "GBP": "GBP",
        "JPY": "JPY",
        "INR": "INR",
        "RUB": "RUB",
        "CZK": "CZK",
        "HUF": "HUF",
        "RON": "RON",
        "BGN": "BGN",
        "PLN": "PLN",
        "SEK": "SEK",
        "NOK": "NOK",
        "DKK": "DKK",
    }
    return mapping.get(sym, sym)


def _parse_line_items(text: str) -> list[ReceiptItem]:
    items: list[ReceiptItem] = []
    for line in text.splitlines():
        m = _LINE_ITEM_RE.match(line.strip())
        if not m:
            continue
        name = m.group(1).strip()
        price_raw = m.group(2).strip()
        # Eliminate lines that look like totals/tax headers.
        lowered = name.lower()
        if any(k in lowered for k in ["total", "tax", "vat", "subtotal", "cash", "change", "balance"]):
            continue
        price = _parse_float(price_raw)
        if price is None or price <= 0:
            continue
        if len(name) < 2:
            continue
        items.append(ReceiptItem(name=name, price=price))
    return items


# ---------------------------------------------------------------------------
# Confidence heuristics
# ---------------------------------------------------------------------------


def _confidence_from_data(image_bytes: bytes) -> dict[str, float | None]:
    """Derive per-field confidence from Tesseract ``image_to_data`` output.

    Returns dict with keys: vendor, total, date, tax, currency, line_items.
    Missing keys default to ``None``.
    """
    try:
        image = preprocess_image(image_bytes)
        data = pytesseract.image_to_data(
            image, config="--oem 3 --psm 6", output_type=pytesseract.Output.DICT
        )
    except Exception:
        return {
            "vendor": None,
            "total": None,
            "date": None,
            "tax": None,
            "currency": None,
            "line_items": None,
        }

    if not data.get("text"):
        return {
            "vendor": None,
            "total": None,
            "date": None,
            "tax": None,
            "currency": None,
            "line_items": None,
        }

    confs = [c for c in data.get("conf", []) if c != "-1"]
    avg_conf = sum(confs) / len(confs) / 100.0 if confs else 0.0
    min_conf = min(confs) / 100.0 if confs else 0.0
    weighted = (avg_conf + min_conf) / 2.0

    clean_text = _clean_text(pytesseract.image_to_string(
        image, config="--oem 3 --psm 6"
    ))

    # Per-field confidence: check if the field value was actually found.
    total_val = _find_first(_TOTAL_RE, clean_text)
    tax_val = _find_first(_TAX_RE, clean_text)
    date_val = _find_first(_DATE_RE, clean_text)
    currency_val = _extract_currency(clean_text)
    merchant_val = _extract_merchant(clean_text)
    items = _parse_line_items(clean_text)

    def _field_conf(found: bool) -> float:
        if not found:
            return 0.0
        return weighted

    return {
        "vendor": _field_conf(merchant_val is not None),
        "total": _field_conf(total_val is not None),
        "date": _field_conf(date_val is not None),
        "tax": _field_conf(tax_val is not None),
        "currency": _field_conf(currency_val is not None),
        "line_items": _field_conf(len(items) > 0),
    }


# ---------------------------------------------------------------------------
# Duplicate detection (v0.4.0)
# ---------------------------------------------------------------------------


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


def _vendor_similarity(a: str | None, b: str | None) -> float:
    if a is None or b is None:
        return 0.0
    return SequenceMatcher(None, a or "", b or "").ratio()


def _canonicalize_total(value: float | int | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def _parse_date(date_str: str | None) -> date | None:
    if not date_str:
        return None
    for fmt in (
        "%Y-%m-%d",
        "%d.%m.%Y",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d.%m.%y",
        "%d/%m/%y",
    ):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


def _group_duplicates(receipts: list[dict]) -> DuplicateResult:
    n = len(receipts)
    if n == 0:
        return DuplicateResult(
            duplicate_groups=[],
            summary={"total": 0, "duplicate_groups": 0, "unique": 0},
        )

    totals = [_canonicalize_total(r.get("total")) for r in receipts]
    dates = [_parse_date(r.get("date")) for r in receipts]
    vendors = [r.get("vendor") for r in receipts]
    vendors_upper = [v.upper() if v else v for v in vendors]

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    for i in range(n):
        for j in range(i + 1, n):
            t_a, t_b = totals[i], totals[j]
            if t_a is None or t_b is None or t_a != t_b:
                continue

            vendor_sim = _vendor_similarity(vendors_upper[i], vendors_upper[j])
            if vendor_sim < 0.70:
                continue

            d_a, d_b = dates[i], dates[j]
            if d_a is not None and d_b is not None:
                day_diff = abs((d_a - d_b).days)
                if day_diff > 3:
                    continue

            union(i, j)

    groups_map: dict[int, list[int]] = {}
    for i in range(n):
        root = find(i)
        groups_map.setdefault(root, []).append(i)

    group_id = 0
    groups: list[DuplicateGroup] = []
    for root, indices in groups_map.items():
        if len(indices) < 2:
            continue
        group_id += 1

        total_score_sum = 0.0
        date_score_sum = 0.0
        vendor_score_sum = 0.0
        pair_count = 0

        for ai, ia in enumerate(indices):
            for ib in indices[ai + 1 :]:
                t_a, t_b = totals[ia], totals[ib]
                total_score = 1.0 if (t_a is not None and t_b is not None and t_a == t_b) else 0.0

                d_a, d_b = dates[ia], dates[ib]
                if d_a is None or d_b is None:
                    date_score = 0.0
                elif d_a == d_b:
                    date_score = 1.0
                elif abs((d_a - d_b).days) <= 3:
                    date_score = 0.5
                else:
                    date_score = 0.0

                vendor_sim = _vendor_similarity(vendors_upper[ia], vendors_upper[ib])

                total_score_sum += total_score
                date_score_sum += date_score
                vendor_score_sum += vendor_sim
                pair_count += 1

        confidence = (
            (total_score_sum + date_score_sum + vendor_score_sum) / (3 * pair_count)
            if pair_count > 0
            else 0.0
        )

        i0, j0 = indices[0], indices[1]
        d_a, d_b = dates[i0], dates[j0]
        if d_a is None or d_b is None:
            date_match: bool | None = None
        else:
            date_match = d_a == d_b or abs((d_a - d_b).days) <= 3

        match_evidence = {
            "total_match": totals[i0] == totals[j0],
            "vendor_similarity": _vendor_similarity(vendors_upper[i0], vendors_upper[j0]),
            "date_match": date_match,
        }

        groups.append(
            DuplicateGroup(
                group_id=group_id,
                indices=indices,
                confidence=confidence,
                match_evidence=match_evidence,
            )
        )

    group_count = sum(1 for indices in groups_map.values() if len(indices) > 1)

    return DuplicateResult(
        duplicate_groups=groups,
        summary={
            "total": n,
            "duplicate_groups": group_count,
            "unique": n - group_count,
        },
    )


def check_duplicates(receipts: list[dict], *, receipt_batch_size: int = 200) -> DuplicateResult:
    if receipt_batch_size < 1:
        raise ValueError("receipt_batch_size must be >= 1")
    if len(receipts) > receipt_batch_size:
        raise ValueError("too many receipts for the configured batch size")
    return _group_duplicates(receipts)


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def extract_text(image_bytes: bytes) -> str:
    """Run OCR on image bytes and return the raw recognized text."""
    if not image_bytes:
        raise InvalidImageError("image_bytes must not be empty")
    try:
        image = preprocess_image(image_bytes)
    except ValueError as exc:
        raise InvalidImageError(str(exc)) from exc
    return pytesseract.image_to_string(image, config="--oem 3 --psm 6")


def parse_receipt(image_bytes: bytes) -> ParsedReceipt:
    """Extract structured receipt data from image bytes."""
    raw = extract_text(image_bytes)
    text = _clean_text(raw)

    total = _parse_float(_find_first(_TOTAL_RE, text))
    # If total missing, fall back to last numeric line.
    if total is None:
        lines = text.splitlines()
        for line in reversed(lines):
            m = re.search(r"(" + _AMOUNT + r")", line)
            if m:
                total = _parse_float(m.group(1))
                if total is not None:
                    break

    tax = _parse_float(_find_first(_TAX_RE, text))

    merchant = _extract_merchant(text)

    date_raw = _find_first(_DATE_RE, text)
    date_str = _normalize_date(date_raw) if date_raw else None

    items = _parse_line_items(text)

    currency = _extract_currency(text)

    return ParsedReceipt(
        merchant=merchant,
        date=date_str,
        items=items,
        total=total,
        tax=tax,
        currency=currency,
        raw_text=text,
    )


def parse_receipt_with_confidence(image_bytes: bytes) -> ConfidenceReceipt:
    """Extract structured receipt data and per-field confidence scores."""
    parsed = parse_receipt(image_bytes)
    confidence = _confidence_from_data(image_bytes)
    return ConfidenceReceipt(
        merchant=parsed.merchant,
        date=parsed.date,
        items=parsed.items,
        total=parsed.total,
        tax=parsed.tax,
        currency=parsed.currency,
        raw_text=parsed.raw_text,
        confidence=confidence,
    )


def _normalize_date(raw: str) -> str | None:
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%y", "%d/%m/%y"):
        try:
            d = datetime.strptime(raw, fmt)
            return d.date().isoformat()
        except ValueError:
            continue
    return raw
