---
type: feature
base_branch: dev
---

# Feature Specification: QSScheduler Multi-Query Support

**Feature ID**: FEAT-092
**Date**: 2026-05-20
**Author**: Jesus Lara / Claude
**Status**: draft
**Target version**: 5.x

> Predecessor specs: FEAT-008 (`querysource-scheduler.spec.md`) introduced
> `QSScheduler`, the two-job-type architecture, and the
> `attributes.scheduler` / `cache_options` schedule contracts. This spec
> extends that work without modifying its public surface.

> Source brainstorm: `sdd/proposals/qsscheduler-multi-support.brainstorm.md`
> (Recommended Option A — Provider-Routing Inside `_load_scheduled_queries`).

---

## 1. Motivation & Business Requirements

### Problem Statement

`QSScheduler._load_scheduled_queries` (`querysource/scheduler/scheduler.py:89`)
registers **every** row carrying `attributes.scheduler` against a single job
callable, `scheduled_query_job` (`querysource/scheduler/jobs.py:12`), which
unconditionally instantiates `QS(slug=...)`. That works for single-source
slugs, but `public.queries` also stores **multi-queries** — rows whose
`query_raw` is a JSON payload of `{"queries": ..., "files": ...}` and whose
runtime entry point is `MultiQS` (`querysource/queries/multi/__init__.py:53`),
not `QS`.

When a multi-query slug is scheduled today, the firing job constructs `QS`
and runs the single-source pipeline against a JSON-shaped `query_raw`. The
sub-query / sub-file orchestration that `MultiQS` performs (its
`ThreadQuery`/`ThreadFile` fan-out) never runs, so the schedule produces
either an outright error or, worse, a silent no-op.

The `provider` column is already in the `SELECT`
(`scheduler.py:206`) but the loader never reads it. This spec consumes that
column as the routing signal between the existing single-query path and a
new multi-query job.

### Goals

- Route schedulable rows by `public.queries.provider`:
  `provider == 'multi'` → new `scheduled_multiqs_job`; any other value →
  existing `scheduled_query_job` (unchanged).
- Introduce one new job callable, `scheduled_multiqs_job`, that runs
  `MultiQS(slug=...).query()` end-to-end on the server event loop and
  discards the result, preserving fire-and-forget semantics.
- Use a distinct APScheduler job-id prefix (`multi_<slug>`) so multi-query
  jobs are trivially distinguishable from `query_<slug>` and `cache_<slug>`
  in logs and APScheduler introspection.
- Exclude `provider == 'multi'` rows from `_load_cache_refresh_jobs`
  (a multi-slug has no single cache checksum to refresh).
- Reserve the JSON sub-key `attributes.scheduler.output` (object,
  optional). Today it must be tolerated and ignored; the JSON shape stays
  forward-compatible for a future result-handling patch.
- Preserve the existing `NotificationManager` callback contract: one
  notification per failed run, with `job_id=f"multi_{slug}"`.

### Non-Goals (explicitly out of scope)

- **No** result persistence in v1 — multi-query results are discarded. A
  future patch may wire `attributes.scheduler.output` to a sink such as
  `TableOutput`; the JSON key is reserved here but not interpreted.
- **No** per-sub-query notification fan-out. If `MultiQS` raises a single
  aggregated exception across its `ThreadQuery` joins, one
  `NotificationManager.notify(...)` call covers it. (Option rejected in
  brainstorm Round 2.)
- **No** load-time validation that `query_raw` is well-formed multi-query
  JSON. `MultiQS` already falls back to single-query mode when `query_raw`
  is plain SQL (`querysource/queries/multi/__init__.py:128-141`); the
  scheduler trusts that fallback. (Option rejected in brainstorm Round 2.)
- **No** strategy-table dispatch / registry abstraction. We branch inline
  on `provider`. (Option C rejected in brainstorm — see
  `proposals/qsscheduler-multi-support.brainstorm.md`.)
- **No** universal MultiQS-only execution path. We do **not** replace
  `scheduled_query_job`; ordinary single-query schedules keep their current
  code path unchanged. (Option B rejected in brainstorm.)
