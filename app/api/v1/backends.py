"""GET /api/v1/backends — admin-flavored introspection."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter

from app.api.deps import AdminAuthDep, RoutingDep
from app.search.schemas import BackendDescriptor, BackendListResponse

router = APIRouter(tags=["admin"])
logger = logging.getLogger(__name__)


@router.get("/backends", response_model=BackendListResponse)
async def list_backends(routing: RoutingDep, _auth: AdminAuthDep) -> BackendListResponse:
    items = list(routing.registry.items())
    # Fan out probes + breaker-state lookups in parallel: backend latencies
    # are independent and used to stack linearly in the response time.
    probes, states = await asyncio.gather(
        asyncio.gather(
            *(backend.healthcheck() for _name, backend in items),
            return_exceptions=True,
        ),
        asyncio.gather(
            *(routing.breaker.state_of(name) for name, _backend in items)
        ),
    )
    descriptors: list[BackendDescriptor] = []
    for (name, backend), probe, state in zip(items, probes, states, strict=True):
        if isinstance(probe, BaseException):
            logger.warning(
                "backend_healthcheck_raised",
                extra={"backend": name, "error": str(probe)},
            )
            healthy = False
        else:
            healthy = probe.status == "ok"
        descriptors.append(
            BackendDescriptor(
                name=name,
                enabled=True,
                healthy=healthy,
                is_mock=getattr(backend, "is_mock", False),
                circuit_state=state,
                capabilities=backend.capabilities(),
            )
        )
    return BackendListResponse(backends=descriptors)
