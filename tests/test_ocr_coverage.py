"""Comprehensive tests for ReceiptLens OCR pipeline — coverage boost.

Targets uncovered branches in app/ocr.py: currency extraction, float parsing,
merchant detection, confidence scoring, date normalization, duplicate detection.
"""
from __future__ import annotations

import io
import pytest
from PIL import Image

from app import ocr


# ---------------------------------------------------------------------------
# _extract_currency
# ---------------------------------------------------------------------------
class TestExtractCurrency:
    def test_dollar_symbol(self):
        assert ocr._extract_currency("Total: $12.50") == "USD"

    def test_euro_symbol(self):
        assert ocr._extract_currency("Gesamt: 25,00 €") == "EUR"

    def test_pound_symbol(self):
        assert ocr._extract_currency("Amount: £45.00") == "GBP"

    def test_yen_symbol(self):
        assert ocr._extract_currency("合計: ¥1200") == "JPY"

    def test_inr_symbol(self):
        assert ocr._extract_currency("Total: ₹500") == "INR"

    def test_rub_symbol(self):
        assert ocr._extract_currency("Итого: ₽1500") == "RUB"

    def test_usd_text(self):
        assert ocr._extract_currency("Total USD 99.99") == "USD"

    def test_eur_text(self):
        assert ocr._extract_currency("Summe EUR 50,00") == "EUR"

    def test_gbp_text(self):
        assert ocr._extract_currency("Total GBP 30.00") == "GBP"

    def test_czk_text(self):
        assert ocr._extract_currency("Celkem 250 CZK") == "CZK"

    def test_huf_text(self):
        assert ocr._extract_currency("Összesen 4500 HUF") == "HUF"

    def test_ron_text(self):
        assert ocr._extract_currency("Total RON 120") == "RON"

    def test_bgn_text(self):
        assert ocr._extract_currency("Total BGN 80") == "BGN"

    def test_pln_text(self):
        assert ocr._extract_currency("Razem PLN 200") == "PLN"

    def test_sek_text(self):
        assert ocr._extract_currency("Total SEK 350") == "SEK"

    def test_nok_text(self):
        assert ocr._extract_currency("Total NOK 275") == "NOK"

    def test_dkk_text(self):
        assert ocr._extract_currency("Total DKK 190") == "DKK"

    def test_no_currency(self):
        assert ocr._extract_currency("Just some text") is None

    def test_empty_text(self):
        assert ocr._extract_currency("") is None


# ---------------------------------------------------------------------------
# _parse_float
# ---------------------------------------------------------------------------
class TestParseFloat:
    def test_none_input(self):
        assert ocr._parse_float(None) is None

    def test_empty_string(self):
        assert ocr._parse_float("") is None

    def test_simple_integer(self):
        assert ocr._parse_float("42") == 42.0

    def test_simple_decimal_dot(self):
        assert ocr._parse_float("12.50") == 12.5

    def test_simple_decimal_comma(self):
        assert ocr._parse_float("12,50") == 12.5

    def test_european_thousands(self):
        assert ocr._parse_float("1.234,56") == 1234.56

    def test_american_thousands(self):
        assert ocr._parse_float("1,234.56") == 1234.56

    def test_currency_prefix(self):
        assert ocr._parse_float("$12.50") == 12.5

    def test_currency_suffix(self):
        assert ocr._parse_float("12.50 EUR") == 12.5

    def test_pure_alpha(self):
        assert ocr._parse_float("abc") is None

    def test_only_dots(self):
        assert ocr._parse_float("...") is None

    def test_only_commas(self):
        assert ocr._parse_float(",,") is None

    def test_mixed_separators(self):
        # Last separator wins: comma after dot → European style
        assert ocr._parse_float("1.234,56") == 1234.56

    def test_single_number(self):
        assert ocr._parse_float("0") == 0.0


