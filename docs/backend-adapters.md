# Adding a new backend

Any class that satisfies `app.backends.base.SearchBackend` can be plugged in
without touching the public API or the response schemas.

## 1. Subclass `BaseBackend`

```python
# my_pkg/google_backend.py
from app.backends.base import BackendContext, BaseBackend
from app.search.schemas import (
    BackendCapabilities, ImageSearchRequest, ImageSearchResponse,
    WebSearchRequest, WebSearchResponse,
)

class GoogleBackend(BaseBackend):
    name = "google"

    def __init__(self, *, api_key: str, http) -> None:
        super().__init__(http=http)
        self._api_key = api_key

    @classmethod
    def from_config(cls, config, http):
        return cls(api_key=config.resolved_api_key, http=http)

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            web_search=True,
            image_search_by_text=True,
            max_results=50,
            response_formats=["json"],
        )

    async def search_web(self, req: WebSearchRequest, ctx: BackendContext) -> WebSearchResponse:
        ...
    async def search_images(self, req: ImageSearchRequest, ctx: BackendContext) -> ImageSearchResponse:
        ...
```

## 2. Map errors

Use `BaseBackend.map_http_error(status, backend=self.name, body=...)` and
`BaseBackend.map_transport_error(exc, backend=self.name)` so the routing layer
sees the same exception types as the bundled adapters.

## 3. Register

### Option A: configuration

```yaml
search:
  backends:
    google:
      enabled: true
      type: google
      api_key_env: GOOGLE_API_KEY
      timeout_ms: 5000
```

…and add an entry to `_DEFAULT_FACTORIES` in `app/search/registry.py`.

### Option B: Python entry points (no fork required)

In your distribution's `pyproject.toml`:

```toml
[project.entry-points."search_service.backends"]
google = "my_pkg.google_backend:GoogleBackend"
```

Install your package alongside this service. The registry will discover it
on startup. The built-in entry-point name (`search_router`) cannot be
overridden by external packages.

## 4. Contract tests

Copy `tests/unit/test_search_router_adapter.py` and replace the transport
fixtures with samples from your provider. The assertions to keep:

- Normalized fields populate `provider`, `domain`, `rank`, `direction`.
- `raw` payload exists in memory but never appears in JSON dumps by default.
- 401/403/402/5xx/transport timeouts map to the matching `Backend*Error`.

## 5. Healthcheck

Override `_healthcheck_probe` to issue a cheap call (e.g. a 1-result search).
Don't reuse production timeouts here; the default `BaseBackend.healthcheck`
records the result and exposes it via `/api/v1/backends`.
