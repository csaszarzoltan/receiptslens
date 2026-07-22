"""ReceiptLens API layer."""
from __future__ import annotations

import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, field_validator

from app.ocr import ConfidenceReceipt, check_duplicates, parse_receipt_with_confidence
from app.security import fetch_image_bytes
from app.ssrf_guard import validate_scheme_and_host
logger = logging.getLogger("uvicorn.error")

app = FastAPI(
    title="ReceiptLens",
    description="Extract structured data from receipt images.",
    version="0.5.0",
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
# ---------------------------------------------------------------------------
# Configurable limits (plumbed into fetch_image_bytes defaults)
# ---------------------------------------------------------------------------
MAX_IMAGE_BYTES: int = 20_000_000  # 20 MB
URL_FETCH_TIMEOUT: float = 30.0  # seconds


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


def _process_one(item_bytes: bytes) -> dict[str, Any]:
    parsed = parse_receipt_with_confidence(item_bytes)
    return _render_receipt(parsed)


async def _process_job(
    image_bytes: bytes | None,
    job_id: str,
    webhook_url: str | None = None,
    image_url: str | None = None,
) -> None:
    """Run OCR in a thread and update job store.

    If *image_url* is provided, the fetch happens here (background) instead
    of in the request handler, keeping the ``/v1/parse-receipt/async``
    response non-blocking (P1-2).
    """
    _job_store.set_status(job_id, "processing")

    def _run() -> dict:
        if image_url is not None:
            image_bytes_url = fetch_image_bytes(image_url)
            return _process_one(image_bytes_url)
        assert image_bytes is not None, "image_bytes required when image_url is not set"
        return _process_one(image_bytes)

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
    except HTTPException as exc:
        # Forward upstream fetch errors (e.g. bad URL) to job status
        _job_store.set_status(job_id, "failed", error=str(exc.detail))
        if webhook_url:
            await _deliver_webhook(webhook_url, {
                "job_id": job_id,
                "status": "failed",
                "error": str(exc.detail),
            })
    except Exception:
        logger.exception("Async OCR job %s failed", job_id)
        _job_store.set_status(job_id, "failed", error="OCR processing failed.")
        if webhook_url:
            await _deliver_webhook(webhook_url, {
                "job_id": job_id,
                "status": "failed",
                "error": "OCR processing failed.",
            })


async def _process_batch_job(
    items: list[dict[str, Any]],
    job_id: str,
    webhook_url: str | None = None,
    image_urls: list[str] | None = None,
) -> None:
    """Run batch OCR in threads and update job store with per-item status.

    If *image_urls* is provided, each URL is fetched inside the background
    job (P1-2 non-blocking contract) rather than in the request handler.
    """
    _job_store.set_status(job_id, "processing")

    # When image_urls are provided, resolve them to items with bytes (or errors)
    if image_urls is not None:
        items = []
        for idx, url in enumerate(image_urls):
            try:
                items.append(_build_batch_item(idx, fetch_image_bytes(url)))
            except HTTPException as exc:
                items.append(_build_error_item(idx, exc.detail))

    total = len(items)
    results: list[dict[str, Any]] = []

    loop = __import__("asyncio").get_running_loop()
    try:
        for idx, item in enumerate(items, start=1):
            _job_store.set_status(
                job_id,
                "processing",
                result={"results": results, "summary": {"total": total, "successful": sum(1 for r in results if r.get("error") is None), "failed": sum(1 for r in results if r.get("error") is not None)}},
            )
            if item.get("_error"):
                results.append({
                    "index": item["index"],
                    "vendor": None,
                    "total": None,
                    "date": None,
                    "tax": None,
                    "currency": None,
                    "line_items": [],
                    "confidence": {
                        "vendor": None,
                        "total": None,
                        "date": None,
                        "tax": None,
                        "currency": None,
                        "line_items": None,
                    },
                    "error": item["_error"],
                })
                continue

            def _run(b=item["bytes"]) -> dict:
                return _process_one(b)

            try:
                rendered = await loop.run_in_executor(_job_store._executor, _run)
            except Exception:
                rendered = None
                results.append({
                    "index": item["index"],
                    "vendor": None,
                    "total": None,
                    "date": None,
                    "tax": None,
                    "currency": None,
                    "line_items": [],
                    "confidence": {
                        "vendor": None,
                        "total": None,
                        "date": None,
                        "tax": None,
                        "currency": None,
                        "line_items": None,
                    },
                    "error": "OCR processing failed.",
                })
                continue

            results.append({
                "index": item["index"],
                **rendered,
                "error": None,
            })

        final_payload = {
            "results": results,
            "summary": {
                "total": total,
                "successful": sum(1 for r in results if r.get("error") is None),
                "failed": sum(1 for r in results if r.get("error") is not None),
            },
        }
        _job_store.set_status(job_id, "completed", result=final_payload)
        if webhook_url:
            await _deliver_webhook(webhook_url, {
                "job_id": job_id,
                "status": "completed",
                "result": final_payload,
            })
    except Exception:
        logger.exception("Async batch OCR job %s failed", job_id)
        _job_store.set_status(job_id, "failed", error="OCR processing failed.")
        if webhook_url:
            await _deliver_webhook(webhook_url, {
                "job_id": job_id,
                "status": "failed",
                "error": "OCR processing failed.",
            })


async def _deliver_webhook(url: str, payload: dict) -> None:
    """POST payload to a webhook URL after SSRF validation."""
    try:
        validate_scheme_and_host(url)
    except ValueError as exc:
        logger.warning("Webhook URL blocked by SSRF guard: %s", exc)
        return
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5.0, read=10.0, write=None, pool=None)) as client:
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
        image_bytes = fetch_image_bytes(image_url)  # type: ignore[arg-type]

    try:
        return await parse_receipt_endpoint(image_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - OCR is unpredictable
        from PIL import UnidentifiedImageError
        if isinstance(exc, UnidentifiedImageError):
            raise HTTPException(
                status_code=400,
                detail="The provided data is not a recognized image format.",
            ) from exc
        logger.exception("OCR processing failed")
        raise HTTPException(
            status_code=500,
            detail="OCR processing failed.",
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
        job = _job_store.create(webhook_url=webhook_url)
        # Fire-and-forget background task — file bytes are ready
        import asyncio

        asyncio.get_running_loop().create_task(
            _process_job(image_bytes, job["job_id"], webhook_url=webhook_url)
        )
    else:
        # Defer the URL fetch to the background job (P1-2 non-blocking)
        job = _job_store.create(webhook_url=webhook_url)
        import asyncio

        asyncio.get_running_loop().create_task(
            _process_job(
                None,
                job["job_id"],
                webhook_url=webhook_url,
                image_url=image_url,
            )
        )
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


# ---------------------------------------------------------------------------
# Batch endpoints
# ---------------------------------------------------------------------------


def _build_batch_item(index: int, image_bytes: bytes) -> dict[str, Any]:
    return {"index": index, "bytes": image_bytes}


def _build_error_item(index: int, error: str) -> dict[str, Any]:
    return {
        "index": index,
        "bytes": b"",
        "_error": error,
    }


@app.post("/v1/parse-receipts", response_model=dict)
async def parse_receipts_route(
    files: list[UploadFile] | None = File(default=None, description="Receipt image files"),
    image_urls: str | None = Form(default=None, description="JSON array of receipt image URLs"),
) -> dict:
    """Parse multiple receipt images in one request.

    Send either **files** (multipart uploads) or **image_urls** (JSON array),
    not both.
    """
    if files is not None and image_urls is not None:
        raise HTTPException(
            status_code=400,
            detail="Provide either 'files' or 'image_urls', not both.",
        )

    items: list[dict[str, Any]] = []

    if files is not None:
        if not files:
            raise HTTPException(status_code=422, detail="Provide at least one file.")
        if len(files) > 20:
            raise HTTPException(
                status_code=413,
                detail="Too many files: maximum 20 per request.",
            )
        for idx, upload in enumerate(files):
            try:
                items.append(_build_batch_item(idx, _bytes_from_upload(upload)))
            except HTTPException as exc:
                items.append(_build_error_item(idx, exc.detail))
    elif image_urls is not None:
        try:
            urls = json.loads(image_urls)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid JSON for image_urls: {exc}",
            ) from exc
        if not isinstance(urls, list) or not urls:
            raise HTTPException(status_code=422, detail="Provide at least one URL.")
        if len(urls) > 20:
            raise HTTPException(
                status_code=413,
                detail="Too many URLs: maximum 20 per request.",
            )
        for idx, url in enumerate(urls):
            try:
                items.append(_build_batch_item(idx, fetch_image_bytes(str(url))))
            except HTTPException as exc:
                items.append(_build_error_item(idx, exc.detail))
    else:
        raise HTTPException(
            status_code=422,
            detail="Missing required input: send 'files' or 'image_urls'.",
        )

    loop = __import__("asyncio").get_running_loop()
    results: list[dict[str, Any]] = []
    for item in items:
        if item.get("_error"):
            results.append({
                "index": item["index"],
                "vendor": None,
                "total": None,
                "date": None,
                "tax": None,
                "currency": None,
                "line_items": [],
                "confidence": {
                    "vendor": None,
                    "total": None,
                    "date": None,
                    "tax": None,
                    "currency": None,
                    "line_items": None,
                },
                "error": item["_error"],
            })
            continue

        def _run(b=item["bytes"]) -> dict:
            return _process_one(b)

        try:
            rendered = await loop.run_in_executor(_job_store._executor, _run)
        except Exception:
            results.append({
                "index": item["index"],
                "vendor": None,
                "total": None,
                "date": None,
                "tax": None,
                "currency": None,
                "line_items": [],
                "confidence": {
                    "vendor": None,
                    "total": None,
                    "date": None,
                    "tax": None,
                    "currency": None,
                    "line_items": None,
                },
                "error": "OCR processing failed.",
            })
            continue

        results.append({
            "index": item["index"],
            **rendered,
            "error": None,
        })

    return {
        "results": results,
        "summary": {
            "total": len(results),
            "successful": sum(1 for r in results if r.get("error") is None),
            "failed": sum(1 for r in results if r.get("error") is not None),
        },
    }


@app.post("/v1/parse-receipts/async", response_model=dict)
async def parse_receipts_async_route(
    files: list[UploadFile] | None = File(default=None, description="Receipt image files"),
    image_urls: str | None = Form(default=None, description="JSON array of receipt image URLs"),
    webhook_url: str | None = Form(default=None, description="Optional webhook URL for completion callback"),
) -> dict:
    """Queue an async batch OCR job and return a job_id immediately."""
    if files is not None and image_urls is not None:
        raise HTTPException(
            status_code=400,
            detail="Provide either 'files' or 'image_urls', not both.",
        )

    items: list[dict[str, Any]] = []

    if files is not None:
        if not files:
            raise HTTPException(status_code=422, detail="Provide at least one file.")
        if len(files) > 20:
            raise HTTPException(
                status_code=413,
                detail="Too many files: maximum 20 per request.",
            )
        for idx, upload in enumerate(files):
            try:
                items.append(_build_batch_item(idx, _bytes_from_upload(upload)))
            except HTTPException as exc:
                items.append(_build_error_item(idx, exc.detail))
    elif image_urls is not None:
        try:
            urls = json.loads(image_urls)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid JSON for image_urls: {exc}",
            ) from exc
        if not isinstance(urls, list) or not urls:
            raise HTTPException(status_code=422, detail="Provide at least one URL.")
        if len(urls) > 20:
            raise HTTPException(
                status_code=413,
                detail="Too many URLs: maximum 20 per request.",
            )
        # Defer URL fetching to the background job (P1-2 non-blocking)
        job = _job_store.create(webhook_url=webhook_url)
        import asyncio

        asyncio.get_running_loop().create_task(
            _process_batch_job(
                [],
                job["job_id"],
                webhook_url=webhook_url,
                image_urls=[str(u) for u in urls],
            )
        )
        return {"job_id": job["job_id"], "status": "queued", "webhook_url": webhook_url}
    else:
        raise HTTPException(
            status_code=422,
            detail="Missing required input: send 'files' or 'image_urls'.",
        )

    job = _job_store.create(webhook_url=webhook_url)
    import asyncio

    asyncio.get_running_loop().create_task(
        _process_batch_job(items, job["job_id"], webhook_url=webhook_url)
    )
    return {"job_id": job["job_id"], "status": "queued", "webhook_url": webhook_url}


