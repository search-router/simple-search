"""Shared dependency-injection helpers for FastAPI handlers."""

from __future__ import annotations

import hmac
import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.ads.models import User
from app.ads.service import AdsService
from app.core.cache import Cache
from app.core.config import AppConfig
from app.core.i18n import Translator
from app.core.ids import new_request_id
from app.search.router import RoutingService

logger = logging.getLogger(__name__)


def get_config(request: Request) -> AppConfig:
    return request.app.state.config  # type: ignore[no-any-return]


def get_routing(request: Request) -> RoutingService:
    return request.app.state.routing  # type: ignore[no-any-return]


def get_cache(request: Request) -> Cache:
    return request.app.state.cache  # type: ignore[no-any-return]


def get_translator(request: Request) -> Translator:
    return request.app.state.translator  # type: ignore[no-any-return]


def get_request_id(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    if not rid:
        rid = new_request_id()
        request.state.request_id = rid
    return rid


def require_admin(request: Request) -> None:
    """Gate admin endpoints behind ``ADMIN_TOKEN``.

    When ``security.require_admin_token`` is true (set this in prod), an
    unset token returns 503 instead of leaving the route open. In dev the
    legacy "open with warning" behavior is preserved so local healthchecks
    keep working without forcing every developer to wire an env var.
    """
    config: AppConfig = request.app.state.config
    expected = config.security.resolved_admin_token
    if not expected:
        if config.security.require_admin_token:
            logger.error(
                "admin_endpoint_misconfigured",
                extra={"path": request.url.path},
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="admin token not configured",
            )
        logger.warning(
            "admin_endpoint_unprotected",
            extra={"path": request.url.path},
        )
        return
    auth = request.headers.get("authorization", "")
    scheme, _, supplied = auth.partition(" ")
    if scheme.lower() != "bearer" or not supplied or not hmac.compare_digest(
        supplied, expected
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="admin token required",
            headers={"WWW-Authenticate": 'Bearer realm="admin"'},
        )


def get_ads_service(request: Request) -> AdsService | None:
    return getattr(request.app.state, "ads_service", None)


async def get_current_user(
    request: Request,
    ads: Annotated[AdsService | None, Depends(get_ads_service)],
) -> User | None:
    if ads is None:
        return None
    try:
        session = request.session
    except AssertionError:
        return None
    uid_raw = session.get("uid")
    if not uid_raw:
        return None
    try:
        uid = int(uid_raw)
    except (TypeError, ValueError):
        session.pop("uid", None)
        return None
    user = await ads.get_user(uid)
    if user is None:
        session.pop("uid", None)
    return user


async def require_user(
    user: Annotated[User | None, Depends(get_current_user)],
) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="auth_required",
            headers={"Location": "/login"},
        )
    return user


ConfigDep = Annotated[AppConfig, Depends(get_config)]
RoutingDep = Annotated[RoutingService, Depends(get_routing)]
CacheDep = Annotated[Cache, Depends(get_cache)]
TranslatorDep = Annotated[Translator, Depends(get_translator)]
RequestIdDep = Annotated[str, Depends(get_request_id)]
AdminAuthDep = Annotated[None, Depends(require_admin)]
AdsServiceDep = Annotated[AdsService | None, Depends(get_ads_service)]
CurrentUserDep = Annotated[User | None, Depends(get_current_user)]
RequireUserDep = Annotated[User, Depends(require_user)]
