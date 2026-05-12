"""``CsrfOriginMiddleware`` rejects unsafe-method requests without a known Origin."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import load_config
from app.main import create_app
from tests.conftest import ROOT


@pytest.fixture
def csrf_client(tmp_path):
    cfg = load_config(ROOT / "config.yaml", env={})
    cfg.security.rate_limit_per_minute = 0
    cfg.security.csrf_allowed_origins = ["http://testserver"]
    cfg.ads.db_path = str(tmp_path / "ads.sqlite")
    cfg.ads.resolved_session_secret = "test-session-secret-not-real"
    cfg.ads.session_https_only = False
    cfg.ads.login_max_attempts = 0
    app = create_app(cfg)
    with TestClient(app) as c:
        yield c


def test_post_without_origin_is_rejected(csrf_client):
    resp = csrf_client.post(
        "/auth/register",
        data={"username": "alice123", "password": "hunter2!aa"},
        follow_redirects=False,
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "csrf_origin_rejected"


def test_post_with_unknown_origin_is_rejected(csrf_client):
    resp = csrf_client.post(
        "/auth/register",
        data={"username": "alice123", "password": "hunter2!aa"},
        headers={"Origin": "https://evil.example.com"},
        follow_redirects=False,
    )
    assert resp.status_code == 403


def test_post_with_allowed_origin_passes(csrf_client):
    resp = csrf_client.post(
        "/auth/register",
        data={"username": "alice123", "password": "hunter2!aa"},
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/cabinet"


def test_post_falls_back_to_referer(csrf_client):
    resp = csrf_client.post(
        "/auth/register",
        data={"username": "alice456", "password": "hunter2!aa"},
        headers={"Referer": "http://testserver/login"},
        follow_redirects=False,
    )
    assert resp.status_code == 303


def test_get_is_not_blocked_even_without_origin(csrf_client):
    """CSRF gate only fires on unsafe methods; idempotent GETs always pass."""
    resp = csrf_client.get("/")
    assert resp.status_code == 200
