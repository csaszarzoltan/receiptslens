"""ReceiptLens API layer."""
from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from app.ocr import ConfidenceReceipt, parse_receipt_with_confidence

logger = logging.getLogger("uvicorn.error")

app = FastAPI(
    title="ReceiptLens",
    description="Extract structured data from receipt images.",
    version="0.2.0",
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# In-memory job store (replace with Redis/DB in production)
# ---------------------------------------------------------------------------

class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._executor = ThreadPoolExecutor(max_workers=2)

    def create(self, webhook_url: str | None = None) -> dict[str, Any]:
        job_id = str(uuid.uuid4())
        self._jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "created_at": datetime.utcnow().isoformat() + "Z",
            "webhook_url": webhook_url,
            "result": None,
            "error": None,
        }
        return self._jobs[job_id]

    def get(self, job_id: str) -> dict[str, Any] | None:
        return self._jobs.get(job_id)

    def set_status(self, job_id: str, status: str, result: Any = None, error: str | None = None) -> None:
        job = self._jobs.get(job_id)
        if job:
            job["status"] = status
            job["result"] = result
            job["error"] = error


_job_store = JobStore()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _render_receipt(parsed: ConfidenceReceipt) -> dict:
    return {
        "vendor": parsed.merchant,
        "total": parsed.total,
        "date": parsed.date,
        "tax": parsed.tax,
        "currency": parsed.currency,
        "line_items": [
            {"name": item.name, "price": item.price} for item in parsed.items
        ],
        "confidence": parsed.confidence,
    }


async def _process_job(image_bytes: bytes, job_id: str, webhook_url: str | None = None) -> None:
    """Run OCR in a thread and update job store."""
    _job_store.set_status(job_id, "processing")

    def _run() -> dict:
        parsed = parse_receipt_with_confidence(image_bytes)
        return _render_receipt(parsed)

    loop = __import__("asyncio").get_running_loop()
    try:
        result = await loop.run_in_executor(_job_store._executor, _run)
        _job_store.set_status(job_id, "completed", result=result)
        if webhook_url:
            await _deliver_webhook(webhook_url, {
                "job_id": job_id,
                "status": "completed",
                "result": result,
            })
    except Exception as exc:
        logger.exception("Async OCR job %s failed", job_id)
        _job_store.set_status(job_id, "failed", error=str(exc))
        if webhook_url:
            await _deliver_webhook(webhook_url, {
                "job_id": job_id,
                "status": "failed",
                "error": str(exc),
            })


async def _deliver_webhook(url: str, payload: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5.0, read=10.0)) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
    except Exception:
        logger.warning("Webhook delivery failed to %s", url)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


async def parse_receipt_endpoint(file: bytes) -> dict:
    """Accept raw image bytes, run OCR, return structured dict."""
    if not file:
        raise HTTPException(status_code=422, detail="Empty image payload")
    parsed = parse_receipt_with_confidence(file)
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


@app.post("/v1/parse-receipt/async", response_model=dict)
async def parse_receipt_async_route(
    file: UploadFile | None = File(default=None, description="Receipt image file"),
    image_url: str | None = Form(default=None, description="Public URL of a receipt image"),
    webhook_url: str | None = Form(default=None, description="Optional webhook URL for completion callback"),
) -> dict:
    """Queue an async OCR job and return a job_id immediately.

        Optionally provide **webhook_url** to receive a JSON POST when processing
    completes or fails.
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

    job = _job_store.create(webhook_url=webhook_url)
    # Fire-and-forget background task
    import asyncio
    asyncio.get_running_loop().create_task(_process_job(image_bytes, job["job_id"], webhook_url=webhook_url))
    return {"job_id": job["job_id"], "status": "queued", "webhook_url": webhook_url}


@app.get("/v1/jobs/{job_id}", response_model=dict)
async def job_status_route(job_id: str) -> dict:
    """Poll the status and result of an async OCR job."""
    job = _job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "result": job.get("result"),
        "error": job.get("error"),
    }
