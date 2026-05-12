"""YAML + environment configuration."""

from __future__ import annotations

import os
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class AppMeta(_Strict):
    name: str = "search-service"
    version: str = "1.0.0"
    public_base_url: str = "http://localhost:8000"
    default_ui_locale: str = "en"
    default_direction: Literal["ltr", "rtl", "auto"] = "auto"
    default_theme: Literal["auto", "light", "dark"] = "auto"


class BackendConfig(_Strict):
    enabled: bool = True
    type: str
    base_url: str | None = None
    api_key_env: str | None = None
    iam_token_env: str | None = None
    folder_id_env: str | None = None
    auth_type: Literal["api_key", "iam"] = "api_key"
    timeout_ms: int = 5000
    max_results: int = 100
    default_search_type: str = "SEARCH_TYPE_COM"
    default_family_mode: str = "FAMILY_MODE_MODERATE"

    resolved_api_key: str | None = Field(default=None, repr=False)
    resolved_iam_token: str | None = Field(default=None, repr=False)
    resolved_folder_id: str | None = None

    @property
    def has_credentials(self) -> bool:
        if self.auth_type == "api_key":
            return bool(self.resolved_api_key)
        return bool(self.resolved_iam_token)


class SearchConfig(_Strict):
    # ``max_limit`` is the per-deployment cap applied to ``req.limit``. The
    # Pydantic schema also enforces ``le=100`` as the protocol-level ceiling,
    # so set this to lower values for tenants with stricter quotas.
    max_limit: int = 100
    fallback_order: list[str] = Field(default_factory=list)
    backends: dict[str, BackendConfig] = Field(default_factory=dict)


class RoutingRule(_Strict):
    when: dict[str, Any] = Field(default_factory=dict)
    use: str | None = None
    prefer: str | None = None
    fallback: str | None = None


class RoutingConfig(_Strict):
    rules: list[RoutingRule] = Field(default_factory=list)


class CircuitBreakerConfig(_Strict):
    failure_threshold: int = 5
    recovery_timeout_seconds: float = 60.0
    half_open_max_requests: int = 1


class I18nConfig(_Strict):
    default_ui_locale: str = "en"
    supported_locales: list[str] = Field(default_factory=list)
    rtl_languages: list[str] = Field(default_factory=lambda: ["ar", "he", "fa", "ur"])


class CacheConfig(_Strict):
    enabled: bool = True
    redis_url_env: str = "REDIS_URL"
    web_default_ttl_seconds: int = 600
    images_default_ttl_seconds: int = 1800
    latest_query_ttl_seconds: int = 60

    resolved_redis_url: str | None = None


class SecurityConfig(_Strict):
    rate_limit_per_minute: int = 60
    max_upload_size_mb: int = 10
    max_response_size_mb: int = 5
    cors_allowed_origins: list[str] = Field(default_factory=list)
    cors_allowed_headers: list[str] = Field(
        default_factory=lambda: ["Content-Type", "Authorization", "X-Request-ID"]
    )
    allowed_hosts: list[str] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "testserver"]
    )
    hsts_enabled: bool = True
    admin_token_env: str = "ADMIN_TOKEN"
    # When true the rate limiter and CSRF Origin check read ``X-Forwarded-For``
    # / ``X-Forwarded-Proto`` / ``X-Forwarded-Host``. Only enable behind a
    # proxy you control — otherwise clients spoof the headers to bypass limits.
    trust_forwarded_headers: bool = False
    # CSRF: when set, POST/PUT/DELETE/PATCH must carry an ``Origin`` (or
    # ``Referer``) whose scheme+host is in this list. Empty disables the check.
    csrf_allowed_origins: list[str] = Field(default_factory=list)
    # Fail-closed admin endpoints in prod when ``ADMIN_TOKEN`` is unset.
    require_admin_token: bool = False

    resolved_admin_token: str | None = Field(default=None, repr=False)


class AdsConfig(_Strict):
    enabled: bool = False
    signup_balance: int = 1000
    db_path: str = "./data/ads.sqlite"
    session_secret_env: str = "SESSION_SECRET"
    session_https_only: bool = True
    session_max_age_seconds: int = 7 * 24 * 3600
    # When true the app refuses to start in non-dev with an unset session
    # secret (otherwise sessions die on every restart / replica).
    require_session_secret: bool = False
    # Per-username login throttle: lock after ``max_attempts`` failures in
    # ``lockout_seconds``. Zero disables the throttle.
    login_max_attempts: int = 10
    login_lockout_seconds: int = 300
    # Optional autocomplete suggestions surfaced in the cabinet form. The list
    # no longer gates bidding — advertisers can target any text.
    queries: list[str] = Field(default_factory=list)

    resolved_session_secret: str | None = Field(default=None, repr=False)


class AppConfig(_Strict):
    app: AppMeta = Field(default_factory=AppMeta)
    search: SearchConfig = Field(default_factory=SearchConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)
    i18n: I18nConfig = Field(default_factory=I18nConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    ads: AdsConfig = Field(default_factory=AdsConfig)


@lru_cache(maxsize=8)
def _read_yaml(path_str: str, mtime_ns: int) -> dict[str, Any]:
    """Read+parse a YAML file, memoized by ``(path, mtime)``.

    Called from :func:`load_config`. The test suite (and any code that
    calls ``create_app`` many times) used to hit disk on every call; the
    ``mtime`` key in the cache makes invalidation automatic when the file
    actually changes. ``mtime_ns == 0`` is the sentinel for "file missing".
    """
    if mtime_ns == 0:
        return {}
    path = Path(path_str)
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_config(
    yaml_path: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> AppConfig:
    """Read YAML, then resolve ``*_env`` indirections from ``env``."""
    env = env if env is not None else os.environ
    path = Path(yaml_path or env.get("APP_CONFIG_FILE") or "config.yaml")
    try:
        mtime_ns = path.stat().st_mtime_ns
    except FileNotFoundError:
        mtime_ns = 0
    raw = _read_yaml(str(path), mtime_ns)
    # Validate from a fresh ``raw`` snapshot each call so mutations from
    # callers (tests routinely tweak ``cfg.security.*``) stay test-local.
    config = AppConfig.model_validate(raw)

    for backend in config.search.backends.values():
        if backend.api_key_env:
            backend.resolved_api_key = env.get(backend.api_key_env) or None
        if backend.iam_token_env:
            backend.resolved_iam_token = env.get(backend.iam_token_env) or None
        if backend.folder_id_env:
            backend.resolved_folder_id = env.get(backend.folder_id_env) or None

    if config.cache.redis_url_env:
        config.cache.resolved_redis_url = env.get(config.cache.redis_url_env) or None
    if config.security.admin_token_env:
        config.security.resolved_admin_token = (
            env.get(config.security.admin_token_env) or None
        )
    if config.ads.enabled and config.ads.session_secret_env:
        config.ads.resolved_session_secret = (
            env.get(config.ads.session_secret_env) or None
        )
    return config
