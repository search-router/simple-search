"""In-memory per-IP rate limiter (fixed window, 60s)."""

from __future__ import annotations

import json
import time
from collections import defaultdict

from app.core.errors import ApiError, ErrorDetail, RateLimitedError
from app.core.ids import new_request_id


class _Window:
    __slots__ = ("count", "reset_at")

    def __init__(self) -> None:
        self.count = 0
        self.reset_at = 0.0


class RateLimitMiddleware:
    """Fixed 60-second window per client IP. Process-local — not for multi-replica.

    Pure ASGI middleware: skips ``BaseHTTPMiddleware``'s anyio-bridged stream
    wrapper, which roughly doubles request overhead on tiny endpoints.

    Lock-free: asyncio is single-threaded, and the window read+write below
    contains no ``await``, so concurrent requests can't interleave inside it.
    """

    # Hard cap on tracked clients. Above this, expired windows are swept; if
    # everyone is still active the oldest reset is dropped to keep memory bounded.
    _MAX_TRACKED = 10_000
    _SWEEP_EVERY = 1024

    def __init__(  # type: ignore[no-untyped-def]
        self,
        app,
        *,
        limit_per_minute: int,
        trust_forwarded_for: bool = False,
    ) -> None:
        self._app = app
        self._limit = max(limit_per_minute, 0)
        self._trust_xff = trust_forwarded_for
        self._windows: dict[str, _Window] = defaultdict(_Window)
        self._calls_since_sweep = 0

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        if scope["type"] != "http" or self._limit == 0:
            await self._app(scope, receive, send)
            return

        key = self._client_key(scope, trust_xff=self._trust_xff)
        now = time.monotonic()
        self._maybe_sweep(now)
        window = self._windows[key]
        if now >= window.reset_at:
            window.count = 0
            window.reset_at = now + 60.0
        if window.count >= self._limit:
            retry_after = max(int(window.reset_at - now), 1)
            await self._too_many(scope, send, retry_after)
            return
        window.count += 1
        await self._app(scope, receive, send)

    def _maybe_sweep(self, now: float) -> None:
        """Drop expired windows so a flood of one-shot IPs can't grow the dict forever."""
        self._calls_since_sweep += 1
        if (
            self._calls_since_sweep < self._SWEEP_EVERY
            and len(self._windows) < self._MAX_TRACKED
        ):
            return
        self._calls_since_sweep = 0
        expired = [k for k, w in self._windows.items() if now >= w.reset_at]
        for k in expired:
            del self._windows[k]
        # Hostile clients can also keep windows live; cap by oldest reset_at.
        if len(self._windows) > self._MAX_TRACKED:
            overflow = len(self._windows) - self._MAX_TRACKED
            victims = sorted(self._windows.items(), key=lambda kv: kv[1].reset_at)[:overflow]
            for k, _ in victims:
                del self._windows[k]

    @staticmethod
    def _client_key(scope, *, trust_xff: bool) -> str:  # type: ignore[no-untyped-def]
        if trust_xff:
            for name, value in scope["headers"]:
                if name == b"x-forwarded-for":
                    decoded: str = value.decode("latin-1")
                    return decoded.split(",")[0].strip()
        client = scope.get("client")
        return client[0] if client else "unknown"

    @staticmethod
    async def _too_many(scope, send, retry_after: int) -> None:  # type: ignore[no-untyped-def]
        state = scope.get("state") or {}
        rid = state.get("request_id") or new_request_id()
        exc = RateLimitedError()
        envelope = ApiError(
            request_id=rid,
            error=ErrorDetail(code=exc.code, message=exc.message),
        )
        body = json.dumps(envelope.model_dump(mode="json")).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": exc.http_status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("latin-1")),
                    (b"retry-after", str(retry_after).encode("latin-1")),
                    (b"x-request-id", rid.encode("latin-1")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
