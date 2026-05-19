---
type: feature
base_branch: dev
---

# Brainstorm: QSScheduler Multi-Query Support

**Date**: 2026-05-20
**Author**: Jesus Lara / Claude
**Status**: exploration
**Recommended Option**: Option A

---

## Problem Statement

`QSScheduler._load_scheduled_queries` (`querysource/scheduler/scheduler.py:89`)
currently registers **every** row carrying `attributes.scheduler` against the
single job callable `scheduled_query_job` (`querysource/scheduler/jobs.py:12`),
which always instantiates `QS(slug=...)`. That works for ordinary single-slug
queries, but the system also stores **multi-queries** in `public.queries` —
rows where `query_raw` is a JSON payload of `{queries: ..., files: ...}` and
where the runtime entry point is the `MultiQS` class
(`querysource/queries/multi/__init__.py:53`), not `QS`.

When a multi-query slug is scheduled today:

- The job fires `QS(slug=...)` → `QS.build_provider()` looks up the provider
  based on the row, but the QS pipeline is designed for single-source slugs
  and does not orchestrate the sub-queries / sub-files declared inside the
  multi-query JSON. Either it fails outright, runs only the outer
  provider against the JSON-shaped `query_raw`, or quietly produces nothing
  useful.
- Operators have no path to schedule a multi-query at all.

We already read `provider` in the `SELECT` (`scheduler.py:206`) but never
inspect it — the column is fetched and discarded. This brainstorm proposes
using it as the routing signal.

## Constraints & Requirements

- Must not regress any existing single-query schedule (provider defaults to
  `'db'` per `models.py:74`; rows with that value MUST continue to use
  `scheduled_query_job`).
- New job type must reuse `NotificationManager` and respect the same
  `(job_id, slug, error)` callback contract
  (`querysource/scheduler/notifications.py:37`).
- Scheduled jobs run without an HTTP request — no PBAC pre-flight, no
  `request.app['security']`, no user session is available. The MultiQS
  invocation path used by the handler (`querysource/handlers/multi.py:222`)
  must therefore be reduced to the minimum that does NOT require a request.
- Job-store remains `MemoryJobStore` (`scheduler.py:48`); no persistence
  changes.
- Cache-refresh path stays single-query-only: multi-slugs do not have a single
  checksum, so `_load_cache_refresh_jobs` must skip rows where
  `provider == 'multi'` cleanly.
- Reserved-but-unused config key `attributes.scheduler.output` (object,
  optional) must be tolerated and ignored today, leaving the JSON schema
  forward-compatible with a future result-handling patch.

---

## Options Explored

### Option A: Provider-Routing Inside `_load_scheduled_queries`

Extend the existing single loader so that for each schedulable row it
inspects `row['provider']`:

- If `provider == 'multi'`: register `scheduled_multiqs_job` with job id
  `multi_<slug>` and a job name `"Scheduled multi-query: <slug>"`.
- Otherwise: keep current behavior — register `scheduled_query_job` with id
  `query_<slug>`.

Add a new async callable `scheduled_multiqs_job(slug, notification_manager,
**kwargs)` in `querysource/scheduler/jobs.py` that does:

1. `qs = MultiQS(slug=slug)` (no `request`, no `user_session`, no
   `conditions`).
2. `await qs.query()` — MultiQS handles the slug fetch, detects whether
   `query_raw` is a multi-query JSON payload, runs `ThreadQuery`/`ThreadFile`
   fan-out, and discards the returned `(result, options)` tuple.
3. On exception → `notification_manager.notify(job_id=f"multi_{slug}",
   slug=slug, error=exc)` (same shape as the single-query path).

`_load_cache_refresh_jobs` gets a one-line guard: skip rows where
`row.get('provider') == 'multi'` so multi-slugs never produce a
`cache_<slug>` job.

The reserved `attributes.scheduler.output` sub-key is parsed off the row but
treated as a no-op today (just logged at `DEBUG` so future implementers can
see it being received).

✅ **Pros:**
- Minimal blast radius — one new job callable, ~25 LOC in `scheduler.py`,
  no public-API changes.
