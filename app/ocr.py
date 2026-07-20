"""ReceiptLens OCR pipeline.

Produces structured receipt data from raw image bytes via Tesseract.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pytesseract
from PIL import Image, ImageEnhance, ImageFilter

# Pillow >=9.1 exposes resampling filters under ``Image.Resampling``; provide a
# compatibility alias so the rest of the module can use ``RESAMPLING`` without
# version checks.
try:
    _RESAMPLING = Image.Resampling.LANCZOS  # type: ignore[attr-defined]
except AttributeError:
    _RESAMPLING = Image.LANCZOS  # type: ignore[attr-defined]

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CURRENCY_SYMBOLS = r"(?:[$€£¥₹₽]|USD|EUR|GBP|JPY|INR|RUB|CZK|HUF|RON|BGN|PLN|SEK|NOK|DKK)"
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
# Public surface
# ---------------------------------------------------------------------------

def extract_text(image_bytes: bytes) -> str:
    """Run OCR on image bytes and return the raw recognized text."""
    if not image_bytes:
        raise ValueError("image_bytes must not be empty")
    image = Image.open(io.BytesIO(image_bytes))
    image = image.convert("L")
    image = image.resize(
        (int(image.width * 1.5), int(image.height * 1.5)),
        _RESAMPLING,
    )
    enhancer = ImageEnhance.Contrast(image)
    image = enhancer.enhance(2.0)
    image = image.filter(ImageFilter.SHARPEN)
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


def _normalize_date(raw: str) -> str | None:
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%y", "%d/%m/%y"):
        try:
            d = datetime.strptime(raw, fmt)
            return d.date().isoformat()
        except ValueError:
            continue
    return raw
