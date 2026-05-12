"""Coverage for ``app.core.cache.RedisCache`` and the ``from_url`` factory.

The integration tests already exercise ``RedisCache`` end-to-end via the API
endpoints; this module covers the remaining branches: the constructor's PING
sniff, fallback to ``NullCache`` when Redis is unreachable, and the small
methods (``get``/``set``/``ping``/``aclose``).
"""

from __future__ import annotations

import sys
import types

import pytest
from fakeredis import aioredis as fake_aioredis

from app.core.cache import NullCache, RedisCache


@pytest.mark.asyncio
async def test_redis_cache_get_set_round_trip():
    cache = RedisCache(fake_aioredis.FakeRedis(decode_responses=False))
    await cache.set("k", b"value", ttl=30)
    assert await cache.get("k") == b"value"
    assert await cache.ping() is True
    await cache.aclose()


@pytest.mark.asyncio
async def test_redis_cache_get_returns_none_for_missing_key():
    cache = RedisCache(fake_aioredis.FakeRedis(decode_responses=False))
    assert await cache.get("missing") is None


@pytest.mark.asyncio
async def test_redis_cache_ping_returns_false_when_client_raises():
    class _BrokenClient:
        async def ping(self):
            raise RuntimeError("redis dropped the line")

        async def aclose(self):
            return None

    cache = RedisCache(_BrokenClient())
    assert await cache.ping() is False
    # aclose must swallow exceptions from underlying clients too
    await cache.aclose()


@pytest.mark.asyncio
async def test_redis_cache_aclose_swallows_errors():
    class _ExplodingClient:
        async def aclose(self):
            raise RuntimeError("nope")

    cache = RedisCache(_ExplodingClient())
    await cache.aclose()  # must not raise


@pytest.mark.asyncio
async def test_redis_cache_ping_handles_sync_client_method():
    """Some redis client variants return a non-awaitable bool from ``ping``."""

    class _SyncPing:
        def ping(self):
            return True

    cache = RedisCache(_SyncPing())
    assert await cache.ping() is True


@pytest.mark.asyncio
async def test_from_url_falls_back_to_null_when_redis_module_missing(monkeypatch):
    """Simulate ``redis`` not being installed — production's optional dep."""
    monkeypatch.setitem(sys.modules, "redis.asyncio", None)
    cache = await RedisCache.from_url("redis://nowhere:6379/0")
    assert isinstance(cache, NullCache)


@pytest.mark.asyncio
async def test_from_url_falls_back_to_null_when_ping_fails(monkeypatch):
    """A reachable factory but a failing PING must downgrade to ``NullCache``."""

    closed: list[bool] = []

    class _Stub:
        async def ping(self):
            raise RuntimeError("can't ping")

        async def aclose(self):
            closed.append(True)

    fake_module = types.SimpleNamespace(from_url=lambda *args, **kwargs: _Stub())
    monkeypatch.setitem(sys.modules, "redis", types.ModuleType("redis"))
    monkeypatch.setitem(sys.modules, "redis.asyncio", fake_module)

    cache = await RedisCache.from_url("redis://x:6379/0")
    assert isinstance(cache, NullCache)
    assert closed == [True]


@pytest.mark.asyncio
async def test_from_url_returns_redis_cache_when_ping_succeeds(monkeypatch):
    class _OkClient:
        def __init__(self):
            self.store: dict[str, bytes] = {}

        async def ping(self):
            return True

        async def get(self, key):
            return self.store.get(key)

        async def set(self, key, value, ex=None):
            self.store[key] = value

        async def aclose(self):
            pass

    fake_module = types.SimpleNamespace(from_url=lambda *args, **kwargs: _OkClient())
    monkeypatch.setitem(sys.modules, "redis", types.ModuleType("redis"))
    monkeypatch.setitem(sys.modules, "redis.asyncio", fake_module)

    cache = await RedisCache.from_url("redis://x:6379/0")
    assert isinstance(cache, RedisCache)
    await cache.set("k", b"v", ttl=10)
    assert await cache.get("k") == b"v"
    assert await cache.ping() is True


# --- NullCache --------------------------------------------------------------

@pytest.mark.asyncio
async def test_null_cache_is_a_total_no_op():
    null = NullCache()
    assert await null.get("anything") is None
    assert await null.set("k", b"v", ttl=10) is None
    assert await null.ping() is True
    await null.aclose()
