"""Routing service — picks a backend, applies fallback under a circuit breaker."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

from app.backends.base import BackendContext, SearchBackend
from app.core.circuit_breaker import CircuitBreaker
from app.core.config import AppConfig
from app.core.errors import (
    BackendError,
    CircuitOpenError,
    ServiceError,
    UnsupportedBackendError,
    UnsupportedCapabilityError,
)
from app.core.ids import new_request_id
from app.search.registry import BackendRegistry
from app.search.schemas import (
    BackendCapabilities,
    ImageSearchRequest,
    ImageSearchResponse,
    WebSearchRequest,
    WebSearchResponse,
)

logger = logging.getLogger(__name__)

SearchKind = Literal["web", "images"]
ResponseT = TypeVar("ResponseT", WebSearchResponse, ImageSearchResponse)
RequestT = WebSearchRequest | ImageSearchRequest


@dataclass(frozen=True, slots=True)
class _PreparedRule:
    """Routing rule with its match conditions pre-lowered once at startup."""

    kind: str | None
    languages: frozenset[str] | None
    use: str | None
    prefer: str | None
    fallback: str | None


class RoutingService:
    """High-level entry point used by API endpoints and UI handlers."""

    def __init__(
        self,
        registry: BackendRegistry,
        breaker: CircuitBreaker,
        config: AppConfig,
    ) -> None:
        self._registry = registry
        self._breaker = breaker
        self._config = config
        self._rules: tuple[_PreparedRule, ...] = tuple(
            _prepare_rule(rule) for rule in config.routing.rules
        )

    @property
    def registry(self) -> BackendRegistry:
        return self._registry

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker

    async def route_web(
        self, req: WebSearchRequest, request_id: str | None = None
    ) -> WebSearchResponse:
        return await self._route(
            "web",
            req,
            request_id=request_id,
            requires=_make_requires(req, kind_cap=lambda c: c.web_search),
            call=lambda be, ctx: be.search_web(req, ctx),
        )

    async def route_images(
        self, req: ImageSearchRequest, request_id: str | None = None
    ) -> ImageSearchResponse:
        return await self._route(
            "images",
            req,
            request_id=request_id,
            requires=_make_requires(req, kind_cap=lambda c: c.image_search_by_text),
            call=lambda be, ctx: be.search_images(req, ctx),
        )

    async def _route(
        self,
        kind: SearchKind,
        req: RequestT,
        *,
        request_id: str | None,
        requires: Callable[[BackendCapabilities], bool],
        call: Callable[[SearchBackend, BackendContext], Awaitable[ResponseT]],
    ) -> ResponseT:
        candidates = self._candidates(kind, req)
        if not candidates:
            raise UnsupportedBackendError(
                f"No backend available for {kind}",
                details={"requested": req.backend, "kind": kind},
            )

        last_exc: BaseException | None = None
        attempts: list[str] = []
        for name in candidates:
            backend = self._registry.get(name)
            if not requires(backend.capabilities()):
                if req.backend not in ("auto", ""):
                    raise UnsupportedCapabilityError(
                        f"Backend {name!r} does not support {kind}",
                        backend=name,
                    )
                continue
            attempts.append(name)
            outer_id = request_id or new_request_id()
            ctx = BackendContext(request_id=outer_id, started_at=time.monotonic())
            try:
                async with await self._breaker.acquire(name):
                    response = await call(backend, ctx)
            except CircuitOpenError as exc:
                last_exc = exc
                logger.info("circuit_open_skipping_backend", extra={"backend": name})
                continue
            except UnsupportedCapabilityError:
                raise
            except BackendError as exc:
                last_exc = exc
                logger.warning(
                    "backend_call_failed",
                    extra={"backend": name, "error": exc.code, "error_message": exc.message},
                )
                if req.backend not in ("auto", ""):
                    raise
                continue
            response.request_id = outer_id
            return response

        if isinstance(last_exc, ServiceError):
            raise last_exc
        if last_exc is not None:
            raise BackendError(
                f"All backends failed: {attempts}",
                details={"attempts": attempts},
            ) from last_exc
        raise UnsupportedBackendError(
            f"No usable backend for {kind}", details={"attempts": attempts}
        )

    def _candidates(self, kind: SearchKind, req: RequestT) -> list[str]:
        registered = self._registry.names()
        if not registered:
            return []
        explicit = getattr(req, "backend", "auto")
        if explicit and explicit not in ("auto", ""):
            return [explicit] if explicit in registered else []

        prefer, fallback = self._rule_match(kind, req)
        seen: set[str] = set()
        ordered: list[str] = []
        for name in (prefer, fallback, *self._config.search.fallback_order, *registered):
            if name and name not in seen and name in registered:
                seen.add(name)
                ordered.append(name)
        return ordered

    def _rule_match(self, kind: SearchKind, req: RequestT) -> tuple[str | None, str | None]:
        language = (getattr(req, "language", None) or "").split("-")[0].lower()
        for rule in self._rules:
            if rule.kind is not None and rule.kind != kind:
                continue
            if rule.languages is not None and language not in rule.languages:
                continue
            if rule.use:
                return rule.use, None
            return rule.prefer, rule.fallback
        return None, None


def _make_requires(
    req: RequestT,
    *,
    kind_cap: Callable[[BackendCapabilities], bool],
) -> Callable[[BackendCapabilities], bool]:
    """Build a capability gate that also enforces request-specific needs.

    A backend that cannot paginate must not silently return page-0 results
    when ``page > 0`` is asked. ``language``/``region`` stay best-effort
    hints — backends that ignore them still produce useful results, and
    requiring caps here would over-constrain auto routing.
    """
    needs_pagination = getattr(req, "page", 0) > 0

    def _check(caps: BackendCapabilities) -> bool:
        if not kind_cap(caps):
            return False
        if needs_pagination and not caps.pagination:
            return False
        return True

    return _check


def _prepare_rule(rule: Any) -> _PreparedRule:
    when = rule.when or {}
    kind = when.get("type")
    languages: frozenset[str] | None = None
    if "language_in" in when:
        languages = frozenset(str(x).lower() for x in when["language_in"])
    return _PreparedRule(
        kind=str(kind) if kind is not None else None,
        languages=languages,
        use=rule.use,
        prefer=rule.prefer,
        fallback=rule.fallback,
    )