# ---------------------------------------------------------------------------
# _extract_merchant
# ---------------------------------------------------------------------------
class TestExtractMerchant:
    def test_all_caps_line(self):
        text = "WALMART STORE\nDate: 2025-01-15\nTotal: $50.00"
        assert ocr._extract_merchant(text) == "WALMART STORE"

    def test_no_merchant(self):
        text = "date: 2025-01-15\ntotal: $50.00"
        assert ocr._extract_merchant(text) is None

    def test_short_caps_ignored(self):
        text = "AB\nDate: 2025-01-15"
        assert ocr._extract_merchant(text) is None

    def test_caps_with_numbers_ignored(self):
        text = "STORE123\nDate: 2025-01-15"
        assert ocr._extract_merchant(text) is None

    def test_mixed_case_not_merchant(self):
        text = "Walmart Store\nDate: 2025-01-15"
        assert ocr._extract_merchant(text) is None

    def test_first_caps_line_wins(self):
        text = "TARGET\nLINE2\nWALMART"
        assert ocr._extract_merchant(text) == "TARGET"

    def test_empty_text(self):
        assert ocr._extract_merchant("") is None


# ---------------------------------------------------------------------------
# _normalize_date
# ---------------------------------------------------------------------------
class TestNormalizeDate:
    def test_dd_mm_yyyy_dot(self):
        assert ocr._normalize_date("15.01.2025") == "2025-01-15"

    def test_dd_mm_yyyy_slash(self):
        assert ocr._normalize_date("15/01/2025") == "2025-01-15"

    def test_dd_mm_yyyy_dash(self):
        assert ocr._normalize_date("15-01-2025") == "2025-01-15"

    def test_dd_mm_yy_dot(self):
        assert ocr._normalize_date("15.01.25") == "2025-01-15"

    def test_dd_mm_yy_slash(self):
        assert ocr._normalize_date("15/01/25") == "2025-01-15"

    def test_unknown_format_returns_raw(self):
        assert ocr._normalize_date("2025/01/15") == "2025/01/15"

    def test_none_input(self):
        # _normalize_date is called only when date_raw is not None
        # but let's test the raw passthrough
        assert ocr._normalize_date("not-a-date") == "not-a-date"


# ---------------------------------------------------------------------------
# _find_first / _clean_text
# ---------------------------------------------------------------------------
class TestHelpers:
    def test_clean_text_strips_blank_lines(self):
        text = "  Line1  \n\n  \n  Line2  \n"
        assert ocr._clean_text(text) == "Line1\nLine2"

    def test_clean_text_empty(self):
        assert ocr._clean_text("") == ""

    def test_find_first_total(self):
        text = "TOTAL: $42.50"
        assert ocr._find_first(ocr._TOTAL_RE, text) == "42.50"

    def test_find_first_subtotal(self):
        text = "Subtotal: 35.00"
        assert ocr._find_first(ocr._SUBTOTAL_RE, text) == "35.00"

    def test_find_first_tax(self):
        text = "TAX: 3.40"
        assert ocr._find_first(ocr._TAX_RE, text) == "3.40"

    def test_find_first_date(self):
        text = "Date: 15.01.2025"
        assert ocr._find_first(ocr._DATE_RE, text) == "15.01.2025"

    def test_find_first_no_match(self):
        assert ocr._find_first(ocr._TOTAL_RE, "no totals here") is None


# ---------------------------------------------------------------------------
# _confidence_from_data
# ---------------------------------------------------------------------------
class TestConfidenceFromData:
    def _make_image_bytes(self, text: str = "TOTAL $10.00") -> bytes:
        """Create a simple image with text for OCR confidence testing."""
        img = Image.new("RGB", (400, 200), color="white")
        # We can't easily draw text without a font, so just return a white image
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_confidence_keys(self):
        data = ocr._confidence_from_data(self._make_image_bytes())
        expected_keys = {"vendor", "total", "date", "tax", "currency", "line_items"}
        assert set(data.keys()) == expected_keys

    def test_confidence_values_are_floats_or_none(self):
        data = ocr._confidence_from_data(self._make_image_bytes())
        for key, val in data.items():
            assert val is None or isinstance(val, float), f"{key}: {val}"

    def test_empty_bytes_returns_none_confidence(self):
        # Should not raise — _confidence_from_data catches exceptions
        # But we pass invalid bytes that will fail to decode
        data = ocr._confidence_from_data(b"not-an-image")
        assert all(v is None for v in data.values())


