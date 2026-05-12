from __future__ import annotations

import time

import httpx
import pytest

from app.backends.base import BackendContext
from app.backends.search_router import SearchRouterBackend
from app.core.errors import (
    BackendAuthError,
    BackendQuotaError,
    BackendServerError,
    BackendTimeoutError,
    BackendUnavailableError,
)
from app.search.schemas import ImageSearchRequest, WebSearchRequest


def _ctx() -> BackendContext:
    return BackendContext(request_id="req-test", started_at=time.monotonic())


def _adapter(transport: httpx.MockTransport) -> SearchRouterBackend:
    return SearchRouterBackend(
        api_key="test",
        base_url="https://search-router.test",
        timeout_ms=2000,
        http=httpx.AsyncClient(transport=transport),
    )


@pytest.mark.asyncio
async def test_search_web_normalizes_results():
    def handler(request):
        assert request.headers["X-API-Key"] == "test"
        return httpx.Response(
            200,
            json={
                "web": [
                    {
                        "title": "A",
                        "url": "https://a.example/1",
                        "domain": "a.example",
                        "snippet": "s1",
                    },
                    {
                        "title": "B",
                        "url": "https://b.example/2",
                        "domain": "b.example",
                        "snippet": "s2",
                    },
                ]
            },
        )

    adapter = _adapter(httpx.MockTransport(handler))
    response = await adapter.search_web(WebSearchRequest(q="hello", limit=2), _ctx())
    assert response.backend == "search_router"
    assert len(response.results) == 2
    assert [r.rank for r in response.results] == [1, 2]
    assert response.results[0].provider == "search_router"


@pytest.mark.asyncio
async def test_search_images_normalizes_dimensions():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "images": [
                    {
                        "title": "a cat",
                        "url": "https://page.example/cat",
                        "image_url": "https://i.example/cat.jpg",
                        "thumbnail_url": "https://i.example/cat-t.jpg",
                        "domain": "page.example",
                        "width": 1024,
                        "height": 768,
                    }
                ]
            },
        )

    adapter = _adapter(httpx.MockTransport(handler))
    response = await adapter.search_images(ImageSearchRequest(q="cat", limit=1), _ctx())
    assert response.results[0].width == 1024
    assert response.results[0].page_url == "https://page.example/cat"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "exc"),
    [
        (401, BackendAuthError),
        (402, BackendQuotaError),
        (500, BackendServerError),
        (503, BackendUnavailableError),
    ],
)
async def test_status_to_exception(status, exc):
    adapter = _adapter(httpx.MockTransport(lambda req: httpx.Response(status, text="boom")))
    with pytest.raises(exc):
        await adapter.search_web(WebSearchRequest(q="x"), _ctx())


@pytest.mark.asyncio
async def test_timeout_maps_to_backend_timeout():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    adapter = _adapter(httpx.MockTransport(handler))
    with pytest.raises(BackendTimeoutError):
        await adapter.search_web(WebSearchRequest(q="x"), _ctx())


def test_search_router_requires_api_key():
    with pytest.raises(ValueError, match="api_key"):
        SearchRouterBackend(api_key="", http=httpx.AsyncClient())


@pytest.mark.asyncio
async def test_search_router_extract_results_skips_non_dict_items():
    """Defensive: a malformed array with strings/numbers must not crash result mapping."""
    def handler(request):
        return httpx.Response(
            200,
            json={
                "web": [
                    {"url": "https://a.test/1", "title": "A"},
                    "garbage-string",
                    42,
                    {"url": "https://b.test/2", "title": "B"},
                ]
            },
        )

    adapter = _adapter(httpx.MockTransport(handler))
    response = await adapter.search_web(WebSearchRequest(q="x", limit=5), _ctx())
    assert [r.url for r in response.results] == [
        "https://a.test/1",
        "https://b.test/2",
    ]


@pytest.mark.asyncio
async def test_search_router_non_json_body_raises_bad_response():
    """A 200 with text/html (or any non-JSON) body must surface as a bad-response error."""
    from app.core.errors import BackendBadResponseError

    def handler(request):
        return httpx.Response(200, text="<html>oops</html>")

    adapter = _adapter(httpx.MockTransport(handler))
    with pytest.raises(BackendBadResponseError):
        await adapter.search_web(WebSearchRequest(q="x"), _ctx())


@pytest.mark.asyncio
async def test_search_router_top_level_array_is_bad_response():
    """The contract requires a JSON object — an array is a server bug."""
    from app.core.errors import BackendBadResponseError

    def handler(request):
        return httpx.Response(200, json=[{"url": "https://x"}])

    adapter = _adapter(httpx.MockTransport(handler))
    with pytest.raises(BackendBadResponseError):
        await adapter.search_web(WebSearchRequest(q="x"), _ctx())