# ---------------------------------------------------------------------------
# Duplicate detection endpoint
# ---------------------------------------------------------------------------


class DuplicateCheckRequest(BaseModel):
    receipts: list[dict]

    @field_validator("receipts")
    @classmethod
    def validate_receipts(cls, v: list[dict]) -> list[dict]:
        if not isinstance(v, list):
            raise ValueError("receipts must be a list")
        if len(v) == 0:
            raise ValueError("receipts list must not be empty")
        if len(v) > 200:
            raise HTTPException(
                status_code=413,
                detail="Too many receipts: maximum 200 per request.",
            )
        for i, receipt in enumerate(v):
            if not isinstance(receipt, dict):
                raise ValueError(f"receipt at index {i} must be a dict")
            total = receipt.get("total")
            if total is None:
                raise ValueError(
                    f"receipt at index {i} is missing required 'total' field"
                )
            if not isinstance(total, (int, float)):
                raise ValueError(
                    f"receipt at index {i} has non-numeric 'total': {total!r}"
                )
        return v


@app.post("/v1/check-duplicates", response_model=dict)
async def check_duplicates_route(body: DuplicateCheckRequest) -> dict:
    """Check a batch of parsed receipts for potential duplicates."""
    try:
        result = check_duplicates(body.receipts)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Duplicate detection failed")
        raise HTTPException(
            status_code=500, detail="Duplicate detection failed."
        ) from exc

    return {
        "duplicate_groups": [
            {
                "group_id": g.group_id,
                "indices": g.indices,
                "confidence": g.confidence,
                "match_evidence": g.match_evidence,
            }
            for g in result.duplicate_groups
        ],
        "summary": result.summary,
    }
