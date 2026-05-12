# Search Router adapter

- Endpoint: `POST https://search-router.com/api/search`
- Auth: header `X-API-Key: <SEARCH_ROUTER_API_KEY>`.
- Request body:
  ```json
  { "query": "text", "search_type": "web" | "images", "num_results": 10 }
  ```
- `num_results` is clamped to 1..100.

## Field mapping (web)

| Search Router | Unified         |
|---------------|-----------------|
| `title`       | `title`         |
| `url`         | `url`           |
| `domain`      | `domain` (fallback: parsed from URL) |
| `snippet` / `description` | `snippet` |

## Field mapping (images)

| Search Router       | Unified         |
|---------------------|-----------------|
| `title` / `alt`     | `title`         |
| `url`               | `page_url`      |
| `image_url`         | `image_url`     |
| `thumbnail_url`     | `thumbnail_url` |
| `width` / `height`  | `width` / `height` |
| `domain`            | `domain`        |

## Error mapping

| HTTP   | Internal                  |
|--------|---------------------------|
| 400    | `BackendBadRequestError`  |
| 401    | `BackendAuthError`        |
| 402    | `BackendQuotaError`       |
| 500    | `BackendServerError`      |
| 503    | `BackendUnavailableError` |
| read timeout / connect error | `BackendTimeoutError` / `BackendUnavailableError` |

## Capabilities

```json
{
  "web_search": true,
  "image_search_by_text": true,
  "max_results": 100,
  "response_formats": ["json"]
}
```
