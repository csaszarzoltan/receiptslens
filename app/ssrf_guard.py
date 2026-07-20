"""SSRF guard for user-supplied image URL fetching."""

from __future__ import annotations

import logging
import ipaddress
import socket
import urllib.parse
from collections.abc import Iterator
from typing import Iterable as IterableType

import httpx

from fastapi import HTTPException

logger = logging.getLogger(__name__)

_DEFAULT_MAX_BYTES = 20_000_000
_DEFAULT_TIMEOUT = 30.0

_ALLOWED_SCHEMES = ("http", "https")

_BLOCKED_HOSTNAME_SUBSTRINGS = ("local", "internal", "localhost")
_BLOCKED_HOSTNAME_EXACT = frozenset(
    {
        "localhost",
        "local.host",
        "metadata.google.internal",
        "metadata.internal",
        "169.254.169.254",
        "metadata",
    }
)

_RESERVED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("fc00::/7"),
]


def _is_reserved(address: ipaddress._BaseAddress) -> bool:
    return any(address in network for network in _RESERVED_NETWORKS)


def _is_blocked_host(host: str) -> bool:
    lowered = host.lower()
    if lowered in _BLOCKED_HOSTNAME_EXACT:
        return True
    for suffix in _BLOCKED_HOSTNAME_SUBSTRINGS:
        if lowered == suffix or lowered.endswith(f".{suffix}"):
            return True
    return False


def _resolve_addresses(host: str) -> list[str]:
    raw_infos = socket.getaddrinfo(host, None)
    seen: set[str] = set()
    addresses: list[str] = []
    for info in raw_infos:
        _, _, _, _, sockaddr = info
        addr = sockaddr[0]
        if addr not in seen:
            seen.add(addr)
            addresses.append(addr)
    if not addresses:
        raise ValueError(f"unresolvable host: {host}")
    return addresses


def validate_scheme(parsed: urllib.parse.ParseResult) -> None:
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"unsupported url scheme: {parsed.scheme}")


def validate_scheme_and_host(url: str) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(url)
    validate_scheme(parsed)
    if not parsed.hostname:
        raise ValueError("url host missing")
    if _is_blocked_host(parsed.hostname):
        raise ValueError(f"blocked hostname: {parsed.hostname}")
    return parsed


def validate_resolved_ips(host: str | None) -> list[str]:
    if not host:
        raise ValueError("url host missing")
    addresses = _resolve_addresses(host)
    for addr in addresses:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError as exc:
            raise ValueError(f"invalid resolved ip: {addr}") from exc
        if _is_reserved(ip):
            raise ValueError(f"reserved ip rejected: {ip}")
    return addresses


def validate_image_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    validate_scheme(parsed)
    if not parsed.hostname:
        raise ValueError("url host missing")
    validate_resolved_ips(parsed.hostname)


def resolve_and_validate(url: str, max_bytes: int = _DEFAULT_MAX_BYTES) -> urllib.parse.ParseResult:
    parsed = validate_scheme_and_host(url)
    validate_resolved_ips(parsed.hostname)
    return parsed


def validate_url(url: str, max_bytes: int = _DEFAULT_MAX_BYTES) -> httpx.Request:
    parsed = validate_scheme_and_host(url)
    return httpx.Request(method="GET", url=urllib.parse.urlunparse(parsed))


def validate_response_headers(headers) -> None:
    if headers is None:
        return
    content_length = headers.get("Content-Length") or headers.get("content-length")
    if content_length is None:
        return
    try:
        int(content_length)
    except (TypeError, ValueError):
        raise ValueError("invalid content-length")


def count_bytes(iterable: IterableType[bytes], max_bytes: int) -> Iterator[bytes]:
    total = 0
    for chunk in iterable:
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"response exceeds max bytes: {total}")
        yield chunk


def _normalize_redirect_url(base_url: str, location: str) -> urllib.parse.ParseResult:
    if location.startswith("http://") or location.startswith("https://"):
        return urllib.parse.urlparse(location)
    return urllib.parse.urlparse(urllib.parse.urljoin(base_url, location))


