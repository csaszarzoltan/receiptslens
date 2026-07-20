# infra

Deployment artifacts for ReceiptLens.

- `Dockerfile` — slim Python 3.11 image with Tesseract OCR pre-installed.
  Build and run:

  ```bash
  docker build -t receiptslens -f infra/Dockerfile .
  docker run -p 8000:8000 receiptslens
  ```

The API serves OpenAPI docs at `/docs` once running.