# ---------------------------------------------------------------------------
# _parse_line_items (additional coverage)
# ---------------------------------------------------------------------------
class TestParseLineItems:
    def test_filters_total_line(self):
        text = "Milk 1.20\nBread 2.50\nTOTAL 7.48"
        items = ocr._parse_line_items(text)
        names = [i.name for i in items]
        assert "TOTAL" not in names

    def test_filters_tax_line(self):
        text = "Item 5.00\nTax 0.40\nItem2 3.00"
        items = ocr._parse_line_items(text)
        names = [i.name for i in items]
        assert "Tax" not in names

    def test_filters_cash_line(self):
        text = "Item 5.00\nCash 10.00\nChange 5.00"
        items = ocr._parse_line_items(text)
        names = [i.name for i in items]
        assert "Cash" not in names
        assert "Change" not in names

    def test_filters_balance_line(self):
        text = "Item 5.00\nBalance Due 5.00"
        items = ocr._parse_line_items(text)
        names = [i.name for i in items]
        assert "Balance Due" not in names

    def test_skips_short_names(self):
        text = "A 1.00\nMilk 2.00"
        items = ocr._parse_line_items(text)
        names = [i.name for i in items]
        assert "A" not in names

    def test_skips_zero_price(self):
        text = "Free Item 0.00\nMilk 2.00"
        items = ocr._parse_line_items(text)
        names = [i.name for i in items]
        assert "Free Item" not in names

    def test_skips_negative_price(self):
        text = "Refund -5.00\nMilk 2.00"
        items = ocr._parse_line_items(text)
        names = [i.name for i in items]
        assert "Refund" not in names

    def test_empty_text(self):
        assert ocr._parse_line_items("") == []

    def test_no_matches(self):
        text = "Just some random text\nwithout prices"
        assert ocr._parse_line_items(text) == []


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------
class TestDuplicateDetection:
    def _receipt(self, vendor="STORE", total=10.0, date="2025-01-15"):
        return {"vendor": vendor, "total": total, "date": date}

    def test_no_duplicates(self):
        r1 = self._receipt(vendor="A", total=10.0)
        r2 = self._receipt(vendor="B", total=20.0)
        result = ocr.check_duplicates([r1, r2])
        assert len(result.duplicate_groups) == 0
        assert result.summary["unique"] == 2

    def test_exact_duplicate(self):
        r1 = self._receipt(vendor="WALMART", total=50.0, date="2025-01-15")
        r2 = self._receipt(vendor="WALMART", total=50.0, date="2025-01-15")
        result = ocr.check_duplicates([r1, r2])
        assert len(result.duplicate_groups) == 1
        assert result.summary["duplicate_groups"] == 1

    def test_same_total_different_vendor(self):
        r1 = self._receipt(vendor="WALMART", total=50.0)
        r2 = self._receipt(vendor="TARGET", total=50.0)
        result = ocr.check_duplicates([r1, r2])
        # Vendors too different → no duplicate
        assert len(result.duplicate_groups) == 0

    def test_same_total_similar_vendor(self):
        r1 = self._receipt(vendor="WALMART STORE", total=50.0)
        r2 = self._receipt(vendor="WALMART STORE", total=50.0)
        result = ocr.check_duplicates([r1, r2])
        assert len(result.duplicate_groups) == 1

    def test_same_total_different_dates(self):
        r1 = self._receipt(vendor="WALMART", total=50.0, date="2025-01-15")
        r2 = self._receipt(vendor="WALMART", total=50.0, date="2025-06-15")
        result = ocr.check_duplicates([r1, r2])
        # Dates too far apart → no duplicate
        assert len(result.duplicate_groups) == 0

    def test_same_total_close_dates(self):
        r1 = self._receipt(vendor="WALMART", total=50.0, date="2025-01-15")
        r2 = self._receipt(vendor="WALMART", total=50.0, date="2025-01-17")
        result = ocr.check_duplicates([r1, r2])
        assert len(result.duplicate_groups) == 1

    def test_empty_list(self):
        result = ocr.check_duplicates([])
        assert result.summary["total"] == 0
        assert result.summary["unique"] == 0

    def test_single_receipt(self):
        result = ocr.check_duplicates([self._receipt()])
        assert len(result.duplicate_groups) == 0
        assert result.summary["unique"] == 1

    def test_batch_size_validation(self):
        with pytest.raises(ValueError, match="must be >= 1"):
            ocr.check_duplicates([], receipt_batch_size=0)

    def test_batch_size_exceeded(self):
        with pytest.raises(ValueError, match="too many receipts"):
            ocr.check_duplicates([self._receipt()] * 5, receipt_batch_size=3)

    def test_none_total_skipped(self):
        r1 = {"vendor": "A", "total": None, "date": "2025-01-15"}
        r2 = {"vendor": "A", "total": None, "date": "2025-01-15"}
        result = ocr.check_duplicates([r1, r2])
        # Both have None total → can't match on total
        assert len(result.duplicate_groups) == 0

    def test_none_vendor(self):
        r1 = {"vendor": None, "total": 50.0, "date": "2025-01-15"}
        r2 = {"vendor": None, "total": 50.0, "date": "2025-01-15"}
        result = ocr.check_duplicates([r1, r2])
        # None vendors have 0.0 similarity → no duplicate
        assert len(result.duplicate_groups) == 0

    def test_group_evidence(self):
        r1 = self._receipt(vendor="WALMART", total=50.0, date="2025-01-15")
        r2 = self._receipt(vendor="WALMART", total=50.0, date="2025-01-15")
        result = ocr.check_duplicates([r1, r2])
        group = result.duplicate_groups[0]
        assert group.confidence > 0.8
        assert group.match_evidence["total_match"] is True
        assert group.match_evidence["vendor_similarity"] > 0.9

    def test_three_receipts_two_groups(self):
        r1 = self._receipt(vendor="A", total=10.0, date="2025-01-15")
        r2 = self._receipt(vendor="A", total=10.0, date="2025-01-15")
        r3 = self._receipt(vendor="B", total=20.0, date="2025-01-15")
        result = ocr.check_duplicates([r1, r2, r3])
        # r1,r2 are duplicates; r3 is unique
        assert len(result.duplicate_groups) == 1
        assert result.summary["unique"] == 2  # total - duplicate_groups


