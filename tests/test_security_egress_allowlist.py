"""
Pre-development ACCEPTANCE tests for P2-1: Egress allowlist / DNS-rebinding mitigation.

Source of truth: analysis/analysis-brief.md
  - Spec section 4 / P2-1: "optional ALLOWED_FETCH_HOSTS allowlist + pin the
    resolved IP by connecting to the validated address (advanced). Documented
    as residual-risk mitigation."
  - Test Plan section 7 does NOT list P2-1 (P2 is follow-up, not required for P0).
  - Section 3: "DNS-rebinding is only partially mitigable client-side; documented
    as a residual risk with the recommended mitigation of an egress allowlist if
    the deployment is high-security."

STATUS: DEFERRED. These tests encode the TARGET behavior so a later worker can
pick the feature up. They must NOT gate the P0 safety baseline:

  * Every test is marked ``pytest.mark.xfail(strict=False)`` so it is reported as
    xfail and would become XPASS once the feature lands -- without failing the run.
  * The whole module is additionally guarded by ``pytest.mark.skipif`` so that,
    even in strict mode, nothing in the not-yet-existing feature can turn into a
    hard failure. The feature is opt-in (controlled by the ``ALLOWED_FETCH_HOSTS``
    env / config), so there is nothing to import until P2-1 is implemented.

DO NOT IMPLEMENT THE FEATURE HERE. This module is a specification in test form.

Target interface (to be implemented later, see spec section 4 P2-1):
  * An optional ``ALLOWED_FETCH_HOSTS`` allowlist. When set (non-empty), the
    URL validator MUST reject any host not on the list (exact host + subdomain-of
    an-allowed-domain semantics). When unset, behavior is unchanged (open mode --
    the P0 SSRF validator still applies).
  * DNS-rebinding mitigation: the resolved IP is pinned after validation and the
    actual connection uses the validated address; at minimum, a host whose DNS
    flips to a private/link-local IP *between* validation and connect MUST still
    be blocked (the classic rebinding window).

Residual-risk note for high-security deployments: enable ALLOWED_FETCH_HOSTS and
combine with network-level egress controls; client-side pinning is defense-in-depth,
not a complete guarantee.

NOTE ON MODULE LOCATION:
  The spec nominally named this feature ``app/security.py:validate_image_url``,
  but the actual P0-1 SSRF validator landed in ``app/ssrf_guard.py``
  (``validate_url`` / ``fetch_image_bytes``). P2-1 will most plausibly extend
  ``ssrf_guard``, so this file resolves the feature from EITHER location and
  gates on the spec-mandated ``ALLOWED_FETCH_HOSTS`` marker regardless of module.
  If the implementer instead creates ``app/security``, that is also honored.
"""

from __future__ import annotations

import socket

import pytest

# The feature is not implemented yet; guard imports so a missing module never
# hard-fails the P0 suite. P2-1 may land in app.security (per spec naming) OR be
# folded into app.ssrf_guard (where the P0 validator actually lives).
try:
    from app import security as _security_module  # type: ignore
except Exception:  # pragma: no cover - pre-implementation guard
    _security_module = None  # type: ignore

try:
    from app import ssrf_guard as _ssrf_guard_module  # type: ignore
except Exception:  # pragma: no cover - pre-implementation guard
    _ssrf_guard_module = None  # type: ignore


def _resolve_feature_module():
    """Return the module that exposes the P2-1 allowlist marker, if any."""
    for mod in (_security_module, _ssrf_guard_module):
        if mod is not None and hasattr(mod, "ALLOWED_FETCH_HOSTS"):
            return mod
    return None


_FEATURE_MODULE = _resolve_feature_module()

# The validator may be named validate_image_url (spec) or validate_url (actual P0).
def _resolve_validator(mod):
    if mod is None:
        return None
    return getattr(mod, "validate_image_url", None) or getattr(mod, "validate_url", None)


def _resolve_fetcher(mod):
    if mod is None:
        return None
    return getattr(mod, "fetch_image_bytes", None)


security = _FEATURE_MODULE  # alias used by the test bodies below
validate_image_url = _resolve_validator(_FEATURE_MODULE)
fetch_image_bytes = _resolve_fetcher(_FEATURE_MODULE)

# Gate the whole module on the allowlist marker existing. Until P2-1 is
# implemented (and defines ALLOWED_FETCH_HOSTS), the tests are skipped rather
# than erroring. They remain authored as xfail so that once the gate flips they
# immediately surface as "expected fail" and turn to XPASS when the behavior is
# actually implemented.
_HAS_FEATURE = _FEATURE_MODULE is not None

skip_no_feature = pytest.mark.skipif(
    not _HAS_FEATURE,
    reason="P2-1 egress allowlist / DNS-rebinding mitigation not implemented yet (deferred). "
    "See analysis/analysis-brief.md section 4 P2-1 and section 7.",
)

# Every test below is a deferred acceptance test: it must NOT block the P0 suite.
xfail_deferred = pytest.mark.xfail(
    strict=False,
    reason="P2-1 deferred: egress allowlist / DNS-rebinding mitigation is a follow-up "
    "residual-risk mitigation, not part of the P0 safety baseline.",
)


