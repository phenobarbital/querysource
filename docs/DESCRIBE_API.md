# Describe API (FEAT-148)

Read-only discovery of stored query slugs. None of these endpoints execute a query.

## Access rules

| Principal Kind | Condition | SQL Pre-filter | Notes |
|---|---|---|---|
| `superuser` | `userinfo["superuser"] is True` | None | ABAC still applies |
| `programs` | Non-empty normalized `userinfo["programs"]` | `lower(program_slug) = ANY($1::text[])` with `programs ∪ {'default'}` | Lowercase both sides |
| `authz` | No session, `QS_PBAC_ALLOW_SESSIONLESS_AUTHZ` and `request[AUTHZ_BACKEND_KEY]` | None | Synthetic identity `{'username': 'authz:<backend>', 'groups': ['authorized', backend]}` |
| `no_programs` | Session present, `programs` missing or empty, not superuser | Deny all (list `204`, detail/columns `404`) | `default` is **not** granted |
| `none` | No session, no sessionless authz | — | `401` on every describe route, before any DB access |

### ABAC Actions
- **List (`GET /api/v1/queries/describe`):** Requires `slug:list` or `slug:execute`.
- **Detail (`GET /api/v1/queries/{slug}/describe`):** Requires `slug:describe` or `slug:execute`. Denials return `404` to prevent enumeration.
- **Columns (`GET /api/v1/queries/{slug}/columns`):** Requires `slug:describe` or `slug:execute`. Denials return `404`.
- **Raw Query (`query_raw`):** Requires `slug:describe_raw`.
- **Admin Fields:** Gated by `userinfo.superuser` or a session group in `QS_DESCRIBE_ADMIN_GROUPS` (default `admin,superuser`).

### PBAC Disabled
When PBAC is disabled (i.e., `app['security']` is absent), ABAC checks are no-ops that allow access, mirroring `_enforce_pbac`. The program pre-filter and the `401` rule still apply.

## GET /api/v1/queries/describe

Retrieves a paginated list of query slugs visible to the caller.

### Query Parameters
- `page` (integer, default `1`): Page number.
- `page_size` (integer, default `50`, max `200`): Number of items per page.
- `sort` (string, default `updated_at:desc`): Sort field and direction (e.g., `query_slug:asc`). Sortable fields: `query_slug`, `description`, `program_slug`, `provider`, `is_cached`, `created_at`, `updated_at`.
- `q` or `search` (string): Case-insensitive search term across `query_slug`, `description`, and `program_slug`.
- Equality filters: Filter by `provider`, `program_slug`, etc.

### Response Headers
- `X-Total-Count`: Total number of matching records.
- `X-Page`: Current page.
- `X-Page-Size`: Page size.
- `X-Total-Pages`: Total pages.
- `X-Truncated`: `true` if the scan cap (`QS_DESCRIBE_MAX_SCAN`, default `10000`) was hit.

### Example Response (`200 OK`)
```json
{
  "data": [
    {
      "query_slug": "active_users",
      "provider": "pg",
      "description": "List of active users",
      "program_slug": "analytics",
      "updated_at": "2026-09-15T12:00:00Z"
    }
  ],
  "meta": {
    "page": 1,
    "page_size": 50,
    "total": 1,
    "total_pages": 1
  }
}
```

## GET /api/v1/queries/{slug}/describe

Retrieves detailed metadata for a specific query slug.

### Example Response (`200 OK`)
```json
{
  "query_slug": "active_users",
  "description": "List of active users",
  "program_slug": "analytics",
  "program_id": 12,
  "provider": "pg",
  "parser": "sql",
  "source": "public.queries",
  "query_raw": "SELECT * FROM users WHERE status = 'active' AND created_at > :start_date",
  "is_raw": true,
  "params": ["start_date"],
  "attributes": {},
  "conditions": {},
  "cond_definition": {},
  "fields": [],
  "filtering": [],
  "ordering": [],
  "grouping": [],
  "h_filtering": [],
  "qry_options": {},
  "is_cached": false,
  "cache_timeout": null,
  "cache_refresh": null,
  "dwh": false,
  "dwh_driver": null,
  "created_at": "2026-09-15T12:00:00Z",
  "updated_at": "2026-09-15T12:00:00Z",
  "derived": {
    "variables": [
      {
        "name": "start_date",
        "type": "date",
        "required": true,
        "default": null
      }
    ],
    "variables_supported": true,
    "structural_placeholders": [],
    "effective_cond_definition": {},
    "capabilities": {
      "fields": true,
      "filtering": true,
      "ordering": true,
      "grouping": true,
      "h_filtering": true,
      "qry_options": {},
      "refresh_param": "refresh"
    },
    "links": {
      "columns": "/api/v1/queries/active_users/columns",
      "vocabulary": "/api/v1/queries/vocabulary"
    },
    "warnings": []
  },
  "redacted": []
}
```

## GET /api/v1/queries/{slug}/columns

Retrieves the typed output columns of a query slug without executing it.

### Query Parameters
- Query-string conditions are merged exactly like `QueryService.get_columns`: `{**json_body, **query_params}`.

### Example Response (`200 OK`)
```json
{
  "slug": "active_users",
  "columns": [
    {"name": "id", "type": "integer"},
    {"name": "username", "type": "varchar"},
    {"name": "created_at", "type": "timestamp"}
  ],
  "columns_source": "prepare",
  "warnings": []
}
```

## GET /api/v1/queries/vocabulary

Retrieves the effective relative-date keyword vocabulary, PG constants, and informational functions.

### Example Response (`200 OK`)
```json
{
  "version": "1.0",
  "case_insensitive": true,
  "keywords": [
    {"name": "TODAY", "example": "09/15/2026"},
    {"name": "YESTERDAY", "example": "2026-09-14"},
    {"name": "FDOM", "example": "2026-09-01"},
    {"name": "LDOM", "example": "2026-09-30"},
    {"name": "CURRENT_YEAR", "example": 2026},
    {"name": "CURRENT_MONTH", "example": 9},
    {"name": "LAST_YEAR", "example": 2025}
  ],
  "constants": [
    {"name": "CURRENT_DATE", "type": "date"}
  ],
  "pg_functions": [
    {"name": "now", "type": "timestamp"}
  ],
  "functions": [
    {"name": "date_add", "invocable": false}
  ],
  "usage": "Relative-date keywords resolve on raw-query providers for untyped and date/datetime/timestamp conditions."
}
```

## Configuration

The Describe API behavior is controlled by the following configuration keys:

| Configuration Key | Default Value | Description |
|---|---|---|
| `QS_DESCRIBE_MAX_SCAN` | `10000` | The maximum number of query slugs scanned during list pagination. |
| `QS_DESCRIBE_ADMIN_GROUPS` | `admin,superuser` | Comma-separated list of session groups granted access to admin-only fields. |
| `QS_DESCRIBE_COLUMNS_TIMEOUT` | `5` | Timeout in seconds for preparing statements to fetch column metadata. |