- Backwards-compatible by construction: rows with `provider in {None, 'db',
  any single-source driver}` keep their current code path bit-for-bit.
- Honors the explicit Round-1 preference for "single loader, branch inside
  loop".
- Distinct `multi_<slug>` job-id prefix makes APScheduler introspection /
  log filtering trivial.

❌ **Cons:**
- The `_load_scheduled_queries` body now has two responsibilities. If we
  later add a third provider category (DWH-scheduler? streaming?), this
  branch will grow.
- Silent fallback inside MultiQS means a misconfigured row
  (`provider='multi'` + plain SQL `query_raw`) will load and run, just
  degenerating to single-query mode at execution time. Acceptable per the
  Round-2 decision but worth noting in code review.

📊 **Effort:** Low

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `apscheduler` | Already a project dep; `AsyncIOScheduler.add_job` accepts any async callable | Same usage as today — no version bump |
| `aiohttp` | Web app lifecycle hooks (`on_startup`/`on_shutdown`) | Already used by `QSScheduler.setup` |
| `asyncdb` | `AsyncPool`/`AsyncDB` for the `public.queries` SELECT | Already used by `QSScheduler.startup` |

🔗 **Existing Code to Reuse:**
- `querysource/scheduler/scheduler.py:89` — `_load_scheduled_queries` is the
  insertion point for the provider branch.
- `querysource/scheduler/scheduler.py:131` — `_load_cache_refresh_jobs` needs
  a one-line provider-guard.
- `querysource/scheduler/scheduler.py:206` — the `SELECT` already lists
  `provider`; only the consumer needs to start reading it.
- `querysource/scheduler/jobs.py` — file already hosts both
  `scheduled_query_job` and `cache_refresh_job`; new `scheduled_multiqs_job`
  belongs alongside them, with the identical try/except/notify shape.
- `querysource/queries/multi/__init__.py:53` — `MultiQS(slug=...)` constructor
  already supports request-less invocation; `MultiQS.query()` already
  handles the single-query-fallback when `query_raw` is plain SQL
  (lines 113–141).
- `querysource/scheduler/notifications.py:37` — `NotificationManager.notify`
  signature is unchanged.

---

### Option B: Universal MultiQS Job (Replace the QS Path Entirely)

Drop `scheduled_query_job` and route **every** scheduled row through a new
job that always calls `MultiQS(slug=slug)`. The fallback inside
`MultiQS.query()` (lines 113–141 in `queries/multi/__init__.py`) already
handles plain-SQL slugs by wrapping them into a single-element
`{slug: {"slug": slug, **conditions}}` map and running them through the
multi pipeline. So in theory, one job-type suffices.

✅ **Pros:**
- One code path for the scheduler; no provider-routing logic at all.
- Future-proof if multi-query JSON eventually becomes the default storage
  format.

❌ **Cons:**
- Behavior change for every existing single-query schedule: each tick now
  spins up a `ThreadQuery` wrapper that runs the SQL on its own thread, then
  joins back to the event loop. Heavier, harder to reason about, observably
  different in metrics.
- The MultiQS code path is currently the **expensive** branch — used for
  composed multi-source queries. Forcing trivial single-query schedules
  through it is a regression risk we cannot quietly accept.
- Cache-refresh job (`cache_refresh_job`) stays on `QS` regardless, so the
  "one code path" claim is already broken.
- Goes against the Round-1 user preference for `provider='multi'` as an
  explicit routing signal.

📊 **Effort:** Low (code-wise) but Medium (regression-test surface)

📦 **Libraries / Tools:** Same as Option A.

🔗 **Existing Code to Reuse:**
- Same touch-points as Option A, but `scheduled_query_job` would be deleted
  (or kept as a thin alias) and every `add_job` call switches to the new
  callable.

---

### Option C: Strategy-Table Dispatch (provider → callable)

Define a module-level mapping in `querysource/scheduler/jobs.py`:

```text
PROVIDER_JOB_MAP = {
    'multi': scheduled_multiqs_job,
    # default fallback for any other provider value
    None: scheduled_query_job,
}
```

