# API reference

OpenAPI is auto-generated and served at `/docs` (Swagger UI) and `/redoc`.
This page covers the conventions that aren't visible in the OpenAPI document.

## Common request fields

| Field         | Type    | Notes                                                    |
| ------------- | ------- | -------------------------------------------------------- |
| `q`           | string  | 1..400 characters, leading/trailing whitespace stripped. |
| `backend`     | string  | `auto` (default), `search_router`, or any name           |
|               |         | registered via entry points.                             |
| `language`    | string  | BCP-47 tag.                                              |
| `region`      | string  | ISO 3166-1 alpha-2 (`RU`, `US`, `AE`, `SA`, `TR`, …).    |
| `ui_locale`   | string  | BCP-47 tag for translated strings in the UI flow.        |
| `direction`   | string  | `auto`, `ltr`, `rtl`. Overrides the heuristic.           |
| `page`        | int     | 0-indexed.                                               |
| `limit`       | int     | 1..100.                                                  |
| `safe_search` | enum    | `off`, `moderate` (default), `strict`.                   |
| `time_range`  | enum    | `day`, `week`, `month`, `year`, `all` (default).         |
| `site`        | string  | Restrict to a host (`example.com`).                      |
| `cache`       | bool    | `false` to bypass the Redis cache for this request.      |

## Image filters

`image_filters` accepts:

- `size`: `large` / `medium` / `small`.
- `orientation`: `horizontal` / `vertical` / `square`.
- `color`: a color name (`color`, `gray`, `red`, …) or omitted.
- `site`: domain restriction.

## Error envelope

Every error response uses:

```json
{
  "request_id": "req_01HX…",
  "error": {
    "code": "backend_unavailable",
    "message": "Search backend is temporarily unavailable",
    "backend": "search_router",
    "details": {}
  }
}
```

Codes: `invalid_request`, `unsupported_backend`, `unsupported_capability`,
`backend_auth_error`, `backend_quota_error`, `backend_timeout`,
`backend_unavailable`, `backend_bad_response`, `rate_limited`,
`internal_error`.
