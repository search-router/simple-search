"""Backend registry — wires YAML config + entry points + mock fallback."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from importlib.metadata import entry_points
from typing import Any

import httpx

from app.backends.base import SearchBackend
from app.backends.mocks import MockSearchRouterBackend
from app.backends.search_router import SearchRouterBackend
from app.core.config import AppConfig, BackendConfig
from app.core.errors import ConfigurationError

logger = logging.getLogger(__name__)

BackendFactory = Callable[[BackendConfig, httpx.AsyncClient], SearchBackend]

_DEFAULT_FACTORIES: dict[str, BackendFactory] = {
    "search_router": SearchRouterBackend.from_config,
}

_MOCK_FACTORIES: dict[str, Callable[[], SearchBackend]] = {
    "search_router": MockSearchRouterBackend,
}


class BackendRegistry:
    """Holds the live ``SearchBackend`` instances keyed by name."""

    def __init__(self, backends: dict[str, SearchBackend]) -> None:
        self._backends = backends

    def get(self, name: str) -> SearchBackend:
        try:
            return self._backends[name]
        except KeyError as exc:
            raise KeyError(f"Backend {name!r} is not registered") from exc

    def has(self, name: str) -> bool:
        return name in self._backends

    def names(self) -> list[str]:
        return list(self._backends.keys())

    def items(self) -> Iterator[tuple[str, SearchBackend]]:
        yield from self._backends.items()


def build_registry(
    config: AppConfig,
    http: httpx.AsyncClient,
    *,
    extra_factories: dict[str, BackendFactory] | None = None,
    force_mocks: bool = False,
) -> BackendRegistry:
    """Construct backends per config; substitute mocks where credentials are missing."""
    factories = dict(_DEFAULT_FACTORIES)
    factories.update(_load_entry_point_factories())
    if extra_factories:
        factories.update(extra_factories)

    cap_bytes = max(config.security.max_response_size_mb, 0) * 1024 * 1024

    backends: dict[str, SearchBackend] = {}
    for name, backend_config in config.search.backends.items():
        if not backend_config.enabled:
            continue
        kind = backend_config.type
        try:
            instance = _instantiate(
                name, kind, backend_config, http, factories, force_mocks=force_mocks
            )
        except ConfigurationError as exc:
            logger.warning(
                "backend_disabled",
                extra={"backend": name, "type": kind, "reason": str(exc)},
            )
            continue
        if cap_bytes > 0 and hasattr(instance, "_max_response_bytes"):
            instance._max_response_bytes = cap_bytes
        backends[name] = instance
    return BackendRegistry(backends)


def _instantiate(
    name: str,
    kind: str,
    cfg: BackendConfig,
    http: httpx.AsyncClient,
    factories: dict[str, BackendFactory],
    *,
    force_mocks: bool,
) -> SearchBackend:
    if force_mocks or not cfg.has_credentials:
        mock_factory = _MOCK_FACTORIES.get(kind)
        if mock_factory is None:
            raise ConfigurationError(
                f"No factory or mock available for backend type {kind!r}",
                backend=name,
            )
        if not force_mocks:
            logger.warning(
                "backend_credentials_missing_using_mock",
                extra={"backend": name, "type": kind},
            )
        instance = mock_factory()
        instance.name = name  # honor the YAML key in /api/v1/backends
        return instance
    factory = factories.get(kind)
    if factory is None:
        raise ConfigurationError(f"Unknown backend type {kind!r}", backend=name)
    instance = factory(cfg, http)
    instance.name = name
    return instance


def _load_entry_point_factories() -> dict[str, BackendFactory]:
    discovered: dict[str, BackendFactory] = {}
    try:
        eps = entry_points(group="search_service.backends")
    except Exception as exc:
        logger.warning("entry_point_discovery_failed", extra={"reason": str(exc)})
        return discovered
    for ep in eps:
        try:
            target: Any = ep.load()
        except Exception as exc:
            logger.warning(
                "entry_point_load_failed",
                # ``name`` is reserved by ``LogRecord``; use a distinct key.
                extra={"entry_point": ep.name, "reason": str(exc)},
            )
            continue
        factory = _coerce_factory(target)
        if factory is None:
            continue
        if ep.name in _DEFAULT_FACTORIES:
            continue  # don't override built-ins from this same package
        discovered[ep.name] = factory
    return discovered


def _coerce_factory(target: Any) -> BackendFactory | None:
    """Accept either a ``from_config(cfg, http)`` callable or a class."""
    if callable(target) and hasattr(target, "from_config"):
        return target.from_config  # type: ignore[no-any-return]
    if callable(target):
        return target  # type: ignore[no-any-return]
    return None