- **No** cache-refresh variant for multi-queries. Multi-slugs are excluded
  from `_load_cache_refresh_jobs` entirely.
- **No** runtime add/remove/reschedule API changes (inherited non-goal
  from FEAT-008).
- **No** schema migration. The `provider` column already exists
  (`querysource/models.py:74`); we simply start reading it in the loader.

---

## 2. Architectural Design

### Overview

`QSScheduler.startup` already loads `query_slug, attributes, cache_options,
provider, is_cached` from `public.queries` (`scheduler.py:206`).
This spec teaches `_load_scheduled_queries` to inspect the `provider`
column — and only that — to choose between two registered job callables:

- `provider == 'multi'` → `scheduled_multiqs_job` (new), id `multi_<slug>`,
  name `"Scheduled multi-query: <slug>"`.
- `provider in {None, 'db', any single-source driver}` → `scheduled_query_job`
  (unchanged), id `query_<slug>`, name `"Scheduled query: <slug>"`.

The new job is a near-symmetric twin of `scheduled_query_job`: lazy-import
`MultiQS`, instantiate with `slug=slug` only, await `qs.query()`, discard
the `(result, options)` tuple, and route any `Exception` through
`NotificationManager.notify(...)` with the `multi_<slug>` job id.

`_load_cache_refresh_jobs` gains a one-line guard at the top of the loop:
when `row.get('provider') == 'multi'`, skip the row before the
`is_cached`/`cache_options` checks. A `provider='multi'` row never produces
a `cache_<slug>` job.

