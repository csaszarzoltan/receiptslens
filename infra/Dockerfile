# =============================================================================
# ReceiptLens — Production Dockerfile (multi-stage)
# =============================================================================
# Runtime requirements:
#   - Python 3.11+
#   - Tesseract OCR engine (tesseract-ocr)
#   - Pillow (needs libjpeg, zlib, libfreetype build deps in builder)
#   - pytesseract (Python wrapper, shells out to tesseract binary)
#
# Environment variables (set at deploy time):
#   PORT               — Bind port for uvicorn (default: 8000)
#   CORS_ORIGINS       — Comma-separated allowed origins for CORS
#   AUTH_TOKEN         — Bearer token for API auth (if enabled)
#   TESSDATA_PREFIX    — Path to Tesseract data dir (default: auto-detected)
# =============================================================================

# ─── Builder stage: install system build deps + Python packages ────────────────
FROM python:3.11-slim AS builder

# System build deps for C extensions (Pillow, etc.)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libjpeg-dev \
        zlib1g-dev \
        libfreetype6-dev \
        libtiff-dev \
        libtesseract-dev \
        libleptonica-dev \
        tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Install Python dependencies from pyproject.toml
COPY pyproject.toml .
# Minimal build-system so pip can resolve the package
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Copy application source for package metadata resolution
COPY app ./app

# Install the package (pulls all [project] dependencies)
RUN pip install --no-cache-dir .


# ─── Runtime stage: slim image with only what's needed ─────────────────────────
FROM python:3.11-slim

# Runtime system deps — tesseract binary + English trained data
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

WORKDIR /app

# Copy application code
COPY app ./app

# Expose default port (overridden at deploy time via PORT env var)
EXPOSE 8000

# Run the ASGI app under uvicorn.
# Respect Railway's PORT env var; fall back to 8000 for local runs.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
