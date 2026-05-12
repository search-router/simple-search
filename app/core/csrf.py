"""Origin/Referer-based CSRF defense for state-changing requests.

Browsers always send ``Origin`` on POST since modern times (and ``Referer``
otherwise). For mutating methods we require one of them to be present and
to point at an allow-listed scheme+host. This complements ``SameSite=strict``
on the session cookie — anything that downgrades or strips SameSite (older
browsers, embedded webviews) still has to clear this gate.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from app.core.errors import ApiError, ErrorDetail
from app.core.ids import new_request_id

_UNSAFE_METHODS = frozenset({b"POST", b"PUT", b"PATCH", b"DELETE"})


def _origin_key(raw: str) -> str | None:
    """Reduce a URL to ``scheme://host[:port]`` for allow-list comparison."""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


class CsrfOriginMiddleware:
    """Reject unsafe-method requests whose ``Origin``/``Referer`` is unknown."""

    def __init__(  # type: ignore[no-untyped-def]
        self,
        app,
        *,
        allowed_origins: list[str],
    ) -> None:
        self._app = app
        self._allowed: set[str] = {
            key for key in (_origin_key(o) for o in allowed_origins) if key
        }

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        if scope["type"] != "http" or not self._allowed:
            await self._app(scope, receive, send)
            return
        method = scope.get("method", "").encode("ascii")
        if method not in _UNSAFE_METHODS:
            await self._app(scope, receive, send)
            return
        origin: str | None = None
        referer: str | None = None
        for name, value in scope["headers"]:
            if name == b"origin":
                origin = value.decode("latin-1")
            elif name == b"referer":
                referer = value.decode("latin-1")
        candidate = _origin_key(origin) if origin else _origin_key(referer or "")
        if candidate not in self._allowed:
            await self._reject(scope, send)
            return
        await self._app(scope, receive, send)

    @staticmethod
    async def _reject(scope, send) -> None:  # type: ignore[no-untyped-def]
        state = scope.get("state") or {}
        rid = state.get("request_id") or new_request_id()
        envelope = ApiError(
            request_id=rid,
            error=ErrorDetail(
                code="csrf_origin_rejected",
                message="Origin header missing or not allowed",
            ),
        )
        body = json.dumps(envelope.model_dump(mode="json")).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("latin-1")),
                    (b"x-request-id", rid.encode("latin-1")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
