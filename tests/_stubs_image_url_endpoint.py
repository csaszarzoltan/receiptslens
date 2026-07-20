"""Pre-dev stubs for the image_url endpoint acceptance contract (task t_51f16ffd).

These helpers encode the acceptance criteria for the ``image_url`` feature on
``POST /v1/parse-receipt`` as specified in kanban task t_51f16ffd. They raise
``NotImplementedError`` because the behavioral acceptance criteria have not yet
been validated against the live endpoint at pre-test time.

The behavioral tests in ``test_image_url_endpoint.py`` import these helpers and
are marked ``xfail(strict=True, raises=NotImplementedError)``. When the
developer validates the feature, replace each stub call with concrete
assertions against the real endpoint (or remove the stub and assert directly)
and drop the xfail marker. Until then the tests fail *as expected* (xfail),
signalling an unmet acceptance contract rather than a green suite that hides
missing behavior.

Note on one genuine spec gap captured here: the spec requires the fetched URL's
``Content-Type`` to be validated as ``image/*`` (415 on mismatch), but the
current ``_bytes_from_url`` helper does not perform this check -- only
``_bytes_from_upload`` does for the file path. ``reject_invalid_content_type``
encodes that contract so the gap is explicit.
"""
from __future__ import annotations


def fetch_valid_image_url_download_and_ocr() -> None:
    raise NotImplementedError(
        "Scenario not validated: a valid public image URL must be downloaded "
        "and run through OCR, returning 200 with the same parsed schema as the "
        "file-upload endpoint."
    )


def reject_invalid_content_type() -> None:
    raise NotImplementedError(
        "Scenario not validated: when the fetched URL returns a non-image "
        "Content-Type (e.g. text/html), the endpoint must reject it with "
        "415 Unsupported Media Type, per the spec."
    )


def handle_invalid_url() -> None:
    raise NotImplementedError(
        "Scenario not validated: a malformed, non-resolvable, or unreachable "
        "URL must be handled gracefully and surface a clear error "
        "(400/422/timeout) instead of a 500."
    )


def file_upload_path_unaffected() -> None:
    raise NotImplementedError(
        "Scenario not validated: the existing file-upload path (file: "
        "UploadFile; non-image content-type -> 415) must remain unchanged when "
        "image_url is added, and existing file-upload tests must keep passing."
    )
