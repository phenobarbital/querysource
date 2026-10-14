# Semantic Layer (FEAT-175)

The semantic layer lets a QuerySource slug describe **business metrics and dimensions** instead of SQL.
A request names metrics and dimensions; QuerySource compiles them to literal SQL for the datasource's
dialect with [Sidemantic](https://github.com/sidequery/sidemantic) and runs it through the normal
`QS` pipeline (cache, output formats, PBAC). Models are written in Sidemantic's native YAML or in
Ossie format.

Install the optional extra:

```bash
uv pip install "querysource[semantic]"   # sidemantic[ossie]>=0.12,<0.13 and sqlglot
```

Without the extra, importing `querysource.semantic` still works; compiling a semantic slug
returns **501** (`Semantic compiler unavailable: install querysource[semantic]`).

## Resource YAML

A semantic slug is an ordinary row of the queries table with `provider = 'semantic'`; its
`query_raw` holds this document:

```yaml
name: epson.sales            # slug name
kind: semantic               # must be "semantic"
connection: pg_admin         # optional datasource name; default connection when omitted
semantic:
  format: sidemantic         # "sidemantic" or "ossie" (default: ossie)
  compiler: sidemantic       # only supported compiler
  scope: null                # Ossie scope id (optional)
  definition:                # inline mapping, OR a path relative to QS_SEMANTIC_MODELS_DIR
    models:
      - name: orders
        table: orders
        metrics:
          - {name: revenue, agg: sum, sql: amount}
          - {name: order_count, agg: count}
        dimensions:
          - {name: region, type: categorical}
          - {name: order_date, type: time, granularity: day}
governance:
  row_filters:
    default: []              # applies to everybody
    analyst:                 # applies to principals whose roles contain "analyst"
      - {field: region, op: in, value: [ES, PT]}
```

Notes:

- Metric keys are Sidemantic's native ones (`agg`, `sql`); `type`/`expression` are rejected.
- A string `definition` is a path resolved inside `QS_SEMANTIC_MODELS_DIR`; paths escaping the
  directory, missing files and files larger than `QS_SEMANTIC_MAX_MODEL_BYTES` are **400**.
- An inline definition wins over a `path` key inside it (a warning is returned).
- Unknown keys anywhere in the resource are rejected (**400**).

## Request contract

Requests are carried in the slug's conditions (JSON body or query parameters):

| Key | Type | Meaning |
|---|---|---|
| `metrics` | list[str] | Metric names, qualified (`orders.revenue`) or unique bare names |
| `dimensions` | list[str] | Dimension names; a `__grain` suffix (`order_date__month`) picks a grain |
| `filters` | list | Structured `{field, op, value}` or SQL-like strings |
| `time_grain` | str | `day`, `week`, `month`, `quarter` or `year`; rewrites time dimensions |
| `order_by` | list[str] | `field [asc|desc]` entries |
| `limit` | int | Positive; defaults to `QS_SEMANTIC_DEFAULT_LIMIT`, capped at `QS_SEMANTIC_MAX_LIMIT` (warning) |
| `offset` | int | Non-negative |

Rules:

- Structured filter operators are exactly: `=`, `!=`, `>`, `>=`, `<`, `<=`, `in`, `not in`, `between`,
  `like`, `ilike`, `is null`, `is not null`. String filters are parsed with sqlglot and rejected on unknown
  columns, subqueries or non-allow-listed functions. Values are always quoted as literals, so
  `"1 OR 1=1"` cannot escape its predicate.
- More than `QS_SEMANTIC_MAX_FILTERS` filters is **400**.
- A `time_grain` without any time dimension in the request is **400**. An explicit `__grain` suffix wins
  over `time_grain`.
- Unknown names list the available ones; ambiguous bare names list the candidates (**400**).
- Transport keys (`refresh`, `_output_options`, `queryformat`, ...) are dropped before validation.
- **Precedence:** `QueryService` merges `{**json_body, **query_params}`, so a `metrics` query parameter
  overrides a `metrics` key in the body.

## Governance

Four independent layers, all conjunctive:

1. **PBAC on the slug and fields** (only when PBAC is configured; otherwise everything is allowed):
   `semantic:execute` on `ResourceType.SEMANTIC` / `<slug>`; `semantic:use` on
   `<slug>:metric:<name>` and `<slug>:dimension:<name>` for every requested field. A denied slug is
   **404** (`QueryAccessDenied`); a denied field is **403** naming the field.
2. **Datasource**: `datasource:use` on the resolved datasource and its driver.
3. **Sidemantic `security:`** blocks in the model. The compiler receives `user_attributes` equal to
   `QSPrincipal.to_userinfo()` plus `tenant_id`. A model with a `security:` block and no principal is
   **403**. Note that `access` is a Jinja boolean expression or a bool.
4. **`governance.row_filters`**: the `default` block and every block named after a role in
   `principal.roles` are validated like caller filters and AND-ed into the request. Unknown roles are
   ignored. An invalid governance filter is **500** (logged with slug and role).

With a principal absent and PBAC enabled the request is denied (fail-closed).

## Dialects

The datasource driver selects the SQL dialect:

| Driver | Dialect |
|---|---|
| `pg`, `pg_admin` | `postgres` |
| `bigquery` | `bigquery` |
| `mysql`, `mariadb` | `mysql` |
| `sqlserver` | `tsql` |
| `oracle` | `oracle` |
| `sqlite` | `sqlite` |
| any other SQL driver | `ansi` (with a warning) |

Non-SQL drivers (mongo, redis, elastic, influx, ...) are **400**.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `QS_SEMANTIC_MODELS_DIR` | `<BASE_DIR>/semantic` | Root for path-based model definitions |
| `QS_SEMANTIC_DEFAULT_LIMIT` | `500` | Row limit when the request has none |
| `QS_SEMANTIC_MAX_LIMIT` | `5000` | Hard cap on `limit` |
| `QS_SEMANTIC_MAX_FILTERS` | `32` | Maximum filters per request |
| `QS_SEMANTIC_CACHE_SIZE` | `64` | Compiled-layer LRU entries |
| `QS_SEMANTIC_MAX_MODEL_BYTES` | `2097152` | Maximum model file size |

Results are cached by the normal QS cache; the cache key includes the model version, so changing a
definition never serves stale rows.

## Errors

| Code | Cause |
|---|---|
| 400 | Invalid request (unknown/ambiguous field, bad operator, grain, limit, filter), malformed resource YAML or model, path escape, non-SQL datasource |
| 403 | A field denied by PBAC or by Sidemantic `security:` |
| 404 | Slug denied by PBAC, or (catalog) missing/non-semantic slug |
| 501 | Compiler not installed (`querysource[semantic]`) |
| 500 | Invalid `governance.row_filters` entry (configuration error) |

All semantic errors derive from `querysource.semantic.SemanticError` and are re-raised unchanged by
`QS.build_provider`.

## Python API

Two steps over `QS`, both async:

```python
from querysource import semantic

compiled = await semantic.compile(
    "epson.sales",
    metrics=["revenue"],
    dimensions=["order_date"],
    time_grain="month",
    principal=principal,       # QSPrincipal | None
)
print(compiled.sql)            # literal SQL, never executed by compile()
rows = await semantic.execute(compiled, principal=principal)
```

`compile()` builds the provider without running a query. `execute()` re-runs the embedded request
through `QS`, so PBAC and governance apply again.

## Catalog endpoint

`GET /api/v1/semantic/{slug}/catalog` and `GET /api/v1/{tenant}/semantic/{slug}/catalog` return a
`CatalogPayload`: `slug`, `model_version`, `dialect`, `models`, `metrics`, `dimensions` and
`row_filters_applied`. Fields denied by PBAC are listed with `granted: false` and no detail. Responses:
200, 401 without a principal, 404 for a missing, non-semantic or denied slug. `describe_slug()` also adds
`derived.semantic` for semantic slugs.

## MultiQS

`MultiQS` forwards its principal to every child `QueryObject`, so a semantic child enforces the same PBAC,
governance and `security:` rules as a direct call. A semantic child run without a principal against a
`security:` model is denied. Remote (qworker) executors do not propagate the principal and log that fact.

## License note

Sidemantic is AGPL-3.0. QuerySource uses it unmodified, as an **optional** extra
(`querysource[semantic]`), and imports it lazily only from `querysource/semantic/compilers/sidemantic.py`.
Deployments that do not install the extra are unaffected.
