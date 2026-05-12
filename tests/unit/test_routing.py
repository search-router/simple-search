from __future__ import annotations

import pytest

from app.backends.mocks import MockSearchRouterBackend
from app.core.circuit_breaker import CircuitBreaker
from app.core.config import AppConfig, RoutingConfig, RoutingRule, SearchConfig
from app.core.errors import (
    BackendUnavailableError,
    UnsupportedBackendError,
    UnsupportedCapabilityError,
)
from app.search.registry import BackendRegistry
from app.search.router import RoutingService
from app.search.schemas import (
    ImageSearchRequest,
    WebSearchRequest,
)


def _registry(broken_first: bool = False, broken_all: bool = False) -> BackendRegistry:
    sr = _BrokenSearchRouter() if broken_first or broken_all else MockSearchRouterBackend()
    sr.name = "search_router"
    yd = _BrokenAlt() if broken_all else MockSearchRouterBackend()
    yd.name = "alt"
    return BackendRegistry({"search_router": sr, "alt": yd})


class _BrokenSearchRouter(MockSearchRouterBackend):
    async def search_web(self, req, ctx):  # type: ignore[override]
        raise BackendUnavailableError(backend=self.name)

    async def search_images(self, req, ctx):  # type: ignore[override]
        raise BackendUnavailableError(backend=self.name)


class _BrokenAlt(MockSearchRouterBackend):
    async def search_web(self, req, ctx):  # type: ignore[override]
        raise BackendUnavailableError(backend=self.name)

    async def search_images(self, req, ctx):  # type: ignore[override]
        raise BackendUnavailableError(backend=self.name)


def _config(rules: list[RoutingRule] | None = None) -> AppConfig:
    return AppConfig(
        search=SearchConfig(fallback_order=["search_router", "alt"]),
        routing=RoutingConfig(rules=rules or []),
    )


@pytest.mark.asyncio
async def test_explicit_backend_unknown_raises():
    routing = RoutingService(_registry(), CircuitBreaker(), _config())
    with pytest.raises(UnsupportedBackendError):
        await routing.route_web(WebSearchRequest(q="x", backend="nonexistent"))


@pytest.mark.asyncio
async def test_explicit_backend_unsupported_capability_raises():
    """Asking a web-only backend for images via an explicit backend must fail loudly."""

    class _WebOnly(MockSearchRouterBackend):
        def capabilities(self):
            caps = super().capabilities()
            caps.image_search_by_text = False
            return caps

    backend = _WebOnly()
    backend.name = "search_router"
    routing = RoutingService(
        BackendRegistry({"search_router": backend}), CircuitBreaker(), _config()
    )
    with pytest.raises(UnsupportedCapabilityError):
        await routing.route_images(
            ImageSearchRequest(backend="search_router", q="cats"),
        )


@pytest.mark.asyncio
async def test_explicit_failure_does_not_fallback():
    routing = RoutingService(_registry(broken_first=True), CircuitBreaker(), _config())
    with pytest.raises(BackendUnavailableError):
        await routing.route_web(WebSearchRequest(q="x", backend="search_router"))


@pytest.mark.asyncio
async def test_all_backends_fail_raises_aggregate_error():
    routing = RoutingService(_registry(broken_all=True), CircuitBreaker(), _config())
    with pytest.raises(BackendUnavailableError):
        await routing.route_web(WebSearchRequest(q="x", backend="auto"))


@pytest.mark.asyncio
async def test_rule_use_pins_backend():
    rules = [RoutingRule(when={"type": "images"}, use="alt")]
    routing = RoutingService(_registry(), CircuitBreaker(), _config(rules))
    response = await routing.route_images(
        ImageSearchRequest(backend="auto", q="cats", limit=2),
    )
    assert response.backend == "alt"


@pytest.mark.asyncio
async def test_rule_prefers_by_language():
    rules = [
        RoutingRule(when={"language_in": ["ru"]}, prefer="alt", fallback="search_router"),
    ]
    routing = RoutingService(_registry(), CircuitBreaker(), _config(rules))
    response = await routing.route_web(
        WebSearchRequest(q="hello", backend="auto", language="ru-RU", limit=2),
    )
    assert response.backend == "alt"


@pytest.mark.asyncio
async def test_image_routing_falls_back_when_first_breaks():
    routing = RoutingService(_registry(broken_first=True), CircuitBreaker(), _config())
    response = await routing.route_images(
        ImageSearchRequest(q="cats", backend="auto", limit=2),
    )
    assert response.backend == "alt"
    assert response.results


