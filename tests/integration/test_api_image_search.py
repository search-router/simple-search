def test_image_search_via_mock(client):
    response = client.post(
        "/api/v1/search/images",
        json={"q": "котики", "language": "ru", "limit": 4},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["type"] == "images"
    assert len(body["results"]) == 4
    assert body["results"][0]["image_url"].startswith("https://")


def test_image_search_corrupt_cache_falls_through(client):
    """The image-search endpoint must also recover from a poisoned cache slot."""

    class _CorruptCache:
        name = "corrupt"

        async def get(self, key):
            return b"this is not JSON"

        async def set(self, key, value, ttl):
            return None

        async def ping(self):
            return True

        async def aclose(self):
            return None

    client.app.state.cache = _CorruptCache()
    try:
        response = client.post(
            "/api/v1/search/images",
            json={"q": "cats", "limit": 2},
        )
        assert response.status_code == 200
        assert response.json()["cache_hit"] is False
    finally:
        from app.core.cache import NullCache

        client.app.state.cache = NullCache()
