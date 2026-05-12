"""POST /api/v1/search/images."""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import ValidationError

from app.api.deps import CacheDep, ConfigDep, RequestIdDep, RoutingDep
from app.core.cache import cache_payload_from_model, make_cache_key
from app.core.errors import InvalidRequestError
from app.core.logging import sampled_warning
from app.search.schemas import (
    ImageSearchRequest,
    ImageSearchResponse,
    TimeRange,
)

router = APIRouter(prefix="/search", tags=["search"])
logger = logging.getLogger(__name__)


# ``response_model=None`` keeps FastAPI from re-validating and re-encoding the
# model on the way out — we already hand it a fully serialized JSON ``Response``.
@router.post("/images", response_model=None)
async def search_images(
    payload: ImageSearchRequest,
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
        "images",
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
                cached = ImageSearchResponse.model_validate_json(hit)
            except ValidationError as exc:
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

    response = await routing.route_images(payload, request_id=request_id)
    body = response.model_dump_json(by_alias=False).encode("utf-8")
    if cache_eligible and response.total_results > 0:
        try:
            await cache.set(
                cache_key,
                body,
                ttl=config.cache.images_default_ttl_seconds,
            )
        except Exception as exc:
            sampled_warning(
                logger, "cache_write_failed", extra={"error": str(exc)}
            )
    return Response(content=body, media_type="application/json")