def _build_validated_request(parsed: urllib.parse.ParseResult) -> httpx.Request:
    if not parsed.hostname:
        raise ValueError("url host missing")
    if _is_blocked_host(parsed.hostname):
        raise ValueError(f"blocked redirect host: {parsed.hostname}")
    addresses = validate_resolved_ips(parsed.hostname)
    host = addresses[0]
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{host}:{parsed.port}" if parsed.port else host
    target = parsed._replace(netloc=netloc)
    return httpx.Request(method="GET", url=urllib.parse.urlunparse(target))


class _SSRFGuardClient:
    def __init__(self, max_redirects: int = 5) -> None:
        self._max_redirects = max_redirects
        self._redirect_depth = 0

    def fetch(self, request: httpx.Request, max_bytes: int = _DEFAULT_MAX_BYTES) -> bytes:
        timeout = httpx.Timeout(connect=10.0, read=30.0, write=None, pool=None)
        with httpx.Client(timeout=timeout) as client:
            return self._send(client, request, max_bytes=max_bytes, buffer=bytearray())

    def _send(self, client: httpx.Client, request: httpx.Request, *, max_bytes: int, buffer: bytearray) -> bytes:
        if self._redirect_depth >= self._max_redirects:
            raise ValueError("too many redirects")
        base_parsed = urllib.parse.urlparse(str(request.url))
        base_parsed = base_parsed._replace(netloc=base_parsed.hostname)
        validated = _build_validated_request(base_parsed)
        response = client.send(validated, stream=True)
        try:
            if response.is_redirect:
                # Do not consume redirect body — just follow Location.
                location = response.headers.get("Location")
                if not location:
                    raise ValueError("redirect missing location")
                self._redirect_depth += 1
                next_parsed = _normalize_redirect_url(str(request.url), location)
                # Validate redirect target: scheme, hostname blocklist, and resolved IPs
                if not next_parsed.hostname:
                    raise ValueError("redirect target missing host")
                if _is_blocked_host(next_parsed.hostname):
                    raise ValueError(f"blocked redirect host: {next_parsed.hostname}")
                validate_resolved_ips(next_parsed.hostname)
                next_request = _build_validated_request(next_parsed)
                response.close()
                return self._send(client, next_request, max_bytes=max_bytes, buffer=bytearray())

            # Non-redirect: validate content type before streaming.
            content_type = response.headers.get("Content-Type")
            if not (isinstance(content_type, str) and content_type.startswith("image/")):
                raise ValueError("non-image content type")

            content_length = response.headers.get("Content-Length")
            allowed = max_bytes if content_length is None else min(int(content_length), max_bytes)
            for chunk in count_bytes(response.iter_raw(chunk_size=65536), allowed):
                buffer.extend(chunk)
            return bytes(buffer)
        finally:
            response.close()


def fetch_image_bytes(
    url: str,
    *,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    timeout: float = _DEFAULT_TIMEOUT,
) -> bytes:
    try:
        request = validate_url(url, max_bytes=max_bytes)
        client = _SSRFGuardClient()
        client_timeout = httpx.Timeout(connect=10.0, read=timeout, write=None, pool=None)
        with httpx.Client(timeout=client_timeout) as http_client:
            return client._send(http_client, request, max_bytes=max_bytes, buffer=bytearray())
    except httpx.InvalidURL as exc:
        logger.warning("Invalid URL rejected: %s", exc)
        raise HTTPException(
            status_code=400, detail="Invalid image URL."
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning("HTTP error fetching image: %s", exc)
        raise HTTPException(
            status_code=400, detail="Failed to fetch image from URL."
        ) from exc
    except socket.gaierror as exc:
        logger.warning("DNS resolution failed: %s", exc)
        raise HTTPException(
            status_code=400, detail="Failed to fetch image from URL."
        ) from exc
    except ValueError as exc:
        message = str(exc)
        if message == "non-image content type":
            raise HTTPException(status_code=400, detail="URL did not return an image") from exc
        if message.startswith("response exceeds max bytes"):
            raise HTTPException(status_code=400, detail="Image exceeds maximum allowed size") from exc
        logger.warning("Validation error fetching image: %s", exc)
        raise HTTPException(
            status_code=400, detail="Failed to fetch image from URL."
        ) from exc
