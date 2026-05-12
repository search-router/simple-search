from __future__ import annotations

import time

import pytest

from app.backends.base import BackendContext
from app.backends.mocks import MockSearchRouterBackend
from app.search.schemas import (
    ImageSearchRequest,
    WebSearchRequest,
)


def _ctx() -> BackendContext:
    return BackendContext(request_id="req-test", started_at=time.monotonic())


@pytest.mark.asyncio
async def test_mock_results_are_deterministic_for_same_query():
    """Identical inputs must yield identical URLs so screenshots/tests stay stable."""
    be = MockSearchRouterBackend()
    a = await be.search_web(WebSearchRequest(q="cats", limit=3), _ctx())
    b = await be.search_web(WebSearchRequest(q="cats", limit=3), _ctx())
    assert [r.url for r in a.results] == [r.url for r in b.results]


@pytest.mark.asyncio
async def test_mock_results_change_with_query():
    be = MockSearchRouterBackend()
    a = await be.search_web(WebSearchRequest(q="cats", limit=3), _ctx())
    b = await be.search_web(WebSearchRequest(q="dogs", limit=3), _ctx())
    assert [r.url for r in a.results] != [r.url for r in b.results]


@pytest.mark.asyncio
async def test_mock_web_pagination_yields_distinct_results_per_page():
    """Page 0 and page 1 must produce disjoint URLs and titles — otherwise
    paginating in the mock-backed UI just replays the first page forever."""
    be = MockSearchRouterBackend()
    page0 = await be.search_web(WebSearchRequest(q="cats", page=0, limit=3), _ctx())
    page1 = await be.search_web(WebSearchRequest(q="cats", page=1, limit=3), _ctx())
    urls0 = [r.url for r in page0.results]
    urls1 = [r.url for r in page1.results]
    assert urls0 != urls1
    assert set(urls0).isdisjoint(set(urls1))
    titles0 = [r.title for r in page0.results]
    titles1 = [r.title for r in page1.results]
    assert set(titles0).isdisjoint(set(titles1))


@pytest.mark.asyncio
async def test_mock_web_pagination_is_stable_per_page():
    """Calling the same page twice must still be deterministic so screenshots stay stable."""
    be = MockSearchRouterBackend()
    a = await be.search_web(WebSearchRequest(q="cats", page=2, limit=3), _ctx())
    b = await be.search_web(WebSearchRequest(q="cats", page=2, limit=3), _ctx())
    assert [r.url for r in a.results] == [r.url for r in b.results]
    assert [r.title for r in a.results] == [r.title for r in b.results]


@pytest.mark.asyncio
async def test_mock_image_pagination_yields_distinct_results_per_page():
    be = MockSearchRouterBackend()
    page0 = await be.search_images(ImageSearchRequest(q="cats", page=0, limit=3), _ctx())
    page1 = await be.search_images(ImageSearchRequest(q="cats", page=1, limit=3), _ctx())
    images0 = [r.image_url for r in page0.results]
    images1 = [r.image_url for r in page1.results]
    page_urls0 = [r.page_url for r in page0.results]
    page_urls1 = [r.page_url for r in page1.results]
    assert set(images0).isdisjoint(set(images1))
    assert set(page_urls0).isdisjoint(set(page_urls1))


@pytest.mark.asyncio
async def test_mock_image_search_uses_image_filters_only_for_template_consistency():
    """The image-search code path uses a different salt than web; results must differ."""
    be = MockSearchRouterBackend()
    web = await be.search_web(WebSearchRequest(q="cats", limit=2), _ctx())
    images = await be.search_images(ImageSearchRequest(q="cats", limit=2), _ctx())
    assert [r.url for r in web.results] != [r.image_url for r in images.results]


@pytest.mark.asyncio
async def test_mock_search_falls_back_to_english_template_for_unknown_locale():
    be = MockSearchRouterBackend()
    resp = await be.search_web(
        WebSearchRequest(q="test", language="zz-ZZ", limit=1), _ctx()
    )
    # English template format is "{q} — example {n}"
    assert resp.results[0].title == "test — example 1"


@pytest.mark.asyncio
async def test_mock_search_uses_localized_template_when_available():
    be = MockSearchRouterBackend()
    resp = await be.search_web(
        WebSearchRequest(q="тест", language="ru-RU", limit=1), _ctx()
    )
    assert "пример" in (resp.results[0].title or "")


@pytest.mark.asyncio
async def test_mock_query_with_format_braces_does_not_crash():
    """Mocks use ``str.format`` on the *template*, not the user query, so braces are safe."""
    be = MockSearchRouterBackend()
    resp = await be.search_web(WebSearchRequest(q="hi {name}", limit=1), _ctx())
    assert resp.results[0].title is not None
    assert "{name}" in resp.results[0].title


@pytest.mark.asyncio
async def test_mock_healthcheck_reports_ok():
    be = MockSearchRouterBackend()
    health = await be.healthcheck()
    assert health.status == "ok"


def test_mock_backend_http_property_raises():
    """Mocks must NEVER make HTTP calls; touching ``.http`` is an immediate red flag."""
    be = MockSearchRouterBackend()
    with pytest.raises(RuntimeError, match="HTTP"):
        _ = be.http
