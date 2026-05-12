"""Soft Redis cache with NullCache fallback."""

from __future__ import annotations

import contextlib
import hashlib
import inspect
import json
import logging
from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

if TYPE_CHECKING:
    from pydantic import BaseModel

    from app.search.schemas import BackendCapabilities

logger = logging.getLogger(__name__)


@runtime_checkable
class Cache(Protocol):
    name: str

    async def get(self, key: str) -> bytes | None: ...
    async def set(self, key: str, value: bytes, ttl: int) -> None: ...
    async def ping(self) -> bool: ...
    async def aclose(self) -> None: ...


class NullCache:
    """No-op cache used whenever Redis is unavailable or disabled."""

    name = "null"

    async def get(self, key: str) -> bytes | None:
        return None

    async def set(self, key: str, value: bytes, ttl: int) -> None:
        return None

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


class RedisCache:
    """Thin wrapper around :mod:`redis.asyncio`."""

    name = "redis"

    def __init__(self, client: Any) -> None:
        self._client = client

    @classmethod
    async def from_url(cls, url: str) -> RedisCache | NullCache:
        try:
            from redis import asyncio as redis_asyncio
        except ImportError:
            logger.warning("redis_unavailable_falling_back_to_null_cache")
            return NullCache()
        client = redis_asyncio.from_url(url, encoding=None, decode_responses=False)
        try:
            ping_result = client.ping()
            if inspect.isawaitable(ping_result):
                await ping_result
        except Exception as exc:
            logger.warning(
                "redis_ping_failed_falling_back_to_null_cache", extra={"error": str(exc)}
            )
            with contextlib.suppress(Exception):
                await client.aclose()
            return NullCache()
        return cls(client)

    async def get(self, key: str) -> bytes | None:
        return cast("bytes | None", await self._client.get(key))

    async def set(self, key: str, value: bytes, ttl: int) -> None:
        await self._client.set(key, value, ex=ttl)

    async def ping(self) -> bool:
        try:
            result = self._client.ping()
            if inspect.isawaitable(result):
                result = await result
            return bool(result)
        except Exception:
            return False

    async def aclose(self) -> None:
        with contextlib.suppress(Exception):
            await self._client.aclose()


def make_cache_key(search_type: str, backend: str, payload: str | bytes) -> str:
    """Stable hash for a serialized request payload.

    Accepts the JSON form (e.g. ``pydantic`` ``model_dump_json``) directly so we
    skip the dict→json round-trip the previous implementation did. ``blake2b``
    is ~2–3x faster than ``sha256`` on short inputs and ample for cache keys
    (no cryptographic adversary on the other side).
    """
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    digest = hashlib.blake2b(payload, digest_size=16).hexdigest()
    return f"search:{search_type}:{backend}:{digest}"


def cache_payload_for_backend(
    request_dict: dict[str, Any], caps: BackendCapabilities | None
) -> str:
    """Project a request dict to the fields that actually shape the upstream call.

    Without this, two requests that differ only in fields the backend ignores
    (e.g. ``language`` against a backend with ``languages=False``) collide on
    the wire but produce distinct cache keys, fragmenting the cache. ``caps=None``
    keeps the full payload — used when the backend is not yet known (auto routing).
    """
    if caps is None:
        # Sort keys so the hash is stable across pydantic dump order changes.
        return json.dumps(request_dict, sort_keys=True, separators=(",", ":"))
    pruned = dict(request_dict)
    if not caps.pagination:
        pruned.pop("page", None)
    if not caps.languages:
        pruned.pop("language", None)
    if not caps.regions:
        pruned.pop("region", None)
    if not caps.safe_search:
        pruned.pop("safe_search", None)
    return json.dumps(pruned, sort_keys=True, separators=(",", ":"))


def cache_payload_from_model(
    request: BaseModel, caps: BackendCapabilities | None
) -> bytes:
    """Canonical bytes for a request model, ready to feed ``make_cache_key``.

    Faster path than ``cache_payload_for_backend`` when no capability pruning
    is needed: ``model_dump_json`` emits bytes directly without the
    ``dict → json.dumps`` round-trip. When ``caps`` requires pruning we still
    go through a dict, but only once per request.
    """
    if caps is None:
        return request.model_dump_json(exclude={"cache"}).encode("utf-8")
    pruned = request.model_dump(exclude={"cache"}, mode="json")
    if not caps.pagination:
        pruned.pop("page", None)
    if not caps.languages:
        pruned.pop("language", None)
    if not caps.regions:
        pruned.pop("region", None)
    if not caps.safe_search:
        pruned.pop("safe_search", None)
    return json.dumps(pruned, sort_keys=True, separators=(",", ":")).encode("utf-8")
