"""Pre-development acceptance tests for P1-1: configurable size/timeout limits.

Source of truth: ``analysis/analysis-brief.md``
  * Spec section 4 / P1-1 -- add module-level constants
    ``MAX_IMAGE_BYTES = 20_000_000`` (20 MB) and ``URL_FETCH_TIMEOUT = 30.0``
    to ``app/api.py``; plumb them into ``fetch_image_bytes`` defaults; document
    in ``docs/api.md``.
  * Acceptance criteria (section 5 P1-1): ``MAX_IMAGE_BYTES`` and
    ``URL_FETCH_TIMEOUT`` exist as module constants and are the default for
    ``fetch_image_bytes``; ``docs/api.md`` documents the limits and the accepted
    ``image_url`` contract.
  * Test plan (section 7): ``fetch_image_bytes`` parametrized cases -- oversize
    body -> 400, timeout -> 400.

PRE-DEV STATUS (observed drift -- see completion comment / kanban_comment):
  The hardened fetcher is already implemented in ``app/ssrf_guard.py`` and
  re-exported from ``app/security.py`` (sibling P0-2 work), with internal
  defaults ``_DEFAULT_MAX_BYTES = 20_000_000`` / ``_DEFAULT_TIMEOUT = 30.0``.
  The spec/task, however, require the *named* constants
  ``MAX_IMAGE_BYTES`` / ``URL_FETCH_TIMEOUT`` to live in ``app/api.py`` and be
  wired as the public ``fetch_image_bytes`` defaults, plus docs coverage. Those
  are NOT yet present, so this file is RED on exactly those P1-1 gaps:

    RED   - ``app.api.MAX_IMAGE_BYTES`` / ``URL_FETCH_TIMEOUT`` do not exist.
    RED   - ``fetch_image_bytes`` defaults do not yet reference the named
            module constants (they reference ssrf_guard's private defaults).
    RED   - ``docs/api.md`` does not document the 20 MB / 30 s limits.
    RED*  - timeout errors are not surfaced as ``HTTPException(400)`` (only
            ``ValueError`` is wrapped today; a slow URL raises a raw
            ``httpx`` timeout). This is the P1-1 "honours timeout" contract.

  One behavioral test (size enforcement) already PASSES because the P0-2
  hardened fetcher enforces the 20 MB cap; it is kept as a regression guard and
  clearly labelled.

No live external network is used: DNS resolution (``_resolve_addresses``) and
``httpx.Client.send`` are monkeypatched so a mock stream / timeout is served
locally.
"""
from __future__ import annotations

import pathlib
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Guarded imports -- keep the module collectable so every gap reports a clear,
# granular RED failure instead of a single opaque collection error.
# ---------------------------------------------------------------------------

# The spec/task require these NAMED constants in ``app/api.py``. They do not
# exist yet (the real implementation uses ``app.ssrf_guard._DEFAULT_*``), so the
# guarded import falls back to ``None`` and the constants checks fail clearly.
try:  # pragma: no cover - import guard for pre-development state
    from app.api import MAX_IMAGE_BYTES, URL_FETCH_TIMEOUT
except Exception:  # noqa: BLE001 - we deliberately fall back to None
    MAX_IMAGE_BYTES = None
    URL_FETCH_TIMEOUT = None

# Public fetcher entry point (API layer imports it from here too).
try:  # pragma: no cover - import guard for pre-development state
    from app.security import fetch_image_bytes
except Exception:  # noqa: BLE001 - we deliberately fall back to None
    fetch_image_bytes = None


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
API_DOCS = REPO_ROOT / "docs" / "api.md"


def _require_fetcher() -> None:
    """Fail clearly (red) if the public fetcher is not importable."""
    if fetch_image_bytes is None:
        pytest.fail(
            "app.security.fetch_image_bytes is not importable yet "
            "(expected pre-development RED state)"
        )


def _mock_streaming_response(
    *,
    content: bytes,
    content_type: str = "image/png",
    status_code: int = 200,
    is_redirect: bool = False,
):
    """Build a MagicMock standing in for an httpx streaming Response."""
    response = MagicMock()
    response.status_code = status_code
    response.headers = httpx.Headers(
        {
            "Content-Type": content_type,
            "Content-Length": str(len(content)),
        }
    )
    response.is_redirect = is_redirect
    response.is_closed = False

    def _iter_raw(*_args, **_kwargs):
        yield content

    response.iter_raw = _iter_raw
    response.read.return_value = content

    def _close() -> None:
        response.is_closed = True

    response.close = _close
    return response


# ---------------------------------------------------------------------------
# P1-1: named module constants exist in app/api.py (spec section 4 / 5 P1-1)
# ---------------------------------------------------------------------------


def test_max_image_bytes_constant_defined_in_api_module():
    """P1-1: ``MAX_IMAGE_BYTES = 20_000_000`` must exist in ``app/api.py``."""
    assert MAX_IMAGE_BYTES is not None, (
        "app.api.MAX_IMAGE_BYTES is not defined. P1-1 requires a module-level "
        "MAX_IMAGE_BYTES = 20_000_000 constant in app/api.py."
    )
    assert MAX_IMAGE_BYTES == 20_000_000, (
        f"MAX_IMAGE_BYTES expected 20_000_000, got {MAX_IMAGE_BYTES!r}"
    )


