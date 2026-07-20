"""ReceiptLens API layer (pre-development stub).

Public contract -- frozen by tests/test_api.py:
  - app: FastAPI instance
  - parse_receipt_endpoint(file: bytes) -> dict   (async)
  - POST /v1/parse-receipt

The endpoint raises NotImplementedError until it is wired to the OCR
pipeline. Type hints and the async signature are part of the contract and
MUST NOT change without updating the pre-dev tests.
"""
from fastapi import FastAPI, File, UploadFile

app = FastAPI(title="ReceiptLens", version="0.0.1")


async def parse_receipt_endpoint(file: bytes) -> dict:
    raise NotImplementedError("parse-receipt endpoint not yet implemented")


@app.post("/v1/parse-receipt")
async def parse_receipt_route(file: UploadFile = File(...)) -> dict:
    contents = await file.read()
    return await parse_receipt_endpoint(contents)