@pytest.mark.asyncio
async def test_empty_registry_raises_unsupported_backend():
    routing = RoutingService(BackendRegistry({}), CircuitBreaker(), _config())
    with pytest.raises(UnsupportedBackendError):
        await routing.route_web(WebSearchRequest(q="x"))


@pytest.mark.asyncio
async def test_auto_skips_backend_lacking_capability():
    """When a capable backend exists, ``auto`` must skip the incapable one rather than fail."""

    class _WebOnly(MockSearchRouterBackend):
        def capabilities(self):
            caps = super().capabilities()
            caps.image_search_by_text = False
            return caps

    sr = _WebOnly()
    sr.name = "search_router"
    yd = MockSearchRouterBackend()
    yd.name = "alt"
    registry = BackendRegistry({"search_router": sr, "alt": yd})
    routing = RoutingService(registry, CircuitBreaker(), _config())
    response = await routing.route_images(
        ImageSearchRequest(backend="auto", q="cats", limit=2),
    )
    # search_router lacks image_search_by_text, so the alt backend is selected.
    assert response.backend == "alt"


@pytest.mark.asyncio
async def test_open_circuit_skips_to_next_backend():
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=60)
    # Trip the breaker for search_router so it is skipped on the next call.
    try:
        async with await breaker.acquire("search_router"):
            raise RuntimeError("trip")
    except RuntimeError:
        pass

    routing = RoutingService(_registry(), breaker, _config())
    response = await routing.route_web(
        WebSearchRequest(q="hello", backend="auto", limit=2),
    )
    assert response.backend == "alt"


@pytest.mark.asyncio
async def test_unicode_query_routes_to_default_when_no_rule_matches():
    """A ``ru`` query with no rule must still resolve through fallback_order."""
    routing = RoutingService(_registry(), CircuitBreaker(), _config())
    response = await routing.route_web(
        WebSearchRequest(q="привет", backend="auto", language="ru-RU", limit=1),
    )
    # fallback_order=["search_router", "alt"]: first one wins.
    assert response.backend == "search_router"


@pytest.mark.asyncio
async def test_rule_use_overrides_fallback_order():
    """``use`` pins the backend, even if it's not first in fallback_order."""
    rules = [RoutingRule(when={"type": "web"}, use="alt")]
    routing = RoutingService(_registry(), CircuitBreaker(), _config(rules))
    response = await routing.route_web(
        WebSearchRequest(q="ok", backend="auto", limit=1),
    )
    assert response.backend == "alt"


@pytest.mark.asyncio
async def test_rule_language_match_is_case_insensitive():
    """``language: 'EN-US'`` must match a rule keyed on ``en``."""
    rules = [
        RoutingRule(when={"language_in": ["en"]}, prefer="alt", fallback="search_router"),
    ]
    routing = RoutingService(_registry(), CircuitBreaker(), _config(rules))
    response = await routing.route_web(
        WebSearchRequest(q="hi", backend="auto", language="EN-US", limit=1),
    )
    assert response.backend == "alt"


@pytest.mark.asyncio
async def test_explicit_unknown_backend_does_not_consult_fallback():
    """Asking for ``backend='nope'`` must not silently fall back to ``alt``."""
    routing = RoutingService(_registry(), CircuitBreaker(), _config())
    with pytest.raises(UnsupportedBackendError):
        await routing.route_web(WebSearchRequest(q="x", backend="nope"))


@pytest.mark.asyncio
async def test_response_request_id_overwritten_with_outer_id():
    """The outer-supplied request_id must end up on the response, not whatever the backend used."""
    routing = RoutingService(_registry(), CircuitBreaker(), _config())
    response = await routing.route_web(
        WebSearchRequest(q="x", backend="auto", limit=1),
        request_id="outer-rid-xyz",
    )
    assert response.request_id == "outer-rid-xyz"


@pytest.mark.asyncio
async def test_routing_skips_rules_that_dont_match_search_kind():
    """A rule keyed to ``type: images`` must not pin the web search."""
    rules = [RoutingRule(when={"type": "images"}, use="alt")]
    routing = RoutingService(_registry(), CircuitBreaker(), _config(rules))
    response = await routing.route_web(
        WebSearchRequest(q="x", backend="auto", limit=1),
    )
    # No web rule matches → falls back to fallback_order[0] (search_router).
    assert response.backend == "search_router"