`_load_scheduled_queries` becomes a single straight-line loop that looks up
`PROVIDER_JOB_MAP.get(row['provider'], scheduled_query_job)` and uses a
per-callable `_job_id_prefix` attribute to build the APScheduler id.

✅ **Pros:**
- Cleanest extensibility for the case where new provider categories arrive
  (e.g. `'dwh'`, `'streaming'`). Each new category is one new callable +
  one new dict entry; the loader body never changes again.
- Inverts dependency: scheduler doesn't know about MultiQS or QS, only
  about a job-callable contract.

❌ **Cons:**
- Premature abstraction for two callables. The current SDD culture and the
  Round-1 user preference both favor in-line branching at this scale.
- Hides the routing logic behind a registry — harder to spot at a glance
  when something new arrives. The `_load_scheduled_queries` body becomes a
  no-op except for the dict lookup, which is fine for veteran readers but
  worse for code-review newcomers.
- Adds two indirections (lookup + prefix attribute) for code that fits in
  one `if/else`.

📊 **Effort:** Low–Medium

📦 **Libraries / Tools:** Same as Option A.

🔗 **Existing Code to Reuse:**
- All of Option A's reuse list, plus a new module-level constant
  `PROVIDER_JOB_MAP` in `querysource/scheduler/jobs.py`.

---

## Recommendation

**Option A** is recommended.

It exactly matches all four Round-1 selections (single loader with inline
branch, explicit `provider='multi'` marker, cache-refresh exclusion,
discard-with-hook-for-later) and all four Round-2 selections (identical
notification semantics, `multi_<slug>` job ids, silent MultiQS fallback on
misconfig, reserved `attributes.scheduler.output` sub-key). It is also the
smallest reachable change — every other affected component in the codebase
keeps its public surface unchanged.

We are trading off Option C's extensibility for clarity at the current
scale: the codebase has exactly two job categories (and Option C's
strategy table would itself need extending the moment a fourth arrives).
We are trading off Option B's "one code path" appeal for behavioral safety
on the long tail of trivial single-query schedules. Both tradeoffs are
acceptable given the user-stated preferences and the
non-regression constraint.

---

## Feature Description

### User-Facing Behavior

DBAs / query authors mark a row in `public.queries` as a multi-query
schedule by setting two columns:

- `provider = 'multi'`
- `attributes.scheduler = { "schedule_type": "interval|cron|crontab",
  "schedule": { ... } }` (identical to the existing single-query schedule
  contract).

`query_raw` carries the multi-query JSON payload (`{"queries": {...},
"files": {...}}`) just as it does today for the `/v2/query` MultiQuery
endpoint.

Once `ENABLE_QS_SCHEDULER` is on (existing gate), at next service startup
the scheduler will register an APScheduler job with id `multi_<slug>` and
human-readable name `"Scheduled multi-query: <slug>"`. On every fire, the
job runs the multi-query end-to-end on the server's own event loop, with
no HTTP request, no user session, and no result returned to any client.

Any failure (whether the orchestration itself or any constituent
ThreadQuery) reaches `NotificationManager.notify(job_id="multi_<slug>",
slug=<slug>, error=...)` — exactly like the single-query path today, so
existing notification callbacks (logging today, Telegram/Slack/webhook in
the future) light up automatically with no per-callback change required.

A multi-query slug is **not** eligible for a cache-refresh job: the
`cache_<slug>` job kind does not apply (multi-queries have no single
cache checksum). Authors who set `is_cached=True` on a `provider='multi'`
row will simply not see a cache-refresh job; sub-slug caching continues to
behave as it does inside the MultiQS pipeline itself.

The schedule payload may optionally include an `output` sub-key (e.g.
`attributes.scheduler.output = {"type": "tableOutput", ...}`). Today it is
parsed but ignored, leaving the JSON shape stable for a future patch that
writes the merged DataFrame back to a sink.

### Internal Behavior

1. `QSScheduler.startup()` (`scheduler.py:179`) runs the existing `SELECT
   query_slug, attributes, cache_options, provider, is_cached FROM
   public.queries`. The `provider` column is now actually consumed.
