# Per-tenant queries

QuerySource supports multiple tenant-owned query definition stores, each with its own
schema containing a `queries` table. This enables isolated query management for different
clients or departments while sharing a single database infrastructure.

## Storage ownership and runtime program

### Physical identity

Each tenant store is identified by its canonical physical identity:

```
(database namespace, schema, table)
```

- **database namespace**: The PostgreSQL database name from the main connection
- **schema**: The tenant's schema (e.g., `client_a`, `sales_dept`)
- **table**: Always `queries` for definition storage

### Persistence versus runtime shape

Tenant-persisted definitions use the same base table structure as legacy `public.queries`
with one key difference:

| Field | Legacy store | Tenant store |
|-------|--------------|--------------|
| `program_slug` | Persisted | **Not persisted** — derived at runtime from owner schema |
| `program_id` | Optional | Required, defaults to 1 |

The runtime adapter creates a detached `QueryModel` with `program_slug=store.schema`
for tenant definitions. This adapter is never exported as DDL, schema metadata,
or CRUD payloads.

### Provisional DDL gate

Tenant stores must be created with the following columns:

```sql
CREATE TABLE "{schema}".queries (
    query_slug VARCHAR PRIMARY KEY,
    attributes JSONB,
    cache_options JSONB,
    provider VARCHAR,
    is_cached BOOLEAN DEFAULT false,
    query_raw TEXT,
    description VARCHAR,
    columns_definition TEXT[] DEFAULT '{}'::text[],
    program_id INTEGER DEFAULT 1,
    updated_at TIMESTAMPTZ DEFAULT now()
);
```

### Legacy store migration (FEAT-151)

Deploy the code first, then run on the legacy store:

```sql
ALTER TABLE public.queries ADD COLUMN IF NOT EXISTS columns_definition TEXT[] DEFAULT '{}'::text[];
```

Reads on a store without the column return an empty list. Writes omit an empty
`columns_definition`, so un-migrated stores keep accepting writes; a non-empty value
requires the column.

**Grants required**:
- SELECT for read operations
- INSERT, UPDATE, DELETE for write operations
- USAGE on the schema

**Foreign keys, sequences, and triggers**: These are permitted but not required.
Copied defaults and foreign keys can still reference public objects — this is
explicitly allowed. The `program_id` column is a provisional gate; future versions
may enforce stricter validation.

## Python and HTTP API

### Python interface

```python
from querysource.services import QuerySource
from querysource.queries import QS, MultiQS

# Initialize with tenant allowlist (None = all discovered stores)
qs = QuerySource(tenant_allowlist=["client_a", "client_b"])

# Execute with explicit tenant (all of QS()/MultiQS().query() are coroutines
# — always await them from inside an async function).
result, error = await QS(slug="my_report", tenant="client_a").query()

# Execute with explicit public (bypasses allowlist for legacy store)
result, error = await QS(slug="my_report", tenant="public").query()

# Execute with no tenant (uses configured default: public.queries)
result, error = await QS(slug="my_report").query()

# Child definitions inherit parent tenant
multi = MultiQS(slug="dashboard", tenant="client_a")
await multi.query()
# Children automatically use client_a unless explicitly overridden with an
# explicit ``tenant`` key (or ``tenant: null`` for the legacy store) on
# that individual child entry — see the Query component catalog.
```

### HTTP API routes

`TenantQueryHandler` (`querysource/handlers/tenant.py`) registers execution
and inspection routes only — it never mutates a stored definition. Defining
(creating/patching/deleting) a stored query is always a **management** route
(`QueryManager`, `querysource/handlers/manager.py`), never the tenant path
below.

| Route | Handler method | Contract |
|-------|-----------------|----------|
| `GET /api/v1/{tenant}/queries/` (and without the trailing slash) | `list` | Tenant definitions listing (paginated) |
| `POST /api/v1/{tenant}/queries/` (and without the trailing slash) | `query` | Inline multi-query execution (no stored slug in the body) |
| `GET /api/v1/{tenant}/queries/{slug}` | `query` | Stored single/multi execution |
| `POST /api/v1/{tenant}/queries/{slug}` | `query` | Execute stored query with a body (conditions) |
| `HEAD /api/v1/{tenant}/queries/{slug}` | `columns` (`get_columns`) | Column inspection headers only |
| `PATCH /api/v1/{tenant}/queries/{slug}` | `columns` | Column inspection body (**not** a definition update — see note above) |
| `GET /api/v1/{tenant}/queries/{slug}/test` and `POST .../test` | `test_slug` | Dry-run: validate without executing the underlying data query |
| `GET /api/v1/management/queries/{slug}?tenant={tenant}` | `QueryManager.get` | Management read for the selected owner |
| `POST /api/v1/management/queries/{slug}` with `{"tenant": "..."}` in the body | `QueryManager.post` | Create/upsert a definition for the selected owner |
| `PATCH /api/v1/management/queries/{slug}` with `{"tenant": "..."}` in the body | `QueryManager.patch` | Update mutable fields of the selected owner's definition |
| `DELETE /api/v1/management/queries/{slug}?tenant={tenant}` | `QueryManager.delete` | Delete the selected owner's definition |

