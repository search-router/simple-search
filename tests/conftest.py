"""Shared pytest fixtures."""

from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient

from app.core.config import load_config
from app.main import create_app

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def config(tmp_path):
    """Load the on-disk config with no env vars so mocks are forced."""
    cfg = load_config(ROOT / "config.yaml", env={})
    # Tests run many requests against the TestClient; the per-IP fixed window
    # would otherwise trip on the third or fourth call from 127.0.0.1.
    cfg.security.rate_limit_per_minute = 0
    # TestClient does not synthesize Origin/Referer headers, so disable the
    # CSRF middleware by default — tests that want to exercise it build their
    # own AppConfig with a populated ``csrf_allowed_origins``.
    cfg.security.csrf_allowed_origins = []
    # Sandbox ad state per test so the repo's ./data/ stays clean and tests
    # never see each other's bids/wallets.
    cfg.ads.db_path = str(tmp_path / "ads.sqlite")
    cfg.ads.resolved_session_secret = "test-session-secret-not-real"
    # TestClient drives plain HTTP, so a Secure cookie would never be sent
    # back. Production config keeps ``session_https_only`` true.
    cfg.ads.session_https_only = False
    # Don't lock test users out after a handful of intentional bad logins.
    cfg.ads.login_max_attempts = 0
    return cfg


@pytest.fixture
def app(config):
    return create_app(config)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def config_no_ads(tmp_path):
    """Variant for the disabled-ads code path."""
    cfg = load_config(ROOT / "config.yaml", env={})
    cfg.security.rate_limit_per_minute = 0
    cfg.security.csrf_allowed_origins = []
    cfg.ads.enabled = False
    cfg.ads.db_path = str(tmp_path / "ads.sqlite")
    return cfg


@pytest.fixture
def client_no_ads(config_no_ads):
    app = create_app(config_no_ads)
    with TestClient(app) as test_client:
        yield test_client
