"""Tests for the backend registry: instantiation rules + entry-point discovery."""

from __future__ import annotations

import httpx
import pytest

from app.backends.base import BaseBackend
from app.backends.mocks import MockSearchRouterBackend
from app.core.config import (
    AppConfig,
    AppMeta,
    BackendConfig,
    SearchConfig,
)
from app.core.errors import ConfigurationError
from app.search.registry import BackendRegistry, build_registry
from app.search.schemas import BackendCapabilities


def _config_with(backends: dict[str, BackendConfig]) -> AppConfig:
    return AppConfig(
        app=AppMeta(),
        search=SearchConfig(max_limit=100, backends=backends),
    )


# --- BackendRegistry --------------------------------------------------------

def test_registry_get_raises_keyerror_with_helpful_message():
    reg = BackendRegistry({})
    with pytest.raises(KeyError, match="not registered"):
        reg.get("nope")


def test_registry_has_and_names_and_items():
    backend = MockSearchRouterBackend()
    reg = BackendRegistry({"search_router": backend})
    assert reg.has("search_router") is True
    assert reg.has("missing") is False
    assert reg.names() == ["search_router"]
    assert dict(reg.items()) == {"search_router": backend}


# --- build_registry ---------------------------------------------------------

def test_build_registry_skips_disabled_backends():
    cfg = _config_with(
        {
            "search_router": BackendConfig(type="search_router", enabled=True),
            "disabled": BackendConfig(type="search_router", enabled=False),
        }
    )
    registry = build_registry(cfg, http=httpx.AsyncClient())
    assert registry.names() == ["search_router"]
    # Mock substitution: no api key in env, so it falls back to a mock backend.
    assert registry.get("search_router").is_mock


def test_build_registry_falls_back_to_mock_when_credentials_missing():
    cfg = _config_with({"search_router": BackendConfig(type="search_router")})
    registry = build_registry(cfg, http=httpx.AsyncClient())
    backend = registry.get("search_router")
    assert backend.is_mock
    # The YAML key is preserved on the instance.
    assert backend.name == "search_router"


def test_build_registry_force_mocks_overrides_real_credentials():
    cfg = _config_with({"search_router": BackendConfig(type="search_router")})
    cfg.search.backends["search_router"].resolved_api_key = "real-key"
    registry = build_registry(cfg, http=httpx.AsyncClient(), force_mocks=True)
    assert registry.get("search_router").is_mock


def test_build_registry_drops_backend_with_unknown_type():
    """An unknown ``type`` is logged and skipped, never raised — health stays up."""
    cfg = _config_with({"weird": BackendConfig(type="weird")})
    cfg.search.backends["weird"].resolved_api_key = "x"
    registry = build_registry(cfg, http=httpx.AsyncClient())
    assert registry.names() == []


def test_build_registry_drops_unknown_type_when_no_mock_either():
    """Even with no credentials, an unknown type can't be substituted with a mock."""
    cfg = _config_with({"weird": BackendConfig(type="weird")})
    registry = build_registry(cfg, http=httpx.AsyncClient())
    assert registry.names() == []


def test_build_registry_uses_extra_factory_for_custom_type():
    """A caller-supplied factory enables a non-builtin backend type."""

    class _CustomAdapter(BaseBackend):
        name = "custom"

        def capabilities(self):
            return BackendCapabilities(web_search=True)

    def factory(_cfg, _http):
        return _CustomAdapter(http=_http)

    cfg = _config_with({"custom": BackendConfig(type="custom")})
    cfg.search.backends["custom"].resolved_api_key = "any"
    registry = build_registry(
        cfg, http=httpx.AsyncClient(), extra_factories={"custom": factory}
    )
    assert isinstance(registry.get("custom"), _CustomAdapter)
    # name is overwritten to honor the YAML key (even if same here).
    assert registry.get("custom").name == "custom"


def test_build_registry_uses_real_search_router_when_credentials_resolved():
    """Real backend instantiation path (not mock)."""
    from app.backends.search_router import SearchRouterBackend

    cfg = _config_with({"search_router": BackendConfig(type="search_router")})
    cfg.search.backends["search_router"].resolved_api_key = "real-key"
    registry = build_registry(cfg, http=httpx.AsyncClient())
    assert isinstance(registry.get("search_router"), SearchRouterBackend)


def test_build_registry_drops_real_backend_with_failing_constructor():
    """If a real backend's constructor raises ``ConfigurationError`` we skip it."""
    cfg = _config_with({"custom": BackendConfig(type="custom")})
    cfg.search.backends["custom"].resolved_api_key = "real"

    def explode(_cfg, _http):
        raise ConfigurationError("missing required field")

    registry = build_registry(cfg, http=httpx.AsyncClient(), extra_factories={"custom": explode})
    assert registry.names() == []


