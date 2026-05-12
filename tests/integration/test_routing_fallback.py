from __future__ import annotations

import pytest

from app.backends.mocks import MockSearchRouterBackend
from app.core.circuit_breaker import CircuitBreaker
from app.core.config import load_config
from app.core.errors import BackendUnavailableError
from app.search.registry import BackendRegistry
from app.search.router import RoutingService
from app.search.schemas import WebSearchRequest


class _BrokenSearchRouter(MockSearchRouterBackend):
    async def search_web(self, req, ctx):  # type: ignore[override]
        raise BackendUnavailableError(backend=self.name)


@pytest.mark.asyncio
async def test_auto_falls_back_to_alt_backend(tmp_path):
    config = load_config(tmp_path / "missing.yaml", env={})

    sr = _BrokenSearchRouter()
    sr.name = "search_router"
    alt = MockSearchRouterBackend()
    alt.name = "alt"
    registry = BackendRegistry({"search_router": sr, "alt": alt})
    breaker = CircuitBreaker(failure_threshold=10, recovery_timeout_seconds=60)
    config.search.fallback_order = ["search_router", "alt"]
    routing = RoutingService(registry, breaker, config)

    response = await routing.route_web(
        WebSearchRequest(q="hello", backend="auto", limit=3),
        request_id="req_test",
    )
    assert response.backend == "alt"
    assert response.results