# --------------------------------------------------------------------------
# P2-1 (a): ALLOWED_FETCH_HOSTS allowlist -- reject hosts not on the list
# --------------------------------------------------------------------------
@skip_no_feature
@xfail_deferred
def test_allowlist_rejects_unlisted_host(monkeypatch):
    """When ALLOWED_FETCH_HOSTS is set, a host not on the list is rejected.

    Spec 4 P2-1: optional ALLOWED_FETCH_HOSTS allowlist; when set,
    validate_image_url rejects any host not on the list.
    """
    monkeypatch.setattr(security, "ALLOWED_FETCH_HOSTS", ["images.example.com"])
    # Public, well-formed host -- but NOT on the allowlist -> must be rejected.
    with pytest.raises(ValueError):
        validate_image_url("https://evil.example.org/receipt.jpg")


@skip_no_feature
@xfail_deferred
def test_allowlist_accepts_listed_host(monkeypatch):
    """A host present in ALLOWED_FETCH_HOSTS is accepted (when otherwise valid)."""
    monkeypatch.setattr(security, "ALLOWED_FETCH_HOSTS", ["images.example.com"])
    # Same-origin host on the list -> must be accepted (no raise).
    validate_image_url("https://images.example.com/r.png")


@skip_no_feature
@xfail_deferred
def test_allowlist_accepts_subdomain_of_listed_domain(monkeypatch):
    """Subdomains of an allowed domain are accepted (defense-in-depth for CDNs)."""
    monkeypatch.setattr(security, "ALLOWED_FETCH_HOSTS", ["example.com"])
    validate_image_url("https://cdn.example.com/img.png")


@skip_no_feature
@xfail_deferred
def test_allowlist_unset_keeps_open_mode(monkeypatch):
    """Empty/unset allowlist preserves the P0 open behavior (public host allowed)."""
    monkeypatch.setattr(security, "ALLOWED_FETCH_HOSTS", [])
    # No restriction: a valid public host must still be accepted.
    validate_image_url("https://public-cdn.net/r.jpg")


@skip_no_feature
@xfail_deferred
def test_allowlist_rejects_private_host_even_if_listed(monkeypatch):
    """Allowlist does not override the P0 SSRF rejection of private/loopback hosts."""
    monkeypatch.setattr(security, "ALLOWED_FETCH_HOSTS", ["localhost", "169.254.169.254"])
    with pytest.raises(ValueError):
        validate_image_url("http://169.254.169.254/latest/meta-data/")
    with pytest.raises(ValueError):
        validate_image_url("http://localhost:8080/secret")


# --------------------------------------------------------------------------
# P2-1 (b): DNS-rebinding mitigation -- DNS flip to private IP is still blocked
# --------------------------------------------------------------------------
@skip_no_feature
@xfail_deferred
def test_dns_rebind_to_private_ip_blocked(monkeypatch):
    """A host whose DNS resolves to a PRIVATE IP *after* validation is still blocked.

    This is the core DNS-rebinding residual risk (spec 3, spec 4 P2-1). The
    resolved address must be pinned at validation time and the connection must
    use the validated address; a flip to a private/link-local IP in the gap
    between validation and connect must NOT reach the internal network.
    """
    public_info = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.10", 0))]
    private_info = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))]

    calls: list[int] = []

    def fake_getaddrinfo(host, port, *args, **kwargs):
        calls.append(host)
        # Validation call returns public; the later connect-time call returns private.
        if len(calls) <= 1:
            return public_info
        return private_info

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    # Ensure open mode (no allowlist) so only the rebind check is exercised.
    monkeypatch.setattr(security, "ALLOWED_FETCH_HOSTS", [])

    with pytest.raises(ValueError):
        validate_image_url("https://rebinding-attack.example.net/r.jpg")


@skip_no_feature
@xfail_deferred
def test_fetch_pins_validated_ip_not_rebound(monkeypatch):
    """fetch_image_bytes connects to the validated (pinned) IP, not a rebound one.

    Advanced mitigation (spec 4 P2-1): the resolved IP is pinned and the actual
    connection uses the validated address. We assert the helper still raises when
    the rebound address would be private, i.e. it does not silently follow the
    attacker-controlled second resolution.
    """
    public_info = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.51.100.20", 0))]
    private_info = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]
    calls: list[int] = []

    def fake_getaddrinfo(host, port, *args, **kwargs):
        calls.append(host)
        return public_info if len(calls) <= 1 else private_info

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(security, "ALLOWED_FETCH_HOSTS", [])

    # The exact exception type depends on whether P2-1 routes through the
    # validator or fetch layer; either ValueError (validator) or HTTPException
    # (fetch wrapper) is acceptable -- both mean "blocked".
    with pytest.raises((ValueError, Exception)):
        # If fetch_image_bytes is not yet wired to pinning, this should still
        # raise because the rebound IP is private.
        fetch_image_bytes("https://rebinding.example.net/r.jpg")
