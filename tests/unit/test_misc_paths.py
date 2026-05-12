"""Sweep the last narrow uncovered branches in router, parsers, deps, and helpers."""

from __future__ import annotations

import sys
import types

import httpx
import pytest

from app.backends.search_router import SearchRouterBackend

# --- app.api.deps.get_request_id (auto-fill) -------------------------------

def test_get_request_id_generates_when_state_is_empty():
    """When the middleware hasn't run, the dep must mint and store its own id."""
    from starlette.requests import Request

    from app.api.deps import get_request_id

    scope = {"type": "http", "headers": [], "state": {}}
    request = Request(scope)
    rid = get_request_id(request)
    assert rid.startswith("req_")
    # Subsequent calls return the same id (it's stashed onto request.state).
    assert get_request_id(request) == rid


# --- app.api.v1.health (cache.ping False + exception) ----------------------

def test_health_endpoint_marks_redis_down_when_ping_returns_false(client):
    class _DownCache:
        name = "down"

        async def get(self, key):
            return None

        async def set(self, key, value, ttl):
            return None

        async def ping(self):
            return False

        async def aclose(self):
            return None

    client.app.state.cache = _DownCache()
    try:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json()["redis"] == "down"
    finally:
        from app.core.cache import NullCache

        client.app.state.cache = NullCache()


def test_health_endpoint_marks_redis_down_when_ping_raises(client):
    class _BoomCache:
        name = "boom"

        async def get(self, key):
            return None

        async def set(self, key, value, ttl):
            return None

        async def ping(self):
            raise RuntimeError("redis unreachable")

        async def aclose(self):
            return None

    client.app.state.cache = _BoomCache()
    try:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json()["redis"] == "down"
    finally:
        from app.core.cache import NullCache

        client.app.state.cache = NullCache()


# --- app.search.router edge paths ------------------------------------------

@pytest.mark.asyncio
async def test_router_reraises_unsupported_capability_from_backend_call():
    """If a backend's call() itself raises UnsupportedCapabilityError, the router must reraise."""
    from app.backends.base import BaseBackend
    from app.core.circuit_breaker import CircuitBreaker
    from app.core.config import AppConfig, RoutingConfig, SearchConfig
    from app.core.errors import UnsupportedCapabilityError
    from app.search.registry import BackendRegistry
    from app.search.router import RoutingService
    from app.search.schemas import (
        BackendCapabilities,
        ImageSearchRequest,
    )

    class _LiarBackend(BaseBackend):
        name = "liar"

        def capabilities(self):
            # Claims to support image search, but raises when actually invoked.
            return BackendCapabilities(image_search_by_text=True)

        async def search_images(self, req, ctx):
            raise UnsupportedCapabilityError(backend=self.name)

    registry = BackendRegistry({"liar": _LiarBackend()})
    config = AppConfig(
        search=SearchConfig(fallback_order=["liar"]),
        routing=RoutingConfig(rules=[]),
    )
    routing = RoutingService(registry, CircuitBreaker(), config)
    with pytest.raises(UnsupportedCapabilityError):
        await routing.route_images(
            ImageSearchRequest(backend="auto", q="cats"),
        )


@pytest.mark.asyncio
async def test_router_raises_unsupported_when_circuit_open_for_all_candidates():
    """If every candidate is circuit-open, the router still fails clearly."""
    from app.backends.mocks import MockSearchRouterBackend
    from app.core.circuit_breaker import CircuitBreaker
    from app.core.config import AppConfig, RoutingConfig, SearchConfig
    from app.core.errors import BackendError
    from app.search.registry import BackendRegistry
    from app.search.router import RoutingService
    from app.search.schemas import WebSearchRequest

    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=60)
    for name in ("search_router", "alt"):
        try:
            async with await breaker.acquire(name):
                raise RuntimeError("trip")
        except RuntimeError:
            pass

    sr = MockSearchRouterBackend()
    sr.name = "search_router"
    alt = MockSearchRouterBackend()
    alt.name = "alt"
    registry = BackendRegistry({"search_router": sr, "alt": alt})
    config = AppConfig(
        search=SearchConfig(fallback_order=["search_router", "alt"]),
        routing=RoutingConfig(rules=[]),
    )
    routing = RoutingService(registry, breaker, config)
    # The aggregate failure surfaces as the breaker error (a ServiceError subclass).
    with pytest.raises(BackendError):
        await routing.route_web(WebSearchRequest(q="x", backend="auto"))