# --- entry point discovery -------------------------------------------------

def test_entry_point_factories_are_loaded(monkeypatch):
    """Custom backends declared via ``search_service.backends`` are picked up."""
    from importlib.metadata import EntryPoint

    from app.search import registry as registry_mod

    class _FromConfig(BaseBackend):
        name = "via_ep"

        def capabilities(self):
            return BackendCapabilities(web_search=True)

        @classmethod
        def from_config(cls, _cfg, _http):
            return cls()

    def fake_load(self):
        return _FromConfig

    monkeypatch.setattr(EntryPoint, "load", fake_load, raising=True)
    monkeypatch.setattr(
        registry_mod,
        "entry_points",
        lambda group: [EntryPoint(name="via_ep", value="x:Y", group=group)],
    )

    cfg = _config_with({"via_ep": BackendConfig(type="via_ep")})
    cfg.search.backends["via_ep"].resolved_api_key = "any"
    registry = build_registry(cfg, http=httpx.AsyncClient())
    assert isinstance(registry.get("via_ep"), _FromConfig)


def test_entry_point_failures_are_swallowed(monkeypatch):
    """A broken ``ep.load()`` must not bring down the service."""
    from importlib.metadata import EntryPoint

    from app.search import registry as registry_mod

    def fake_load(self):
        raise ImportError("plugin missing dep")

    monkeypatch.setattr(EntryPoint, "load", fake_load, raising=True)
    monkeypatch.setattr(
        registry_mod,
        "entry_points",
        lambda group: [EntryPoint(name="broken", value="x:Y", group=group)],
    )

    cfg = _config_with({})
    registry = build_registry(cfg, http=httpx.AsyncClient())
    assert registry.names() == []


def test_entry_point_discovery_failure_is_swallowed(monkeypatch):
    """A misbehaving ``entry_points()`` itself must not crash startup."""
    from app.search import registry as registry_mod

    def boom(group):
        raise RuntimeError("metadata corrupt")

    monkeypatch.setattr(registry_mod, "entry_points", boom)
    cfg = _config_with({})
    registry = build_registry(cfg, http=httpx.AsyncClient())
    assert registry.names() == []


def test_entry_point_does_not_override_builtins(monkeypatch):
    """Same-package built-ins must win over duplicate entry-point declarations."""
    from importlib.metadata import EntryPoint

    from app.backends.search_router import SearchRouterBackend
    from app.search import registry as registry_mod

    class _Different(BaseBackend):
        name = "search_router"

        def capabilities(self):
            return BackendCapabilities()

        @classmethod
        def from_config(cls, _cfg, _http):
            return cls()

    def fake_load(self):
        return _Different

    monkeypatch.setattr(EntryPoint, "load", fake_load, raising=True)
    monkeypatch.setattr(
        registry_mod,
        "entry_points",
        lambda group: [EntryPoint(name="search_router", value="x:Y", group=group)],
    )

    cfg = _config_with({"search_router": BackendConfig(type="search_router")})
    cfg.search.backends["search_router"].resolved_api_key = "real"
    registry = build_registry(cfg, http=httpx.AsyncClient())
    assert isinstance(registry.get("search_router"), SearchRouterBackend)


def test_coerce_factory_accepts_plain_callable(monkeypatch):
    """An entry-point that points at a bare callable (no class) must still load."""
    from importlib.metadata import EntryPoint

    from app.search import registry as registry_mod

    class _Adapter(BaseBackend):
        name = "from_callable"

        def capabilities(self):
            return BackendCapabilities()

    def make(_cfg, _http):
        return _Adapter()

    def fake_load(self):
        return make

    monkeypatch.setattr(EntryPoint, "load", fake_load, raising=True)
    monkeypatch.setattr(
        registry_mod,
        "entry_points",
        lambda group: [EntryPoint(name="from_callable", value="x:y", group=group)],
    )

    cfg = _config_with({"from_callable": BackendConfig(type="from_callable")})
    cfg.search.backends["from_callable"].resolved_api_key = "x"
    registry = build_registry(cfg, http=httpx.AsyncClient())
    assert isinstance(registry.get("from_callable"), _Adapter)


def test_coerce_factory_rejects_non_callable_target(monkeypatch):
    """An entry-point that resolves to a non-callable is dropped, not crashed-on."""
    from importlib.metadata import EntryPoint

    from app.search import registry as registry_mod

    def fake_load(self):
        return "not-callable"

    monkeypatch.setattr(EntryPoint, "load", fake_load, raising=True)
    monkeypatch.setattr(
        registry_mod,
        "entry_points",
        lambda group: [EntryPoint(name="bad", value="x:y", group=group)],
    )

    cfg = _config_with({})
    registry = build_registry(cfg, http=httpx.AsyncClient())
    assert registry.names() == []