def test_url_fetch_timeout_constant_defined_in_api_module():
    """P1-1: ``URL_FETCH_TIMEOUT = 30.0`` must exist in ``app/api.py``."""
    assert URL_FETCH_TIMEOUT is not None, (
        "app.api.URL_FETCH_TIMEOUT is not defined. P1-1 requires a module-level "
        "URL_FETCH_TIMEOUT = 30.0 constant in app/api.py."
    )
    assert URL_FETCH_TIMEOUT == 30.0, (
        f"URL_FETCH_TIMEOUT expected 30.0, got {URL_FETCH_TIMEOUT!r}"
    )


# ---------------------------------------------------------------------------
# P1-1: fetch_image_bytes defaults come from the module constants
# ---------------------------------------------------------------------------


def test_fetch_image_bytes_defaults_reference_constants():
    """P1-1: the keyword defaults must equal the named module constants."""
    _require_fetcher()
    kwdefaults = fetch_image_bytes.__kwdefaults__
    assert kwdefaults is not None and len(kwdefaults) >= 2, (  # type: ignore[union-attr]
        f"fetch_image_bytes has no keyword defaults: {kwdefaults!r}"
    )
    max_default = kwdefaults.get("max_bytes")
    timeout_default = kwdefaults.get("timeout")

    assert MAX_IMAGE_BYTES is not None and max_default == MAX_IMAGE_BYTES, (
        f"fetch_image_bytes max_bytes default ({max_default!r}) must equal "
        f"app.api.MAX_IMAGE_BYTES ({MAX_IMAGE_BYTES!r})."
    )
    assert URL_FETCH_TIMEOUT is not None and timeout_default == URL_FETCH_TIMEOUT, (
        f"fetch_image_bytes timeout default ({timeout_default!r}) must equal "
        f"app.api.URL_FETCH_TIMEOUT ({URL_FETCH_TIMEOUT!r})."
    )


# ---------------------------------------------------------------------------
# P1-1: fetch_image_bytes honours max_bytes (spec section 5 P0-2 / 7)
# NOTE: this already passes against the P0-2 hardened fetcher -- kept as a
# regression guard; it confirms the 20 MB cap is enforced as HTTPException(400).
# ---------------------------------------------------------------------------


def test_fetch_image_bytes_enforces_max_bytes(monkeypatch):
    """A streamed body exceeding ``max_bytes`` raises HTTPException(400)."""
    _require_fetcher()
    # Avoid real DNS; pretend every host resolves to a public address.
    monkeypatch.setattr(
        "app.ssrf_guard._resolve_addresses", lambda host: ["93.184.216.34"]
    )
    # Serve a 50-byte image body through a stubbed stream.
    resp = _mock_streaming_response(content=b"x" * 50, content_type="image/png")
    monkeypatch.setattr(httpx.Client, "send", lambda self, *a, **k: resp)

    with pytest.raises(HTTPException) as exc:
        fetch_image_bytes("http://example.com/big.png", max_bytes=10)
    assert exc.value.status_code == 400
    assert "Image exceeds maximum allowed size" in exc.value.detail


# ---------------------------------------------------------------------------
# P1-1: fetch_image_bytes honours timeout (spec section 5 P0-2 / 7)
# RED*: a slow/never-responding URL must surface as HTTPException(400) within
# the budget. Today only ValueError is wrapped; a raw httpx timeout escapes.
# ---------------------------------------------------------------------------


def test_fetch_image_bytes_enforces_timeout(monkeypatch):
    """A slow/never-responding URL raises HTTPException(400) within budget."""
    _require_fetcher()
    monkeypatch.setattr(
        "app.ssrf_guard._resolve_addresses", lambda host: ["93.184.216.34"]
    )

    def _slow_send(self, *args, **kwargs):  # pragma: no cover - injected stub
        raise httpx.ReadTimeout("request timed out")

    monkeypatch.setattr(httpx.Client, "send", _slow_send)

    with pytest.raises(HTTPException) as exc:
        fetch_image_bytes("http://example.com/slow.png", timeout=0.01)
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# P1-1: docs/api.md documents the limits (spec section 5 P1-1 / 7)
# ---------------------------------------------------------------------------


def test_api_docs_document_size_and_timeout_limits():
    """``docs/api.md`` must document the 20 MB cap and the 30 s timeout."""
    assert API_DOCS.exists(), f"{API_DOCS} is missing"
    text = API_DOCS.read_text(encoding="utf-8")

    # 20 MB size limit -- accept any of the common renderings.
    size_documented = any(
        token in text
        for token in ("20 MB", "20MB", "20_000_000", "20000000", "20 MiB")
    )
    assert size_documented, (
        "docs/api.md does not document the 20 MB image size limit "
        "(MAX_IMAGE_BYTES)."
    )

    # 30 s fetch timeout.
    assert "30" in text, (
        "docs/api.md does not document the 30 s URL fetch timeout "
        "(URL_FETCH_TIMEOUT)."
    )
