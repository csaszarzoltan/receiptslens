"""Pre-development acceptance tests for duplicate receipt detection (v0.4.0)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api import app
from app.ocr import (
    _canonicalize_total,
    _group_duplicates,
    _vendor_similarity,
)

client = TestClient(app)


# ---------------------------------------------------------------------------
# Interfaces
# ---------------------------------------------------------------------------


class TestDuplicateEndpointInterface:
    """Verify the /v1/check-duplicates endpoint exists and validates input."""

    def test_registered(self):
        resp = client.get("/openapi.json")
        paths = resp.json()["paths"]
        assert "/v1/check-duplicates" in paths
        assert "post" in paths["/v1/check-duplicates"]

    def test_accepts_json(self):
        resp = client.post("/v1/check-duplicates", json={"receipts": []})
        # Empty list -> 422, not 415 or 500, so JSON was accepted
        assert resp.status_code == 422

    def test_rejects_missing_receipts_key(self):
        resp = client.post("/v1/check-duplicates", json={})
        assert resp.status_code == 422

    def test_rejects_non_array_receipts(self):
        resp = client.post("/v1/check-duplicates", json={"receipts": "not-a-list"})
        assert resp.status_code == 422

    def test_rejects_empty_receipts_list(self):
        resp = client.post("/v1/check-duplicates", json={"receipts": []})
        assert resp.status_code == 422

    def test_rejects_over_two_hundred_receipts(self):
        receipts = [{"vendor": "A", "total": 1.0}] * 201
        resp = client.post("/v1/check-duplicates", json={"receipts": receipts})
        assert resp.status_code == 413

    def test_rejects_missing_total(self):
        resp = client.post(
            "/v1/check-duplicates",
            json={"receipts": [{"vendor": "A"}]},
        )
        # total missing means numeric field absent -> 422
        assert resp.status_code == 422

    def test_rejects_non_numeric_total(self):
        resp = client.post(
            "/v1/check-duplicates",
            json={"receipts": [{"vendor": "A", "total": "abc"}]},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Behavioral tests for matching logic
# ---------------------------------------------------------------------------


class TestDuplicateMatching:
    """Verify core duplicate-detection logic using the internal helper."""

    def test_identical_receipts_one_group(self):
        receipts = [
            {"vendor": "STARBUCKS", "total": 5.75, "date": "2025-03-14"},
            {"vendor": "STARBUCKS", "total": 5.75, "date": "2025-03-14"},
        ]
        result = _group_duplicates(receipts)
        assert len(result.duplicate_groups) == 1
        assert result.duplicate_groups[0].indices == [0, 1]
        assert result.duplicate_groups[0].confidence == pytest.approx(1.0)

    def test_unique_receipts_no_groups(self):
        receipts = [
            {"vendor": "STARBUCKS", "total": 5.75, "date": "2025-03-14"},
            {"vendor": "MCDONALDS", "total": 8.50, "date": "2025-03-14"},
        ]
        result = _group_duplicates(receipts)
        assert len(result.duplicate_groups) == 0

    def test_same_vendor_same_total_same_date_flagged(self):
        receipts = [
            {"vendor": "STARBUCKS", "total": 5.75, "date": "2025-03-14"},
            {"vendor": "STARBUCKS", "total": 5.75, "date": "2025-03-14"},
        ]
        result = _group_duplicates(receipts)
        assert len(result.duplicate_groups) == 1

    def test_same_vendor_same_total_different_date_beyond_three_days_not_flagged(self):
        receipts = [
            {"vendor": "STARBUCKS", "total": 5.75, "date": "2025-03-01"},
            {"vendor": "STARBUCKS", "total": 5.75, "date": "2025-03-14"},
        ]
        result = _group_duplicates(receipts)
        assert len(result.duplicate_groups) == 0

    def test_different_vendor_similarity_below_threshold_not_flagged(self):
        receipts = [
            {"vendor": "WALMART", "total": 50.00, "date": "2025-03-14"},
            {"vendor": "TARGET", "total": 50.00, "date": "2025-03-14"},
        ]
        result = _group_duplicates(receipts)
        assert len(result.duplicate_groups) == 0

    def test_exact_total_mismatch_not_flagged(self):
        receipts = [
            {"vendor": "STARBUCKS", "total": 5.75, "date": "2025-03-14"},
            {"vendor": "STARBUCKS", "total": 5.76, "date": "2025-03-14"},
        ]
        result = _group_duplicates(receipts)
        assert len(result.duplicate_groups) == 0

    def test_vendor_none_handled_gracefully(self):
        receipts = [
            {"vendor": None, "total": 5.75, "date": "2025-03-14"},
            {"vendor": None, "total": 5.75, "date": "2025-03-14"},
        ]
        result = _group_duplicates(receipts)
        # None/empty vendor similarity is 0 -> not flagged
        assert len(result.duplicate_groups) == 0

    def test_missing_date_still_flags_if_vendor_and_total_match(self):
        """Missing date should not block detection but lowers confidence."""
        receipts = [
            {"vendor": "STARBUCKS", "total": 5.75},
            {"vendor": "STARBUCKS", "total": 5.75},
        ]
        result = _group_duplicates(receipts)
        # vendor=1.0, date_score=0.0, total=1.0 -> avg=0.667
        assert len(result.duplicate_groups) == 1
        assert result.duplicate_groups[0].confidence == pytest.approx((1.0 + 0.0 + 1.0) / 3, abs=1.5e-2)
        assert result.duplicate_groups[0].match_evidence["date_match"] is None

    def test_transitive_chain_grouped_together(self):
        receipts = [
            {"vendor": "STARBUCKS", "total": 5.75, "date": "2025-03-14"},
            {"vendor": "STARBUCKS", "total": 5.75, "date": "2025-03-14"},
            {"vendor": "STARBUCKS", "total": 5.75, "date": "2025-03-14"},
        ]
        result = _group_duplicates(receipts)
        assert len(result.duplicate_groups) == 1
        assert result.duplicate_groups[0].indices == [0, 1, 2]

    def test_summary_counts_correct(self):
        receipts = [
            {"vendor": "STARBUCKS", "total": 5.75, "date": "2025-03-14"},
            {"vendor": "STARBUCKS", "total": 5.75, "date": "2025-03-14"},
            {"vendor": "MCDONALDS", "total": 8.50, "date": "2025-03-14"},
        ]
        result = _group_duplicates(receipts)
        assert result.summary["total"] == 3
        assert result.summary["duplicate_groups"] == 1
        assert result.summary["unique"] == 2

    def test_single_receipt_no_duplicates(self):
        receipts = [
            {"vendor": "STARBUCKS", "total": 5.75, "date": "2025-03-14"},
        ]
        result = _group_duplicates(receipts)
        assert len(result.duplicate_groups) == 0
        assert result.summary["unique"] == 1


# ---------------------------------------------------------------------------
# API behavioral tests
# ---------------------------------------------------------------------------


class TestDuplicateEndpointBehavior:
    """Verify the /v1/check-duplicates endpoint returns expected JSON."""

    def test_returns_duplicate_groups(self):
        body = {
            "receipts": [
                {"vendor": "STARBUCKS", "total": 5.75, "date": "2025-03-14"},
                {"vendor": "STARBUCKS", "total": 5.75, "date": "2025-03-14"},
            ]
        }
        resp = client.post("/v1/check-duplicates", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert "duplicate_groups" in data
        assert "summary" in data
        assert len(data["duplicate_groups"]) == 1
        assert data["summary"]["duplicate_groups"] == 1

    def test_match_evidence_fields_present(self):
        body = {
            "receipts": [
                {"vendor": "STARBUCKS", "total": 5.75, "date": "2025-03-14"},
                {"vendor": "STARBUCKS", "total": 5.75, "date": "2025-03-14"},
            ]
        }
        resp = client.post("/v1/check-duplicates", json=body)
        data = resp.json()
        evidence = data["duplicate_groups"][0]["match_evidence"]
        assert "total_match" in evidence
        assert "vendor_similarity" in evidence
        assert "date_match" in evidence

    def test_unique_receipts_summary(self):
        body = {
            "receipts": [
                {"vendor": "STARBUCKS", "total": 5.75, "date": "2025-03-14"},
                {"vendor": "MCDONALDS", "total": 8.50, "date": "2025-03-14"},
            ]
        }
        resp = client.post("/v1/check-duplicates", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["duplicate_groups"] == 0
        assert data["summary"]["unique"] == 2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestHelperFunctions:
    """Unit tests for the exposed OCR helpers."""

    def test_canonicalize_total_two_decimals(self):
        assert _canonicalize_total(5.75) == pytest.approx(5.75)
        assert _canonicalize_total(5.756) == pytest.approx(5.76)
        assert _canonicalize_total(5) == pytest.approx(5.0)

    def test_vendor_similarity_identical(self):
        assert _vendor_similarity("STARBUCKS", "STARBUCKS") == pytest.approx(1.0)

    def test_vendor_similarity_case_insensitive(self):
        sim = _vendor_similarity("Starbucks", "STARBUCKS")
        assert sim < 1.0 and sim > 0.0

    def test_vendor_similarity_empty_strings(self):
        assert _vendor_similarity("", "") == pytest.approx(1.0)
        assert _vendor_similarity("", "A") == pytest.approx(0.0)
