"""Rate limit middleware: nth request beyond the limit returns 429."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import load_config
from app.main import create_app
from tests.conftest import ROOT


@pytest.fixture
def limited_client():
    cfg = load_config(ROOT / "config.yaml", env={})
    cfg.security.rate_limit_per_minute = 2
    app = create_app(cfg)
    with TestClient(app) as c:
        yield c


def test_third_request_in_window_is_rate_limited(limited_client):
    assert limited_client.get("/").status_code == 200
    assert limited_client.get("/").status_code == 200
    blocked = limited_client.get("/")
    assert blocked.status_code == 429
    assert blocked.headers.get("Retry-After")
    body = blocked.json()
    assert body["error"]["code"] == "rate_limited"
    assert body["request_id"]


def test_zero_limit_disables_throttling(client):
    # Default test fixture sets rate_limit_per_minute=0 — many requests OK.
    for _ in range(5):
        assert client.get("/").status_code == 200
