"""Search backend protocol and a base ABC with shared helpers."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

import httpx

from app.core.errors import (
    BackendAuthError,
    BackendBadRequestError,
    BackendBadResponseError,
    BackendQuotaError,
    BackendServerError,
    BackendTimeoutError,
    BackendUnavailableError,
)
from app.search.schemas import (
    BackendCapabilities,
    BackendHealth,
    ImageSearchRequest,
    ImageSearchResponse,
    WebSearchRequest,
    WebSearchResponse,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_RESPONSE_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class BackendContext:
    """Per-call runtime context passed to every backend method."""

    request_id: str
    started_at: float


@runtime_checkable
class SearchBackend(Protocol):
    name: str

    async def search_web(
        self, req: WebSearchRequest, ctx: BackendContext
    ) -> WebSearchResponse: ...

    async def search_images(
        self, req: ImageSearchRequest, ctx: BackendContext
    ) -> ImageSearchResponse: ...

    async def healthcheck(self) -> BackendHealth: ...

    def capabilities(self) -> BackendCapabilities: ...


class BaseBackend(ABC):
    """Default implementations shared by every adapter."""

    name: str = "unknown"
    is_mock: bool = False

    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self._http = http
        self._last_health: BackendHealth | None = None
        self._max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError(f"Backend {self.name!r} requires an httpx client")
        return self._http

    @abstractmethod
    def capabilities(self) -> BackendCapabilities: ...

    async def healthcheck(self) -> BackendHealth:
        started = time.monotonic()
        try:
            await self._healthcheck_probe()
        except Exception as exc:
            health = BackendHealth(
                status="down",
                latency_ms=int((time.monotonic() - started) * 1000),
                last_checked=datetime.now(UTC),
                last_error=str(exc),
            )
        else:
            health = BackendHealth(
                status="ok",
                latency_ms=int((time.monotonic() - started) * 1000),
                last_checked=datetime.now(UTC),
                last_error=None,
            )
        self._last_health = health
        return health

    async def _healthcheck_probe(self) -> None:  # noqa: B027
        """Override in subclasses with a cheap call. Default is a no-op."""

    @staticmethod
    def map_http_error(status: int, *, backend: str, body: str | None = None) -> Exception:
        details: dict[str, Any] = {"http_status": status}
        if body:
            details["body_preview"] = body[:240]
        if status == 400:
            return BackendBadRequestError(backend=backend, details=details)
        if status in (401, 403):
            return BackendAuthError(backend=backend, details=details)
        if status in (402, 429):
            return BackendQuotaError(backend=backend, details=details)
        if status == 503:
            return BackendUnavailableError(backend=backend, details=details)
        if 500 <= status < 600:
            return BackendServerError(backend=backend, details=details)
        return BackendBadRequestError(backend=backend, details=details)

    @staticmethod
    def map_transport_error(error: BaseException, *, backend: str) -> Exception:
        if isinstance(error, httpx.TimeoutException):
            return BackendTimeoutError(backend=backend, details={"reason": str(error)})
        return BackendUnavailableError(backend=backend, details={"reason": str(error)})

    def enforce_response_size(self, response: httpx.Response) -> None:
        """Reject upstream payloads larger than ``self._max_response_bytes``.

        httpx already buffers the body by default, so this is the parse-time
        guard against malicious or runaway upstreams — not a network-level cap.
        """
        cap = self._max_response_bytes
        if cap <= 0:
            return
        declared = response.headers.get("content-length")
        if declared and declared.isdigit():
            length = int(declared)
            if length > cap:
                raise BackendBadResponseError(
                    backend=self.name,
                    details={"reason": "response_too_large", "content_length": length},
                )
            # Trust the declared length when it's present and within cap — saves
            # an extra ``len()`` on the buffered body for the common path.
            return
        size = len(response.content)
        if size > cap:
            raise BackendBadResponseError(
                backend=self.name,
                details={"reason": "response_too_large", "bytes": size},
            )
