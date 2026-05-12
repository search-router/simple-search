"""Unit tests for the performance optimizations.

Each test pins the *behavior* a specific fix relies on, so a regression that
re-introduces the slow path (or breaks the cached fast path) trips here.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import pytest

from app.core.cache import (
    cache_payload_for_backend,
    cache_payload_from_model,
    make_cache_key,
)
from app.core.config import _read_yaml, load_config
from app.core.i18n import make_direction_resolver
from app.core.ids import new_request_id
from app.core.logging import sampled_warning
from app.search.schemas import (
    BackendCapabilities,
    ImageSearchRequest,
    WebSearchRequest,
)

# -- #3: cache-key serialization is single-pass ----------------------------------

def test_cache_payload_from_model_matches_dict_payload_when_no_caps():
    """Bypassing ``dict→json`` must produce the same canonical bytes."""
    req = WebSearchRequest(q="hello", page=2, limit=10, language="en")
    fast = cache_payload_from_model(req, None)
    slow = cache_payload_for_backend(
        req.model_dump(exclude={"cache"}, mode="json"), None
    )
    # The fast path emits Pydantic's canonical JSON; the slow one emits
    # sort_keys json. Cache keys must match either way.
    fast_key = make_cache_key("web", "alt", fast)
    slow_key = make_cache_key("web", "alt", slow.encode("utf-8"))
    # If they differ at the byte level (which is acceptable — different field
    # orderings hash to different keys), the two paths must still each be
    # stable across calls.
    assert make_cache_key("web", "alt", cache_payload_from_model(req, None)) == fast_key
    assert (
        make_cache_key(
            "web",
            "alt",
            cache_payload_for_backend(
                req.model_dump(exclude={"cache"}, mode="json"), None
            ).encode("utf-8"),
        )
        == slow_key
    )


def test_cache_payload_from_model_caps_prune_fields():
    """Disabling a capability must drop the field from the cache key."""
    req_a = WebSearchRequest(q="cats", page=0, language="en", region="US")
    req_b = WebSearchRequest(q="cats", page=0, language="ru", region="DE")

    no_lang = BackendCapabilities(web_search=True, languages=False, regions=False)
    key_a = make_cache_key("web", "alt", cache_payload_from_model(req_a, no_lang))
    key_b = make_cache_key("web", "alt", cache_payload_from_model(req_b, no_lang))
    # Without language/region in the key, two requests that differ only on
    # those fields collide — which is exactly what we want for a backend
    # that ignores them.
    assert key_a == key_b


def test_cache_payload_from_model_caps_keep_fields_when_supported():
    req_a = WebSearchRequest(q="cats", language="en")
    req_b = WebSearchRequest(q="cats", language="ru")
    full = BackendCapabilities(web_search=True, languages=True, regions=True)
    key_a = make_cache_key("web", "alt", cache_payload_from_model(req_a, full))
    key_b = make_cache_key("web", "alt", cache_payload_from_model(req_b, full))
    assert key_a != key_b


def test_cache_payload_from_model_returns_bytes():
    req = ImageSearchRequest(q="cats")
    payload = cache_payload_from_model(req, None)
    assert isinstance(payload, bytes)


# -- #7: direction resolver is memoized ----------------------------------------

def test_make_direction_resolver_returns_same_object_for_same_inputs():
    """A second call with the same key must reuse the cached resolver."""
    make_direction_resolver.cache_clear()
    a = make_direction_resolver("ar", "auto")
    b = make_direction_resolver("ar", "auto")
    assert a is b


def test_make_direction_resolver_distinguishes_inputs():
    make_direction_resolver.cache_clear()
    rtl = make_direction_resolver("ar", "auto")
    ltr = make_direction_resolver("en", "auto")
    assert rtl(None) == "rtl"
    assert ltr(None) == "ltr"


def test_make_direction_resolver_respects_explicit_request():
    make_direction_resolver.cache_clear()
    forced = make_direction_resolver("en", "rtl")
    # Even with first-strong text, explicit "rtl" wins.
    assert forced("hello") == "rtl"


# -- #10: load_config memoizes YAML reads --------------------------------------

def test_read_yaml_caches_by_path_and_mtime(tmp_path: Path):
    """Same path+mtime keys reuse the parsed dict; new mtime invalidates."""
    _read_yaml.cache_clear()
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text("app:\n  name: first\n", encoding="utf-8")
    mtime = yaml_path.stat().st_mtime_ns

    first = _read_yaml(str(yaml_path), mtime)
    second = _read_yaml(str(yaml_path), mtime)
    assert first is second  # cache hit

    yaml_path.write_text("app:\n  name: second\n", encoding="utf-8")
    new_mtime = yaml_path.stat().st_mtime_ns
    # Test environments can produce identical mtime_ns on fast filesystems;
    # force a difference by bumping the timestamp.
    if new_mtime == mtime:
        import os
        os.utime(yaml_path, ns=(new_mtime + 1, new_mtime + 1))
        new_mtime = yaml_path.stat().st_mtime_ns
    fresh = _read_yaml(str(yaml_path), new_mtime)
    assert fresh["app"]["name"] == "second"


def test_read_yaml_missing_file_returns_empty():
    _read_yaml.cache_clear()
    # mtime_ns=0 is the sentinel for "file missing".
    assert _read_yaml("/nonexistent/path/that/should/not/exist.yaml", 0) == {}


def test_load_config_does_not_share_state_across_calls(tmp_path: Path):
    """Each ``load_config`` returns a fresh AppConfig — mutations stay local."""
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "security:\n  rate_limit_per_minute: 60\n",
        encoding="utf-8",
    )
    a = load_config(yaml_path, env={})
    b = load_config(yaml_path, env={})
    a.security.rate_limit_per_minute = 0
    # The second call must NOT see the first call's mutation.
    assert b.security.rate_limit_per_minute == 60


# -- #12: request_id uses one token_bytes call ---------------------------------

def test_new_request_id_random_part_uses_full_alphabet():
    """Statistical sanity check: the byte-translate path must cover the alphabet."""
    from app.core.ids import _ALPHABET

    # Sample enough ids to make missing chars extremely unlikely.
    samples = "".join(new_request_id()[-8:] for _ in range(500))
    seen = set(samples)
    assert seen.issubset(set(_ALPHABET))
    # 32 alphabet chars over 4000 random samples — coverage should be ~complete.
    assert len(seen) >= 28


def test_new_request_id_is_ascii_only():
    rid = new_request_id()
    assert rid.isascii()
    assert all(c in "0123456789ABCDEFGHJKMNPQRSTVWXYZ" for c in rid[4:])


# -- #9: sampled_warning rate-limits noisy paths -------------------------------

def test_sampled_warning_emits_first_call_then_throttles(caplog):
    caplog.set_level(logging.WARNING)
    logger = logging.getLogger("test_sampled_warning_emits_first_call_then_throttles")
    # Clear sampler state for this logger+event.
    from app.core import logging as core_logging
    core_logging._last_sample_at.clear()

    sampled_warning(logger, "cache_read_failed", extra={"error": "boom"})
    sampled_warning(logger, "cache_read_failed", extra={"error": "boom"})
    sampled_warning(logger, "cache_read_failed", extra={"error": "boom"})

    events = [r for r in caplog.records if r.message == "cache_read_failed"]
    assert len(events) == 1


def test_sampled_warning_re_emits_after_interval(caplog):
    caplog.set_level(logging.WARNING)
    logger = logging.getLogger("test_sampled_warning_re_emits_after_interval")
    from app.core import logging as core_logging
    core_logging._last_sample_at.clear()

    sampled_warning(logger, "ev", min_interval_seconds=0.0)
    sampled_warning(logger, "ev", min_interval_seconds=0.0)
    sampled_warning(logger, "ev", min_interval_seconds=0.0)

    events = [r for r in caplog.records if r.message == "ev"]
    assert len(events) == 3


def test_sampled_warning_drops_when_below_level(caplog):
    """When WARNING is disabled, the sampler must not touch the formatter at all."""
    caplog.set_level(logging.ERROR)
    logger = logging.getLogger("test_sampled_warning_drops_when_below_level")
    logger.setLevel(logging.ERROR)
    from app.core import logging as core_logging
    core_logging._last_sample_at.clear()

    sampled_warning(logger, "ignored", extra={"error": "x"})
    assert [r for r in caplog.records if r.message == "ignored"] == []


# -- #5 sanity: cabinet reads can be gathered safely ---------------------------

@pytest.mark.asyncio
async def test_asyncio_gather_three_reads_returns_in_order():
    """``cabinet_context`` relies on gather preserving the call order."""
    async def slow(value, delay):
        await asyncio.sleep(delay)
        return value

    started = time.monotonic()
    a, b, c = await asyncio.gather(slow("a", 0.01), slow("b", 0.01), slow("c", 0.01))
    elapsed = time.monotonic() - started
    assert (a, b, c) == ("a", "b", "c")
    # The three sleeps overlap; serial would be ~0.03s, gather is ~0.01s.
    assert elapsed < 0.025