2. `_load_scheduled_queries(rows)` iterates rows. For each row with a valid
   `attributes.scheduler`:
   - Parse `schedule_type` + `schedule` (unchanged) via `_parse_trigger`.
   - Branch on `row.get('provider')`:
     - `'multi'` → `add_job(scheduled_multiqs_job, ..., id=f"multi_{slug}",
       name=f"Scheduled multi-query: {slug}", kwargs={"slug": slug,
       "notification_manager": ...})`.
     - anything else → unchanged single-query path
       (`scheduled_query_job`, `id=f"query_{slug}"`).
3. `_load_cache_refresh_jobs(rows)` gains one early-continue: if
   `row.get('provider') == 'multi'`, skip the row before the
   `is_cached`/`cache_options` checks. Single-query rows with caching
   behave exactly as today.
4. `scheduled_multiqs_job(slug, notification_manager, **kwargs)` (new in
   `jobs.py`):
   - Lazy-imports `MultiQS` (the existing single-query job does the same
     lazy-import dance for `QS`).
   - Instantiates `MultiQS(slug=slug)` — no `request`, no `queries=`, no
     `files=`, no `query=`, no `conditions=`, no `user_session=`.
   - `await qs.query()` and discard the returned `(result, options)`.
   - On any `Exception`: `logger.warning(...)`, then
     `notification_manager.notify(job_id=f"multi_{slug}", slug=slug,
     error=exc)`.

### Edge Cases & Error Handling

- **Misconfigured row** (`provider='multi'`, `query_raw` empty or plain
  SQL): MultiQS' existing fallback at
  `queries/multi/__init__.py:128-141` wraps the slug into
  `{slug: {"slug": slug}}` and runs it as a single-query through
  `ThreadQuery`. The scheduler does not warn; the row will execute and
  most-likely succeed.
- **`provider='multi'` row missing `attributes.scheduler`**: same skip path
  the single-query loader uses today (`if not scheduler_def: continue`).
  No job is registered.
- **`provider='multi'` row with `cache_options`/`is_cached=True`**:
  scheduled multi job is registered, cache-refresh job is **not**. This is
  deliberate — there is no single checksum to refresh.
- **DB query for schedulable rows fails**: existing fallback (`rows = []`)
  applies; no jobs of either kind get registered. Unchanged.
- **`MultiQS.query()` raises `DataNotFound`**: notify with the exception
  (same as today for `QS`); not treated specially. A scheduled job that
  produces no data is, by user choice, considered a failure to investigate.
- **`MultiQS.query()` raises partial failure** (e.g. one ThreadQuery
  raised): MultiQS' existing aggregation re-raises a single exception
  (`querysource/queries/multi/__init__.py:165-194`). The scheduler notifies
  once per fire — matching the Round-2 "identical to scheduled_query_job"
  choice.
- **Job-id collision with existing `query_<slug>`**: impossible by
  construction — multi jobs use the `multi_` prefix, single-query jobs use
  `query_`, cache-refresh jobs use `cache_`.
- **Reserved `attributes.scheduler.output` sub-key today**: the loader does
  not inspect it; the new job callable does not accept it; it is
  effectively forward-compatible dead data until a follow-up patch wires
  it.

---

## Capabilities

### New Capabilities

- `qs-scheduled-multiquery-job`: Job type for executing multi-queries on a
  schedule. Registered for any `public.queries` row where
  `provider == 'multi'` and `attributes.scheduler` is well-formed.

### Modified Capabilities

- `qs-scheduled-query-job` (introduced in
  `sdd/proposals/querysource-scheduler.brainstorm.md`): the loader now
  routes rows by `provider`, so this capability covers only rows where
  `provider != 'multi'`. Behavior for those rows is unchanged.

---

## Impact & Integration

