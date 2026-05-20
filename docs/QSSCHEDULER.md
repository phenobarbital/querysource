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

Multi-query slugs do **not** receive a cache-refresh job; their sub-slug
caches are refreshed inside the MultiQS pipeline itself.

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
