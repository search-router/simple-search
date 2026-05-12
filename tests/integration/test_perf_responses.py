"""Integration tests for the response-path optimizations.

Covers the behaviour the perf fixes pinned:
- ``response_model=None`` keeps the JSON output identical and parseable.
- Cache-hit responses still expose ``cache_hit=True`` and a fresh ``request_id``.
- ``/api/v1/backends`` continues to surface circuit_state under the parallelized path.
- ``/api/v1/health`` parallelism still aggregates statuses correctly.
"""

from __future__ import annotations

import asyncio
import time


def test_web_response_payload_shape_unchanged(client):
    """response_model=None must not change the JSON shape clients depend on."""
    response = client.post("/api/v1/search/web", json={"q": "shape", "limit": 3})
    assert response.status_code == 200
    body = response.json()
    # Top-level fields all present.
    for key in (
        "request_id", "query", "backend", "page", "limit",
        "total_results", "response_time_ms", "cache_hit", "type", "results",
    ):
        assert key in body, key
    assert body["type"] == "web"
    assert len(body["results"]) == 3
    for r in body["results"]:
        assert "rank" in r
        assert "url" in r
        assert "provider" in r
        # ``raw`` is excluded from the wire payload.
        assert "raw" not in r


def test_image_response_payload_shape_unchanged(client):
    response = client.post("/api/v1/search/images", json={"q": "kittens", "limit": 4})
    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "images"
    assert len(body["results"]) == 4
    for r in body["results"]:
        assert "image_url" in r
        assert "page_url" in r


def test_web_response_content_type_is_json(client):
    response = client.post("/api/v1/search/web", json={"q": "ct"})
    assert response.headers["content-type"].startswith("application/json")


def test_web_cache_hit_reuses_bytes_but_patches_ids(client):
    """A cache hit must surface ``cache_hit=True`` with the *current* request id."""
    from fakeredis import aioredis as _fake

    from app.core.cache import NullCache, RedisCache

    fake = _fake.FakeRedis(decode_responses=False)
    client.app.state.cache = RedisCache(fake)
    try:
        first = client.post("/api/v1/search/web", json={"q": "patch ids", "limit": 2})
        assert first.status_code == 200
        first_body = first.json()
        assert first_body["cache_hit"] is False

        second = client.post(
            "/api/v1/search/web",
            json={"q": "patch ids", "limit": 2},
            headers={"X-Request-Id": "req_pinned_test_value"},
        )
        body = second.json()
        assert body["cache_hit"] is True
        # The cached payload's request_id was rewritten to the inbound id.
        assert body["request_id"] == "req_pinned_test_value"
        # Result set survives the round-trip.
        assert [r["url"] for r in body["results"]] == [
            r["url"] for r in first_body["results"]
        ]
    finally:
        client.app.state.cache = NullCache()


def test_health_parallelizes_backend_probes(client, monkeypatch):
    """Probes must run concurrently — wall time must not stack linearly."""
    from app.search.schemas import BackendCapabilities, BackendHealth

    class _Sleepy:
        is_mock = False

        def __init__(self, name: str) -> None:
            self.name = name

        async def healthcheck(self):
            await asyncio.sleep(0.05)
            return BackendHealth(status="ok")

        def capabilities(self):
            return BackendCapabilities()

    routing = client.app.state.routing
    saved = dict(routing.registry._backends)
    routing.registry._backends.clear()
    for name in ("a", "b", "c", "d"):
        routing.registry._backends[name] = _Sleepy(name)
    try:
        started = time.monotonic()
        response = client.get("/api/v1/health")
        elapsed = time.monotonic() - started
        assert response.status_code == 200
        body = response.json()
        assert body["backends"] == {"a": "ok", "b": "ok", "c": "ok", "d": "ok"}
        # Serial would be ~0.20s; parallel should land well under 0.15s even
        # on a busy laptop.
        assert elapsed < 0.15, elapsed
    finally:
        routing.registry._backends.clear()
        routing.registry._backends.update(saved)


def test_backends_listing_parallelizes_probes(client):
    """The listing endpoint also goes through ``asyncio.gather``."""
    from app.search.schemas import BackendCapabilities, BackendHealth

    class _Sleepy:
        is_mock = False

        def __init__(self, name: str) -> None:
            self.name = name

        async def healthcheck(self):
            await asyncio.sleep(0.05)
            return BackendHealth(status="ok")

        def capabilities(self):
            return BackendCapabilities()

    routing = client.app.state.routing
    saved = dict(routing.registry._backends)
    routing.registry._backends.clear()
    for name in ("a", "b", "c", "d"):
        routing.registry._backends[name] = _Sleepy(name)
    try:
        started = time.monotonic()
        response = client.get("/api/v1/backends")
        elapsed = time.monotonic() - started
        assert response.status_code == 200
        names = sorted(b["name"] for b in response.json()["backends"])
        assert names == ["a", "b", "c", "d"]
        assert elapsed < 0.15, elapsed
    finally:
        routing.registry._backends.clear()
        routing.registry._backends.update(saved)


def test_health_one_raising_backend_does_not_block_others(client):
    """A single misbehaving probe must not abort the gather."""
    from app.search.schemas import BackendCapabilities, BackendHealth

    class _Broken:
        name = "broken"
        is_mock = False

        async def healthcheck(self):
            raise RuntimeError("boom")

        def capabilities(self):
            return BackendCapabilities()

    class _OK:
        name = "ok"
        is_mock = False

        async def healthcheck(self):
            return BackendHealth(status="ok")

        def capabilities(self):
            return BackendCapabilities()

    routing = client.app.state.routing
    saved = dict(routing.registry._backends)
    routing.registry._backends.clear()
    routing.registry._backends["broken"] = _Broken()
    routing.registry._backends["ok"] = _OK()
    try:
        body = client.get("/api/v1/health").json()
        assert body["backends"] == {"broken": "down", "ok": "ok"}
        assert body["status"] == "degraded"
    finally:
        routing.registry._backends.clear()
        routing.registry._backends.update(saved)
