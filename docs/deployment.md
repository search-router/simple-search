# Deployment

## docker-compose (default)

```bash
cp .env.example .env
docker compose up --build
```

The compose file ships an `app` service and a `redis` service. The app waits
for Redis to be healthy. Without keys in `.env`, the app falls back to mock
backends — useful for staging and demos.

## Bare uvicorn

```bash
pip install -e ".[redis]"
APP_CONFIG_FILE=config.yaml uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Behind nginx / Caddy, set `proxy_set_header X-Forwarded-For $remote_addr;`
and `proxy_set_header X-Request-Id $request_id;` so the access logs stay
correlated.

## Configuration

- `APP_CONFIG_FILE` — path to YAML; defaults to `./config.yaml`.
- `LOG_LEVEL` — Python logging level for stdout JSON logs.
- `SEARCH_ROUTER_API_KEY` — Search Router REST key.
- `REDIS_URL` — `redis://host:6379/0`. Omit to use the in-process `NullCache`.

Secrets are read from environment only. Logs mask any field whose key matches
`api[_-]?key|authorization|x-api-key|token`.

## Operational notes / TODOs

- **Metrics.** TZ §15 lists Prometheus counters; we leave hooks but no
  exporter is wired. Add `prometheus_client` and a `/metrics` route in a
  follow-up.
- **Tracing.** OpenTelemetry not wired.
- **Rate limiting.** A skeleton is in place but disabled in v1.
  Use a Redis token-bucket or upstream gateway.
- **Admin auth.** `/api/v1/backends` is open in v1. Lock it behind a bearer
  token from env before exposing the service publicly.
- **Postgres.** Not used; healthcheck reflects this. If you add audit logs
  later, expose the connection string via `DATABASE_URL`.
- **Scaling.** The circuit breaker state is in-process. For multiple replicas,
  push it into Redis or accept that each replica trips independently — the
  blast radius of a flaky backend is bounded by `failure_threshold`.

## Contract checks before going to prod

1. `pytest -q` is green.
2. `ruff check .` and `mypy app/` are clean.
3. `/api/v1/health` returns `ok` for every backend with credentials provided.
4. `/api/v1/backends` shows `is_mock: false` for the providers you expect.