The reserved `attributes.scheduler.output` sub-key is parsed off the row
but treated as a no-op (a single `DEBUG` log on load, so future
implementers can confirm it's being seen).

### Component Diagram

```
QSScheduler.startup(app)
       │
       ├── SELECT query_slug, attributes, cache_options, provider, is_cached
       │   FROM public.queries  (unchanged)
       │
       ├── _load_scheduled_queries(rows)
       │      │
       │      └── for row in rows:
       │             ├── if not attributes.scheduler:        continue
       │             ├── trigger = _parse_trigger(...)        (unchanged)
       │             ├── if row['provider'] == 'multi':
       │             │      add_job(scheduled_multiqs_job,
       │             │              id=f"multi_{slug}",
       │             │              name=f"Scheduled multi-query: {slug}",
       │             │              kwargs={"slug": slug,
       │             │                      "notification_manager": ...})
       │             └── else:
       │                    add_job(scheduled_query_job,        # unchanged
       │                            id=f"query_{slug}", ...)
       │
       └── _load_cache_refresh_jobs(rows)
              │
              └── for row in rows:
                     ├── if row['provider'] == 'multi':   continue   (NEW)
                     ├── if not is_cached:                continue   (unchanged)
                     └── … existing single-query cache-refresh path …

AsyncIOScheduler (MemoryJobStore — unchanged)
       │
       ├── ScheduledQueryJob   ──→ QS(slug=X).query()       discard result
       ├── ScheduledMultiQSJob ──→ MultiQS(slug=X).query()  discard tuple  (NEW)
       └── CacheRefreshJob     ──→ QS(slug=X).query()       save_cache
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `querysource/scheduler/scheduler.py:_load_scheduled_queries` | modifies | Adds inline branch on `row.get('provider')`; selects new job callable + id prefix for the `multi` case. |
| `querysource/scheduler/scheduler.py:_load_cache_refresh_jobs` | modifies | One-line guard: `continue` when `row.get('provider') == 'multi'`. |
| `querysource/scheduler/scheduler.py` (imports) | modifies | Import list adds `scheduled_multiqs_job`. |
| `querysource/scheduler/jobs.py` | extends | New async callable `scheduled_multiqs_job(slug, notification_manager=None, **kwargs)`; module docstring updated to describe three job kinds. |
| `querysource/queries/multi/__init__.py:MultiQS` | depends on (no changes) | Slug-only invocation `MultiQS(slug=slug).query()` is already supported (constructor at line 59; slug-only path at lines 110–141). |
| `querysource/queries/__init__.py` | depends on (no changes) | `MultiQS` is re-exported here (line 7). |
| `querysource/scheduler/notifications.py:NotificationManager` | depends on (no changes) | `notify(job_id, slug, error)` signature unchanged. |
| `public.queries.provider` (DB column) | reads | Loader now consumes the column it has been fetching all along (no DDL change; default `'db'` preserved). |
| `attributes.scheduler.output` (JSON sub-key) | reserves | Parsed but ignored; documented as forward-compatible for a future patch. No schema migration. |
| Operator runbook / docs | extends | A one-paragraph addition: "To schedule a multi-query, set `provider = 'multi'`; jobs appear with id `multi_<slug>`." |

### Data Models

**Schedule definition (unchanged contract from FEAT-008)** — applies
identically to `provider='multi'` rows:

```python
# attributes = {
#   "scheduler": {
#     "schedule_type": "cron" | "crontab" | "interval",
#     "schedule": { ... trigger kwargs ... },
#     "output": { ... reserved, ignored in v1 ... }   # NEW (optional)
#   }
# }
```

**Routing key on `public.queries` (existing column, newly consumed):**

| `provider` value | Routed to | Job id prefix | Cache-refresh job |
|---|---|---|---|
| `'db'` (default), or any single-source driver name | `scheduled_query_job` | `query_<slug>` | yes, if `is_cached=True` |
| `'multi'` | `scheduled_multiqs_job` | `multi_<slug>` | no — row is skipped |

### New Public Interfaces

```python
# querysource/scheduler/jobs.py — new sibling of scheduled_query_job / cache_refresh_job
async def scheduled_multiqs_job(
    slug: str,
    notification_manager=None,
    **kwargs,
) -> None:
    """Execute a scheduled multi-query by slug. Result is discarded."""
    # implementation: lazy-import MultiQS, await MultiQS(slug=slug).query(),
    # forward any exception to notification_manager.notify(
    #     job_id=f"multi_{slug}", slug=slug, error=exc
    # ).
```

No new public classes. No change to `QSScheduler`'s public methods
(`setup`, `startup`, `shutdown`, `add_notification_callback`). The two
private loader methods (`_load_scheduled_queries`,
`_load_cache_refresh_jobs`) are modified in place — they have no external
callers.

---

## 3. Module Breakdown

### Module 1: `scheduled_multiqs_job` callable

- **Path**: `querysource/scheduler/jobs.py`
- **Responsibility**: New async job callable. Lazy-imports `MultiQS`,
  instantiates `MultiQS(slug=slug)` (no request, no user session, no
  conditions, no queries/files/query kwargs), awaits `qs.query()`,
  discards the returned `(result, options)` tuple, and forwards any
  exception to `notification_manager.notify(job_id=f"multi_{slug}",
  slug=slug, error=exc)`. Module docstring is updated to describe all
  three job kinds.
- **Depends on**:
  - `querysource.queries.MultiQS` (re-exported via
    `querysource/queries/__init__.py:7`).
  - `querysource.scheduler.notifications.NotificationManager` (signature
    only; not directly imported here — passed in via `kwargs`).

### Module 2: `_load_scheduled_queries` provider routing

- **Path**: `querysource/scheduler/scheduler.py`
- **Responsibility**: Inside the existing for-row loop, after trigger
  parsing, branch on `row.get('provider')`. When it equals `'multi'`,
  call `self._scheduler.add_job(scheduled_multiqs_job, ..., id=f"multi_{slug}",
  name=f"Scheduled multi-query: {slug}", ...)`. Otherwise, keep the
  existing `scheduled_query_job` registration unchanged. Parse and log
  (at DEBUG only) the reserved `attributes.scheduler.output` sub-key if
  present. Update the import line to add `scheduled_multiqs_job`.
- **Depends on**: Module 1.

### Module 3: `_load_cache_refresh_jobs` multi-skip guard

- **Path**: `querysource/scheduler/scheduler.py`
- **Responsibility**: Insert `if row.get('provider') == 'multi': continue`
  at the top of the for-row loop, before the existing `is_cached` check.
  No other change. This makes the exclusion explicit and self-documenting.
- **Depends on**: nothing (orthogonal to Module 1/2 logic).

### Module 4: Operator-facing documentation

- **Path**: documentation surface for QSScheduler (e.g. the QSScheduler
  README under `docs/` or the scheduler module docstring at the top of
  `querysource/scheduler/scheduler.py`). The exact home is one of the
  open questions in §8.
- **Responsibility**: One paragraph describing the new routing contract:
  - "Set `provider = 'multi'` on a `public.queries` row to schedule a
    multi-query. Jobs appear as `multi_<slug>` in APScheduler logs and
    are excluded from cache-refresh."
  - One sentence noting `attributes.scheduler.output` is reserved for
    future use and currently ignored.
- **Depends on**: Modules 1–3 (describes their behavior).

---

## 4. Test Specification

### Unit Tests

| Test | Module | Description |
|---|---|---|
| `test_load_scheduled_queries_routes_multi_to_new_job` | Module 2 | Given a fake row with `provider='multi'` and a valid `attributes.scheduler`, `_load_scheduled_queries` calls `_scheduler.add_job(...)` once with `scheduled_multiqs_job` as the callable and `id=f"multi_{slug}"`. |
| `test_load_scheduled_queries_keeps_single_query_path_for_non_multi` | Module 2 | Given a row with `provider='db'` (or unset), `_load_scheduled_queries` still calls `add_job(scheduled_query_job, ..., id=f"query_{slug}", ...)`. No regression vs FEAT-008. |
| `test_load_scheduled_queries_skips_row_without_scheduler` | Module 2 | A `provider='multi'` row with no `attributes.scheduler` is skipped — no `add_job` call regardless of provider. |
| `test_load_scheduled_queries_parses_output_subkey_as_noop` | Module 2 | A `provider='multi'` row with `attributes.scheduler.output = {...}` registers exactly as if `output` were absent (today). Emits a `DEBUG`-level log mentioning the slug. |
| `test_load_cache_refresh_jobs_skips_multi_rows` | Module 3 | A row with `provider='multi'` and `is_cached=True, cache_options={...}` is skipped by `_load_cache_refresh_jobs` — no `cache_<slug>` job is registered. |
| `test_load_cache_refresh_jobs_unchanged_for_single_query` | Module 3 | Row with `provider='db', is_cached=True, cache_options={...}` still registers `cache_<slug>`. |
| `test_scheduled_multiqs_job_calls_multiqs_with_slug_only` | Module 1 | `await scheduled_multiqs_job(slug='x', notification_manager=mgr)` instantiates `MultiQS(slug='x')` (verify via patch on `querysource.queries.MultiQS`) and awaits `query()`. No `request`, no `conditions` passed. |
| `test_scheduled_multiqs_job_notifies_on_exception` | Module 1 | If patched `MultiQS.query()` raises, `notification_manager.notify` is called exactly once with `job_id="multi_x"`, `slug="x"`, and the raised exception. |
| `test_scheduled_multiqs_job_swallows_exception` | Module 1 | The job callable itself does NOT re-raise after notifying (APScheduler firing semantics: any unhandled exception in a coroutine propagates as a job-error event, which is fine; but the existing single-query job swallows — symmetry matters). Mirror the existing `scheduled_query_job` behavior at `jobs.py:23-37`. |

### Integration Tests

| Test | Description |
|---|---|
| `test_qsscheduler_registers_multi_job_from_db_row` | With `ENABLE_QS_SCHEDULER=True`, a seeded `public.queries` row with `provider='multi'`, `attributes={"scheduler": {"schedule_type": "interval", "schedule": {"minutes": 60}}}` produces exactly one APScheduler job with id `multi_<slug>` after `QSScheduler.startup`. Cache-refresh count is 0. |
| `test_qsscheduler_mixed_row_population` | Seed three rows: one `provider='db'` with scheduler, one `provider='multi'` with scheduler, one `provider='db'` with `is_cached=True, cache_options`. Assert exactly one of each: `query_<slug>`, `multi_<slug>`, `cache_<slug>`. |
| `test_qsscheduler_multi_query_dry_fire` | If the local test environment supports it (lives in `tests/integration/`), simulate a multi-query JSON `query_raw` payload with a trivial `queries` map and assert that one fire produces no exception and emits the expected log line. |

### Test Data / Fixtures

```python
# Key fixtures needed
@pytest.fixture
def fake_multi_row():
    return {
        "query_slug": "test_multi",
        "attributes": {
            "scheduler": {
                "schedule_type": "interval",
                "schedule": {"minutes": 30},
            }
        },
        "cache_options": {},
        "provider": "multi",
        "is_cached": False,
    }

@pytest.fixture
def fake_single_row():
    return {
        "query_slug": "test_single",
        "attributes": {
            "scheduler": {
                "schedule_type": "interval",
                "schedule": {"minutes": 30},
            }
        },
        "cache_options": {},
        "provider": "db",
        "is_cached": False,
    }
```

---

## 5. Acceptance Criteria

> This feature is complete when ALL of the following are true:

- [ ] `querysource/scheduler/jobs.py` exposes a new async callable
      `scheduled_multiqs_job(slug, notification_manager=None, **kwargs)`
      with the same try/except/notify shape as `scheduled_query_job`.
- [ ] `_load_scheduled_queries` registers `scheduled_multiqs_job` (id
      `multi_<slug>`) for every schedulable row where `row['provider']
      == 'multi'`, and continues to register `scheduled_query_job` (id
      `query_<slug>`) for every other schedulable row.
- [ ] `_load_cache_refresh_jobs` skips rows where `row['provider'] ==
      'multi'` before its `is_cached` check, so multi-slugs never get a
      `cache_<slug>` job.
- [ ] No regression for existing rows: a `provider='db'` row with
      `attributes.scheduler` produces exactly the same APScheduler job
      (id, callable, kwargs) as it did before this feature.
- [ ] `scheduled_multiqs_job` invokes `MultiQS(slug=slug)` with no extra
      kwargs (no `request`, no `user_session`, no `conditions`) and
      awaits `query()`.
- [ ] On any exception inside `scheduled_multiqs_job`,
      `notification_manager.notify(job_id="multi_<slug>", slug=<slug>,
      error=<exc>)` is called exactly once and the exception is
      swallowed (mirroring `scheduled_query_job`).
- [ ] `attributes.scheduler.output`, when present on a row, does NOT
      cause a load-time error and does NOT change the registered job.
- [ ] Unit tests in §4 all pass (`pytest tests/unit/ -v`).
- [ ] Integration test `test_qsscheduler_mixed_row_population` passes
      (`pytest tests/integration/ -v`).
- [ ] Documentation updated: the operator-facing doc surface for
      QSScheduler describes the new `provider='multi'` routing and the
      reserved `attributes.scheduler.output` sub-key (see §3 Module 4
      and §8 Open Question regarding placement).
- [ ] No new external dependencies introduced (`pyproject.toml`
      unchanged).
- [ ] No DDL change to `public.queries`. `provider` column default
      remains `'db'` per `querysource/models.py:74`.

---

## 6. Codebase Contract

> **CRITICAL — Anti-Hallucination Anchor**
> Every reference below was re-verified against the current `dev` tree
> on 2026-05-20. Implementation agents MUST NOT reference imports,
> attributes, or methods not listed here without first verifying them
> with `grep` or `read`.

### Verified Imports

```python
# Scheduler module-level imports (already present)
from querysource.scheduler.jobs import scheduled_query_job, cache_refresh_job
# verified: querysource/scheduler/scheduler.py:24

# This feature ADDS scheduled_multiqs_job to the same import line:
from querysource.scheduler.jobs import (
    scheduled_query_job,
    cache_refresh_job,
    scheduled_multiqs_job,   # NEW — defined by this feature
)

# Lazy-imported inside the new job callable (matches existing pattern
# at jobs.py:25 and :56):
from querysource.queries import MultiQS
# verified: re-exported at querysource/queries/__init__.py:7
# verified: defined at querysource/queries/multi/__init__.py:53

# Notification manager (already in scheduler.py)
from querysource.scheduler.notifications import NotificationManager
# verified: querysource/scheduler/scheduler.py:25
# class defined: querysource/scheduler/notifications.py:21
```

### Existing Class Signatures

```python
# querysource/scheduler/scheduler.py
class QSScheduler:                                            # line 30
    def __init__(
        self, loop: asyncio.AbstractEventLoop = None,
    ): ...                                                     # line 37
    def _create_scheduler(self) -> AsyncIOScheduler: ...       # line 45
    def _parse_trigger(
        self, schedule_type: str, schedule: dict,
    ): ...                                                     # line 57
    def _load_scheduled_queries(self, rows: list) -> int: ...  # line 89 — MODIFIED
    def _load_cache_refresh_jobs(self, rows: list) -> int: ... # line 131 — MODIFIED
    def setup(self, app: web.Application) -> None: ...         # line 170
    async def startup(self, app: web.Application) -> None: ... # line 179
    async def shutdown(self, app: web.Application) -> None: ...# line 230
    def add_notification_callback(
        self, callback: Callable,
    ) -> None: ...                                             # line 245

# querysource/scheduler/jobs.py — existing siblings of the new callable
async def scheduled_query_job(
    slug: str,
    notification_manager=None,
    **kwargs,
) -> None: ...                                                 # line 12

async def cache_refresh_job(
    slug: str,
    notification_manager=None,
    **kwargs,
) -> None: ...                                                 # line 40

# querysource/scheduler/notifications.py
class NotificationManager:                                     # line 21
    def add_callback(self, callback: Callable) -> None: ...    # line 29
    def notify(
        self, job_id: str, slug: str, error: Exception,
    ) -> None: ...                                             # line 37

# querysource/queries/multi/__init__.py
class MultiQS(BaseQuery):                                      # line 53
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
    ): ...                                                     # line 59
    async def query(self): ...                                 # line 105
    # Returns a tuple — destructured by handler at
    # querysource/handlers/multi.py:231 as: result, options = await qs.query()

