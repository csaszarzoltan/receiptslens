"""Pre-dev stubs for the `_bytes_from_url` error-handling regression contract.

These helpers represent the acceptance criteria for hardening the shared URL
fetch helper ``app.api._bytes_from_url`` against the four error modes named in
kanban task t_ff73d2db:

  1. Invalid / malformed URL (e.g. ``http://example.com:abc/``) -> 400, not 500.
  2. Non-image ``Content-Type`` in the upstream response -> 415.
  3. Oversized upstream response (default cap > 25 MB) -> 413.
  4. A single bad URL inside a batch (``POST /v1/parse-receipts``) must yield a
     per-item ``error`` field with an overall 200 status, not a 500.

The helpers raise ``NotImplementedError`` because the behavioral acceptance
criteria have not yet been validated against the (partially implemented) fetch
helper at pre-test time.

The behavioral tests in ``tests/test_api.py`` import these helpers and are
marked ``xfail(strict=True, raises=NotImplementedError)``. Once the developer
(or the downstream regression/integration tester) validates the helper against
the real endpoint, each stub call should be replaced with concrete assertions
(and the xfail marker dropped). Until then the tests fail *as expected* (xfail),
signalling an unmet acceptance contract rather than a green suite that hides
missing behavior.

SPEC DEVIATION FLAGGED TO ANALYST
---------------------------------
The status codes named in this task (415 for non-image, 413 for oversize, and a
25 MB default cap) CONFLICT with the established repo brief
(``analysis/analysis-brief.md`` section 5 P0-2) and the current implementation in
``app/ssrf_guard.fetch_image_bytes``:

  * The brief + current impl map a non-image Content-Type to **400**
    ("URL did not return an image."), not 415.
  * The brief + current impl map an oversize body to **400**
    ("Image exceeds maximum allowed size."), not 413, and the real default cap
    is **20 MB** (``_DEFAULT_MAX_BYTES = 20_000_000``), not 25 MB.

This stub encodes the task's literal acceptance criteria (415 / 413 / 25 MB) so
the conflict is explicit and tracked. If the analyst intends to revise the brief
to 415/413/25MB, these stubs are already correct; otherwise the task criteria
must be reconciled with the brief before the developer converges the
implementation.
"""
from __future__ import annotations


def rejects_invalid_url_format() -> None:
    raise NotImplementedError(
        "Mode 1 not validated: a malformed/invalid URL such as "
        "'http://example.com:abc/' must be rejected with HTTP 400 (not 500)."
    )


def rejects_non_image_content_type() -> None:
    raise NotImplementedError(
        "Mode 2 not validated: an upstream response with a non-image "
        "Content-Type must be rejected with HTTP 415 (not 500)."
    )


def rejects_oversized_response() -> None:
    raise NotImplementedError(
        "Mode 3 not validated: an upstream response larger than the default 25 MB "
        "cap must be rejected with HTTP 413 (not 500)."
    )


def hide_upstream_error_detail() -> None:
    raise NotImplementedError(
        "Mode (defensive): upstream fetch failures must be wrapped as a generic "
        "400 without leaking internal host/URL detail into the error message."
    )


def batch_route_survives_bad_url_with_per_item_error() -> None:
    raise NotImplementedError(
        "Mode 4 not validated: a single bad URL inside a batch "
        "(POST /v1/parse-receipts) must yield a per-item 'error' field with an "
        "overall HTTP 200 status (not a 500)."
    )
