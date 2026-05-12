"""Cache failure-path coverage for ``POST /api/v1/search/images``."""

from __future__ import annotations


def test_image_search_cache_read_failure_falls_back_to_backend(client):
    class _ReadFailCache:
        name = "read_fail"

        async def get(self, key):
            raise RuntimeError("redis read died")

        async def set(self, key, value, ttl):
            return None

        async def ping(self):
            return False

        async def aclose(self):
            return None

    client.app.state.cache = _ReadFailCache()
    try:
        response = client.post(
            "/api/v1/search/images",
            json={"q": "cats", "limit": 2},
        )
        assert response.status_code == 200
        assert response.json()["cache_hit"] is False
    finally:
        from app.core.cache import NullCache

        client.app.state.cache = NullCache()


def test_image_search_cache_write_failure_does_not_break_request(client):
    class _WriteFailCache:
        name = "write_fail"

        async def get(self, key):
            return None

        async def set(self, key, value, ttl):
            raise RuntimeError("redis write died")

        async def ping(self):
            return False

        async def aclose(self):
            return None

    client.app.state.cache = _WriteFailCache()
    try:
        response = client.post(
            "/api/v1/search/images",
            json={"q": "kittens", "limit": 2},
        )
        assert response.status_code == 200
        assert response.json()["cache_hit"] is False
    finally:
        from app.core.cache import NullCache

        client.app.state.cache = NullCache()


def test_image_search_cache_hit_round_trips_results(client):
    """Identical requests return the cached response with cache_hit=True."""
    from fakeredis import aioredis as _fake

    from app.core.cache import RedisCache

    fake = _fake.FakeRedis(decode_responses=False)
    client.app.state.cache = RedisCache(fake)
    try:
        first = client.post(
            "/api/v1/search/images",
            json={"q": "cache me images", "limit": 3},
        )
        assert first.status_code == 200
        assert first.json()["cache_hit"] is False

        second = client.post(
            "/api/v1/search/images",
            json={"q": "cache me images", "limit": 3},
        )
        assert second.status_code == 200
        body = second.json()
        assert body["cache_hit"] is True
        assert [r["image_url"] for r in body["results"]] == [
            r["image_url"] for r in first.json()["results"]
        ]
        assert body["request_id"] == second.headers["X-Request-Id"]
    finally:
        from app.core.cache import NullCache

        client.app.state.cache = NullCache()


def test_image_search_cache_skipped_for_day_time_range(client):
    """``time_range=day`` must always re-hit the backend (fresh news flow)."""
    from fakeredis import aioredis as _fake

    from app.core.cache import RedisCache

    fake = _fake.FakeRedis(decode_responses=False)
    client.app.state.cache = RedisCache(fake)
    try:
        for _ in range(2):
            response = client.post(
                "/api/v1/search/images",
                json={"q": "fresh image", "time_range": "day"},
            )
            assert response.status_code == 200
            assert response.json()["cache_hit"] is False
    finally:
        from app.core.cache import NullCache

        client.app.state.cache = NullCache()


