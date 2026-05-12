"""HTML UI routes — home, search."""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import Enum
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.datastructures import QueryParams

from app.api.deps import (
    AdsServiceDep,
    CacheDep,
    ConfigDep,
    CurrentUserDep,
    RequestIdDep,
    RoutingDep,
    TranslatorDep,
)
from app.core.config import AppConfig
from app.core.errors import ServiceError
from app.core.i18n import Translator, resolve_direction
from app.search.normalizer import coerce_int
from app.search.schemas import (
    ImageOrientation,
    ImageSearchRequest,
    ImageSize,
    SafeSearch,
    TimeRange,
    WebSearchRequest,
)


def _coerce_enum[E: Enum](enum_cls: type[E], value: str | None, default: E) -> E:
    """Coerce a query-string value to an enum, returning ``default`` for unknown input."""
    if not value:
        return default
    try:
        return enum_cls(value)
    except ValueError:
        return default

logger = logging.getLogger(__name__)
router = APIRouter()


_LANGUAGE_OPTIONS = ["en", "ru", "ar", "he", "fa", "ur", "tr", "kk", "be", "uz"]
_REGION_OPTIONS = ["RU", "US", "AE", "SA", "TR", "BY", "KZ", "UA", "UZ"]


def get_templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates  # type: ignore[no-any-return]


def _resolve_locale(request: Request, config_default: str) -> tuple[str, str]:
    raw = request.query_params.get("ui_locale") or config_default
    direction = resolve_direction(raw, requested="auto")
    return raw, direction


def _backend_descriptors(request: Request) -> list[dict[str, Any]]:
    """Snapshot of the backend list used by every UI render.

    The registry is built once at lifespan start and never mutated in prod,
    so memoize the descriptor list on the app state. Tests that swap
    ``routing.registry._backends`` can invalidate by deleting the attribute.
    """
    app = request.app
    cached: list[dict[str, Any]] | None = getattr(
        app.state, "_ui_backend_descriptors", None
    )
    if cached is not None:
        return cached
    routing = app.state.routing
    descriptors: list[dict[str, Any]] = []
    for name, backend in routing.registry.items():
        descriptors.append(
            {
                "name": name,
                "is_mock": getattr(backend, "is_mock", False),
                "healthy": True,
            }
        )
    app.state._ui_backend_descriptors = descriptors
    return descriptors


def _common_context(
    request: Request,
    config: AppConfig,
    translator: Translator,
    *,
    ui_locale: str | None = None,
    direction: str | None = None,
    current_user: Any | None = None,
) -> dict[str, Any]:
    locale, computed_dir = _resolve_locale(request, config.app.default_ui_locale)
    backends = _backend_descriptors(request)
    return {
        "request": request,
        "ui_locale": ui_locale or locale,
        "direction": direction or computed_dir,
        "theme": "",
        "supported_locales": config.i18n.supported_locales,
        "available_backends": backends,
        "any_mock": any(b["is_mock"] for b in backends),
        "language_options": _LANGUAGE_OPTIONS,
        "region_options": _REGION_OPTIONS,
        "ads_enabled": config.ads.enabled,
        "current_user": current_user,
        "ad": None,
        "t": lambda key, **kw: translator.t(key, ui_locale or locale, **kw),
    }


@router.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    config: ConfigDep,
    translator: TranslatorDep,
    current_user: CurrentUserDep,
) -> HTMLResponse:
    ctx = _common_context(request, config, translator, current_user=current_user)
    return get_templates(request).TemplateResponse(request, "home.html", ctx)


