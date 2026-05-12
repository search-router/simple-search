def test_health(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["version"]
    assert "search_router" in body["backends"]
    assert body["redis"] in {"ok", "down"}


def test_backends(client):
    response = client.get("/api/v1/backends")
    assert response.status_code == 200
    body = response.json()
    names = [b["name"] for b in body["backends"]]
    assert "search_router" in names
    for entry in body["backends"]:
        assert "capabilities" in entry
        assert entry["circuit_state"] in {"closed", "open", "half_open"}


def test_backends_endpoint_survives_misbehaving_healthcheck(client):
    """A custom backend whose healthcheck raises must not 500 the listing."""
    from app.search.schemas import BackendCapabilities

    class _BrokenAdapter:
        name = "broken"
        is_mock = False

        async def healthcheck(self):
            raise RuntimeError("boom")

        def capabilities(self):
            return BackendCapabilities()

    registry = client.app.state.routing.registry
    registry._backends["broken"] = _BrokenAdapter()
    try:
        response = client.get("/api/v1/backends")
        assert response.status_code == 200
        body = response.json()
        broken = next(b for b in body["backends"] if b["name"] == "broken")
        assert broken["healthy"] is False
    finally:
        registry._backends.pop("broken", None)


def test_health_endpoint_survives_misbehaving_healthcheck(client):
    from app.search.schemas import BackendCapabilities

    class _BrokenAdapter:
        name = "broken"
        is_mock = False

        async def healthcheck(self):
            raise RuntimeError("boom")

        def capabilities(self):
            return BackendCapabilities()

    registry = client.app.state.routing.registry
    registry._backends["broken"] = _BrokenAdapter()
    try:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        body = response.json()
        assert body["backends"]["broken"] == "down"
    finally:
        registry._backends.pop("broken", None)


def test_health_overall_is_degraded_when_all_backends_only_degraded(client):
    """All-degraded means partial service, not full outage — must report 'degraded'."""
    from app.search.schemas import BackendCapabilities, BackendHealth

    class _Degraded:
        name = "deg"
        is_mock = False

        async def healthcheck(self):
            return BackendHealth(status="degraded")

        def capabilities(self):
            return BackendCapabilities()

    routing = client.app.state.routing
    saved = dict(routing.registry._backends)
    routing.registry._backends.clear()
    routing.registry._backends["deg-a"] = _Degraded()
    routing.registry._backends["deg-b"] = _Degraded()
    try:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "degraded", body
        assert body["backends"] == {"deg-a": "degraded", "deg-b": "degraded"}
    finally:
        routing.registry._backends.clear()
        routing.registry._backends.update(saved)


def test_health_overall_is_down_when_every_backend_is_down(client):
    from app.search.schemas import BackendCapabilities, BackendHealth

    class _Down:
        name = "d"
        is_mock = False

        async def healthcheck(self):
            return BackendHealth(status="down")

        def capabilities(self):
            return BackendCapabilities()

    routing = client.app.state.routing
    saved = dict(routing.registry._backends)
    routing.registry._backends.clear()
    routing.registry._backends["d-a"] = _Down()
    routing.registry._backends["d-b"] = _Down()
    try:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json()["status"] == "down"
    finally:
        routing.registry._backends.clear()
        routing.registry._backends.update(saved)


def test_health_overall_is_ok_with_empty_registry(client):
    """No backends configured is not a failure — service is up to render the UI."""
    routing = client.app.state.routing
    saved = dict(routing.registry._backends)
    routing.registry._backends.clear()
    try:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["backends"] == {}
    finally:
        routing.registry._backends.update(saved)
