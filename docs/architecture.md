# Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                              FastAPI app                              │
│  /api/v1/search/web          /api/v1/search/images                    │
│  /api/v1/backends            /api/v1/health                           │
│  GET / · /search             (Jinja2 templates)                       │
└────────────────────────────────────────────────────────────────────────┘
                │
                ▼
┌────────────────────────────────────────────────────────────────────────┐
│                       app.search.RoutingService                       │
│  - resolve candidate backend list (rules ∩ fallback_order ∩ enabled)  │
│  - circuit breaker per backend                                         │
│  - first successful response wins                                     │
└────────────────────────────────────────────────────────────────────────┘
                │                                         │
                ▼                                         ▼
   ┌──────────────────────┐                    ┌────────────────────┐
   │ SearchRouterBackend  │                    │   MockBackend(s)   │
   │ POST /api/search     │                    │  deterministic     │
   │ X-API-Key            │                    │  blake2b seeds     │
   └──────────────────────┘                    └────────────────────┘
```

## Module layout

- `app/api/v1/` — REST endpoints. Each handler delegates to `RoutingService`
  and never reaches into a specific backend.
- `app/core/` — config, i18n, logging, cache, circuit breaker, errors.
- `app/search/` — schemas, registry, router, normalization helpers.
- `app/backends/` — concrete adapters. Each implements the
  `SearchBackend` Protocol via the `BaseBackend` ABC.
- `app/ui/` — server-rendered HTML routes, Jinja2 templates, CSS, JS.

## Request lifecycle

1. `RequestIdMiddleware` either reuses `X-Request-Id` from the caller or
   mints a new one (`new_request_id` from `app.core.ids`).
2. Pydantic validates the body. Validation failures map to `invalid_request`.
3. The endpoint computes a canonical cache key (sha256 of sorted JSON of the
   request fields) and asks the cache. On hit, the response is returned with
   `cache_hit=true`.
4. Otherwise the endpoint calls `RoutingService.route_*`, which:
   - Picks candidates per `routing.rules` and `fallback_order`.
   - Skips backends whose breaker is `open`.
   - On exception from a candidate, records the failure on the breaker and
     advances to the next candidate (only when `backend=auto`).
5. The endpoint stores the successful response in cache (modulo skip rules:
   `cache=false`, `time_range=day`, empty results, auth/quota errors).

## Why this shape

- **No DB.** v1 is stateless apart from Redis. Adding Postgres is a future
  step for audit logs and admin auth.
- **Pydantic-first contracts.** API + UI both consume the same Pydantic models,
  so behavioral changes to filters or fields propagate everywhere by design.
- **Mock-first dev loop.** The registry substitutes `MockSearchRouterBackend`
  whenever credentials are missing. The UI is therefore always demoable,
  screenshots are deterministic, and tests don't need network access.
