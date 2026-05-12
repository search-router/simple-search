"""Helpers for normalizing third-party responses into unified schemas."""

from __future__ import annotations

from urllib.parse import urlparse

# Only http(s) URLs ever reach the rendered page. A compromised or malicious
# upstream could try to slip ``javascript:`` or ``data:`` into ``<a href>`` —
# Jinja autoescape stops HTML injection but does not block these schemes.
_SAFE_URL_SCHEMES: frozenset[str] = frozenset({"http", "https"})


def safe_url(url: str | None) -> str:
    """Return ``url`` if it's a safe http(s) URL with a host, else ``""``."""
    if not url:
        return ""
    candidate = url.strip()
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in _SAFE_URL_SCHEMES:
        return ""
    if not parsed.netloc:
        return ""
    return candidate


def domain_of(url: str | None) -> str | None:
    """Return host from URL, stripping a leading ``www.`` if present."""
    if not url:
        return None
    parsed = urlparse(url.strip())
    # ``urlparse`` preserves trailing whitespace inside the netloc when there's
    # no path delimiter after the host (e.g. ``https://example.com  ``); strip
    # so callers don't see ragged domains.
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return None
    return host[4:] if host.startswith("www.") else host


def clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def coerce_int(value: object, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default
