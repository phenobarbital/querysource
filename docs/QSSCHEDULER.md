# QSScheduler — Embedded Query Scheduler

QSScheduler is an embedded APScheduler that runs scheduled queries from
`public.queries` definitions. It is gated behind the `ENABLE_QS_SCHEDULER`
config flag and uses an in-memory job store (jobs rebuild on every restart).

## Job kinds

| Job kind        | Job ID prefix   | Source column(s)                                    | Runtime entry point          |
|-----------------|-----------------|-----------------------------------------------------|------------------------------|
| Single-query    | `query_<slug>`  | `attributes.scheduler`                              | `QS(slug=...).query()`       |
| Multi-query     | `multi_<slug>`  | `attributes.scheduler` + `provider='multi'`         | `MultiQS(slug=...).query()`  |
| Cache refresh   | `cache_<slug>`  | `cache_options` + `is_cached=True`                  | `QS(slug=...).query()`       |

Multi-query slugs do **not** receive a cache-refresh job; sub-slug caches
are written by normal QS execution if `is_cached=True` is set on each
sub-slug row.

## Scheduling a single query

Set `attributes.scheduler` on a `public.queries` row with the default
`provider='db'` (or any single-source driver name):

```sql
UPDATE public.queries
SET attributes = jsonb_set(
    COALESCE(attributes, '{}'::jsonb),
    '{scheduler}',
    '{"schedule_type": "interval", "schedule": {"minutes": 30}}'::jsonb
)
WHERE query_slug = 'my_query';
```

The job registers at startup as `query_my_query` and runs `QS(slug='my_query').query()`
on each tick. The result is discarded (fire-and-forget).

## Scheduling a multi-query

Set `provider = 'multi'` and supply a multi-query JSON payload in `query_raw`:

```sql
UPDATE public.queries
SET attributes  = jsonb_set(
        COALESCE(attributes, '{}'::jsonb),
        '{scheduler}',
        '{"schedule_type": "interval", "schedule": {"hours": 1}}'::jsonb
    ),
    provider    = 'multi',
    query_raw   = '{"queries": {"sub_a": {"slug": "a"}, "sub_b": {"slug": "b"}}}'
WHERE query_slug = 'my_multi';
```

The job registers at startup as `multi_my_multi` and runs the MultiQS
sub-query fan-out end-to-end on each tick. Results are discarded.
The row is excluded from cache-refresh job registration.

## Reserved JSON sub-key

The JSON key `attributes.scheduler.output` is reserved for a future
result-handling patch (see FEAT-092). In v1, QSScheduler parses this
key but ignores it. You may include it today; the scheduler will log a
single `DEBUG` line per startup acknowledging it:

```json
{
  "scheduler": {
    "schedule_type": "interval",
    "schedule": {"hours": 1},
    "output": {"type": "tableOutput"}
  }
}
```

The presence of `output` does **not** change the registered job or its
kwargs. It is forward-compatible — present-day schedulers can include
it harmlessly.

## Misconfiguration WARN

If `provider='multi'` but `query_raw` is empty, plain SQL, or otherwise
not a valid multi-query JSON payload (a dict with a `queries` or `files`
key), the scheduler logs a `WARNING` at startup and registers the job
anyway:

```
WARNING  QSScheduler: Multi-query slug 'my_multi' has query_raw that is
not a multi-query JSON payload — MultiQS will fall back to single-query
mode at runtime.
```

At runtime, MultiQS silently falls back to single-query mode for
non-JSON `query_raw`. This is a known footgun: authors expecting the
sub-query fan-out will see it silently run only the plain SQL.

## Configuration flags

| Flag | Default | Description |
|------|---------|-------------|
| `ENABLE_QS_SCHEDULER` | `False` | Gate: must be `True` for QSScheduler to activate. |
| `QS_SCHEDULER_TIMEZONE` | system | Timezone for all triggers. |
| `QS_SCHEDULER_MAX_INSTANCES` | 1 | Max concurrent firings per job. |
| `QS_SCHEDULER_COALESCE` | `True` | Coalesce missed firings into one. |

No new flag is introduced by multi-query support (FEAT-092).

## Tenant ownership

QSScheduler supports scheduling queries from both legacy (`public.queries`)
and tenant-owned stores. Each scheduled job carries ownership information
that determines which store its query is loaded from.

### Default IDs versus qsj2

For the configured default store (typically `public.queries`), jobs use the
legacy ID format:

- Single-query: `query_<slug>`
- Multi-query: `multi_<slug>`
- Cache refresh: `cache_<slug>`

For tenant stores, jobs use the qualified ID format:

```
qsj2:<kind>:<store_digest>:<encoded_slug>
```

Where:
- `<kind>` is `query`, `multi`, or `cache`
- `<store_digest>` is a URL-safe digest of the store's physical identity
- `<encoded_slug>` is the URL-safe encoding of the query slug

### Owner envelope

Every tenant job carries an owner envelope that identifies the source store:

```python
{
    "version": 1,
    "database_namespace": "querysource",
    "schema": "client_a",
    "table": "queries",
    "contract": "tenant"
}
```

The scheduler validates this envelope against its initialized registry
on deserialization. If the store is no longer available, the job fails
with `tenant_not_available`.

### Selected-owner sync

When a management mutation changes a scheduled query (update/delete),
the scheduler synchronizes only the affected owner's jobs. A sync
failure is reported in the response header:

```
X-QS-Scheduler-Sync: failed
```

This header indicates the database mutation succeeded but scheduler
synchronization failed. The job may continue running with stale data
until the next restart or manual refresh.

### Failure header

If scheduler synchronization fails after a management mutation, the
response includes:

```
X-QS-Scheduler-Sync: failed
```

Check logs for details. The mutation is **not** rolled back — the database
change persists even if the scheduler fails to update.

### Restart semantics

The scheduler registry is rebuilt on every restart. To change scheduled
jobs for a tenant:
1. Update the query definition in the tenant's `queries` table
2. Restart the QuerySource to rebuild the scheduler registry

There is no runtime registry refresh — restart is required for any
changes to the tenant registry or allowlist.

## Troubleshooting

**All jobs are missing after startup**

If the database pool fails to connect at scheduler startup, all job
registration is silently skipped and the scheduler logs:

```
ERROR  QSScheduler: Failed to query schedulable rows: <error detail>
```

Verify that `default_dsn` is correctly configured and that the PostgreSQL
server is reachable before the aiohttp app starts.

**Tenant jobs fail with tenant_not_available**

The tenant store is no longer in the registry. This can happen if:
- The tenant was removed from the allowlist
- The tenant schema was dropped
- The registry was rebuilt without the tenant

Verify the tenant is still in the allowlist and the schema exists.

**Scheduler sync failed header on management update**

The database mutation succeeded but the scheduler could not update
the job. Check logs for the underlying error. The job may need a
manual restart.
