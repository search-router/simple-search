"""Search Router REST adapter."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.backends.base import BackendContext, BaseBackend
from app.core.config import BackendConfig
from app.core.errors import BackendBadResponseError
from app.core.i18n import DirectionFn, make_direction_resolver
from app.search.normalizer import clamp, coerce_int, domain_of, safe_url
from app.search.schemas import (
    BackendCapabilities,
    ImageResult,
    ImageSearchRequest,
    ImageSearchResponse,
    WebResult,
    WebSearchRequest,
    WebSearchResponse,
)

logger = logging.getLogger(__name__)


class SearchRouterBackend(BaseBackend):
    """Adapter for ``POST https://search-router.com/api/search``."""

    name = "search_router"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://search-router.com",
        timeout_ms: int = 5000,
        http: httpx.AsyncClient,
    ) -> None:
        super().__init__(http=http)
        if not api_key:
            raise ValueError("SearchRouterBackend requires an api_key")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_ms / 1000

    @classmethod
    def from_config(cls, config: BackendConfig, http: httpx.AsyncClient) -> SearchRouterBackend:
        return cls(
            api_key=config.resolved_api_key or "",
            base_url=config.base_url or "https://search-router.com",
            timeout_ms=config.timeout_ms,
            http=http,
        )

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            web_search=True,
            image_search_by_text=True,
            safe_search=False,
            pagination=False,
            regions=False,
            languages=False,
            max_results=100,
            response_formats=["json"],
        )

    async def search_web(self, req: WebSearchRequest, ctx: BackendContext) -> WebSearchResponse:
        payload = {
            "query": req.q,
            "search_type": "web",
            "num_results": clamp(req.limit, 1, 100),
        }
        data = await self._post(payload)
        items = self._extract_results(data, "web")
        resolve_dir = make_direction_resolver(req.language, req.direction)
        results = [
            self._to_web_result(item, req, rank=i, resolve_dir=resolve_dir)
            for i, item in enumerate(items, start=1)
        ]
        return WebSearchResponse(
            request_id=ctx.request_id,
            query=req.q,
            backend=self.name,
            language=req.language,
            direction=resolve_dir(req.q),
            page=req.page,
            limit=req.limit,
            total_results=len(results),
            response_time_ms=int((time.monotonic() - ctx.started_at) * 1000),
            results=results,
        )

    async def search_images(
        self, req: ImageSearchRequest, ctx: BackendContext
    ) -> ImageSearchResponse:
        payload = {
            "query": req.q,
            "search_type": "images",
            "num_results": clamp(req.limit, 1, 100),
        }
        data = await self._post(payload)
        items = self._extract_results(data, "images")
        resolve_dir = make_direction_resolver(req.language, req.direction)
        results = [
            self._to_image_result(item, req, rank=i, resolve_dir=resolve_dir)
            for i, item in enumerate(items, start=1)
        ]
        return ImageSearchResponse(
            request_id=ctx.request_id,
            query=req.q,
            backend=self.name,
            language=req.language,
            direction=resolve_dir(req.q),
            page=req.page,
            limit=req.limit,
            total_results=len(results),
            response_time_ms=int((time.monotonic() - ctx.started_at) * 1000),
            results=results,
        )

    async def _healthcheck_probe(self) -> None:
        await self._post({"query": "ping", "search_type": "web", "num_results": 1})

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}/api/search"
        headers = {
            "X-API-Key": self._api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            response = await self.http.post(
                url, json=payload, headers=headers, timeout=self._timeout
            )
        except httpx.HTTPError as exc:
            raise self.map_transport_error(exc, backend=self.name) from exc
        if response.status_code >= 400:
            raise self.map_http_error(
                response.status_code, backend=self.name, body=response.text
            )
        self.enforce_response_size(response)
        try:
            data = response.json()
        except ValueError as exc:
            raise BackendBadResponseError(backend=self.name) from exc
        if not isinstance(data, dict):
            raise BackendBadResponseError(backend=self.name)
        return data

    @staticmethod
    def _extract_results(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
        if isinstance(data, dict):
            for candidate in (key, "results", "items", "data"):
                value = data.get(candidate)
                if isinstance(value, list):
                    return [v for v in value if isinstance(v, dict)]
        return []

    @staticmethod
    def _to_web_result(
        item: dict[str, Any],
        req: WebSearchRequest,
        *,
        rank: int,
        resolve_dir: DirectionFn,
    ) -> WebResult:
        url = safe_url(str(item.get("url") or item.get("link") or ""))
        title = item.get("title")
        snippet = item.get("snippet") or item.get("description")
        return WebResult(
            rank=rank,
            title=title,
            url=url,
            domain=item.get("domain") or domain_of(url),
            snippet=snippet,
            language=req.language,
            direction=resolve_dir(title or snippet),
            provider="search_router",
            raw=item,
        )

    @staticmethod
    def _to_image_result(
        item: dict[str, Any],
        req: ImageSearchRequest,
        *,
        rank: int,
        resolve_dir: DirectionFn,
    ) -> ImageResult:
        page_url = safe_url(
            str(item.get("url") or item.get("page_url") or item.get("source_url") or "")
        )
        image_url = safe_url(str(item.get("image_url") or item.get("image") or ""))
        thumb = safe_url(str(item.get("thumbnail_url") or item.get("thumbnail") or "")) or None
        title = item.get("title") or item.get("alt")
        return ImageResult(
            rank=rank,
            title=title,
            page_url=page_url,
            image_url=image_url,
            thumbnail_url=thumb,
            domain=item.get("domain") or domain_of(page_url) or domain_of(image_url),
            width=coerce_int(item.get("width")),
            height=coerce_int(item.get("height")),
            snippet=item.get("snippet"),
            language=req.language,
            direction=resolve_dir(title),
            provider="search_router",
            raw=item,
        )