### Kind-aware dispatch on the stored-slug routes (FEAT-151)

`GET|POST /api/v1/{tenant}/queries/{slug}`, `HEAD|PATCH .../{slug}`, and
`GET|POST .../{slug}/test` all read the stored definition **once**
(`TenantQueryHandler._prepare`) and classify it as **multi iff
`runtime.provider == 'multi'`** — the same predicate the scheduler uses.
`query_raw` is never sniffed. Multi definitions execute through
`QueryHandler`/`MultiQS`; every other provider keeps exact v2 parity through
`QueryService`/`QS` (headers, Redis cache, 204/404 mapping,
`_download`/`_filename`, `queryformat`, `X-Slug`).

Authorization (`_enforce_owned_slug`, action `slug:execute`) runs **before**
the definition is loaded, for both kinds — an unauthorized caller cannot
distinguish an existing slug from a missing one; both answer 404.

**Limitation**: a `provider='multi'` row whose `query_raw` is not multi JSON
still dispatches to `QueryHandler`, which falls back to single-query mode
(v3 and scheduler parity). Conversely, a JSON multi payload saved under
`provider='db'` is treated as an ordinary single query — `provider` is the
only discriminator, by design.

#### Multi columns (`HEAD`/`PATCH`)

For a multi definition, column inspection answers from the declared
`columns_definition` array (never `attributes.columns`, which is a
single-query-only fallback):

- `HEAD`: `204` with `X-Columns`/`X-Slug` headers, plus
  `X-Message: No Columns found` when the array is empty.
- `PATCH`: `200` with the JSON list when non-empty, else `204` with
  `X-Message: No Columns available`.

Without a stored definition on the request (legacy `/api/v3` callers), the
existing `204 No Columns available` response is unchanged.

#### Multi dry-run (`GET|POST .../{slug}/test`)

Validates a stored multi definition — resolving every saved child's owner
and existence — without executing any datasource query or `EXPLAIN`. The
JSON envelope extends the single dry-run shape with `kind`, `children`,
`files`, `sources`, and `warnings`:

```json
{
  "slug": "<slug>", "kind": "multi", "works": true, "error": null,
  "generated": 0.012, "execution": null,
  "tenant": "<url tenant or null>", "store": "<schema>.<table>",
  "children": [
    {"alias": "a", "slug": "child_a", "kind": "slug", "tenant": "<resolved>", "store": "<schema>.<table>",
     "exists": true, "allowed": true, "error": null},
    {"alias": "r", "slug": null, "kind": "raw", "tenant": null, "store": null,
     "exists": null, "allowed": null, "error": null}
  ],
  "files": ["f1"], "sources": ["sharepoint"], "warnings": [],
  "conditions": {}, "query": {"queries": {"...": "..."}}
}
```

`conditions` / `query` are omitted when `ignore_query` is set, mirroring the
single envelope. The single envelope itself (`/queries/{slug}/test` for a
non-multi definition) is unchanged — it never gains a `kind` key.

### Selector matrix

| Input | Resolution |
|-------|------------|
| No tenant / `None` | The registry's discovered default store: the `public`-schema store if one was discovered, else the first discovered store, else the hardcoded `public.queries` fallback (`TenantRegistry.resolve`) — **not** `QS_QUERIES_SCHEMA`/`QS_QUERIES_TABLE`, which only govern the separate legacy `QueryModel`-based path used when the tenant feature is not configured at all |
| Explicit nonempty tenant | Exact registered schema, fixed table `queries` |
| Explicit `public` | Literal `public.queries`, independently from the discovered default |
| Child definition without `tenant` | Inherit parent's resolved store |
| Child definition with `tenant: null` | Explicit configured legacy store |

