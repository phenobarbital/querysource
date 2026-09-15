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
    program_id INTEGER DEFAULT 1,
    updated_at TIMESTAMPTZ DEFAULT now()
);
```

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
from querysource import QuerySource, QS, MultiQS

# Initialize with tenant allowlist (None = all discovered stores)
qs = QuerySource(tenant_allowlist=["client_a", "client_b"])

# Execute with explicit tenant
result = QS(slug="my_report", tenant="client_a").query()

# Execute with explicit public (bypasses allowlist for legacy store)
result = QS(slug="my_report", tenant="public").query()

# Execute with no tenant (uses configured default: public.queries)
result = QS(slug="my_report").query()

# Child definitions inherit parent tenant
multi = MultiQS(slug="dashboard", tenant="client_a")
# Children automatically use client_a unless explicitly overridden
```

### HTTP API routes

| Route | Contract |
|-------|----------|
| `GET /api/v1/{tenant}/queries/` | Tenant definitions listing |
| `POST /api/v1/{tenant}/queries/` | Inline multi execution |
| `GET /api/v1/{tenant}/queries/{slug}` | Stored single/multi execution |
| `POST /api/v1/{tenant}/queries/{slug}` | Execute stored query |
| `HEAD /api/v1/{tenant}/queries/{slug}` | Column inspection |
| `PATCH /api/v1/{tenant}/queries/{slug}` | Update definition |
| `GET /api/v1/{tenant}/queries/{slug}/test` | Dry-run execution |
| `GET /api/v1/management/queries?tenant={tenant}` | Management listing |
| `POST /api/v1/management/queries` with `{"tenant": "..."}` | Management write |

### Selector matrix

| Input | Resolution |
|-------|------------|
| No tenant / `None` | Configured `QS_QUERIES_SCHEMA.QS_QUERIES_TABLE` (default: `public.queries`) |
| Explicit nonempty tenant | Exact registered schema, fixed table `queries` |
| Explicit `public` | Literal `public.queries`, independently from configured default |
| Child definition without `tenant` | Inherit parent's resolved store |
| Child definition with `tenant: null` | Explicit configured legacy store |

### Route exclusions

- `management` schema is excluded from explicit tenant registration
- Test routes (`/test`, `/qs`) preserve legacy precedence
- Slug output suffixes (`:meta`, `:insert`) are reserved

### Explicit-public override examples

```python
# Force use of legacy public store, bypassing tenant allowlist
result = QS(slug="legacy_report", tenant="public").query()

# HTTP equivalent
# GET /api/v1/public/queries/legacy_report
```

## Discovery and allowlist

### Startup discovery

On QuerySource initialization:

1. Open metadata connection to the main PostgreSQL database
2. Query `information_schema.tables` and `information_schema.columns` for tables
   named `queries` with a `query_slug` column
3. Exclude `information_schema`, `pg_catalog`, `pg_*` schemas
4. Exclude `management` schema (reserved for legacy routes)
5. Validate complete column contract for tenant compatibility
6. Publish immutable registry snapshot

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
restart the QuerySource instance.

## Deployment, verification and rollback

### Staged allowlist rollout

1. **Phase 1**: Deploy tenant DDL to new schema(s)
2. **Phase 2**: Initialize QuerySource with `tenant_allowlist=None` to discover
3. **Phase 3**: Test with specific tenants via HTTP/Python
4. **Phase 4**: Expand allowlist incrementally
5. **Phase 5**: Remove legacy access if desired

### DDL/program_id gate

The `program_id` column is a provisional gate. It defaults to `1` and can be
used for future routing or policy decisions. Existing tenant stores without
this column are accepted but may be flagged in diagnostics.

### Cache transition

- **Cold start**: All tenant caches start empty (`qs:r2:*` keys)
- **No dual-read**: Old unqualified keys are not read for tenants
- **Revision hashing**: Each lookup uses stable hash of persisted fields
- **TTL preservation**: Existing TTL and refresh options remain intact

### Scheduler ownership

The scheduler uses qualified job IDs:

| Store | Job ID format |
|-------|---------------|
| Legacy (public) | `query_<slug>`, `multi_<slug>`, `cache_<slug>` |
| Tenant | `qsj2:<kind>:<store_digest>:<encoded_slug>` |

Every job carries canonical schema/table/contract and tenant selection.
Scheduler sync failures are reported in response header `X-QS-Scheduler-Sync: failed`.

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
4. Verify legacy routes still work: `GET /api/v1/queries/{slug}`
5. Monitor scheduler for `tenant_not_available` errors

## Integration evidence

- Tenant registry discovery: Implemented in `querysource/tenants.py`
- Repository boundary: Separate storage/runtime shapes in `querysource/repository.py`
- HTTP routes: Tenant handlers in `querysource/handlers/tenant.py`
- Scheduler integration: Qualified job IDs in `querysource/scheduler/jobs.py`
- Cache keys: Owner-scoped in `querysource/cache.py`