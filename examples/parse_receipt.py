#!/usr/bin/env python3
"""Minimal example: parse a receipt image using the ReceiptLens OCR pipeline.

Usage:
    python examples/parse_receipt.py /path/to/receipt.jpg

Requires:
    - Tesseract OCR installed (apt install tesseract-ocr)
    - pip install -e .  (from the receiptslens root)
"""
from __future__ import annotations

import sys
from pathlib import Path

from app.ocr import extract_text, parse_receipt, parse_receipt_with_confidence
from app.preprocessing import preprocess_image
from app.exceptions import InvalidImageError


def main(image_path: str) -> None:
    path = Path(image_path)
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    image_bytes = path.read_bytes()

    # --- 1. Raw OCR text ---
    print("=" * 60)
    print("RAW OCR TEXT")
    print("=" * 60)
    try:
        text = extract_text(image_bytes)
        print(text)
    except InvalidImageError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # --- 2. Structured parsing ---
    print("=" * 60)
    print("STRUCTURED PARSE")
    print("=" * 60)
    receipt = parse_receipt(image_bytes)
    print(f"  vendor : {receipt.merchant}")
    print(f"  date   : {receipt.date}")
    print(f"  total  : {receipt.total}")
    print(f"  tax    : {receipt.tax}")
    print(f"  currency: {receipt.currency}")
    print(f"  items  :")
    for item in receipt.items:
        print(f"    - {item.name}: {item.price:.2f}")

    # --- 3. Confidence scores ---
    print("=" * 60)
    print("CONFIDENCE SCORES")
    print("=" * 60)
    result = parse_receipt_with_confidence(image_bytes)
    for field, score in result.confidence.items():
        label = f"{score:.2f}" if score is not None else "N/A"
        print(f"  {field:12s} {label}")

    # --- 4. Preprocessing preview ---
    print("=" * 60)
    print("PREPROCESSING")
    print("=" * 60)
    image = preprocess_image(image_bytes, deskew=True)
    print(f"  Output size: {image.size[0]}x{image.size[1]} px")
    print(f"  Mode: {image.mode}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <receipt-image>", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1])
