"""HTML routes for login, registration, and the personal cabinet."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, get_args

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.ads.errors import (
    AdsError,
    InsufficientFundsError,
    InvalidBidError,
    InvalidCredentialsError,
    UsernameTakenError,
)
from app.ads.models import AdCreative, MatchMode
from app.ads.service import AdsService
from app.api.deps import (
    AdsServiceDep,
    ConfigDep,
    CurrentUserDep,
    RequireUserDep,
    TranslatorDep,
)
from app.core.config import AppConfig
from app.core.i18n import Translator, resolve_direction
from app.ui.routes import get_templates

logger = logging.getLogger(__name__)
router = APIRouter()

_VALID_MATCH_MODES: frozenset[str] = frozenset(get_args(MatchMode))


def _coerce_match_mode(raw: str | None) -> MatchMode:
    value = (raw or "exact").strip().lower()
    if value not in _VALID_MATCH_MODES:
        raise InvalidBidError("Match mode must be 'exact' or 'phrase'")
    return value  # type: ignore[return-value]


def _locale(request: Request, config: AppConfig) -> tuple[str, str]:
    raw = request.query_params.get("ui_locale") or config.app.default_ui_locale
    direction = resolve_direction(raw, requested="auto")
    return raw, direction


def _ctx(
    request: Request, config: AppConfig, translator: Translator, **extra: Any
) -> dict[str, Any]:
    ui_locale, direction = _locale(request, config)
    base = {
        "request": request,
        "ui_locale": ui_locale,
        "direction": direction,
        "theme": "",
        "supported_locales": config.i18n.supported_locales,
        "available_backends": [],
        "any_mock": False,
        "language_options": [],
        "region_options": [],
        "t": lambda key, **kw: translator.t(key, ui_locale, **kw),
    }
    base.update(extra)
    return base


def _require_ads(ads: AdsService | None) -> AdsService:
    if ads is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return ads


@router.get("/login", response_model=None)
async def login_page(
    request: Request,
    config: ConfigDep,
    translator: TranslatorDep,
    ads: AdsServiceDep,
    user: CurrentUserDep,
) -> HTMLResponse | RedirectResponse:
    _require_ads(ads)
    if user is not None:
        return RedirectResponse("/cabinet", status_code=status.HTTP_303_SEE_OTHER)
    ctx = _ctx(request, config, translator, error=None, login_value="")
    return get_templates(request).TemplateResponse(request, "login.html", ctx)


def _form_error_response(
    request: Request,
    config: AppConfig,
    translator: Translator,
    *,
    error_key: str,
    login_value: str,
    status_code: int = 400,
) -> HTMLResponse:
    ui_locale = request.query_params.get("ui_locale") or config.app.default_ui_locale
    ctx = _ctx(
        request,
        config,
        translator,
        error=translator.t(error_key, ui_locale),
        login_value=login_value,
    )
    return get_templates(request).TemplateResponse(
        request, "login.html", ctx, status_code=status_code
    )


def _rotate_session(request: Request) -> None:
    """Drop any pre-existing session state on auth transitions.

    Defends against session fixation: an attacker who plants a session
    cookie in the victim's browser must not retain it after the victim
    authenticates. ``SessionMiddleware`` rewrites the cookie signature on
    every modified session, so clearing here forces a fresh value.
    """
    try:
        session = request.session
    except AssertionError:
        return
    session.clear()


def _login_throttle(request: Request) -> Any | None:
    return getattr(request.app.state, "login_throttle", None)


@router.post("/auth/login", response_model=None)
async def login_submit(
    request: Request,
    config: ConfigDep,
    translator: TranslatorDep,
    ads: AdsServiceDep,
    username: str = Form(...),
    password: str = Form(...),
) -> HTMLResponse | RedirectResponse:
    service = _require_ads(ads)
    throttle = _login_throttle(request)
    if throttle is not None and throttle.is_locked(username):
        logger.info(
            "login_throttled", extra={"username": username.strip().lower()[:32]}
        )
        return _form_error_response(
            request,
            config,
            translator,
            error_key="ads.error.invalid_credentials",
            login_value=username,
            status_code=429,
        )
    try:
        user = await service.login(username, password)
    except AdsError:
        if throttle is not None:
            throttle.record_failure(username)
        return _form_error_response(
            request,
            config,
            translator,
            error_key="ads.error.invalid_credentials",
            login_value=username,
            status_code=401,
        )
    if throttle is not None:
        throttle.reset(username)
    _rotate_session(request)
    request.session["uid"] = user.id
    return RedirectResponse("/cabinet", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/auth/register", response_model=None)
async def register_submit(
    request: Request,
    config: ConfigDep,
    translator: TranslatorDep,
    ads: AdsServiceDep,
    username: str = Form(...),
    password: str = Form(...),
) -> HTMLResponse | RedirectResponse:
    service = _require_ads(ads)
    try:
        user = await service.register(username, password)
    except UsernameTakenError:
        # Don't tell the caller *why* registration failed — that exposes a
        # username-enumeration oracle. The shape stays identical to the
        # validation-failure path: same status, same generic message.
        logger.info(
            "register_failed_username_taken",
            extra={"username": username.strip().lower()[:32]},
        )
        return _form_error_response(
            request,
            config,
            translator,
            error_key="ads.error.invalid_signup",
            login_value=username,
            status_code=400,
        )
    except InvalidCredentialsError:
        return _form_error_response(
            request,
            config,
            translator,
            error_key="ads.error.invalid_signup",
            login_value=username,
            status_code=400,
        )
    _rotate_session(request)
    request.session["uid"] = user.id
    return RedirectResponse("/cabinet", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/auth/logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.pop("uid", None)
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


def _format_ctr(impressions: int, clicks: int) -> str:
    if impressions <= 0:
        return "—"
    return f"{(clicks * 100 / impressions):.1f}%"


async def _cabinet_context(
    request: Request,
    config: AppConfig,
    translator: Translator,
    service: AdsService,
    user: Any,
    *,
    flash: str | None = None,
    flash_kind: str = "info",
    form_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Three independent read-only queries — kick them off together. The
    # aiosqlite connection still serializes them on the writer thread, but we
    # save the awaiter ping-pong between each await.
    bids, per_bid, raw_summary = await asyncio.gather(
        service.list_user_bids(user.id),
        service.user_bid_stats(user.id),
        service.user_stats_summary(user.id),
    )
    summary: dict[str, Any] = {**raw_summary}
    summary["ctr"] = _format_ctr(raw_summary["impressions"], raw_summary["clicks"])

    rows = []
    for bid in bids:
        stats = per_bid.get(bid.id, {"impressions": 0, "spent": 0, "clicks": 0})
        rows.append(
            {
                "bid": bid,
                "paused": bid.amount > user.wallet,
                "stats": {
                    **stats,
                    "ctr": _format_ctr(stats["impressions"], stats["clicks"]),
                },
            }
        )
    defaults: dict[str, Any] = {
        "query": "",
        "title": "",
        "url": "",
        "snippet": "",
        "amount": "",
        "match_mode": "exact",
    }
    if form_values:
        defaults.update(form_values)
    return _ctx(
        request,
        config,
        translator,
        user=user,
        rows=rows,
        summary=summary,
        suggested_queries=service.suggested_queries,
        signup_balance=service.signup_balance,
        flash=flash,
        flash_kind=flash_kind,
        form=defaults,
    )


@router.get("/cabinet", response_model=None)
async def cabinet(
    request: Request,
    config: ConfigDep,
    translator: TranslatorDep,
    ads: AdsServiceDep,
    user: RequireUserDep,
) -> HTMLResponse:
    service = _require_ads(ads)
    ctx = await _cabinet_context(request, config, translator, service, user)
    return get_templates(request).TemplateResponse(request, "cabinet.html", ctx)


def _coerce_amount(raw: str) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise InvalidBidError("Amount must be an integer") from exc


@router.post("/cabinet/bid", response_model=None)
async def place_bid(
    request: Request,
    config: ConfigDep,
    translator: TranslatorDep,
    ads: AdsServiceDep,
    user: RequireUserDep,
    query: str = Form(...),
    title: str = Form(...),
    url: str = Form(...),
    snippet: str = Form(""),
    amount: str = Form(...),
    match_mode: str = Form("exact"),
) -> HTMLResponse | RedirectResponse:
    service = _require_ads(ads)
    form_values = {
        "query": query,
        "title": title,
        "url": url,
        "snippet": snippet,
        "amount": amount,
        "match_mode": match_mode,
    }
    try:
        mode = _coerce_match_mode(match_mode)
        creative = AdCreative(
            title=title,
            url=url,
            snippet=snippet,
            amount=_coerce_amount(amount),
            match_mode=mode,
        )
        await service.place_bid(user, query, creative)
    except (InvalidBidError, InsufficientFundsError) as exc:
        ctx = await _cabinet_context(
            request, config, translator, service, user,
            flash=exc.message, flash_kind="danger",
            form_values=form_values,
        )
        return get_templates(request).TemplateResponse(
            request, "cabinet.html", ctx, status_code=400
        )
    except Exception:
        # Pydantic ValidationError shapes are noisy and may echo the user's
        # input back; show a stable generic message and let the logs hold
        # the real diagnostics.
        logger.exception(
            "place_bid_unexpected_failure",
            extra={"user_id": user.id, "form_keys": list(form_values.keys())},
        )
        ui_locale = request.query_params.get("ui_locale") or config.app.default_ui_locale
        ctx = await _cabinet_context(
            request, config, translator, service, user,
            flash=translator.t("ads.error.invalid_bid", ui_locale),
            flash_kind="danger",
            form_values=form_values,
        )
        return get_templates(request).TemplateResponse(
            request, "cabinet.html", ctx, status_code=400
        )
    return RedirectResponse("/cabinet", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/cabinet/bid/delete")
async def delete_bid(
    request: Request,
    ads: AdsServiceDep,
    user: RequireUserDep,
    query: str = Form(...),
    match_mode: str = Form("exact"),
) -> RedirectResponse:
    service = _require_ads(ads)
    try:
        mode = _coerce_match_mode(match_mode)
    except InvalidBidError:
        mode = "exact"
    await service.delete_bid(user, query, mode)
    return RedirectResponse("/cabinet", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/ads/click/{bid_id}", response_model=None)
async def click_redirect(
    bid_id: int,
    ads: AdsServiceDep,
    r: str | None = None,
) -> RedirectResponse:
    """Record a click and redirect to the advertiser's URL.

    Unknown/deleted bids fall back to the home page so a stale impression
    never produces a hard error for the searcher."""
    service = _require_ads(ads)
    bid = await service.record_click(bid_id, r)
    target = bid.url if bid is not None else "/"
    return RedirectResponse(target, status_code=status.HTTP_302_FOUND)