@router.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    config: ConfigDep,
    translator: TranslatorDep,
    routing: RoutingDep,
    cache: CacheDep,
    request_id: RequestIdDep,
    ads: AdsServiceDep,
    current_user: CurrentUserDep,
) -> HTMLResponse:
    qp = request.query_params
    query = (qp.get("q") or "").strip()
    active_type = qp.get("type") or "web"
    backend = qp.get("backend") or "auto"
    language = qp.get("language") or None
    region = qp.get("region") or None
    safe_search = _coerce_enum(SafeSearch, qp.get("safe_search"), SafeSearch.MODERATE)
    time_range = _coerce_enum(TimeRange, qp.get("time_range"), TimeRange.ALL)
    page = max(coerce_int(qp.get("page"), default=0) or 0, 0)
    raw_size = qp.get("size") or None
    raw_orientation = qp.get("orientation") or None
    size = _coerce_enum(ImageSize, raw_size, ImageSize.ANY) if raw_size else None
    orientation = (
        _coerce_enum(ImageOrientation, raw_orientation, ImageOrientation.ANY)
        if raw_orientation
        else None
    )

    ctx = _common_context(request, config, translator, current_user=current_user)
    ctx.update(
        {
            "query": query,
            "active_type": active_type,
            "filters": {
                "backend": backend,
                "language": language,
                "region": region,
                "safe_search": safe_search.value,
                "time_range": time_range.value,
                "size": size.value if size else None,
                "orientation": orientation.value if orientation else None,
            },
            "response": None,
            "error": None,
            "pagination_link": _pagination_link_for(qp),
            "request_id": request_id,
        }
    )

    if not query:
        return get_templates(request).TemplateResponse(request, "home.html", ctx)

    ctx["ad"] = await _maybe_run_auction(ads, query, request_id)

    try:
        if active_type == "images":
            req = ImageSearchRequest(
                q=query,
                backend=backend,
                language=language,
                region=region,
                ui_locale=ctx["ui_locale"],
                page=page,
                limit=20,
                safe_search=safe_search,
                image_filters={
                    "size": size if size and size != ImageSize.ANY else None,
                    "orientation": (
                        orientation
                        if orientation and orientation != ImageOrientation.ANY
                        else None
                    ),
                },
            )
            image_response = await routing.route_images(req, request_id=request_id)
            ctx["response"] = image_response
            return get_templates(request).TemplateResponse(request, "search_images.html", ctx)

        req_web = WebSearchRequest(
            q=query,
            backend=backend,
            language=language,
            region=region,
            ui_locale=ctx["ui_locale"],
            page=page,
            limit=10,
            safe_search=safe_search,
            time_range=time_range,
        )
        web_response = await routing.route_web(req_web, request_id=request_id)
        ctx["response"] = web_response
        return get_templates(request).TemplateResponse(request, "search_web.html", ctx)
    except ServiceError as exc:
        ctx["error"] = _error_view(translator, ctx["ui_locale"], exc, request_id)
        template = "search_images.html" if active_type == "images" else "search_web.html"
        return get_templates(request).TemplateResponse(request, template, ctx, status_code=200)


async def _maybe_run_auction(
    ads: Any | None, query: str, request_id: str
) -> Any | None:
    """Run the ad auction for a query without letting failures break search.

    Returns the winning ad (``AuctionWinner``) or ``None``. Logs and swallows
    every exception — the auction is supplementary to search, never the path."""
    if ads is None or not query:
        return None
    try:
        return await ads.run_auction(query, request_id)
    except Exception:  # pragma: no cover — defensive
        logger.exception("auction_failed", extra={"query": query, "request_id": request_id})
        return None


def _pagination_link_for(qp: QueryParams) -> Callable[[int], str]:
    base_pairs = [
        (key, value)
        for key, value in qp.multi_items()
        if key not in ("page",)
    ]

    def build(page: int) -> str:
        return urlencode([*base_pairs, ("page", str(page))])

    return build


def _error_view(
    translator: Translator, locale: str, exc: ServiceError, request_id: str
) -> dict[str, Any]:
    title_map = {
        "invalid_request": translator.t("error.invalid_request", locale),
        "unsupported_backend": translator.t("error.unsupported_backend", locale),
        "unsupported_capability": translator.t("error.unsupported_capability", locale),
        "backend_auth_error": translator.t("error.backend_auth_error", locale),
        "backend_quota_error": translator.t("error.backend_quota_error", locale),
        "backend_timeout": translator.t("error.backend_timeout", locale),
        "backend_unavailable": translator.t("error.backend_unavailable", locale),
        "backend_bad_response": translator.t("error.backend_bad_response", locale),
        "rate_limited": translator.t("error.rate_limited", locale),
        "internal_error": translator.t("error.internal_error", locale),
    }
    return {
        "title": title_map.get(exc.code, translator.t("error.internal_error", locale)),
        "message": exc.message,
        "request_id": request_id,
        "try_other": None,
    }
