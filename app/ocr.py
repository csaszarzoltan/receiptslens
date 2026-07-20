"""ReceiptLens OCR pipeline (pre-development stub).

Public contract -- frozen by tests/test_ocr.py:
  - extract_text(image_bytes: bytes) -> str
  - parse_receipt(image_bytes: bytes) -> ParsedReceipt

Implementations raise NotImplementedError until the OCR backend is wired up.
Type hints and signatures are part of the contract and MUST NOT change without
updating the pre-dev tests.
"""
from dataclasses import dataclass


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
    raw_text: str


def extract_text(image_bytes: bytes) -> str:
    raise NotImplementedError("ocr.extract_text is not implemented yet")


def parse_receipt(image_bytes: bytes) -> ParsedReceipt:
    raise NotImplementedError("ocr.parse_receipt is not implemented yet")
