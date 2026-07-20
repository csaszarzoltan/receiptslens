"""Pre-dev stubs for the image_url receipt parsing acceptance contract.

These helpers represent the acceptance-criteria contract for the `image_url`
receipt parsing feature on `POST /v1/parse-receipt`. They raise
`NotImplementedError` because the behavioral acceptance criteria have not yet
been validated/implemented at pre-test time.

The behavioral tests in `test_receipt_parsing.py` import these helpers and are
marked `xfail(strict=True, raises=NotImplementedError)`. Once the developer
validates the feature against the real endpoint, they should replace each stub
call with concrete assertions (and drop the xfail marker). Until then the tests
fail *as expected* (xfail), signalling an unmet acceptance contract rather than
a green suite that hides missing behavior.
"""
from __future__ import annotations


def fetch_valid_image_url_returns_parsed() -> None:
    raise NotImplementedError(
        "Scenario 1 not validated: a valid image URL must return 200 OK "
        "with correctly parsed receipt data."
    )


def fetch_invalid_url_returns_error() -> None:
    raise NotImplementedError(
        "Scenario 2 not validated: an invalid/malformed/non-existent URL must "
        "return 400/422 with an appropriate error message."
    )


def fetch_url_timeout_returns_timeout_error() -> None:
    raise NotImplementedError(
        "Scenario 3 not validated: a URL fetch timeout must return a clear "
        "timeout error code/message."
    )


def missing_url_falls_back_to_upload() -> None:
    raise NotImplementedError(
        "Scenario 4 not validated: a request with no URL (and no file) must "
        "fall back to / error the same way as the existing file upload flow."
    )


def response_schema_matches_sync_endpoint() -> None:
    raise NotImplementedError(
        "Scenario 5 not validated: the image_url response JSON schema must "
        "exactly match the existing sync receipt parsing endpoint schema."
    )
