"""Typed exception hierarchy for the ReceiptLens OCR pipeline.

Maps to HTTP status codes:
  - InvalidImageError         → 400 (bad input)
  - UnsupportedImageFormatError → 415 (unsupported media type)
  - CorruptImageError         → 422 (unprocessable entity)
"""
from __future__ import annotations


class OCRError(Exception):
    """Base exception for all OCR pipeline errors."""


class InvalidImageError(OCRError, ValueError):
    """Raised when input bytes are not a valid image."""


class UnsupportedImageFormatError(OCRError):
    """Raised when the image format is recognized but not supported."""


class CorruptImageError(OCRError):
    """Raised when the image is structurally corrupt."""
