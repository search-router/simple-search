"""Response security headers (CSP, frame-ancestors, MIME-sniff, referrer, HSTS)."""

from __future__ import annotations

_CSP = (
    b"default-src 'self'; "
    b"img-src 'self' data: https:; "
    b"style-src 'self' 'unsafe-inline'; "
    b"script-src 'self'; "
    b"connect-src 'self'; "
    b"frame-ancestors 'none'; "
    b"base-uri 'self'; "
    b"form-action 'self'"
)

_STATIC_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"content-security-policy", _CSP),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"geolocation=(), microphone=(), camera=()"),
)
_HSTS_HEADER = (
    b"strict-transport-security",
    b"max-age=31536000; includeSubDomains",
)


class SecurityHeadersMiddleware:
    """Pure-ASGI middleware: tacks security headers onto every response.

    Bypasses ``BaseHTTPMiddleware``'s stream bridge, which adds ~one task
    swap per request for what is fundamentally a header append.
    """

    def __init__(  # type: ignore[no-untyped-def]
        self,
        app,
        *,
        hsts_enabled: bool = False,
        trust_forwarded_proto: bool = False,
    ) -> None:
        self._app = app
        self._hsts_enabled = hsts_enabled
        self._trust_forwarded_proto = trust_forwarded_proto

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        scheme = scope.get("scheme")
        if self._trust_forwarded_proto:
            for name, value in scope["headers"]:
                if name == b"x-forwarded-proto":
                    scheme = value.decode("latin-1").split(",")[0].strip().lower()
                    break
        add_hsts = self._hsts_enabled and scheme == "https"

        async def send_wrapper(message):  # type: ignore[no-untyped-def]
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                present = {name.lower() for name, _ in headers}
                for name, value in _STATIC_HEADERS:
                    if name not in present:
                        headers.append((name, value))
                if add_hsts and _HSTS_HEADER[0] not in present:
                    headers.append(_HSTS_HEADER)
            await send(message)

        await self._app(scope, receive, send_wrapper)
