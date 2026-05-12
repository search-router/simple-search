"""Deterministic mock backends used when API keys are missing.

Mock results are stable for a given query and locale so that the UI looks
predictable in screenshots and tests, and they cover both LTR and RTL text
samples so RTL rendering can be exercised offline.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from app.backends.base import BackendContext, BaseBackend
from app.core.i18n import make_direction_resolver, parse_bcp47
from app.search.normalizer import domain_of
from app.search.schemas import (
    BackendCapabilities,
    ImageResult,
    ImageSearchRequest,
    ImageSearchResponse,
    WebResult,
    WebSearchRequest,
    WebSearchResponse,
)

_TLDS = ("com", "org", "net", "dev", "io", "co", "info", "blog")

_TEMPLATES_TITLE = {
    "en": "{q} — example {n}",
    "ru": "{q} — пример {n}",
    "ar": "{q} — مثال {n}",
    "he": "{q} — דוגמה {n}",
    "fa": "{q} — نمونه {n}",
    "ur": "{q} — مثال {n}",
    "tr": "{q} — örnek {n}",
}

_TEMPLATES_SNIPPET = {
    "en": "A demo result for the query '{q}'. Backend: {backend}. Page {page}, item {n}.",
    "ru": "Демо-результат по запросу «{q}». Бекенд: {backend}. Страница {page}, элемент {n}.",
    "ar": "نتيجة تجريبية للاستعلام «{q}». الخلفية: {backend}. الصفحة {page}، العنصر {n}.",
    "he": "תוצאת הדגמה עבור השאילתה ״{q}״. backend: {backend}. עמוד {page}, פריט {n}.",
    "fa": "نتیجه نمایشی برای پرس‌وجوی «{q}». backend: {backend}. صفحه {page}، مورد {n}.",
    "ur": "استفسار «{q}» کے لئے ڈیمو نتیجہ۔ backend: {backend}. صفحہ {page}، آئٹم {n}.",
    "tr": "'{q}' sorgusu için demo sonuç. backend: {backend}. Sayfa {page}, öge {n}.",
}


def _seed(query: str, salt: str) -> int:
    digest = hashlib.blake2b(f"{salt}:{query}".encode(), digest_size=4).digest()
    return int.from_bytes(digest, "big")


def _pick_locale(req_language: str | None) -> str:
    parsed = parse_bcp47(req_language) if req_language else None
    if parsed and parsed.language in _TEMPLATES_TITLE:
        return parsed.language
    return "en"


class _MockBase(BaseBackend):
    is_mock = True

    def __init__(self, *, salt: str) -> None:
        super().__init__(http=None)
        self._salt = salt

    @property
    def http(self) -> Any:
        raise RuntimeError("Mock backends never make HTTP calls")

    async def _healthcheck_probe(self) -> None:
        return None

    async def search_web(self, req: WebSearchRequest, ctx: BackendContext) -> WebSearchResponse:
        locale = _pick_locale(req.language)
        seed = _seed(req.q, self._salt)
        resolve_dir = make_direction_resolver(req.language, req.direction)
        results: list[WebResult] = []
        base = req.page * req.limit
        for n in range(1, req.limit + 1):
            # Use a globally unique index across pages so paginating through the mock
            # backend yields fresh URLs/titles instead of replaying page 0 forever.
            g = base + n
            tld = _TLDS[(seed + g) % len(_TLDS)]
            slug = hashlib.blake2b(f"{seed}-{g}".encode(), digest_size=3).hexdigest()
            url = f"https://example-{g}.{tld}/articles/{slug}"
            title = _TEMPLATES_TITLE[locale].format(q=req.q, n=g)
            snippet = _TEMPLATES_SNIPPET[locale].format(
                q=req.q, backend=self.name, page=req.page + 1, n=n
            )
            results.append(
                WebResult(
                    rank=n,
                    title=title,
                    url=url,
                    domain=domain_of(url),
                    snippet=snippet,
                    language=req.language,
                    direction=resolve_dir(title),
                    provider=self.name,
                    raw={},
                )
            )
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
        locale = _pick_locale(req.language)
        seed = _seed(req.q, f"img-{self._salt}")
        resolve_dir = make_direction_resolver(req.language, req.direction)
        results: list[ImageResult] = []
        base = req.page * req.limit
        for n in range(1, req.limit + 1):
            # See ``search_web``: use a global index so successive pages don't
            # repeat the page-0 image set.
            g = base + n
            stamp = (seed ^ (g * 9176)) & 0xFFFFFFFF
            tld = _TLDS[(seed + g) % len(_TLDS)]
            page_url = f"https://gallery-{g}.{tld}/photo/{stamp}"
            image_url = f"https://picsum.photos/seed/{self._salt}-{stamp}/800/600"
            thumb_url = f"https://picsum.photos/seed/{self._salt}-{stamp}/240/180"
            title = _TEMPLATES_TITLE[locale].format(q=req.q, n=g)
            results.append(
                ImageResult(
                    rank=n,
                    title=title,
                    page_url=page_url,
                    image_url=image_url,
                    thumbnail_url=thumb_url,
                    domain=domain_of(page_url),
                    width=800,
                    height=600,
                    language=req.language,
                    direction=resolve_dir(title),
                    provider=self.name,
                    raw={},
                )
            )
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


class MockSearchRouterBackend(_MockBase):
    name = "search_router"

    def __init__(self) -> None:
        super().__init__(salt="search_router")

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            web_search=True,
            image_search_by_text=True,
            max_results=100,
            response_formats=["json"],
        )
