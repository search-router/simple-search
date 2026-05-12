from __future__ import annotations

from app.search.ranking import assign_ranks, dedupe_by_url
from app.search.schemas import ImageResult, WebResult


def _web(url: str, title: str = "t") -> WebResult:
    return WebResult(rank=0, title=title, url=url, provider="search_router")


def test_assign_ranks_starts_at_one_by_default():
    items = [_web("https://a.test/1"), _web("https://b.test/2")]
    ranked = assign_ranks(items)
    assert [r.rank for r in ranked] == [1, 2]


def test_assign_ranks_custom_start():
    items = [_web("https://a.test/1"), _web("https://b.test/2")]
    ranked = assign_ranks(items, start=11)
    assert [r.rank for r in ranked] == [11, 12]


def test_assign_ranks_for_image_results():
    images = [
        ImageResult(rank=0, page_url="https://p/1", image_url="https://i/1", provider="alt"),
        ImageResult(rank=0, page_url="https://p/2", image_url="https://i/2", provider="alt"),
    ]
    ranked = assign_ranks(images)
    assert [r.rank for r in ranked] == [1, 2]


def test_assign_ranks_handles_empty_input():
    assert assign_ranks([]) == []


def test_dedupe_by_url_keeps_first_occurrence():
    items = [
        _web("https://a.test/1", "first"),
        _web("https://a.test/1", "duplicate"),
        _web("https://b.test/2", "third"),
    ]
    out = dedupe_by_url(items)
    assert [r.url for r in out] == ["https://a.test/1", "https://b.test/2"]
    assert out[0].title == "first"


def test_dedupe_by_url_passthrough_when_unique():
    items = [_web("https://a.test/1"), _web("https://b.test/2"), _web("https://c.test/3")]
    out = dedupe_by_url(items)
    assert len(out) == 3


def test_dedupe_by_url_empty():
    assert dedupe_by_url([]) == []


def test_dedupe_by_url_does_not_collapse_url_less_items():
    # An empty URL is not a meaningful identity — multiple URL-less items must
    # all survive deduplication so that backends returning sparse rows aren't
    # silently dropped to a single result.
    items = [
        _web("", "first"),
        _web("", "second"),
        _web("https://x.test/1", "real"),
    ]
    out = dedupe_by_url(items)
    assert [r.title for r in out] == ["first", "second", "real"]


def test_dedupe_by_url_still_dedupes_real_urls_alongside_empty():
    items = [
        _web("", "blank-a"),
        _web("https://a.test/1", "first"),
        _web("https://a.test/1", "dup"),
        _web("", "blank-b"),
    ]
    out = dedupe_by_url(items)
    assert [r.title for r in out] == ["blank-a", "first", "blank-b"]


def test_assign_ranks_supports_zero_start():
    items = [_web("https://a.test/1"), _web("https://b.test/2")]
    ranked = assign_ranks(items, start=0)
    assert [r.rank for r in ranked] == [0, 1]


def test_assign_ranks_mutates_input_in_place():
    """Documented side effect: callers can rely on the same WebResult instances being numbered."""
    one = _web("https://a.test/1")
    two = _web("https://b.test/2")
    assign_ranks([one, two])
    assert one.rank == 1
    assert two.rank == 2


def test_dedupe_by_url_is_case_sensitive():
    """Different-cased URLs are kept separate — backends sometimes carry meaningful path case."""
    items = [
        _web("https://Example.com/a", "upper"),
        _web("https://example.com/a", "lower"),
    ]
    out = dedupe_by_url(items)
    assert len(out) == 2
