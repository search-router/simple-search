"""Backend rejects upstream responses larger than the configured cap."""

from __future__ import annotations

import httpx
import pytest

from app.backends.base import BackendContext
from app.backends.search_router import SearchRouterBackend
from app.core.errors import BackendBadResponseError
from app.search.schemas import WebSearchRequest


@pytest.mark.asyncio
async def test_search_router_rejects_oversized_response():
    big_payload = b'{"results": ["' + b"a" * (2 * 1024 * 1024) + b'"]}'

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=big_payload)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        backend = SearchRouterBackend(api_key="x", http=http)
        backend._max_response_bytes = 1024  # tiny cap
        with pytest.raises(BackendBadResponseError):
            await backend.search_web(
                WebSearchRequest(q="hi"),
                BackendContext(request_id="r", started_at=0.0),
            )


@pytest.mark.asyncio
async def test_search_router_accepts_response_under_cap():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [{"url": "https://x", "title": "t"}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        backend = SearchRouterBackend(api_key="x", http=http)
        backend._max_response_bytes = 1024 * 1024
        resp = await backend.search_web(
            WebSearchRequest(q="hi"),
            BackendContext(request_id="r", started_at=0.0),
        )
        assert resp.total_results == 1
