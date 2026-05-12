"""Admin endpoints (/health, /backends) gated by ADMIN_TOKEN when set."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import load_config
from app.main import create_app
from tests.conftest import ROOT


@pytest.fixture
def admin_client():
    cfg = load_config(ROOT / "config.yaml", env={"ADMIN_TOKEN": "s3cret"})
    cfg.security.rate_limit_per_minute = 0
    app = create_app(cfg)
    with TestClient(app) as c:
        yield c


def test_health_open_when_no_token_configured(client):
    # Default conftest config has no ADMIN_TOKEN — endpoint open with warning.
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200


def test_health_requires_token_when_set(admin_client):
    resp = admin_client.get("/api/v1/health")
    assert resp.status_code == 401
    assert resp.headers.get("WWW-Authenticate", "").startswith("Bearer")


def test_health_accepts_correct_bearer(admin_client):
    resp = admin_client.get(
        "/api/v1/health", headers={"Authorization": "Bearer s3cret"}
    )
    assert resp.status_code == 200


def test_health_rejects_wrong_bearer(admin_client):
    resp = admin_client.get(
        "/api/v1/health", headers={"Authorization": "Bearer wrong"}
    )
    assert resp.status_code == 401


def test_backends_requires_token(admin_client):
    assert admin_client.get("/api/v1/backends").status_code == 401
    ok = admin_client.get(
        "/api/v1/backends", headers={"Authorization": "Bearer s3cret"}
    )
    assert ok.status_code == 200
