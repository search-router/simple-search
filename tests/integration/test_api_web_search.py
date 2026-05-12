from __future__ import annotations


def test_web_search_returns_normalized_payload(client):
    response = client.post(
        "/api/v1/search/web",
        json={"q": "python async", "limit": 5},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["type"] == "web"
    assert len(body["results"]) == 5
    assert body["request_id"].startswith("req_")
    assert body["backend"] == "search_router"
    for result in body["results"]:
        assert "raw" not in result
        assert result["provider"] == "search_router"


def test_web_search_rejects_extra_fields(client):
    response = client.post(
        "/api/v1/search/web",
        json={"q": "ok", "foo": "bar"},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "invalid_request"


def test_web_search_explicit_backend(client):
    response = client.post(
        "/api/v1/search/web",
        json={"q": "search router test", "backend": "search_router", "limit": 3},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["backend"] == "search_router"


def test_web_search_unknown_backend(client):
    response = client.post(
        "/api/v1/search/web",
        json={"q": "x", "backend": "doesnotexist"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_backend"


def test_web_search_cache_write_failure_does_not_break_request(client):
    """If Redis fails on write, the user must still get the search response."""

    class _ExplodingCache:
        name = "exploding"

        async def get(self, key):
            return None

        async def set(self, key, value, ttl):
            raise RuntimeError("redis disconnected")

        async def ping(self):
            return False

        async def aclose(self):
            return None

    client.app.state.cache = _ExplodingCache()
    try:
        response = client.post(
            "/api/v1/search/web",
            json={"q": "still works", "limit": 3},
        )
        assert response.status_code == 200
        assert len(response.json()["results"]) == 3
    finally:
        from app.core.cache import NullCache

        client.app.state.cache = NullCache()


def test_web_search_cache_read_failure_falls_back_to_backend(client):
    class _ReadFailCache:
        name = "read_fail"

        async def get(self, key):
            raise RuntimeError("redis went away")

        async def set(self, key, value, ttl):
            return None

        async def ping(self):
            return False

        async def aclose(self):
            return None

    client.app.state.cache = _ReadFailCache()
    try:
        response = client.post(
            "/api/v1/search/web",
            json={"q": "read failure", "limit": 2},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["cache_hit"] is False
    finally:
        from app.core.cache import NullCache

        client.app.state.cache = NullCache()


def test_web_search_cache_hit_round_trips_results(client):
    """Two identical requests must return the same results, with cache_hit=True on the second."""
    from fakeredis import aioredis as _fake

    from app.core.cache import RedisCache

    fake = _fake.FakeRedis(decode_responses=False)
    client.app.state.cache = RedisCache(fake)
    try:
        first = client.post(
            "/api/v1/search/web",
            json={"q": "cache me", "limit": 3},
        )
        assert first.status_code == 200
        assert first.json()["cache_hit"] is False

        second = client.post(
            "/api/v1/search/web",
            json={"q": "cache me", "limit": 3},
        )
        assert second.status_code == 200
        body = second.json()
        assert body["cache_hit"] is True
        assert [r["url"] for r in body["results"]] == [
            r["url"] for r in first.json()["results"]
        ]
        # The new request_id from the inbound header is used, not the cached one.
        assert body["request_id"] == second.headers["X-Request-Id"]
    finally:
        from app.core.cache import NullCache

        client.app.state.cache = NullCache()


def test_web_search_cache_skipped_for_day_time_range(client):
    """time_range=day must always re-hit the backend (fresh news)."""
    from fakeredis import aioredis as _fake

    from app.core.cache import RedisCache

    fake = _fake.FakeRedis(decode_responses=False)
    client.app.state.cache = RedisCache(fake)
    try:
        for _ in range(2):
            response = client.post(
                "/api/v1/search/web",
                json={"q": "fresh news", "time_range": "day"},
            )
            assert response.status_code == 200
            assert response.json()["cache_hit"] is False
    finally:
        from app.core.cache import NullCache

        client.app.state.cache = NullCache()


def test_web_search_cache_disabled_via_request_field(client):
    from fakeredis import aioredis as _fake

    from app.core.cache import RedisCache

    fake = _fake.FakeRedis(decode_responses=False)
    client.app.state.cache = RedisCache(fake)
    try:
        for _ in range(2):
            response = client.post(
                "/api/v1/search/web",
                json={"q": "no caching plz", "cache": False},
            )
            assert response.status_code == 200
            assert response.json()["cache_hit"] is False
    finally:
        from app.core.cache import NullCache

        client.app.state.cache = NullCache()


def test_web_search_corrupt_cache_entry_falls_through_to_backend(client):
    """A schema-incompatible cache value must NOT 500 — re-fetch from the backend."""

    class _CorruptCache:
        name = "corrupt"

        async def get(self, key):
            return b"not valid json{{{"

        async def set(self, key, value, ttl):
            return None

        async def ping(self):
            return True

        async def aclose(self):
            return None

    client.app.state.cache = _CorruptCache()
    try:
        response = client.post(
            "/api/v1/search/web",
            json={"q": "after corruption", "limit": 2},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["cache_hit"] is False
        assert len(body["results"]) == 2
    finally:
        from app.core.cache import NullCache

        client.app.state.cache = NullCache()


def test_web_search_schema_changed_cache_entry_falls_through(client):
    """An old payload missing required fields must be discarded, not crash."""

    class _StaleSchemaCache:
        name = "stale"

        async def get(self, key):
            # Looks like JSON but lacks all required fields of WebSearchResponse.
            return b'{"foo": "bar"}'

        async def set(self, key, value, ttl):
            return None

        async def ping(self):
            return True

        async def aclose(self):
            return None

    client.app.state.cache = _StaleSchemaCache()
    try:
        response = client.post(
            "/api/v1/search/web",
            json={"q": "stale schema", "limit": 1},
        )
        assert response.status_code == 200
        assert response.json()["cache_hit"] is False
    finally:
        from app.core.cache import NullCache

        client.app.state.cache = NullCache()
