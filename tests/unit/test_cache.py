from __future__ import annotations

import json

import pytest

from app.core.cache import NullCache, make_cache_key


def _canonical(payload: dict) -> str:
    """Canonical JSON shape — what callers (pydantic ``model_dump_json``) produce."""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def test_make_cache_key_stable_under_reordering():
    a = make_cache_key("web", "search_router", _canonical({"q": "foo", "page": 0, "limit": 10}))
    b = make_cache_key("web", "search_router", _canonical({"limit": 10, "q": "foo", "page": 0}))
    assert a == b


def test_make_cache_key_distinct_per_backend():
    a = make_cache_key("web", "alt", _canonical({"q": "foo"}))
    b = make_cache_key("web", "search_router", _canonical({"q": "foo"}))
    assert a != b


@pytest.mark.asyncio
async def test_null_cache_is_a_no_op():
    cache = NullCache()
    assert await cache.get("anything") is None
    await cache.set("anything", b"x", 60)
    assert await cache.ping() is True
    await cache.aclose()


def test_make_cache_key_handles_unicode_query():
    a = make_cache_key("web", "alt", _canonical({"q": "Привет"}))
    b = make_cache_key("web", "alt", _canonical({"q": "Привет"}))
    assert a == b
    c = make_cache_key("web", "alt", _canonical({"q": "Hello"}))
    assert a != c


def test_make_cache_key_distinct_per_search_type():
    a = make_cache_key("web", "alt", _canonical({"q": "x"}))
    b = make_cache_key("images", "alt", _canonical({"q": "x"}))
    assert a != b


def test_make_cache_key_treats_none_as_distinct_from_missing():
    """``q=None`` and ``q absent`` yield different cache keys, matching JSON semantics."""
    a = make_cache_key("web", "alt", _canonical({"q": "x", "lang": None}))
    b = make_cache_key("web", "alt", _canonical({"q": "x"}))
    assert a != b


def test_make_cache_key_distinct_per_nested_payload():
    """Nested dicts (image_filters) must differentiate cache keys."""
    a = make_cache_key(
        "images", "alt", _canonical({"q": "x", "image_filters": {"size": "large"}})
    )
    b = make_cache_key(
        "images", "alt", _canonical({"q": "x", "image_filters": {"size": "small"}})
    )
    assert a != b


def test_make_cache_key_returns_predictable_prefix():
    key = make_cache_key("web", "alt", _canonical({"q": "ok"}))
    assert key.startswith("search:web:alt:")
    # blake2b with digest_size=16 → 32 hex chars.
    assert len(key.split(":")[-1]) == 32


def test_make_cache_key_accepts_bytes():
    a = make_cache_key("web", "alt", _canonical({"q": "ok"}))
    b = make_cache_key("web", "alt", _canonical({"q": "ok"}).encode("utf-8"))
    assert a == b
