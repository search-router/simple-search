"""TrustedHostMiddleware rejects unknown Host headers."""

from __future__ import annotations


def test_unknown_host_rejected(client):
    # TestClient adds Host: testserver, which is explicitly allowed in config.
    resp = client.get("/", headers={"Host": "evil.example.com"})
    assert resp.status_code == 400


def test_allowed_host_accepted(client):
    resp = client.get("/", headers={"Host": "127.0.0.1"})
    assert resp.status_code == 200
