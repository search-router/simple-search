from __future__ import annotations


def test_request_id_middleware_echoes_inbound_header(client):
    response = client.post(
        "/api/v1/search/web",
        json={"q": "echo me", "limit": 2},
        headers={"X-Request-Id": "rid-from-client"},
    )
    assert response.status_code == 200
    assert response.headers["X-Request-Id"] == "rid-from-client"
    assert response.json()["request_id"] == "rid-from-client"


def test_request_id_middleware_generates_when_absent(client):
    response = client.post(
        "/api/v1/search/web",
        json={"q": "anonymous", "limit": 1},
    )
    assert response.status_code == 200
    rid = response.headers["X-Request-Id"]
    assert rid.startswith("req_")
    assert response.json()["request_id"] == rid


def test_request_id_present_on_validation_error(client):
    response = client.post(
        "/api/v1/search/web",
        json={"q": "ok", "foo": "bar"},
        headers={"X-Request-Id": "stable-rid"},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["request_id"] == "stable-rid"
    assert response.headers["X-Request-Id"] == "stable-rid"


def test_request_id_present_on_service_error(client):
    response = client.post(
        "/api/v1/search/web",
        json={"q": "x", "backend": "doesnotexist"},
        headers={"X-Request-Id": "explicit-rid"},
    )
    assert response.status_code == 400
    assert response.json()["request_id"] == "explicit-rid"