### Route exclusions

- `management` schema is excluded from explicit tenant registration
- Test routes (`/test`, `/qs`) preserve legacy precedence
- Slug output suffixes (`:meta`, `:insert`) are reserved

### Explicit-public override examples

```python
# Force use of legacy public store, bypassing tenant allowlist
result, error = await QS(slug="legacy_report", tenant="public").query()

# HTTP equivalent
# GET /api/v1/public/queries/legacy_report
```

## Discovery and allowlist

### Startup discovery

On QuerySource initialization:

1. Open metadata connection to the main PostgreSQL database
2. Query `information_schema.tables` and `information_schema.columns` for tables
   named `queries` with a `query_slug` column
3. Exclude the system schemas `information_schema`, `pg_catalog`, `pg_toast`, `pg_temp`
4. Exclude the reserved `management` schema (reserved for legacy routes)
5. Exclude any schema whose name contains a `/`
6. Validate complete column contract for tenant compatibility
7. Publish immutable registry snapshot

### Allowlist behavior

```python
# Register all discovered compatible stores
qs = QuerySource(tenant_allowlist=None)

# Register only specific stores
qs = QuerySource(tenant_allowlist=["client_a", "client_b"])

# Register none (legacy only)
qs = QuerySource(tenant_allowlist=[])
```

- `None`: All compatible discovered stores
- `[]`: None (legacy only)
- `["a", "b"]`: Only exact names

Duplicate names collapse. Tenant strings are **not** trimmed, lowercased, or
Unicode-normalized.

### Diagnostics

The registry includes diagnostics for:
- Incompatible stores (missing columns, wrong types)
- Explicitly requested but missing/invisible stores
- Explicitly requested but disallowed stores

### Restart requirements

The registry is **immutable**. To change the allowlist or refresh discovery:
restart the QuerySource instance (a fresh process — `QuerySource` uses a
Singleton metaclass, and calling `QuerySource(tenant_allowlist=...)` again
within the same process with a *different* allowlist than the one already
initialized raises an "Incompatible QuerySource reinitialization" error
rather than silently reconfiguring; calling it again with the identical
allowlist is a no-op and returns the existing instance).

## Deployment, verification and rollback

### Staged allowlist rollout

1. **Phase 1**: Deploy tenant DDL to new schema(s)
2. **Phase 2**: Initialize QuerySource with `tenant_allowlist=None` to discover
3. **Phase 3**: Test with specific tenants via HTTP/Python
4. **Phase 4**: Expand allowlist incrementally
5. **Phase 5**: Remove legacy access if desired

### DDL/program_id gate

The `program_id` column is a provisional gate reserved for future routing or
policy decisions; `TenantQueryDefinition`/`QueryModel` both default it to
`1`. Discovery's own compatibility check (`TenantRegistry._is_compatible_store`)
only strictly requires a `query_slug` column — `program_id` is **not**
currently validated or required for a store to be discovered as tenant-
compatible, so an existing tenant table created without it is still
accepted as-is (no diagnostic entry is raised for its absence).

### Cache transition

- **Cold start applies to LEGACY/public queries too, not only tenant
  ones.** The new `qs:r2:<sha256(...)>` result-cache key format
  (`querysource/cache_identity.py` `result_cache_key`) replaces the
  PREVIOUS cache key entirely for every slug-based query — before this
  feature, the result cache key was the raw provider checksum; every
  slug-based `QS.query()` call, tenant or legacy, now composes its cache
  key from `(database_namespace, schema, table, slug, definition_revision,
  provider_checksum)` instead. This means rolling out this feature cold-
  starts the ENTIRE result cache for the whole QuerySource instance — every
  previously-cached `public.queries` result becomes unreachable under the
  old key on deploy, not just new tenant-store data. Operators should
  expect a cache-miss spike immediately after rollout and size downstream
  capacity accordingly; this is a one-time transition cost, not an ongoing
  behavior change (subsequent lookups populate the new key format normally,
  and Redis eviction/TTL reclaims the old, now-orphaned keys over time).
- **No dual-read**: Old unqualified keys are not read for tenants
- **Revision hashing**: Each lookup uses stable hash of persisted fields
- **TTL preservation**: Existing TTL and refresh options remain intact

### Scheduler ownership

The scheduler uses qualified job IDs:

| Store | Job ID format |
|-------|---------------|
| Legacy (public) | `query_<slug>`, `multi_<slug>`, `cache_<slug>` |
| Tenant | `qsj2-<kind>-<store_digest>-<encoded_slug>` |

Every job carries canonical schema/table/contract and tenant selection.
Scheduler sync failures are reported in response header `X-QS-Scheduler-Sync: failed`.

**Process ownership (per-process visibility only).** `QSScheduler`'s
APScheduler instance uses an in-memory `MemoryJobStore` — jobs live only in
the process that registered them. Under gunicorn (or any multi-worker
deployment) each worker process runs its own independent scheduler and
independently discovers/registers the same rows from the DB at its own
startup, so:

- The `GET`/`POST`/`DELETE`/`PATCH` job-management HTTP routes
  (`querysource/handlers/scheduler.py`) only see and mutate **the worker
  process that served that particular request** — a `POST` to sync a slug
  against worker A never affects worker B's copy of that same job, and a
  `GET /jobs` listing reflects only one worker's view, not a
  cluster-wide one.
- Actual scheduled execution therefore fires independently, once per
  worker process, for every job every worker has registered — there is no
  cross-process coordination or distributed lock. This is unchanged,
  pre-existing `QSScheduler` behavior (not introduced by per-tenant
  queries) but applies identically to every qualified tenant job.
- Operators running more than one worker process should account for this
  when sizing schedules (e.g. a `cache_refresh_job` firing N times, once
  per worker, for the same slug) — see "Unverified production gates"
  below.

### Versioned worker contract

Remote tenant execution uses a distinct handler:

```python
# Legacy remote
querysource.remote.query_handler

# Tenant remote (external worker contract)
querysource.remote.tenant_query_handler_v1
```

The tenant handler requires a versioned `owner` envelope:

```python
TenantOwnerEnvelope = {
    "version": 1,  # Literal[1]
    "database_namespace": str,
    "schema": str,
    "table": str,
    "contract": "legacy" | "tenant"
}
```

### Unsupported-worker errors

| Error code | HTTP status | Description |
|------------|-------------|-------------|
| `invalid_tenant` | 400 | Malformed tenant selector |
| `tenant_not_available` | 404 | Unknown/disallowed/incompatible selector |
| `query_not_found` | 404 | Slug not found in selected store |
| `tenant_store_unavailable` | 503 | Runtime store loss |
| `tenant_write_forbidden` | 403 | Database mutation permission failure |
| `tenant_worker_unsupported` | 502 | Remote worker does not support protocol |

### Unverified production gates

The following are **not** claimed to be verified in this implementation:

- Production DDL deployment automation
- External worker deployment and rollout
- Data migration from public to tenant stores
- Cross-tenant data movement

These remain deployment gates that operators must verify independently.

### Rollback procedure

1. Remove tenant from allowlist: `QuerySource(tenant_allowlist=[...])`
2. Restart QuerySource to refresh registry
3. Clear tenant cache keys: `DEL qs:r2:*` for tenant namespace
4. Verify legacy routes still work: `GET /api/v2/services/queries/{slug}`
5. Monitor scheduler for `tenant_not_available` errors

## Integration evidence

- Tenant registry discovery: `querysource/tenants.py` (`TenantRegistry.discover`/`resolve`)
- Repository boundary: separate persisted/runtime shapes in
  `querysource/repositories/definitions.py` (`DefinitionRepository`),
  built on `querysource/tenant_models.py` (`TenantQueryDefinition`)
- HTTP routes: tenant handlers in `querysource/handlers/tenant.py`
  (`TenantQueryHandler`); definition CRUD in `querysource/handlers/manager.py`
  (`QueryManager`)
- Scheduler integration: qualified job IDs generated in
  `querysource/scheduler/scheduler.py` (`QSScheduler._qualified_job_id`);
  owner envelope threaded through the job callables in
  `querysource/scheduler/jobs.py`
- Cache keys: owner-scoped revision keys in `querysource/cache_identity.py`
  (`definition_revision`/`result_cache_key`)
- Focused regression evidence: `tests/tenants/` (per-task unit/integration
  suites, TASK-716 through TASK-732) — see TASK-732's own completion note
  for exact commands and pass/skip/fail counts; `tests/tenants/
  test_integration.py` is the real, opt-in PostgreSQL/Redis suite and is
  correctly SKIPPED in any environment without
  `QS_TEST_POSTGRES_DSN`/`QS_TEST_REDIS_URL` configured (see "Unverified
  production gates" below).