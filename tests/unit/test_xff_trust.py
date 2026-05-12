"""Rate limiter only honors ``X-Forwarded-For`` when proxy trust is enabled."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import load_config
from app.main import create_app
from tests.conftest import ROOT


def _build_client(*, trust_xff: bool, limit: int, tmp_path) -> TestClient:
    cfg = load_config(ROOT / "config.yaml", env={})
    cfg.security.rate_limit_per_minute = limit
    cfg.security.trust_forwarded_headers = trust_xff
    cfg.security.csrf_allowed_origins = []
    cfg.ads.db_path = str(tmp_path / "ads.sqlite")
    cfg.ads.resolved_session_secret = "test-session-secret-not-real"
    cfg.ads.session_https_only = False
    app = create_app(cfg)
    return TestClient(app)


def test_spoofed_xff_cannot_bypass_rate_limit(tmp_path):
    """Default mode: XFF is ignored — every request keys on the socket IP."""
    with _build_client(trust_xff=False, limit=2, tmp_path=tmp_path) as c:
        assert c.get("/", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
        assert c.get("/", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 200
        # All requests share the testserver socket address — third one is blocked.
        blocked = c.get("/", headers={"X-Forwarded-For": "3.3.3.3"})
        assert blocked.status_code == 429


def test_xff_is_consulted_when_proxy_trust_is_on(tmp_path):
    """Behind a trusted proxy, each XFF identity gets its own window."""
    with _build_client(trust_xff=True, limit=2, tmp_path=tmp_path) as c:
        for ip in ("1.1.1.1", "1.1.1.1", "2.2.2.2", "2.2.2.2"):
            assert c.get("/", headers={"X-Forwarded-For": ip}).status_code == 200
        # The third hit from 1.1.1.1 trips its window, but 2.2.2.2 is still fine.
        assert c.get("/", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 429
        assert c.get("/", headers={"X-Forwarded-For": "9.9.9.9"}).status_code == 200
