# ReceiptLens API image
FROM python:3.11-slim

# Tesseract OCR engine + English data.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app

# Install runtime deps only (no dev tooling).
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
        fastapi uvicorn pydantic pillow pytesseract httpx python-multipart

EXPOSE 8000

# Run the ASGI app under uvicorn.
# Respect Railway's PORT env var; fall back to 8000 for local runs.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
