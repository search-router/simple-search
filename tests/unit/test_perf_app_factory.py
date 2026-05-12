"""Tests for the create_app/lifespan perf changes (#6, #8).

#6: Jinja2 ``auto_reload`` defaults to ``False`` for the prod build, opt-in
via ``TEMPLATES_AUTO_RELOAD`` or ``UVICORN_RELOAD``.
#8: ``_backend_descriptors`` is memoized on ``app.state`` so per-render UI
context construction does not rebuild the same list each call.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.core.config import load_config
from app.main import create_app

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def fresh_app(tmp_path, monkeypatch):
    cfg = load_config(os.path.join(ROOT, "config.yaml"), env={})
    cfg.security.rate_limit_per_minute = 0
    cfg.security.csrf_allowed_origins = []
    cfg.ads.db_path = str(tmp_path / "ads.sqlite")
    cfg.ads.resolved_session_secret = "test-secret"
    cfg.ads.session_https_only = False
    cfg.ads.login_max_attempts = 0
    # Make sure neither toggle leaks in from the harness env.
    monkeypatch.delenv("TEMPLATES_AUTO_RELOAD", raising=False)
    monkeypatch.delenv("UVICORN_RELOAD", raising=False)
    return create_app(cfg)


def test_jinja_auto_reload_is_off_by_default(fresh_app):
    """Without an explicit opt-in, template auto-reload must be disabled.

    The default ``Jinja2Templates`` stat()s every template on every render
    in dev mode; that's wasted work for production.
    """
    with TestClient(fresh_app) as _client:
        templates = fresh_app.state.templates
        assert templates.env.auto_reload is False
        # Jinja's bytecode cache must be enabled (default size of 400 is fine).
        assert templates.env.cache is not None


def test_jinja_auto_reload_opt_in_via_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TEMPLATES_AUTO_RELOAD", "true")
    cfg = load_config(os.path.join(ROOT, "config.yaml"), env={})
    cfg.security.rate_limit_per_minute = 0
    cfg.security.csrf_allowed_origins = []
    cfg.ads.db_path = str(tmp_path / "ads.sqlite")
    cfg.ads.resolved_session_secret = "t"
    cfg.ads.session_https_only = False
    cfg.ads.login_max_attempts = 0
    app = create_app(cfg)
    with TestClient(app) as _client:
        assert app.state.templates.env.auto_reload is True


def test_jinja_auto_reload_opt_in_via_uvicorn_reload(tmp_path, monkeypatch):
    monkeypatch.setenv("UVICORN_RELOAD", "true")
    cfg = load_config(os.path.join(ROOT, "config.yaml"), env={})
    cfg.security.rate_limit_per_minute = 0
    cfg.security.csrf_allowed_origins = []
    cfg.ads.db_path = str(tmp_path / "ads.sqlite")
    cfg.ads.resolved_session_secret = "t"
    cfg.ads.session_https_only = False
    cfg.ads.login_max_attempts = 0
    app = create_app(cfg)
    with TestClient(app) as _client:
        assert app.state.templates.env.auto_reload is True


def test_backend_descriptors_are_memoized_on_app_state(client):
    """First UI render fills the cache, subsequent renders reuse the list."""
    # First request populates the cache.
    response = client.get("/")
    assert response.status_code == 200
    cached = client.app.state._ui_backend_descriptors
    assert isinstance(cached, list)
    assert cached  # non-empty

    # Second request must not rebuild the list (identity check).
    client.get("/")
    assert client.app.state._ui_backend_descriptors is cached
