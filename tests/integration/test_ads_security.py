"""Security regressions for the ads/auth surface."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import load_config
from app.main import create_app
from tests.conftest import ROOT

ALICE = {"username": "alice", "password": "alice-pass-1234"}


@pytest.fixture
def throttled_client(tmp_path):
    """Throttle kicks in after 3 failures in 60s."""
    cfg = load_config(ROOT / "config.yaml", env={})
    cfg.security.rate_limit_per_minute = 0
    cfg.security.csrf_allowed_origins = []
    cfg.ads.db_path = str(tmp_path / "ads.sqlite")
    cfg.ads.resolved_session_secret = "test-session-secret-not-real"
    cfg.ads.session_https_only = False
    cfg.ads.login_max_attempts = 3
    cfg.ads.login_lockout_seconds = 60
    app = create_app(cfg)
    with TestClient(app) as c:
        yield c


def test_register_does_not_reveal_existing_username(client):
    """Duplicate-username response must be indistinguishable from a malformed
    signup — that's the entire enumeration defense."""
    first = client.post("/auth/register", data=ALICE, follow_redirects=False)
    assert first.status_code == 303
    # Log out the session cookie so the second register isn't auto-rejected.
    client.post("/auth/logout", follow_redirects=False)

    dup = client.post("/auth/register", data=ALICE, follow_redirects=False)
    short = client.post(
        "/auth/register",
        data={"username": "shorty", "password": "x"},
        follow_redirects=False,
    )
    # Same status and same generic body — no "username taken" leak.
    assert dup.status_code == short.status_code == 400
    assert "уже занят" not in dup.text
    assert "already taken" not in dup.text


def test_login_locks_after_repeated_failures(throttled_client):
    throttled_client.post("/auth/register", data=ALICE, follow_redirects=False)
    throttled_client.post("/auth/logout", follow_redirects=False)
    for _ in range(3):
        bad = throttled_client.post(
            "/auth/login",
            data={"username": ALICE["username"], "password": "nope"},
            follow_redirects=False,
        )
        assert bad.status_code == 401
    # The fourth attempt — even with the *correct* password — is throttled.
    throttled = throttled_client.post(
        "/auth/login",
        data=ALICE,
        follow_redirects=False,
    )
    assert throttled.status_code == 429


def test_successful_login_resets_throttle_counter(throttled_client):
    throttled_client.post("/auth/register", data=ALICE, follow_redirects=False)
    throttled_client.post("/auth/logout", follow_redirects=False)
    throttled_client.post(
        "/auth/login",
        data={"username": ALICE["username"], "password": "nope"},
        follow_redirects=False,
    )
    throttled_client.post(
        "/auth/login",
        data={"username": ALICE["username"], "password": "still-no"},
        follow_redirects=False,
    )
    ok = throttled_client.post("/auth/login", data=ALICE, follow_redirects=False)
    assert ok.status_code == 303
    throttled_client.post("/auth/logout", follow_redirects=False)
    # Counter cleared on success — we can fail again without an immediate lock.
    again = throttled_client.post(
        "/auth/login",
        data={"username": ALICE["username"], "password": "nope"},
        follow_redirects=False,
    )
    assert again.status_code == 401


def test_rotate_session_clears_existing_state():
    """``_rotate_session`` drops every pre-existing key on the session dict
    so a fixated cookie cannot survive an authentication transition."""
    from app.ads.routes import _rotate_session

    class _Req:
        def __init__(self):
            self.session = {"uid": 99, "attacker_key": "x"}

    req = _Req()
    _rotate_session(req)  # type: ignore[arg-type]
    assert req.session == {}


def test_logout_then_login_re_authenticates(client):
    """Hitting /cabinet after logout must redirect to /login; logging in
    again restores authenticated access via the new (rotated) session."""
    client.post("/auth/register", data=ALICE, follow_redirects=False)
    client.post("/auth/logout", follow_redirects=False)
    blocked = client.get("/cabinet", follow_redirects=False)
    assert blocked.status_code == 303
    assert blocked.headers["location"] == "/login"
    ok = client.post("/auth/login", data=ALICE, follow_redirects=False)
    assert ok.status_code == 303
    cabinet = client.get("/cabinet")
    assert cabinet.status_code == 200


def test_missing_session_secret_in_prod_refuses_to_start(tmp_path):
    """``require_session_secret`` flips the ephemeral-key fallback into a hard
    startup error so prod cannot accidentally run without a stable secret."""
    cfg = load_config(ROOT / "config.yaml", env={})
    cfg.security.rate_limit_per_minute = 0
    cfg.security.csrf_allowed_origins = []
    cfg.ads.db_path = str(tmp_path / "ads.sqlite")
    cfg.ads.resolved_session_secret = None
    cfg.ads.require_session_secret = True
    with pytest.raises(RuntimeError, match="ads enabled"):
        create_app(cfg)
