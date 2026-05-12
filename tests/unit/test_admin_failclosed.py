"""``require_admin_token=true`` fails closed when ``ADMIN_TOKEN`` is unset.

Also covers the new public ``/livez`` probe — it must remain reachable
without auth so the Dockerfile HEALTHCHECK keeps working in prod.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import load_config
from app.main import create_app
from tests.conftest import ROOT


@pytest.fixture
def failclosed_client(tmp_path):
    cfg = load_config(ROOT / "config.yaml", env={})
    cfg.security.rate_limit_per_minute = 0
    cfg.security.csrf_allowed_origins = []
    cfg.security.require_admin_token = True  # no ADMIN_TOKEN configured
    cfg.ads.db_path = str(tmp_path / "ads.sqlite")
    cfg.ads.resolved_session_secret = "test-session-secret-not-real"
    cfg.ads.session_https_only = False
    app = create_app(cfg)
    with TestClient(app) as c:
        yield c


def test_health_returns_503_when_token_required_but_unset(failclosed_client):
    resp = failclosed_client.get("/api/v1/health")
    assert resp.status_code == 503


def test_backends_returns_503_when_token_required_but_unset(failclosed_client):
    resp = failclosed_client.get("/api/v1/backends")
    assert resp.status_code == 503


def test_livez_open_even_when_admin_locked_down(failclosed_client):
    """``/livez`` is a process-liveness probe — never gated by ``ADMIN_TOKEN``."""
    resp = failclosed_client.get("/api/v1/livez")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_livez_open_with_no_token_at_all(client):
    """Same as above but in the default conftest config (token unset, not required)."""
    resp = client.get("/api/v1/livez")
    assert resp.status_code == 200
