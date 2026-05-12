"""Response should carry CSP and the rest of the static security headers."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import load_config
from app.main import create_app
from tests.conftest import ROOT


def test_security_headers_present_on_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Content-Security-Policy" in resp.headers
    assert "frame-ancestors 'none'" in resp.headers["Content-Security-Policy"]
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    assert "Permissions-Policy" in resp.headers


def test_security_headers_present_on_json(client):
    resp = client.post("/api/v1/search/web", json={"q": "hello"})
    assert resp.status_code == 200
    assert "Content-Security-Policy" in resp.headers
    assert resp.headers["X-Frame-Options"] == "DENY"


def test_hsts_absent_over_plain_http(client):
    """No HSTS over plain HTTP — that would teach browsers to upgrade requests
    to an origin that may not actually serve HTTPS yet."""
    resp = client.get("/")
    assert "Strict-Transport-Security" not in resp.headers


def test_hsts_added_when_forwarded_proto_is_https(tmp_path):
    """When the deployment trusts proxy headers and the proxy reports HTTPS,
    HSTS is added even though the upstream socket is plain HTTP."""
    cfg = load_config(ROOT / "config.yaml", env={})
    cfg.security.rate_limit_per_minute = 0
    cfg.security.csrf_allowed_origins = []
    cfg.security.hsts_enabled = True
    cfg.security.trust_forwarded_headers = True
    cfg.ads.db_path = str(tmp_path / "ads.sqlite")
    cfg.ads.resolved_session_secret = "test-session-secret-not-real"
    cfg.ads.session_https_only = False
    app = create_app(cfg)
    with TestClient(app) as c:
        resp = c.get("/", headers={"X-Forwarded-Proto": "https"})
        assert resp.status_code == 200
        assert "Strict-Transport-Security" in resp.headers
        # And not when the proxy reports plain HTTP.
        resp2 = c.get("/", headers={"X-Forwarded-Proto": "http"})
        assert "Strict-Transport-Security" not in resp2.headers