| Affected Component | Impact Type | Notes |
|---|---|---|
| `querysource/scheduler/scheduler.py` | modifies | `_load_scheduled_queries` branches on `provider`; `_load_cache_refresh_jobs` skips `provider=='multi'`; import list adds `scheduled_multiqs_job`. |
| `querysource/scheduler/jobs.py` | extends | New async callable `scheduled_multiqs_job(slug, notification_manager, **kwargs)` alongside existing two. Module docstring updated to describe three job kinds. |
| `querysource/scheduler/notifications.py` | depends on | No change — `NotificationManager.notify` signature unchanged. |
| `querysource/queries/multi/__init__.py` | depends on | No change — uses `MultiQS(slug=...)` exactly as supported by the existing constructor and the slug-only `query()` branch. |
| `public.queries.provider` | reads | Loader newly **consumes** the column it has been fetching all along. No DDL change. |
| `attributes.scheduler.output` (JSON sub-key) | reserves | Parsed but ignored. No schema-level migration; just documented as reserved. |
| Operator runbook / docs | extends | The QSScheduler README/runbook needs one paragraph: "to schedule a multi-query, set `provider = 'multi'`; jobs appear as `multi_<slug>` in APScheduler logs." |

---

## Code Context

### User-Provided Code

No code snippets were pasted by the user; the brief was prose-only.

### Verified Codebase References

#### Classes & Signatures

```python
# From querysource/scheduler/scheduler.py:30
class QSScheduler:
    def __init__(self, loop: asyncio.AbstractEventLoop = None): ...  # line 37
    def _parse_trigger(self, schedule_type: str, schedule: dict): ...  # line 57
    def _load_scheduled_queries(self, rows: list) -> int: ...  # line 89
    def _load_cache_refresh_jobs(self, rows: list) -> int: ...  # line 131
    async def startup(self, app: web.Application) -> None: ...  # line 179
    async def shutdown(self, app: web.Application) -> None: ...  # line 230

# From querysource/scheduler/jobs.py:12
async def scheduled_query_job(
    slug: str,
    notification_manager=None,
    **kwargs,
) -> None: ...

# From querysource/scheduler/jobs.py:40
async def cache_refresh_job(
    slug: str,
    notification_manager=None,
    **kwargs,
) -> None: ...

# From querysource/scheduler/notifications.py:21
class NotificationManager:
    def add_callback(self, callback: Callable) -> None: ...  # line 29
    def notify(self, job_id: str, slug: str, error: Exception) -> None: ...  # line 37

# From querysource/queries/multi/__init__.py:53
class MultiQS(BaseQuery):
    def __init__(
        self,
        slug: str = None,
        queries: Optional[list] = None,
        files: Optional[list] = None,
        query: Optional[dict] = None,
        conditions: dict = None,
        request: web.Request = None,
        loop: asyncio.AbstractEventLoop = None,
        user_session: Optional[object] = None,
        **kwargs,
    ): ...  # line 59
    async def query(self): ...  # line 105 — returns (result_dict, options) tuple

# From querysource/models.py:48
class QueryModel(Model):
    query_slug: str  # line 49 — primary key
    attributes: Optional[dict]  # line 54 — jsonb, holds 'scheduler' sub-dict
    cache_options: Optional[dict]  # line 78 — jsonb
    is_cached: bool  # line 73 — default True
    provider: str  # line 74 — default 'db'
    query_raw: str  # line 71
```

#### Verified Imports

```python
# Confirmed to resolve in the current tree:
from querysource.scheduler.jobs import scheduled_query_job, cache_refresh_job
# (querysource/scheduler/scheduler.py:24)

from querysource.queries.qs import QS
# (lazy-imported at querysource/scheduler/jobs.py:25 and :56)

from querysource.queries import MultiQS
# (re-exported at querysource/queries/__init__.py:7; same path used by
# querysource/handlers/multi.py:14)

from querysource.scheduler.notifications import NotificationManager
# (querysource/scheduler/scheduler.py:25)
```

#### Key Attributes & Constants

- `QSScheduler._scheduler` → `AsyncIOScheduler | None` (scheduler.py:41)
- `QSScheduler._notification_manager` → `NotificationManager`
  (scheduler.py:43)
