"""ReceiptLens API layer."""
from __future__ import annotations

import logging

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from app.ocr import ParsedReceipt, parse_receipt

logger = logging.getLogger("uvicorn.error")

app = FastAPI(
    title="ReceiptLens",
    description="Extract structured data from receipt images.",
    version="0.1.0",
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


def _bytes_from_upload(upload: UploadFile) -> bytes:
    if upload.content_type and not upload.content_type.startswith("image/"):
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported media type: {upload.content_type}. Expected an image.",
        )
    return upload.file.read()


def _bytes_from_url(url: str) -> bytes:
    try:
        with httpx.Client(timeout=httpx.Timeout(connect=10.0, read=30.0)) as client:
            resp = client.get(url, follow_redirects=True)
            resp.raise_for_status()
            return resp.content
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to fetch image from URL: {exc}",
        ) from exc


def _render_receipt(parsed: ParsedReceipt) -> dict:
    return {
        "vendor": parsed.merchant,
        "total": parsed.total,
        "date": parsed.date,
        "tax": parsed.tax,
        "currency": parsed.currency,
        "line_items": [
            {"name": item.name, "price": item.price} for item in parsed.items
        ],
    }


async def parse_receipt_endpoint(file: bytes) -> dict:
    """Accept raw image bytes, run OCR, return structured dict."""
    if not file:
        raise HTTPException(status_code=422, detail="Empty image payload")
    parsed = parse_receipt(file)
    return _render_receipt(parsed)


@app.post("/v1/parse-receipt", response_model=dict)
async def parse_receipt_route(
    file: UploadFile | None = File(default=None, description="Receipt image file"),
    image_url: str | None = Form(default=None, description="Public URL of a receipt image"),
) -> dict:
    """Parse a receipt image returned as structured JSON.

    Send either **file** (multipart upload) or **image_url** (form field).
    """
    if file is not None and image_url is not None:
        raise HTTPException(
            status_code=400,
            detail="Provide either 'file' or 'image_url', not both.",
        )
    if file is None and not image_url:
        raise HTTPException(
            status_code=422,
            detail="Missing required input: send 'file' or 'image_url'.",
        )

    if file is not None:
        image_bytes = _bytes_from_upload(file)
    else:
        image_bytes = _bytes_from_url(image_url)  # type: ignore[arg-type]

    try:
        return await parse_receipt_endpoint(image_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - OCR is unpredictable
        logger.exception("OCR processing failed")
        raise HTTPException(
            status_code=500,
            detail=f"OCR processing failed: {exc}",
        ) from exc
