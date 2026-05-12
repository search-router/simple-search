"""POST /api/v1/search/web."""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import ValidationError

from app.api.deps import CacheDep, ConfigDep, RequestIdDep, RoutingDep
from app.core.cache import cache_payload_from_model, make_cache_key
from app.core.errors import InvalidRequestError
from app.core.logging import sampled_warning
from app.search.schemas import TimeRange, WebSearchRequest, WebSearchResponse

router = APIRouter(prefix="/search", tags=["search"])
logger = logging.getLogger(__name__)


# ``response_model=None`` keeps FastAPI from re-validating and re-encoding the
# model on the way out — we already hand it a fully serialized JSON ``Response``.
@router.post("/web", response_model=None)
async def search_web(
    payload: WebSearchRequest,
    routing: RoutingDep,
    cache: CacheDep,
    config: ConfigDep,
    request_id: RequestIdDep,
) -> Response:
    if payload.limit > config.search.max_limit:
        raise InvalidRequestError(
            f"limit exceeds configured max_limit={config.search.max_limit}",
            details={"limit": payload.limit, "max_limit": config.search.max_limit},
        )
    caps = (
        routing.registry.get(payload.backend).capabilities()
        if payload.backend not in ("auto", "") and routing.registry.has(payload.backend)
        else None
    )
    cache_key = make_cache_key(
        "web",
        payload.backend,
        cache_payload_from_model(payload, caps),
    )
    cache_eligible = payload.cache and payload.time_range != TimeRange.DAY
    if cache_eligible:
        try:
            hit = await cache.get(cache_key)
        except Exception as exc:
            sampled_warning(
                logger, "cache_read_failed", extra={"error": str(exc)}
            )
            hit = None
        if hit is not None:
            try:
                cached = WebSearchResponse.model_validate_json(hit)
            except ValidationError as exc:
                # A schema migration or corrupted entry must not 500 the user —
                # fall through to the backend and overwrite the bad cache slot.
                logger.warning(
                    "cache_payload_invalid_falling_through",
                    extra={"error": str(exc)[:200]},
                )
            else:
                cached.request_id = request_id
                cached.cache_hit = True
                return Response(
                    content=cached.model_dump_json(by_alias=False),
                    media_type="application/json",
                )

    response = await routing.route_web(payload, request_id=request_id)
    body = response.model_dump_json(by_alias=False).encode("utf-8")
    if cache_eligible and response.total_results > 0:
        try:
            await cache.set(
                cache_key,
                body,
                ttl=config.cache.web_default_ttl_seconds,
            )
        except Exception as exc:
            sampled_warning(
                logger, "cache_write_failed", extra={"error": str(exc)}
            )
    return Response(content=body, media_type="application/json")