# --- app.backends.search_router uncovered branches -------------------------

@pytest.mark.asyncio
async def test_search_router_healthcheck_returns_ok_via_probe():
    """Exercises the ``_healthcheck_probe`` ping path (line 125)."""

    def handler(request: httpx.Request) -> httpx.Response:
        # The probe sends a small ``query=ping`` payload.
        import json
        body = json.loads(request.content.decode("utf-8"))
        assert body["query"] == "ping"
        return httpx.Response(200, json={"web": []})

    transport = httpx.MockTransport(handler)
    backend = SearchRouterBackend(api_key="k", http=httpx.AsyncClient(transport=transport))
    health = await backend.healthcheck()
    assert health.status == "ok"


def test_search_router_extract_results_returns_empty_for_unknown_keys():
    """Falls through every candidate key and returns an empty list (line 159)."""
    items = SearchRouterBackend._extract_results({"unrelated": [{"x": 1}]}, key="web")
    assert items == []


# --- app.core.logging idempotency + exc_info -------------------------------

def test_configure_logging_is_idempotent():
    """Calling ``configure_logging`` twice must short-circuit on the second call."""
    import logging

    from app.core.logging import configure_logging

    root = logging.getLogger()
    # Force a fresh setup so the test isn't order-dependent.
    if hasattr(root, "_search_service_configured"):
        delattr(root, "_search_service_configured")

    configure_logging("INFO")
    handler_count = len(root.handlers)
    configure_logging("DEBUG")  # second call must be a no-op
    assert len(root.handlers) == handler_count


def test_json_formatter_renders_exc_info():
    """Records carrying ``exc_info`` must include a stringified traceback in JSON output."""
    import json
    import logging

    from app.core.logging import JsonFormatter

    formatter = JsonFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord(
            name="t",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="failed",
            args=(),
            exc_info=sys.exc_info(),
        )
    out = json.loads(formatter.format(record))
    assert "exc_info" in out
    assert "ValueError" in out["exc_info"]


# --- app.core.cache from_url ImportError fallback --------------------------

@pytest.mark.asyncio
async def test_from_url_falls_back_to_null_when_redis_submodule_missing(monkeypatch):
    """Simulate a ``redis`` package without an ``asyncio`` submodule."""
    from app.core.cache import NullCache, RedisCache

    fake_redis = types.ModuleType("redis")  # no ``asyncio`` attr/submodule
    monkeypatch.setitem(sys.modules, "redis", fake_redis)
    monkeypatch.delitem(sys.modules, "redis.asyncio", raising=False)
    cache = await RedisCache.from_url("redis://x:6379/0")
    assert isinstance(cache, NullCache)


# --- app.main fallback Exception handler -----------------------------------

def test_unhandled_exception_returns_500_envelope():
    """Any unexpected exception must surface as the JSON internal_error envelope."""
    from fastapi import APIRouter
    from fastapi.testclient import TestClient

    from app.core.config import load_config
    from app.main import create_app

    config = load_config(env={})
    app = create_app(config)

    router = APIRouter()

    @router.get("/__boom__")
    def boom():
        raise RuntimeError("kaboom")

    app.include_router(router)
    # ``raise_server_exceptions=False`` lets the registered exception handler
    # produce its JSON envelope rather than re-raising into the test.
    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get("/__boom__")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "internal_error"
    assert body["request_id"]
    # Stack trace must not leak into the response.
    assert "kaboom" not in body["error"]["message"]


# --- app.main RedisCache wiring (lifespan) ---------------------------------

def test_app_lifespan_uses_redis_cache_when_url_resolved(monkeypatch):
    """``main.create_app`` lifespan must instantiate ``RedisCache`` when redis_url is set."""
    from fakeredis import aioredis as _fake
    from fastapi.testclient import TestClient

    from app.core.cache import RedisCache
    from app.core.config import load_config
    from app.main import create_app

    config = load_config(env={})
    config.cache.resolved_redis_url = "redis://fake-redis:6379/0"

    async def _from_url(_url):
        return RedisCache(_fake.FakeRedis(decode_responses=False))

    monkeypatch.setattr(RedisCache, "from_url", classmethod(lambda cls, url: _from_url(url)))

    app = create_app(config)
    with TestClient(app) as test_client:
        response = test_client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json()["redis"] == "ok"
        assert isinstance(app.state.cache, RedisCache)