# ---------------------------------------------------------------------------
# parse_receipt integration
# ---------------------------------------------------------------------------
class TestParseReceiptIntegration:
    def _make_receipt_image(self, text: str = "") -> bytes:
        """Create a minimal image with text (no font needed — blank image)."""
        img = Image.new("RGB", (400, 200), color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_parse_receipt_returns_parsed(self):
        data = self._make_receipt_image()
        result = ocr.parse_receipt(data)
        assert isinstance(result, ocr.ParsedReceipt)
        assert isinstance(result.items, list)
        assert isinstance(result.raw_text, str)

    def test_parse_receipt_with_confidence(self):
        data = self._make_receipt_image()
        result = ocr.parse_receipt_with_confidence(data)
        assert isinstance(result, ocr.ConfidenceReceipt)
        assert isinstance(result.confidence, dict)
        assert set(result.confidence.keys()) == {
            "vendor", "total", "date", "tax", "currency", "line_items"
        }

    def test_extract_text_returns_string(self):
        data = self._make_receipt_image()
        result = ocr.extract_text(data)
        assert isinstance(result, str)

    def test_parse_receipt_empty_raises(self):
        with pytest.raises(ValueError):
            ocr.parse_receipt(b"")

    def test_extract_text_empty_raises(self):
        with pytest.raises(ValueError):
            ocr.extract_text(b"")