# querysource/models.py — QueryModel.provider (the routing column)
class QueryModel(Model):                                       # line 48
    query_slug: str                                            # line 49 (primary key)
    attributes: Optional[dict]                                 # line 54 (jsonb)
    cache_options: Optional[dict]                              # line 78 (jsonb)
    is_cached: bool                                            # line 73 (default True)
    provider: str                                              # line 74 (default 'db')
    query_raw: str                                             # line 71
```

### Integration Points

| New / Modified Component | Connects To | Via | Verified At |
|---|---|---|---|
| `scheduled_multiqs_job` (new) | `MultiQS.__init__` + `MultiQS.query()` | constructor + `await` call | `querysource/queries/multi/__init__.py:59,105` |
| `scheduled_multiqs_job` (new) | `NotificationManager.notify(...)` | method call (passed in `kwargs`) | `querysource/scheduler/notifications.py:37` |
| `_load_scheduled_queries` (modified) | `scheduled_multiqs_job` (new) | `add_job(callable, ..., kwargs=...)` | `querysource/scheduler/scheduler.py:116-126` (existing call site to be branched) |
| `_load_scheduled_queries` (modified) | `row['provider']` | dict access (column from the existing SELECT) | `querysource/scheduler/scheduler.py:206` |
| `_load_cache_refresh_jobs` (modified) | `row['provider']` | dict access (guard) | `querysource/scheduler/scheduler.py:141-145` (existing for-row loop to be guarded) |

### Configuration References

- `ENABLE_QS_SCHEDULER` (existing) — already gates the entire scheduler.
  No new flag introduced by this feature.
- `QS_SCHEDULER_TIMEZONE`, `QS_SCHEDULER_MAX_INSTANCES`,
  `QS_SCHEDULER_COALESCE` (existing) — apply to multi jobs identically;
  no change.
- No new environment variable. No new entry in `querysource/conf.py`.

### Does NOT Exist (Anti-Hallucination)

- ~~`querysource.scheduler.jobs.scheduled_multiqs_job`~~ — does not exist
  yet; this feature defines it. Until the feature merges, do not import
  it.
- ~~`querysource.scheduler.jobs.PROVIDER_JOB_MAP`~~ — does not exist.
  Brainstorm Option C proposed a strategy table; the spec rejects that
  approach in favor of inline branching (§1 Non-Goals).
- ~~`QSScheduler._load_scheduled_multi_queries`~~ — does not exist. The
  spec keeps a single `_load_scheduled_queries` with an inline branch
  (brainstorm Round-1 decision).
- ~~`MultiQS.from_slug` / `MultiQS.dispatch_slug` / `MultiQS.slug_only`~~ —
  not real methods. Slug-only invocation is `MultiQS(slug=...).query()`.
- ~~`scheduled_query_job(slug, request=...)` accepting a `request`
  kwarg~~ — the signature is `(slug, notification_manager=None,
  **kwargs)`. Scheduled jobs run without an HTTP request.
- ~~`public.queries.is_multiquery`~~ — there is no such column. Routing
  uses the existing `provider` column (default `'db'`,
  `querysource/models.py:74`).
- ~~An existing `provider == 'multi'` row anywhere in the codebase~~ —
  the literal string `'multi'` does NOT appear as a provider value in
  any source file today (`grep -rn "'multi'" querysource/` returns no
  provider-side hits). This spec formally introduces it.
- ~~Guardian / PBAC pre-flight inside a scheduled job~~ — does not
  apply. The handler-side pre-flight at
  `querysource/handlers/multi.py:25-99` requires a `web.Request` and
  `request.app.get('security')`. Neither is available inside an
  APScheduler-fired coroutine. `scheduled_multiqs_job` MUST NOT attempt
  PBAC.

---

## 7. Implementation Notes & Constraints

### Patterns to Follow

- **Mirror the existing job-callable shape**: `scheduled_multiqs_job`
  should match `scheduled_query_job` (`jobs.py:12-37`) line-for-line in
  structure — lazy import inside `try:`, await the query, swallow
  exceptions via the `if notification_manager:` guard. The only diffs
  are the imported class (`MultiQS` vs `QS`), the discard target (a
  tuple, not a `_output_format` return), and the job-id prefix in the
  notification call.
- **Lazy import**: keep the `MultiQS` import inside the function body
  (matching the existing `from querysource.queries.qs import QS` lazy
  import at `jobs.py:25,56`). This preserves cold-start ordering and
  avoids circular-import surprises in test harnesses.
- **Branch placement in the loader**: insert the `provider` branch
  immediately after `trigger = self._parse_trigger(...)` and the `if
  trigger is None: continue` (i.e. just before the existing
  `add_job(...)` call at `scheduler.py:115-126`). Both branches share
  the same `trigger`, `kwargs`, and `replace_existing=True` semantics.
- **Job-id prefix uniqueness**: never reuse `query_<slug>` for multi
  jobs. Distinct prefixes are how the operator runbook will tell job
  kinds apart in APScheduler introspection (`scheduler.print_jobs()`)
  and in log lines.
- **Async-first**: `scheduled_multiqs_job` is an `async def`. Do not
  introduce thread executors; `MultiQS` already manages its
  `ThreadQuery` fan-out internally.
- **Logging**: use the module logger `logger =
  logging.getLogger("QSScheduler.Jobs")` already at `jobs.py:9`. For
  load-time logs, use `self.logger` on `QSScheduler` (`scheduler.py:38`,
  bound to `logger = logging.getLogger("QSScheduler")` at line 27).

### Known Risks / Gotchas

- **Silent fallback on misconfig**: if a row carries `provider='multi'`
  but `query_raw` is plain SQL or empty, `MultiQS` falls back to
  single-query mode (`querysource/queries/multi/__init__.py:128-141`).
  The scheduler **will not** log a warning at load time — that was the
  Round-2 decision. Mitigation: the operator runbook entry (§3 Module
  4) should call this out explicitly so authors don't get confused
  about why their multi schedule "just ran SQL".
- **APScheduler swallowing exceptions**: the existing
  `scheduled_query_job` swallows exceptions (only logging + notifying,
  not re-raising). The new job MUST mirror that — APScheduler emits a
  `JobErrorEvent` on unhandled exceptions, which would notify a second
  time via `NotificationManager`. Symmetry with `scheduled_query_job`
  prevents that double-notify. Tested via
  `test_scheduled_multiqs_job_swallows_exception`.
- **DataFrame memory pressure**: `MultiQS.query()` materializes one
  DataFrame per sub-query and returns them in a dict. For
  multi-queries with many large sub-queries, scheduled fires can spike
  memory. Mitigation: since the result is immediately discarded, Python
  GC reclaims promptly; no streaming refactor is in scope here.
- **`MultiQS` constructs `asyncio.Queue` and `ThreadQuery` threads on
  every call**: each scheduled fire pays the thread-startup cost. For
  multi-queries on small interval schedules (sub-minute), watch for
  thread churn. Mitigation: documented as a known cost; tuning lives
  outside this feature.
- **Provider column may be `None` for legacy rows**: defensive code uses
  `row.get('provider')` (not `row['provider']`) when branching, to
  treat NULL as "not multi" — same fall-through path that single-query
  rows already use. `QueryModel.provider` has a default of `'db'`
  (`models.py:74`) but older rows may still carry NULL.
- **Test isolation**: the existing scheduler test suite (introduced by
  FEAT-008) should be the home for new unit tests. New fixtures live
  in the same `conftest.py` to avoid duplication.

### External Dependencies

| Package | Version | Reason |
|---|---|---|
| `apscheduler` | (existing pin) | Job registration. Same usage as today. |
| `aiohttp` | (existing pin) | Web app lifecycle hooks. |
| `asyncdb` | (existing pin) | `AsyncPool` for the `public.queries` SELECT (used in `QSScheduler.startup`). |

**No new external dependency is introduced by this feature.**

---

## 8. Open Questions

> Resolved (`[x]`) items are pinned for audit; unresolved (`[ ]`) items
> are deferrable to implementation. None of these block the spec.

- [ ] Should `_load_scheduled_queries` log an `INFO`/`WARN` line when a
      `provider='multi'` row has `query_raw` that does not parse as
      multi-query JSON, even though MultiQS will silently fall back to
      single-query mode? Round-2 chose "silent fallback"; a single
      info-level log per startup is cheap insurance and would not
      change behavior. — *Owner: Jesus Lara*
- [ ] Where should `attributes.scheduler.output` be documented as
      reserved-but-unused so future implementers don't re-litigate its
      shape? Candidates: the QSScheduler README/runbook, a docstring
      on `scheduled_multiqs_job`, or the module docstring at the top
      of `querysource/scheduler/scheduler.py`. Resolving this picks
      where Module 4 (§3) actually writes. — *Owner: Jesus Lara*
- [ ] Should there be a dedicated smoke-test fixture
      (`tests/scheduler/test_multi_routing.py`) that seeds a
      `provider='multi'` row in a test schema and asserts both the
      routing decision AND that `_load_cache_refresh_jobs` skipped it?
      Strongly recommended; deferred unless we hit a gap in the unit
      tests of §4. — *Owner: Jesus Lara*
- [ ] Should v1 of `scheduled_multiqs_job` accept an optional
      `conditions` kwarg (so a single multi-slug can be scheduled
      multiple times with different conditions, mirroring what
      `QS(slug, conditions={...})` allows for single queries)? The
      brief said "v1 has no conditions"; the current schedule schema
      has no place to specify per-job conditions anyway. Decide before
      tasks for any v2 expansion. — *Owner: Jesus Lara*

---

## Worktree Strategy

- **Default isolation unit**: `per-spec`. All tasks run sequentially in
  a single worktree branched from `dev`.
- **Branch name**: `feat-092-qsscheduler-multi-support` (per project
  convention, `feat-<NNN>-<slug>`).
- **Worktree path**: `.claude/worktrees/feat-092-qsscheduler-multi-support`
  (per CLAUDE.md / `.claude/rules/using-git-worktrees.md`).
- **Cross-feature dependencies**: none. The feature touches only
  `querysource/scheduler/scheduler.py` and `querysource/scheduler/jobs.py`,
  plus tests. No other in-flight spec is modifying this surface.
- **Rationale**: the change is ~25–40 LOC across two files with two
  unit-testable seams (loader routing + new job callable). Splitting
  the work across worktrees would create merge friction without
  yielding parallel velocity. Single sequential worktree is the right
  shape.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-05-20 | Jesus Lara / Claude | Initial draft, scaffolded from `sdd/proposals/qsscheduler-multi-support.brainstorm.md` (Recommended Option A). |
