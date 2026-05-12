"""Tests for the in-process phrase-bid pool cache (fix #4).

The cache memoizes ``AdsStore.list_phrase_bids_with_tokens`` for a short TTL,
saves a SQLite scan on every ``/search`` auction, and pre-builds the
``frozenset`` of tokens per bid. It must invalidate on upsert/delete.
"""

from __future__ import annotations

import pytest

from app.ads import storage as storage_module
from app.ads.storage import AdsStore


@pytest.fixture
async def store(tmp_path):
    s = await AdsStore.open(str(tmp_path / "phrase_pool.sqlite"))
    await s.init_schema()
    yield s
    await s.aclose()


async def _make_user(store: AdsStore, name: str, wallet: int = 1000):
    return await store.create_user(
        username=name, password_hash="hash:dummy", wallet=wallet
    )


async def _place_phrase(store: AdsStore, user_id: int, q: str, tokens: str, amount: int):
    await store.upsert_bid(
        user_id=user_id,
        query_normalized=q,
        query_tokens=tokens,
        match_mode="phrase",
        title=f"ad-{q}",
        url=f"https://example.com/{q.replace(' ', '-')}",
        snippet="",
        amount=amount,
    )


@pytest.mark.asyncio
async def test_phrase_pool_cache_returns_same_snapshot_within_ttl(store):
    """Within the TTL window the same list object must be returned."""
    user = await _make_user(store, "u1")
    await _place_phrase(store, user.id, "pizza dubai", "pizza dubai", 10)
    a = await store.list_phrase_bids_with_tokens()
    b = await store.list_phrase_bids_with_tokens()
    assert a is b
    assert len(a) == 1
    bid, tokens = a[0]
    assert bid.title == "ad-pizza dubai"
    assert tokens == frozenset({"pizza", "dubai"})


@pytest.mark.asyncio
async def test_phrase_pool_cache_invalidates_on_upsert(store):
    """A new phrase bid must show up on the next call, not after the TTL."""
    user = await _make_user(store, "u1")
    snapshot = await store.list_phrase_bids_with_tokens()
    assert snapshot == []
    await _place_phrase(store, user.id, "milk", "milk", 5)
    fresh = await store.list_phrase_bids_with_tokens()
    assert len(fresh) == 1


@pytest.mark.asyncio
async def test_phrase_pool_cache_invalidates_on_delete(store):
    user = await _make_user(store, "u1")
    await _place_phrase(store, user.id, "milk", "milk", 5)
    await store.list_phrase_bids_with_tokens()  # warm cache
    await store.delete_bid(user.id, "milk", "phrase")
    fresh = await store.list_phrase_bids_with_tokens()
    assert fresh == []


@pytest.mark.asyncio
async def test_exact_bid_writes_do_not_invalidate_phrase_pool(store):
    """An exact-mode bid is not part of the phrase pool, so writing it must
    not bust the phrase cache (cache hit must keep the same snapshot)."""
    user = await _make_user(store, "u1")
    await _place_phrase(store, user.id, "x", "x", 5)
    cached = await store.list_phrase_bids_with_tokens()
    await store.upsert_bid(
        user_id=user.id,
        query_normalized="cake",
        query_tokens="cake",
        match_mode="exact",
        title="cake",
        url="https://example.com/cake",
        snippet="",
        amount=3,
    )
    same = await store.list_phrase_bids_with_tokens()
    assert same is cached


@pytest.mark.asyncio
async def test_phrase_pool_cache_ttl_expiry_reads_fresh(store, monkeypatch):
    """Once the TTL elapses, the cache must re-read from SQLite."""
    user = await _make_user(store, "u1")
    await _place_phrase(store, user.id, "x", "x", 5)
    first = await store.list_phrase_bids_with_tokens()
    # Jump time forward beyond the TTL.
    real_monotonic = storage_module.time.monotonic
    bumped = real_monotonic() + storage_module._PHRASE_POOL_TTL_SECONDS + 1
    monkeypatch.setattr(storage_module.time, "monotonic", lambda: bumped)
    second = await store.list_phrase_bids_with_tokens()
    assert second is not first
    assert len(second) == len(first)


@pytest.mark.asyncio
async def test_phrase_pool_includes_pre_built_token_set(store):
    """Token frozensets must be ready-to-use for the auction filter."""
    user = await _make_user(store, "u1")
    await _place_phrase(store, user.id, "pizza dubai", "pizza dubai", 10)
    snapshot = await store.list_phrase_bids_with_tokens()
    _bid, tokens = snapshot[0]
    assert isinstance(tokens, frozenset)
    # Auction-time path: subset check works without re-splitting strings.
    assert tokens.issubset({"where", "to", "get", "pizza", "near", "dubai"})
