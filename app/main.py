"""FastAPI application factory and lifespan wiring."""

from __future__ import annotations

import logging
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.v1 import backends as backends_routes
from app.api.v1 import health as health_routes
from app.api.v1 import image_search as image_routes
from app.api.v1 import web_search as web_routes
from app.core.cache import Cache, NullCache, RedisCache
from app.core.circuit_breaker import CircuitBreaker
from app.core.config import AppConfig, load_config
from app.core.csrf import CsrfOriginMiddleware
from app.core.errors import ApiError, ErrorDetail, InvalidRequestError, ServiceError
from app.core.i18n import Translator
from app.core.ids import new_request_id
from app.core.logging import configure_logging
from app.core.rate_limit import RateLimitMiddleware
from app.core.security_headers import SecurityHeadersMiddleware
from app.search.registry import build_registry
from app.search.router import RoutingService
from app.ui import routes as ui_routes

logger = logging.getLogger(__name__)

PACKAGE_ROOT = Path(__file__).parent
TEMPLATES_DIR = PACKAGE_ROOT / "ui" / "templates"
STATIC_DIR = PACKAGE_ROOT / "ui" / "static"
TRANSLATIONS_DIR = PACKAGE_ROOT / "ui" / "translations"


class RequestIdMiddleware:
    """Pure-ASGI: propagate ``X-Request-Id`` in and out.

    Sits in front of every request, so the cost matters; ``BaseHTTPMiddleware``
    would add an anyio task and a stream bridge here for no good reason.
    """

    def __init__(self, app) -> None:  # type: ignore[no-untyped-def]
        self._app = app

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        rid: str | None = None
        for name, value in scope["headers"]:
            if name == b"x-request-id":
                rid = value.decode("latin-1")
                break
        if not rid:
            rid = new_request_id()
        # ``scope["state"]`` is Starlette's underlying dict — ``request.state``
        # wraps it; setting the key here surfaces as ``request.state.request_id``.
        state = scope.setdefault("state", {})
        state["request_id"] = rid
        rid_bytes = rid.encode("latin-1")

        async def send_wrapper(message):  # type: ignore[no-untyped-def]
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append((b"x-request-id", rid_bytes))
            await send(message)

        await self._app(scope, receive, send_wrapper)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config: AppConfig = app.state.config
    timeout = httpx.Timeout(connect=5.0, read=15.0, write=15.0, pool=5.0)
    limits = httpx.Limits(max_keepalive_connections=20, max_connections=50)
    http_client = httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        follow_redirects=False,
    )
    app.state.http = http_client

    registry = build_registry(config, http_client)
    breaker = CircuitBreaker(
        failure_threshold=config.circuit_breaker.failure_threshold,
        recovery_timeout_seconds=config.circuit_breaker.recovery_timeout_seconds,
        half_open_max_requests=config.circuit_breaker.half_open_max_requests,
    )
    for backend_name in registry.names():
        breaker.register(backend_name)
    app.state.registry = registry
    app.state.routing = RoutingService(registry, breaker, config)

    if config.cache.enabled and config.cache.resolved_redis_url:
        cache: Cache = await RedisCache.from_url(config.cache.resolved_redis_url)
    else:
        cache = NullCache()
    app.state.cache = cache

    translator = Translator.from_directory(TRANSLATIONS_DIR, default="en")
    app.state.translator = translator

    # Default Jinja2 leaves ``auto_reload=True``, which ``stat()``s every template
    # on every render. Honour the dev-server toggle (uvicorn's ``--reload``
    # exports ``UVICORN_RELOAD=true``) so dev still hot-reloads templates, but
    # production avoids the per-request filesystem hit.
    reload_templates = os.environ.get("TEMPLATES_AUTO_RELOAD", "").lower() in (
        "1", "true", "yes",
    ) or os.environ.get("UVICORN_RELOAD", "").lower() in ("1", "true", "yes")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    # Jinja's default ``cache_size=400`` is already large enough for this app;
    # ``auto_reload`` is the costly default — flip it.
    templates.env.auto_reload = reload_templates
    templates.env.globals["t"] = lambda key, locale=None, **kw: translator.t(key, locale, **kw)
    app.state.templates = templates

    ads_store = None
    if config.ads.enabled:
        # Import lazily so the aiosqlite dep stays optional when ads are off.
        from app.ads.service import AdsService
        from app.ads.storage import AdsStore
        from app.ads.throttle import LoginThrottle

        ads_store = await AdsStore.open(config.ads.db_path)
        await ads_store.init_schema()
        app.state.ads_store = ads_store
        app.state.ads_service = AdsService(
            ads_store, config.ads, logging.getLogger("app.ads")
        )
        app.state.login_throttle = LoginThrottle(
            max_attempts=config.ads.login_max_attempts,
            window_seconds=config.ads.login_lockout_seconds,
        )

    logger.info(
        "service_started",
        extra={
            "backends": registry.names(),
            "cache": cache.name,
            "translations": translator.supported(),
            "ads_enabled": config.ads.enabled,
        },
    )

    try:
        yield
    finally:
        await http_client.aclose()
        await cache.aclose()
        if ads_store is not None:
            await ads_store.aclose()


