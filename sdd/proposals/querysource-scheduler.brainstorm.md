# Brainstorm: QuerySource Scheduler (QSScheduler)

**Date**: 2026-03-25
**Author**: Jesus Lara / Claude
**Status**: exploration
**Recommended Option**: Option A

---

## Problem Statement

QuerySource currently has no mechanism to execute queries on a scheduled basis.
Users must manually trigger queries or rely on external cron jobs / orchestrators
to refresh cached data. This creates two pain points:

1. **Scheduled query execution**: Some queries need to run at fixed intervals
   (e.g., ETL-style refreshes, periodic reports). Today this requires external
   tooling outside QuerySource's control.

2. **Stale cache**: Queries with `is_cached=True` only refresh their cache when
   a user requests them. If a query is infrequently accessed but needs fresh data,
   the cache goes stale until the next request — causing slow first-hit latency.

**Who is affected**: Developers and operators who use QuerySource-backed dashboards,
reports, and data pipelines that depend on timely, pre-warmed data.

## Constraints & Requirements

- Must use APScheduler (already a dependency: `>=3.11.0,<3.12.0`)
- MemoryJobStore only — jobs rebuilt from `public.queries` on each startup
- Gated behind `ENABLE_QS_SCHEDULER` flag (default `False`) in `querysource/conf.py`
- `max_instances=1` per job, coalesce enabled (skip overlapping executions)
- Own PostgreSQL connection pool (not reusing QuerySource's pool)
- Integrates into aiohttp `on_startup` / `on_shutdown` signals via `QuerySource.setup()`
- Two job types:
  - **Scheduled queries**: driven by `attributes->scheduler` in `public.queries`
  - **Cache refresh**: driven by `cache_options` in `public.queries` (only if `is_cached=True`)
- v1: load all jobs on startup, no runtime add/remove API
- Error notification via pluggable callback system (logging-only for v1)
- Cache refresh uses existing `QS(slug=...).query()` pipeline (respects `is_cached` flag)

---

## Options Explored

### Option A: Embedded AsyncIOScheduler (NavScheduler Pattern)

Port the NavScheduler approach directly into QuerySource. A `QSScheduler` class
creates an APScheduler `AsyncIOScheduler`, reads `public.queries` at startup,
parses schedule definitions from `attributes` and `cache_options` columns, and
registers jobs. The scheduler lives inside the QuerySource process, sharing the
event loop.

Two job types are registered as lightweight async callables:
- **ScheduledQueryJob**: instantiates `QS(slug=...)`, calls `.query()`, discards result.
- **CacheRefreshJob**: same as above but specifically for cache warming — only registered
  when `cache_options` has schedule info AND `is_cached=True`.

Error handling uses a callback registry (`List[Callable]`) initialized with a
logging callback. Future callbacks (Telegram, Slack, webhook) can be appended.

**Pros:**
- Proven pattern — NavScheduler has been running in production in FlowTask
- Minimal new dependencies (APScheduler already installed)
- Full control over job lifecycle within the same process
- AsyncIOScheduler integrates naturally with aiohttp's event loop
- Schedule parsing logic can be directly ported from NavScheduler

**Cons:**
- Scheduler is coupled to the QuerySource process — if QS restarts, all jobs restart
- No distributed coordination (only one QS instance should run the scheduler)
- MemoryJobStore means no persistence across restarts (acceptable per requirements)

**Effort:** Medium

**Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `APScheduler>=3.11.0,<3.12.0` | Job scheduling engine | Already in pyproject.toml |
| `asyncdb` | Own PostgreSQL pool for reading queries | Already a dependency |

**Existing Code to Reuse:**
- `flowtask/scheduler/scheduler.py` — schedule parsing logic (cron, crontab, interval), listener patterns, job structure
- `querysource/models.py:QueryModel` — `attributes` (line 54), `cache_options` (line 78), `is_cached` (line 73) fields
- `querysource/queries/qs.py:QS` — query execution + caching via `.query()` method
- `querysource/conf.py` — configuration loading pattern (`config.getboolean`)
- `querysource/services.py:QuerySource.setup()` — aiohttp startup/shutdown signal registration (line 211-217)

---

### Option B: Standalone Scheduler Process

Instead of embedding the scheduler inside QuerySource, run it as a separate
process/service that connects to the same database, reads `public.queries`,
and triggers query execution via QuerySource's HTTP API (`/api/v2/services/queries/{slug}`).

The scheduler process uses APScheduler independently and calls QS endpoints
over HTTP when jobs fire.

**Pros:**
- Decoupled from QuerySource lifecycle — scheduler can restart independently
- No risk of scheduler work competing with user-facing query requests for resources
- Easy to scale: one scheduler instance, multiple QS instances
- Natural fit for distributed deployments

**Cons:**
- Additional process to deploy and monitor
- HTTP overhead for every job execution (latency, auth, error handling)
- Cache refresh requires the HTTP endpoint to support forced cache invalidation
- More complex deployment and configuration
- Loses the simplicity of the NavScheduler pattern

**Effort:** High

**Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `APScheduler>=3.11.0,<3.12.0` | Job scheduling | Already available |
| `aiohttp` | HTTP client for calling QS endpoints | Already a dependency |

**Existing Code to Reuse:**
- `flowtask/scheduler/scheduler.py` — schedule parsing only
- `querysource/handlers/` — HTTP endpoints as execution targets

---

### Option C: Database-Driven Polling (No APScheduler)

Instead of APScheduler, implement a simple async polling loop that periodically
(e.g., every 60 seconds) reads `public.queries` for rows with schedule definitions,
checks if each job's next run time has passed, and executes overdue queries.

Track last execution time in a separate table or in a `last_exec_time` column
on `public.queries`. The polling loop is a single `asyncio.create_task()` in the
aiohttp startup.

**Pros:**
- No scheduler library dependency (though APScheduler is already installed)
- Extremely simple to implement and reason about
- Naturally database-driven — schedule changes take effect on next poll
- Easy to add distributed locking (SELECT ... FOR UPDATE SKIP LOCKED)

**Cons:**
- Loses APScheduler's rich trigger system (cron expressions, intervals, jitter)
- Polling interval creates minimum latency (jobs can't fire more precisely than poll rate)
- Must implement cron/interval parsing manually or use a separate library
- No coalescing, misfire grace time, or max_instances out of the box
- Reinvents what APScheduler already provides

**Effort:** Medium (but higher long-term maintenance)

**Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `croniter` | Cron expression parsing | Would need to be added |

**Existing Code to Reuse:**
- `querysource/queries/qs.py:QS` — query execution
- `querysource/connections.py` — PostgreSQL pool

---

## Recommendation

**Option A** is recommended because:

- It follows the **proven NavScheduler pattern** already running in production in FlowTask,
  reducing design risk and enabling direct code reuse for schedule parsing.
- APScheduler is already a dependency — no new packages needed.
- The embedded approach matches the v1 requirements perfectly: single-instance,
  memory-only jobstore, load-on-startup, no runtime API.
- AsyncIOScheduler shares the aiohttp event loop naturally, avoiding thread/process
  coordination complexity.
- The callback-based notification system provides a clean extension point for future
  notification methods without changing the core scheduler.

**Tradeoff accepted**: The scheduler is coupled to the QuerySource process. For v1
this is fine since we're targeting single-instance deployments. If multi-instance
coordination is needed later, Option B's ideas (separate process or distributed
locking) can be layered on.

---

## Feature Description

### User-Facing Behavior

Operators configure scheduled execution by adding JSON to two columns in `public.queries`:

**1. Scheduled query execution** (`attributes` column):
```json
{
  "scheduler": {
    "schedule_type": "cron",
    "schedule": {"timezone": "America/New_York", "minute": 27, "hour": "*/2"}
  }
}
```

**2. Cache refresh** (`cache_options` column):
```json
{
  "schedule_type": "interval",
  "schedule": {"minutes": 30}
}
```

When QuerySource starts with `ENABLE_QS_SCHEDULER=true`, it automatically picks up
these definitions and registers APScheduler jobs. No API interaction needed for v1.

Supported `schedule_type` values: `cron`, `crontab`, `interval`.

### Internal Behavior

1. **Startup**: `QuerySource.setup()` creates `QSScheduler` (if enabled) and registers
   `scheduler.startup()` on `app.on_startup` and `scheduler.shutdown()` on `app.on_shutdown`.

2. **Initialization** (`scheduler.startup()`):
   - Creates own PostgreSQL connection pool via `asyncdb`.
   - Queries `SELECT query_slug, attributes, cache_options, is_cached FROM public.queries
     WHERE attributes IS NOT NULL OR cache_options IS NOT NULL`.
   - For each row:
     - If `attributes->'scheduler'` exists → parse and register a **ScheduledQueryJob**.
     - If `cache_options` has schedule info AND `is_cached=True` → register a **CacheRefreshJob**.
   - Creates `AsyncIOScheduler` with `MemoryJobStore`, `AsyncIOExecutor`, `coalesce=True`,
     `max_instances=1`.
   - Starts the scheduler.

3. **Job execution**:
   - **ScheduledQueryJob**: `async def run()` → `QS(slug=slug).query()` → discard result.
   - **CacheRefreshJob**: Same pipeline — `QS(slug=slug).query()` which internally calls
     `save_cache` when `is_cached=True`.

4. **Error handling**: Jobs catch exceptions and invoke all registered notification callbacks.
   v1 ships with a single `logging_callback` that logs errors at WARNING level.

5. **Shutdown** (`scheduler.shutdown()`): Gracefully shuts down APScheduler, closes the
   dedicated PostgreSQL pool.

### Edge Cases & Error Handling

- **`cache_options` present but `is_cached=False`**: Cache refresh job is NOT registered
  (no point refreshing a cache that won't be written).
- **Invalid schedule definition** (bad cron expression, missing fields): Log error, skip
  that job, continue loading others.
- **Query execution failure during job**: Caught by job wrapper, passed to notification
  callbacks, job remains scheduled for next run.
- **QSScheduler disabled** (`ENABLE_QS_SCHEDULER=False`): No scheduler created, no DB
  queries for schedule info, zero overhead.
- **No schedulable queries found**: Scheduler starts but with zero jobs — logs info message.
- **Overlapping execution**: Coalesced by APScheduler (`coalesce=True`, `max_instances=1`).

---

## Capabilities

### New Capabilities
- `qs-scheduler-core`: QSScheduler class with APScheduler integration, startup/shutdown lifecycle
- `qs-scheduled-query-job`: Job type for executing queries on a schedule from `attributes.scheduler`
- `qs-cache-refresh-job`: Job type for refreshing query cache from `cache_options` schedule
- `qs-scheduler-notifications`: Pluggable callback system for job error notifications

### Modified Capabilities
- `querysource-startup`: `QuerySource.setup()` conditionally creates and registers QSScheduler
- `querysource-conf`: Add `ENABLE_QS_SCHEDULER` flag

---

## Impact & Integration

| Affected Component | Impact Type | Notes |
|---|---|---|
| `querysource/services.py` | modifies | Import and conditionally init QSScheduler in `setup()` |
| `querysource/conf.py` | extends | Add `ENABLE_QS_SCHEDULER` config flag |
| `querysource/queries/qs.py` | depends on | Used by jobs to execute queries (no changes needed) |
| `querysource/models.py` | depends on | Uses `attributes`, `cache_options`, `is_cached` fields (no changes) |
| `querysource/scheduler/` | new | New package: `scheduler.py`, `jobs.py`, `notifications.py`, `__init__.py` |
| `pyproject.toml` | no change | APScheduler already a dependency |

---

## Parallelism Assessment

- **Internal parallelism**: Tasks can be split into 2-3 independent streams:
  (1) core scheduler + parsing, (2) job types + QS integration, (3) notification callbacks.
  However, jobs depend on the core scheduler, so true parallelism is limited.
- **Cross-feature independence**: No conflicts with in-flight specs. The scheduler is a
  new package (`querysource/scheduler/`) touching only `services.py` and `conf.py` at
  integration points.
- **Recommended isolation**: `per-spec` — all tasks sequential in one worktree.
- **Rationale**: The feature is self-contained with a clear dependency chain
  (conf → core scheduler → jobs → integration). Tasks are small enough that sequential
  execution in a single worktree is more efficient than coordinating parallel worktrees.

---

## Open Questions

- [ ] Should `dwh_scheduler` column (already exists in QueryModel, line 86) be used for DWH-specific scheduled jobs in the future, or is `attributes.scheduler` the universal mechanism? — *Owner: Jesus Lara*
- [ ] For v2: should cache refresh jobs support a minimal "execute + cache only" path that bypasses QS validation overhead? — *Owner: Jesus Lara*
- [ ] Should there be a maximum number of concurrent scheduled jobs across all queries (global limit), or is per-job `max_instances=1` sufficient? — *Owner: Jesus Lara*
