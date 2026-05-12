"""GET /api/v1/health — service-level liveness."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from app import __version__
from app.api.deps import AdminAuthDep, CacheDep, RoutingDep
from app.search.schemas import HealthStatus

router = APIRouter(tags=["admin"])


@router.get("/livez", response_model=None, tags=["public"])
async def livez() -> dict[str, str]:
    """Process-liveness probe. No auth, no dependency checks — only confirms
    the ASGI app is running. The Dockerfile HEALTHCHECK uses this so the
    container does not depend on ``ADMIN_TOKEN`` being shipped to it."""
    return {"status": "ok"}


@router.get("/health", response_model=HealthStatus)
async def health(routing: RoutingDep, cache: CacheDep, _auth: AdminAuthDep) -> HealthStatus:
    items = list(routing.registry.items())
    # Probes are independent — running them sequentially makes wall-time stack
    # linearly in the number of backends. ``return_exceptions`` keeps a single
    # faulty adapter from poisoning the whole listing.
    probes = await asyncio.gather(
        *(backend.healthcheck() for _name, backend in items),
        return_exceptions=True,
    )
    backends: dict[str, str] = {}
    overall = "ok"
    for (name, _backend), outcome in zip(items, probes, strict=True):
        if isinstance(outcome, BaseException):
            backends[name] = "down"
            overall = "degraded"
            continue
        backends[name] = outcome.status
        if outcome.status != "ok":
            overall = "degraded"

    redis_status = "ok"
    try:
        if not await cache.ping():
            redis_status = "down"
    except Exception:
        redis_status = "down"

    # "down" means *no* backend can serve traffic. A degraded backend is still
    # serving — only treat the service as fully down when every backend is down.
    overall_status = (
        "down" if backends and all(v == "down" for v in backends.values()) else overall
    )
    return HealthStatus(
        status=overall_status,
        version=__version__,
        backends=backends,
        redis=redis_status,
    )