- `QueryModel.provider` default → `'db'` (models.py:74)
- `MultiQS.query()` return shape → `(dict[str, DataFrame], dict)` —
  inferred from the handler's unpack at
  `querysource/handlers/multi.py:231` (`result, options = await
  qs.query()`).
- Scheduler SELECT columns →
  `query_slug, attributes, cache_options, provider, is_cached`
  (scheduler.py:206)
- Existing job-id prefixes → `query_<slug>` (scheduler.py:115),
  `cache_<slug>` (scheduler.py:154). New prefix → `multi_<slug>`.

### Does NOT Exist (Anti-Hallucination)

- ~~`querysource.scheduler.jobs.scheduled_multiqs_job`~~ — not yet defined;
  this feature creates it.
- ~~`querysource.scheduler.jobs.PROVIDER_JOB_MAP`~~ — not present; only
  introduced by Option C, which is **not** recommended.
- ~~`QSScheduler._load_scheduled_multi_queries`~~ — does not exist; would
  only exist under the rejected "two loaders" sub-option of Round-1.
- ~~`MultiQS.from_slug` / `MultiQS.dispatch_slug`~~ — not a real method.
  Slug-only invocation goes through `MultiQS(slug=...).query()`.
- ~~`scheduled_query_job(slug, request=...)` accepting a `request`
  kwarg~~ — the signature is `(slug, notification_manager=None,
  **kwargs)`; there is no aiohttp request inside a scheduled job.
- ~~`public.queries.is_multiquery`~~ — there is no such column.
  Routing relies on the existing `provider` column (default `'db'`,
  `models.py:74`).
- ~~A `provider == 'multi'` row anywhere in production today~~ — the
  literal string `'multi'` as a provider value does NOT appear in the
  codebase (`grep -rn "'multi'" querysource/` returns no provider-side
  hits). This brainstorm formally introduces it.

---

## Parallelism Assessment

- **Internal parallelism**: Low. The change is concentrated in two files
  (`scheduler.py`, `jobs.py`) plus a documentation paragraph. There is no
  natural axis along which it splits into independent sub-tasks — the new
  job callable and its loader-side routing must land together to be
  testable.
- **Cross-feature independence**: The feature touches only the QSScheduler
  module (`querysource/scheduler/*`). No other in-flight specs that I'm
  aware of are modifying this surface area. The earlier
  `querysource-scheduler.brainstorm.md` introduced these files and is
  shipped; this is a follow-on extension, not a conflicting branch.
- **Recommended isolation**: `per-spec`. All tasks should live in a single
  worktree off `dev`, since they share files and a contiguous test surface.
- **Rationale**: ~25–40 LOC change with two unit-testable seams
  (`_load_scheduled_queries` routing + `scheduled_multiqs_job` execution).
  Splitting it across worktrees would create merge noise without parallel
  velocity gain.

---

## Open Questions

- [ ] Should we add a `WARN` log when `_load_scheduled_queries` encounters
      `provider == 'multi'` but `query_raw` doesn't parse as a multi-query
      JSON payload? Round-2 chose "silent fallback", but a single info-level
      log per startup might be cheap insurance. — *Owner: Jesus Lara*
- [ ] Where should the `attributes.scheduler.output` reserved sub-key be
      documented as forward-compatible-but-unused, so a future implementer
      doesn't re-litigate the shape? Candidates: the QSScheduler README,
      the JSON-schema doc for `attributes`, or the docstring on
      `scheduled_multiqs_job`. — *Owner: Jesus Lara*
- [ ] Should there be a smoke-test fixture (e.g.
      `tests/scheduler/test_multi_routing.py`) that seeds a `provider='multi'`
      row in a test schema and asserts both the routing decision and that
      `cache_refresh_job` is **not** registered for it? Strongly recommended
      but out-of-scope here. — *Owner: Jesus Lara*
- [ ] Do we want `scheduled_multiqs_job` to accept a `conditions` kwarg
      passed through from the schedule definition (so the same multi-slug
      can be scheduled twice with different conditions)? Not required for
      v1 per the brief, but worth deciding before the spec hardens. —
      *Owner: Jesus Lara*