def create_app(config: AppConfig | None = None) -> FastAPI:
    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
    app_config = config or load_config()

    app = FastAPI(
        title=app_config.app.name,
        version=app_config.app.version,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.config = app_config

    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        SecurityHeadersMiddleware,
        hsts_enabled=app_config.security.hsts_enabled,
        trust_forwarded_proto=app_config.security.trust_forwarded_headers,
    )
    if app_config.security.csrf_allowed_origins:
        app.add_middleware(
            CsrfOriginMiddleware,
            allowed_origins=app_config.security.csrf_allowed_origins,
        )
    if app_config.security.rate_limit_per_minute > 0:
        app.add_middleware(
            RateLimitMiddleware,
            limit_per_minute=app_config.security.rate_limit_per_minute,
            trust_forwarded_for=app_config.security.trust_forwarded_headers,
        )
    if app_config.security.allowed_hosts:
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=app_config.security.allowed_hosts,
        )
    if app_config.security.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=app_config.security.cors_allowed_origins,
            allow_methods=["GET", "POST"],
            allow_headers=app_config.security.cors_allowed_headers,
            allow_credentials=False,
        )

    if app_config.ads.enabled:
        secret = app_config.ads.resolved_session_secret
        if not secret:
            if app_config.ads.require_session_secret:
                raise RuntimeError(
                    f"ads enabled but {app_config.ads.session_secret_env} is not "
                    "set; refusing to start with an ephemeral session secret"
                )
            secret = secrets.token_urlsafe(32)
            logger.warning(
                "session_secret_ephemeral",
                extra={"env": app_config.ads.session_secret_env},
            )
        app.add_middleware(
            SessionMiddleware,
            secret_key=secret,
            session_cookie="session",
            same_site="strict",
            https_only=app_config.ads.session_https_only,
            max_age=app_config.ads.session_max_age_seconds,
        )

    app.include_router(web_routes.router, prefix="/api/v1")
    app.include_router(image_routes.router, prefix="/api/v1")
    app.include_router(backends_routes.router, prefix="/api/v1")
    app.include_router(health_routes.router, prefix="/api/v1")
    app.include_router(ui_routes.router)

    if app_config.ads.enabled:
        from app.ads import routes as ads_routes

        app.include_router(ads_routes.router)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.exception_handler(ServiceError)
    async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
        rid = getattr(request.state, "request_id", new_request_id())
        envelope = ApiError(request_id=rid, error=exc.as_detail())
        return JSONResponse(envelope.model_dump(mode="json"), status_code=exc.http_status)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        rid = getattr(request.state, "request_id", new_request_id())
        # ``model_validator`` errors include a ``ctx.error`` ValueError object that
        # is not JSON-serializable; drop ``ctx`` and ``url`` to keep the response stable.
        errors = [
            {k: v for k, v in err.items() if k not in ("ctx", "url")}
            for err in exc.errors()[:5]
        ]
        envelope = ApiError(
            request_id=rid,
            error=ErrorDetail(
                code="invalid_request",
                message="Validation failed",
                details={"errors": errors},
            ),
        )
        return JSONResponse(envelope.model_dump(mode="json"), status_code=400)

    @app.exception_handler(InvalidRequestError)
    async def invalid_request_handler(request: Request, exc: InvalidRequestError) -> JSONResponse:
        return await service_error_handler(request, exc)

    @app.exception_handler(Exception)
    async def fallback_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_exception", extra={"path": request.url.path})
        rid = getattr(request.state, "request_id", new_request_id())
        envelope = ApiError(
            request_id=rid,
            error=ErrorDetail(code="internal_error", message="Internal server error"),
        )
        return JSONResponse(envelope.model_dump(mode="json"), status_code=500)

    return app


# ASGI entry point used by ``uvicorn app.main:app``.
app = create_app()
