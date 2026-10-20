---
# SDD flow type and base branch (FEAT-145).
# - type: feature  (default)  → base_branch: dev (or any non-main branch)
# - type: hotfix              → base_branch MUST be: main
type: feature
base_branch: dev
# projects: parts of the codebase this doc concerns: `querysource` or a subsystem
#   (providers, parsers, rust-parsers, outputs, multiquery, handlers, datasources,
#   auth, cache, scheduler) or an area (sdd-tooling, dev-loop, docs, ci). Unknown values warn, not fail.
projects: [scheduler, handlers, multiquery]
# tags: free-form kebab-case keywords for organizing specs (e.g. bigquery, cache).
tags: [qsscheduler, execution-history, observability, concierge, single-execution]
---

# Brainstorm: Scheduler Execution History

**Date**: 2026-10-20
**Author**: Juan Rodriguez
**Status**: exploration
**Recommended Option**: A

---

## Problem Statement

QSScheduler (`querysource/scheduler/`) records nothing about the jobs it runs.
When a job finishes or fails, `jobs.py` writes one log line and calls
`NotificationManager.notify`, and the only callback is a logging one. Nothing is
written to a database, and Concierge (`navigator-frontend-next`) can only show
what is in memory right now: registered jobs and their `next_run_time`.

**Incident that prompted this (2026-10-17 → 2026-10-19).** The multi
`assembly_employee_detail_profile_insert` failed every day at 06:00 America/New_York
with `canceling statement due to conflict with recovery`. Its source query read
from the RO replica and took longer than the replica's 30 s
`max_standby_streaming_delay`. The table went stale for three days. Nobody noticed
until a business user (Carlos Montes) reported it, and the diagnosis needed SSH
access to the gunicorn logs on the prod host.

Three more defects showed up while diagnosing it. They are in scope:

1. **Each job runs N times.** QSScheduler uses a `MemoryJobStore`
   (`scheduler.py:148-158`) and starts inside every gunicorn worker. Prod has
   4 workers, so every scheduled job fires 4 times; the prod logs show 4×
   `ExecuteSQL: 3 statement(s) committed` for one 06:00 occurrence. The incident
   multi only stays correct because a `LOCK TABLE` was added by hand.
2. **Pausing does not stick.** `PATCH /api/v1/qs/scheduler/jobs/{id}` pauses the
   job only in the worker that served the request (`set_job_paused`,
   `scheduler.py:592`). Nothing is persisted, so the other workers keep running
   it and a restart re-registers it as active. The user's words: *"pausarlas no
   hace nada, luego se vuelven a activar solas"* (pausing them does nothing; they
   turn themselves back on later).
3. **Failures are silent.** Failure "notification" means a WARNING in a log file
   that nobody watches.

**Who is affected:**
- Business and data users (e.g. Carlos Montes, Verizon/WM programs), who depend on
  daily refreshes.
- Navigator developers, who triage failures.
- Concierge operators, who manage schedules.

## Constraints & Requirements

- **Async-first.** aiohttp + gunicorn (aiohttp worker). No blocking I/O on the event
  loop. DB access goes through `asyncdb` / `asyncpg`.
- **Run records go to the primary DB.** Use the `default_dsn` / `DBHOST` path,
  never the RO replica (`asyncpg_url` / `PG_HOST`). The replica lag and
  recovery-conflict behaviour are what caused the incident.
- **Every job kind is covered:** `scheduled_query_job`, `scheduled_multiqs_job` and
  `cache_refresh_job`.
- **One execution per occurrence across all workers and hosts.** Do not rely on a
  "single worker" deployment trick.
- **Pause and resume must be durable** and apply in every worker and across
  restarts.
- **Run now:** an operator triggers a job from Concierge. The run is recorded with
  `trigger = manual` and the user who triggered it.
- **Failure alert:** one alert per failed occurrence, not one per worker, sent to
  a configurable channel.
- **Orphaned runs:** a run left in `running` after its worker died is marked
  `abandoned`.
- **One central table** in the primary DB, carrying tenant/store columns. It is not
  per store, even though FEAT-147 allows per-tenant query stores.
- **Permissions** are the same as the existing `SchedulerJobsView` (GET/PATCH
  checks). "Run now" on a write-capable multi also needs the FEAT-160
  `pg_admin` grant (`_enforce_scheduler_grant`).
- **Retention:** no automatic purge (user decision). Index the table for
  time-ordered, per-job reads.
- **Cheap mechanisms.** The FEAT-176 lead (2026-10-20) rejected the heavy
  handoff/journal/lease machinery; the solution must stay small.
- **DDL** ships as a reviewed `.sql` under `docs/sql/` and is applied manually, which
  is the repo convention. Nothing auto-creates tables at startup.
- **Repo conventions:** no `print`, use `self.logger`; config via navconfig;
  Google docstrings and strict type hints.

---

## Prior Art: Flowtask `NavScheduler`

Flowtask (`~/Documents/navigator/flowtask`) has run APScheduler in production for
years. I researched it through its wiki (`wikitoolkit query "scheduler service jobs"`)
and then read the source directly (flowtask branch `feat-FEAT-576-workday-fetch-many`).

| Concern | How flowtask does it | Lesson for QS |
|---|---|---|
| **Single execution** | `gunicorn_config.py` hard-codes `workers = 1`, and the scheduler starts in that single worker. A Redis singleton lock (`SCHEDULER_LOCK_KEY = "navigator_singleton_lock"`, `acquire_scheduler_lock` / `renew_scheduler_lock` / `periodic_lock_check`) was built and then **disabled: "cluster feature has design flaws"** (`flowtask/scheduler/scheduler.py:118,158-161,282-295,406-436`). | QS cannot copy `workers = 1`: navigator-api serves web traffic with 4 workers. The abandoned TTL/renewal leader lock is real evidence against Option B. A per-occurrence claim (Option A) needs no lease, renewal or failover. |
| **Durable pause** | `troc.jobs.enabled`. Startup loads `SELECT * FROM troc.jobs WHERE enabled = true` (`scheduler.py:473`). PATCH flips `enabled` and removes or re-adds the job in the jobstore (`handlers/manager.py:_patch_response`, ~412-452). | The DB is the source of truth for paused state. That works in flowtask only because there is one worker. In QS the flag must also be **checked at fire time**, because the other workers' in-memory jobs are not touched. |
| **Run outcome** | APScheduler **listeners**: `EVENT_JOB_EXECUTED`, `EVENT_JOB_ERROR \| EVENT_JOB_MISSED`, `EVENT_JOB_SUBMITTED` and `EVENT_JOB_ADDED` (`add_listeners`, `scheduler.py:643`). They read `event.scheduled_run_time`, `event.exception` and `event.traceback`. | This is how to get the real `scheduled_run_time`, and how to record **missed** and **max-instances** occurrences, which never reach the job body. Listeners fire after submission, so they cannot be the dedup gate; the claim still has to live in the job wrapper. |
| **History storage** | Only the last run is kept, overwritten in place on `troc.jobs` (`last_exec_time`, `next_run_time`, `job_status` 1=ok/2=error/3=missed/4=max-instances, `job_state`, `traceback`) via `update_task_status` (`scheduler.py:868`). Per-run task history goes to **InfluxDB** (`events/events/exec.py` `LogExecution`, `TASK_EXEC_TABLE`). | Keep both: a denormalized "last run" for a cheap jobs list, and an append-only run table for history. Do not put history in Influx: Concierge and SQL users need it in Postgres. |
| **Run identity** | NAV-6365 (`sdd/proposals/SDD_NAV-6365.md`): scheduler-dispatched runs reused a stale `execution_id`; manual runs were fine. | Generate the run id **at claim time** (the run row PK) and pass it down. Never store it on the job definition. |
| **Alerts** | Telegram via `scheduler/notifications.send_notification`, called from `job_status`. | Same idea: pluggable callback, channel from config. |
| **Anti-patterns to avoid** | `save_db_event` builds SQL with `str.format` ("TODO: migrate to Prepared statements") and runs it on a **new event loop inside a `ThreadPoolExecutor`** from a sync listener (`scheduler.py:726-893`). | Use parameterized asyncpg queries on the running loop. Listeners should be cheap and `asyncio.create_task` the DB write. |

---

## Options Explored

### Option A: The run ledger is the lock (fire-time claim + durable job state)

Add one table, `qs_scheduler_runs`, with a **unique key on
`(job_id, scheduled_for)`**. When a job fires in any worker, it first
`INSERT … ON CONFLICT DO NOTHING RETURNING id`s its run row on the primary:

- The worker whose insert succeeds runs the job, then updates the row to
  `success` or `failed` with `finished_at`, `duration_ms`, `error` and `details`.
- The other N-1 workers get no row back and skip silently.

That single insert is both the history record and the single-execution guard.

A second small table, `qs_scheduler_job_state` (one row per `job_id`: `paused`,
`paused_by`, `paused_at`, `reason`), holds the durable desired state:

- At fire time the job checks it, before the claim, and skips if paused.
- `PATCH pause/resume` writes it, and still pauses the local in-memory job for
  immediate effect.
- `GET jobs` overlays it, so every worker reports the same paused flag.
- Startup applies it after registration, so a restart no longer resumes paused
  jobs.

**Run now** inserts a row with `trigger = 'manual'`, `scheduled_for = now()` and
`requested_by = <user>`, then executes the same job callable as a background
task in the worker that served the request. It returns `202 {run_id}`.

**Alerts** are a new `NotificationManager` callback backed by `async-notify`
(already a dependency). Only the claim winner reaches it, so there is one alert
per failure.

**Abandoned runs:**
- At startup, each worker marks `running` rows from its own host whose pid no
  longer exists.
- On read, plus an optional sweep, rows older than a configurable
  `QS_SCHEDULER_RUN_TIMEOUT` are marked `abandoned`.

APScheduler 3.11 does not pass `scheduled_run_time` to the job callable. So
`scheduled_for` is derived inside the job from the job's trigger, as the latest
fire time ≤ now within the misfire grace. It is not truncated wall-clock time.

Following flowtask, A also registers APScheduler listeners:
- **`EVENT_JOB_MISSED` and `EVENT_JOB_MAX_INSTANCES`** record `missed` /
  `skipped` rows, which never reach the job body. These rows use the event's real
  `scheduled_run_time`, through the same `ON CONFLICT DO NOTHING` key, so only one
  is kept.
- **`EVENT_JOB_SUBMITTED`** can cross-check the derived slot in tests.

`qs_scheduler_job_state` also holds a denormalized `last_run_id` / `last_status` /
`last_run_at`, which keeps the flowtask `troc.jobs` "last run" convenience for the
jobs list.

✅ **Pros:**
- One mechanism solves history, the N-fold execution and the one-alert-per-failure
  rule. It is roughly 2 small tables plus a thin wrapper around the three job
  callables.
- Works across workers **and** hosts with no leader and no long-held connections.
- Matches the lead's "cheap mechanisms" direction. It also gives the FEAT-176 query
  move the durable pause and fire-time guard that its "pause → move → re-register"
  procedure is missing (see memory caveat: pause today only affects one worker).
- APScheduler stays as-is (`MemoryJobStore`, triggers, coalesce). Nothing about
  trigger semantics changes.
- History is plain SQL: Concierge, and anyone with DB access, can query it.

❌ **Cons:**
- Every fire costs one primary-DB round-trip per worker, which is negligible: N ≤ a few
  per occurrence.
- Deriving `scheduled_for` from the trigger must be correct for every
  `schedule_type` (cron/crontab/interval/hourly/daily/weekly/monthly/biweekly),
  and needs its own tests.
- **If the primary is unreachable at fire time, the claim cannot be made.** Running
  anyway allows N runs; skipping means no run. This is a policy decision (see Open
  Questions).
- The in-memory APScheduler `next_run_time` on non-leader workers still exists. That
  is harmless, but the UI must take paused state from the DB overlay.

📊 **Effort:** Medium

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `apscheduler` | Existing scheduler; `Job.trigger.get_next_fire_time` for slot derivation | 3.11.2 installed (`uv.lock`) |
| `asyncdb` / `asyncpg` | Primary-DB writes via `default_dsn` | existing; same path `execute_guarded` uses |
| `async-notify` | Failure alerts (email / Teams / Slack) | 2.0.0 in `uv.lock`; declared in `pyproject.toml:71`, unused in `querysource/` today |
| `pytest-asyncio` | Tests incl. a disposable-Postgres race test with 2+ scheduler instances | existing |

🔗 **Existing Code to Reuse:**
- `querysource/scheduler/jobs.py:61,104,174`: the three job callables to wrap
  (claim → execute → finalize).
- `querysource/scheduler/notifications.py`: `NotificationManager.add_callback`
  for the alert channel.
- `querysource/scheduler/scheduler.py:592` `set_job_paused`, `:608` `register_slug`,
  `:681` `startup`: where durable state is written or applied.
- `querysource/handlers/scheduler.py:74` `SchedulerJobsView`: GET/PATCH auth and
  serialization; extended with `/runs` and `/run`.
- `querysource/interfaces/guarded_sql.py:71` `execute_guarded`: the pattern for a
  short primary-DB connection via `default_dsn`.

---

### Option B: Leader election (exactly one worker owns the scheduler)

Each worker tries a **session-level** `pg_try_advisory_lock(<qs-scheduler-key>)`
on a dedicated primary-DB connection. The winner starts APScheduler; the others
keep retrying periodically and take over if the leader's session dies. Run history
is a plain append log written by the leader. Pause/resume is persisted in a state
table that the leader polls or listens to (`LISTEN/NOTIFY`).

✅ **Pros:**
- Only one APScheduler instance actually fires, so there is no per-occurrence claim
  logic.
- Failover is automatic: Postgres releases session advisory locks when the connection
  drops.

❌ **Cons:**
- HTTP requests land on any worker. Non-leaders have an empty jobstore, so
  `GET /scheduler/jobs`, PATCH and "run now" must be proxied to the leader or
  re-implemented over the DB. That is a bigger rewrite of the handler.
- A long-lived dedicated connection per worker, plus a lock-retry loop. With several
  hosts it becomes one leader across hosts, which is fine but harder to reason
  about during deploys.
- Failover gap: an occurrence due during takeover may be missed or coalesced.
- **Already tried and abandoned in flowtask.** Its Redis singleton lock with TTL
  renewal is commented out as "cluster feature has design flaws"
  (`flowtask/scheduler/scheduler.py:118,282-295,406-436`).
- Still needs the durable pause table and the history table, so it costs more than A.

📊 **Effort:** Medium-High

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `asyncpg` | Session advisory lock + optional `LISTEN/NOTIFY` | existing |
| `apscheduler` | Unchanged, started only on the leader | 3.11.2 |

🔗 **Existing Code to Reuse:**
- `querysource/scheduler/scheduler.py:681` `startup` / `:787` `shutdown`: gate
  `self._scheduler.start()` (line 780) behind leadership.
- `querysource/handlers/scheduler.py`: would need leader-aware responses.

---

### Option C: Dedicated scheduler process (deployment split)

Set `ENABLE_QS_SCHEDULER=False` in the gunicorn web workers and run the scheduler
as a separate supervisor program: a small aiohttp app or CLI entrypoint that calls
`QSScheduler.startup`. Web workers read history and state from the DB, and
pause/run-now are requests that the scheduler process picks up (DB queue or
`LISTEN/NOTIFY`).

✅ **Pros:**
- Clean separation: web latency is never affected by job execution, and there is
  exactly one scheduler by construction.
- Long-running jobs no longer tie up a web worker's event loop.

❌ **Cons:**
- This is what flowtask effectively does, by forcing `workers = 1` in
  `gunicorn_config.py`. QS cannot do the same inside navigator-api without
  dropping its web concurrency, so it needs a separate process.
- **Ops change on every host:** a new supervisor program, plus env and deploy
  changes in `navigator-api` (outside this repo), which has already drifted from its
  pinned QS version.
- Exactly-one depends on deployment discipline: two hosts each running the program
  means double runs again, unless A's claim is added anyway.
- Pause/run-now become asynchronous cross-process commands, so the UI needs
  polling.
- Still needs the same history and state tables.

📊 **Effort:** High (code + infra)

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `supervisor` (host) | Run the scheduler process | ops-owned |
| `asyncpg` `LISTEN/NOTIFY` | Command channel web → scheduler | existing driver |

🔗 **Existing Code to Reuse:**
- `querysource/services.py:444-455`: the `ENABLE_QS_SCHEDULER` gate and route
  registration.
- `querysource/scheduler/scheduler.py`: reused wholesale inside the new process.

---

### Option D (unconventional, rejected): Shared APScheduler SQL jobstore

Swap `MemoryJobStore` for APScheduler's `SQLAlchemyJobStore` pointed at the
primary, so all workers share job definitions, `next_run_time` and paused state.

✅ **Pros:**
- Pause/resume persistence would come built in.

❌ **Cons:**
- APScheduler 3.x documents that **sharing one persistent jobstore between
  multiple scheduler instances is unsupported**: there is no locking, so it gives
  duplicate or missed runs. It does not solve the N-fold problem.
- Adds a synchronous SQLAlchemy engine to an async-first codebase, and pickles the
  job callables and kwargs, including the owner envelope.
- No run history either.

📊 **Effort:** Medium, and it does not meet the requirements.

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `apscheduler.jobstores.sqlalchemy` | Persistent jobstore | sync driver; multi-scheduler unsupported in 3.x |

🔗 **Existing Code to Reuse:**
- `querysource/scheduler/scheduler.py:148` `_create_scheduler`.

---

## Recommendation

**Option A** is recommended because:

- **It is the smallest change that meets every requirement at once.**
  - The unique `(job_id, scheduled_for)` claim is the history row, the N-worker
    dedup and the one-alert-per-failure rule.
  - `qs_scheduler_job_state` fixes "pausing does nothing" without touching the
    APScheduler internals.
  - No leader, no long-lived connections, no deployment change. That respects the
    lead's rejection of heavy machinery in FEAT-176 and the fact that prod
    `navigator-api` is already hard to change.
- **Why not B:** it needs the same tables and also forces a handler rewrite, because
  of the leader/non-leader split for HTTP. Flowtask already built and disabled a
  leader lock of this kind.
- **What A takes from flowtask:** DB-as-truth for pause, listeners for
  missed/max-instances and the real `scheduled_run_time`, a denormalized last-run,
  and per-run ids generated at dispatch (the NAV-6365 lesson).
- **Why not C:** it is the cleanest long term, but needs infra changes on every
  host, and without A's claim it still double-runs if deployed twice.
- **D** does not work in APScheduler 3.x.
- **What we trade off:**
  - One primary-DB round-trip per worker per fire.
  - A slot-derivation helper that must be correct for all eight schedule types.
  - A policy choice for "primary unreachable at fire time".
- **A leaves room for C.** A can later be combined with C (move the scheduler into
  its own process) without schema changes, because the claim keeps working even if
  someone runs two scheduler processes.

---

## Feature Description

### User-Facing Behavior

In Concierge (`navigator-frontend-next`, route
`program/[slug]/querysource/scheduler`, `SchedulerMonitor.svelte`):

- **Jobs table:**
  - New columns: **Last run** (relative time + duration) and **Status**, a badge for
    `success`, `failed`, `running` or `abandoned`. A failed last run is
    highlighted in red.
  - The **Paused** state comes from the DB, so it looks the same whichever worker
    answers and it survives restarts.
  - New **Run now** action. It opens a confirmation dialog; on confirm, the UI
    shows `running` and then the final status.
- **Run history drawer per job:** a paginated list of runs.
  - Columns: scheduled for, started, finished, duration, trigger (`scheduled` or
    `manual` + user), status.
  - Filters: status and date range.
  - Expanding a row shows the full error message and `details`, e.g. the
    `ExecuteSQL` tags `['LOCK TABLE','DELETE 55229','INSERT 0 55439']` and the row
    counts written by `Table`.
- **Slug inspector (`SlugInspectorPanel.svelte`):** a "Schedule runs" panel with
  the last N runs for that slug's job(s).
- **Failure alerts:** when a scheduled run fails, one alert goes to the configured
  channel. It contains the slug, job id, scheduled time, error summary and a link to
  the Concierge history.

### Internal Behavior

1. **Fire, in every worker.** The APScheduler trigger fires and the wrapped job
   callable starts.
2. **Paused check.** Read `qs_scheduler_job_state` for `job_id` on the primary. If
   paused, log at DEBUG, also pause the local job if it isn't already, and return.
3. **Compute `scheduled_for`** from the job's trigger and the current time.
4. **Claim.** `INSERT INTO qs_scheduler_runs (job_id, slug, kind, tenant/store
   cols, scheduled_for, trigger='scheduled', status='running', started_at,
   host, pid) ON CONFLICT (job_id, scheduled_for) DO NOTHING RETURNING id`.
   No row back means another worker owns the occurrence, so return.
5. **Execute.** Run the existing body (owner revalidation +
   `QS` / `MultiQS` / cache refresh) unchanged. Capture details through a
   run-scoped collector (e.g. a `contextvars.ContextVar`). `ExecuteSQL` and `Table`
   append their status tags or row counts to it when a run context is active; they
   are no-ops otherwise.
6. **Finalize.** Update the row: `status = success|failed`, `finished_at`,
   `duration_ms`, `error` (type + message, truncated) and `details` (jsonb). On
   failure, call `notification_manager.notify(...)`. Only the claimer gets here,
   so there is one alert.
7. **Run now.** `POST /api/v1/qs/scheduler/jobs/{job_id}/run` checks the same auth
   as PATCH, plus `_enforce_scheduler_grant` for write-capable multis.
   - It returns `409` if a run for the job is currently `running`.
   - Otherwise it inserts a `manual` row and schedules the same body via
     `asyncio.create_task` in the serving worker, then returns
     `202 {run_id}`.
8. **Read API.**
   - `GET /api/v1/qs/scheduler/jobs/{job_id}/runs?status=&since=&until=&limit=&offset=`
     returns runs for one job.
   - `GET /api/v1/qs/scheduler/runs?slug=&status=…` covers the cross-job and
     slug-inspector view.
   - `GET /api/v1/qs/scheduler/jobs` adds `last_run` and the durable `paused` to
     each serialized job.
9. **Pause/resume.** PATCH upserts `qs_scheduler_job_state` (who, when, optional
   reason) and pauses the local job. Other workers enforce it at their next fire via
   step 2. `startup` applies the durable state after registering jobs.
10. **Abandoned runs.**
    - At startup, each worker marks `running` rows with `host = this host` whose
      `pid` is not alive as `abandoned`.
    - Rows `running` for longer than `QS_SCHEDULER_RUN_TIMEOUT` are marked
      `abandoned` lazily on read; a periodic sweep is optional.
11. **DDL.** `docs/sql/qs_scheduler_runs.sql` creates both tables and their
    indexes:
    - unique `(job_id, scheduled_for)`;
    - `(job_id, started_at DESC)`;
    - `(slug, started_at DESC)`;
    - a partial index on `status = 'running'`.

    It is applied manually. If the tables are missing, the scheduler degrades:
    it logs once and runs the old way, without history and without the guard.

### Edge Cases & Error Handling

- **Primary unreachable at fire time:** policy decision (see Open Questions). Either
  fail-open (run without a claim, log loudly; N runs possible) or fail-closed (skip,
  log, alert).
- **Clock skew between hosts:** `scheduled_for` comes from the trigger, not from
  wall-clock truncation, so skew of seconds still maps to the same slot.
- **Coalesce/misfire:** with `coalesce = True`, APScheduler runs one late fire. The
  derived slot is the latest due fire time, so a late worker still collides with
  the on-time worker's claim.
- **Interval triggers with per-worker start dates:** each worker's
  `IntervalTrigger` anchors at its own registration time, so slots may not align
  across workers. The spec must anchor interval triggers to a deterministic
  `start_date` (e.g. epoch-aligned), or key the claim on a coarser bucket.
- **Manual run while a scheduled run is in flight:** `409`. A scheduled fire during
  a manual run proceeds; its own slot is unique. The spec decides whether it should
  skip.
- **Job removed or slug moved (FEAT-176) while running:** finalize still updates
  the row. History stays keyed by `job_id` + `slug` + store columns.
- **Very long error messages or tracebacks:** truncated in `error`, with the full
  text in the logs.
- **`details` collector absent (non-multi query jobs):** `details` holds only the
  row count / result shape if available, else `{}`.
- **Tables not deployed yet:** feature-flag-free degradation (see Internal Behavior,
  step 11).

---

## Capabilities

### New Capabilities
- `scheduler-run-ledger`: persisted run records per job occurrence (scheduled + manual), with status lifecycle and abandoned detection.
- `scheduler-single-execution`: fire-time claim on `(job_id, scheduled_for)` so each occurrence runs once across all workers/hosts.
- `scheduler-durable-job-state`: DB-backed pause/resume applied at fire time and on startup.
- `scheduler-run-now`: on-demand execution endpoint recorded as a manual run.
- `scheduler-failure-alerts`: async-notify-backed `NotificationManager` callback.
- `scheduler-runs-api`: read endpoints for run history and last-run overlay.

### Modified Capabilities
- `querysource-scheduler` (`sdd/proposals/querysource-scheduler.brainstorm.md`, FEAT-100 docs `docs/QSSCHEDULER.md`): jobs gain claim/finalize wrapper; PATCH semantics become durable.
- `qsscheduler-multi-support`: multi jobs report `ExecuteSQL`/`Table` details into the run record.

---

## Impact & Integration

| Affected Component | Impact Type | Notes |
|---|---|---|
| `querysource/scheduler/jobs.py` | modifies | Wrap the 3 callables with paused-check → claim → execute → finalize; slot derivation |
| `querysource/scheduler/scheduler.py` | modifies | Thread `scheduled_for` derivation inputs (trigger) to jobs; apply durable state in `startup`; `set_job_paused` persists; abandoned sweep at startup |
| `querysource/scheduler/notifications.py` | extends | New async-notify callback; config via navconfig |
| `querysource/scheduler/runs.py` (new) | new | Ledger repository: claim, finalize, list, last-run, mark-abandoned, job-state get/set |
| `querysource/handlers/scheduler.py` | extends | `/jobs/{id}/runs`, `/runs`, `/jobs/{id}/run`; `last_run` + durable `paused` in `_serialize_job` |
| `querysource/services.py` | modifies | Register new routes under the `ENABLE_QS_SCHEDULER` gate (lines 444-455) |
| `querysource/queries/multi/destinations/execute_sql.py`, `Table` destination | modifies (tiny) | Append status tags / row counts to the active run collector |
| `querysource/conf.py` | extends | `QS_SCHEDULER_RUN_TIMEOUT`, alert channel/recipients, `QS_SCHEDULER_CLAIM_POLICY` |
| `docs/sql/qs_scheduler_runs.sql` (new) | new | DDL for `qs_scheduler_runs`, `qs_scheduler_job_state` + indexes; applied manually |
| `docs/QSSCHEDULER.md` | modifies | Document history, run-now, durable pause, single-execution |
| `navigator-frontend-next` (`SchedulerMonitor.svelte`, `SlugInspectorPanel.svelte`, `src/lib/api/querysource.ts`, `src/lib/types/querysource.ts`) | extends (other repo) | History drawer, last-run columns, run-now, inspector panel |
| FEAT-176 (query-store-management-and-migration) | depends on / coordinates | Its cheap "pause → move → re-register" can reuse durable pause + fire-time guard; must not also add its own claim |
| `navigator-api` prod deploy | depends on | Needs a QS release + manual DDL; prod pin drift (4.5.13 pin vs 5.1.5 installed) must be fixed first |

---

## Code Context

### User-Provided Code
_None. The requirements came from discovery answers and the 2026-10-17..19 incident
logs (prod `/var/log/supervisor/navigator.log*`)._

```text
# Source: prod navigator-api logs 2026-10-20 (after the LOCK TABLE hotfix) — evidence of N-fold execution
[INFO] 2026-10-20 10:00:17,231 ... ExecuteSQL: 3 statement(s) committed: ['LOCK TABLE', 'DELETE 55229', 'INSERT 0 55439']
[INFO] 2026-10-20 10:00:27,691 ... ExecuteSQL: 3 statement(s) committed: ['LOCK TABLE', 'DELETE 55439', 'INSERT 0 55439']
[INFO] 2026-10-20 10:00:37,798 ... ExecuteSQL: 3 statement(s) committed: ['LOCK TABLE', 'DELETE 55439', 'INSERT 0 55439']
[INFO] 2026-10-20 10:00:47,910 ... ExecuteSQL: 3 statement(s) committed: ['LOCK TABLE', 'DELETE 55439', 'INSERT 0 55439']
# Source: prod logs 2026-10-17..19 — the silent failure
[WARNING] 2026-10-19 10:00:38,748 QSScheduler.Jobs(jobs.py:162) :: Scheduled multi-query job failed for slug 'assembly_employee_detail_profile_insert' (...): Query Error: QS unhandled Exception: Postgres Error: canceling statement due to conflict with recovery
```

### Verified Codebase References

#### Classes & Signatures
```python
# From querysource/scheduler/jobs.py (dev @ 66741a4)
async def _revalidate_owner(query_obj: Any, owner: TenantOwnerEnvelope, slug: str) -> None:  # line 26
async def scheduled_query_job(                                                              # line 61
    slug: str, notification_manager: NotificationManager | None = None, *,
    owner: TenantOwnerEnvelope | None = None, job_id: str | None = None, **kwargs: Any) -> None
async def scheduled_multiqs_job(                                                            # line 104
    slug: str, notification_manager: NotificationManager | None = None, *,
    owner: TenantOwnerEnvelope | None = None, job_id: str | None = None,
    run_as_user_id: int | None = None, identity_auth: Any = None, **kwargs: Any) -> None
async def cache_refresh_job(                                                                # line 174
    slug: str, notification_manager: NotificationManager | None = None, *,
    owner: TenantOwnerEnvelope | None = None, job_id: str | None = None, **kwargs: Any) -> None
# All three catch Exception, logger.warning(...), then notification_manager.notify(job_id=..., slug=..., error=exc); never re-raise.

# From querysource/scheduler/notifications.py
def logging_callback(job_id: str, slug: str, error: Exception) -> None   # line 14
class NotificationManager:                                                # line 21
    def add_callback(self, callback: Callable) -> None                    # line 29
    def notify(self, job_id: str, slug: str, error: Exception) -> None    # line 37 (sync; callbacks called with kwargs)

# From querysource/scheduler/scheduler.py
class QSScheduler:                                                        # line 121
    self._notification_manager = NotificationManager()                    # line 133
    def _create_scheduler(self) -> AsyncIOScheduler                       # line 148 (MemoryJobStore + AsyncIOExecutor, coalesce/max_instances from conf)
    def _register_query_row(...)                                          # line 281 (add_job scheduled_multiqs_job / scheduled_query_job)
    def _register_cache_row(...)                                          # line 402
    def _slug_job_ids(self, slug: str, *, store: QueryStore | None = None) -> list   # line 469
    def remove_job(self, job_id: str) -> bool                             # line 580
    def set_job_paused(self, job_id: str, paused: bool) -> bool           # line 592 (in-memory pause_job/resume_job only)
    async def register_slug(self, slug: str, *, tenant: str | None = None) -> dict   # line 608
    def setup(self, app: web.Application) -> None                         # line 672
    async def startup(self, app: web.Application) -> None                 # line 681 (self._scheduler.start() at 780; app["qs_scheduler"] = self at 785)
    async def shutdown(self, app: web.Application) -> None                # line 787
    def add_notification_callback(self, callback: Callable) -> None       # line 802

# From querysource/handlers/scheduler.py
def _kind_from_id(job_id: str) -> str                                     # line 42
class SchedulerJobsView(BaseView):                                        # line 74
    def _get_scheduler(self) -> QSScheduler | None                        # line 88 (request.app.get("qs_scheduler"))
    async def _enforce_scheduler_grant(self, slug: str, tenant: str | None) -> None  # line 98 (FEAT-160 pg_admin gate)
    def _serialize_job(self, job: Job) -> dict                            # line 157
    async def get(self) -> web.Response                                   # line 202
    async def post(self) -> web.Response                                  # line 271 (sync slug live)
    async def delete(self) -> web.Response                                # line 343
    async def patch(self) -> web.Response                                 # line 374 (body {"action": "pause"|"resume"} → set_job_paused)

# From querysource/interfaces/guarded_sql.py
def guard_statements(sql: Union[str, List[str]]) -> List[str]             # line 37
async def execute_guarded(statements: List[str], *, timeout: float = 3600.0) -> List[str]   # line 71
#   uses AsyncDB("pg", dsn=default_dsn) → primary; returns asyncpg status tags

# From querysource/queries/multi/destinations/execute_sql.py
class ExecuteSQLDestination(AbstractDestination):
    self.results: List[str] = []                                          # line 137
    async def run(self) -> Union[dict, pd.DataFrame]                      # ~line 139; sets self.results at line 150, logs at 153

# From querysource/repositories/definitions.py
_SCHEDULABLE_COLUMNS = "query_slug, attributes, cache_options, provider, is_cached, query_raw"   # line 87
async def schedulable(self, store: QueryStore) -> tuple[Mapping[str, Any], ...]               # line 322
```

#### Verified Imports
```python
from querysource.scheduler.notifications import NotificationManager       # querysource/scheduler/notifications.py:21
from querysource.interfaces.guarded_sql import execute_guarded, guard_statements   # guarded_sql.py:37,71
from querysource.conf import default_dsn, asyncpg_url, ENABLE_QS_SCHEDULER, QS_SCHEDULER_TIMEZONE  # conf.py:32,44,388,389
from apscheduler.events import JobExecutionEvent  # (code, job_id, jobstore, scheduled_run_time, retval=None, exception=None, traceback=None) — verified 3.11.2
from apscheduler.jobstores.memory import MemoryJobStore                   # scheduler.py:27
from apscheduler.triggers.cron import CronTrigger                         # scheduler.py:30
from apscheduler.triggers.interval import IntervalTrigger                 # scheduler.py:31
```

#### Key Attributes & Constants
- `default_dsn` → primary `DBHOST` DSN (`querysource/conf.py:32`). Prod: `navigator-production-tf…`
- `asyncpg_url` → `PG_HOST` DSN (`querysource/conf.py:44`). Prod: `navigator-ro-replica…`, a hot standby with `max_standby_streaming_delay=30s` and `hot_standby_feedback=off` (verified 2026-10-19).
- `ENABLE_QS_SCHEDULER` (`conf.py:388`), `QS_SCHEDULER_TIMEZONE` (`:389`), `QS_SCHEDULER_MAX_INSTANCES` (`:390`), `QS_SCHEDULER_COALESCE` (`:391`).
- Routes: `/api/v1/qs/scheduler/jobs` and `/api/v1/qs/scheduler/jobs/{job_id}`, registered in `querysource/services.py:449-455` under the `ENABLE_QS_SCHEDULER` gate (line 445).
- Job id shapes: `query_<slug>`, `multi_<slug>`, `cache_<slug>`; non-default stores use the qualified `qsj2-…` ids (`scheduler.py:181` `_qualified_job_id`).
- `async-notify` 2.0.0 is in `uv.lock:659-660` and declared in `pyproject.toml:71`.
- Frontend (`navigator-frontend-next`): `getSchedulerJobs`, `getSchedulerJob` and `syncSchedulerSlug` in `src/lib/api/querysource.ts:561,576,592`. `interface SchedulerJob` is in `src/lib/types/querysource.ts:519`, with fields id, name, kind, slug, next_run_time, trigger, coalesce, max_instances, misfire_grace_time and pending. `SchedulerMonitor.svelte` has 637 lines and `SlugInspectorPanel.svelte` 690. `CrewBuilder/ExecutionHistory.svelte` is an existing execution-history table pattern to mirror (uses `Badge`, `AppDatePicker`, `AppDialog` from `$lib/ui/components`).

#### Flowtask references (other repo; read-only prior art, do not import)
```python
# ~/Documents/navigator/flowtask/flowtask/scheduler/scheduler.py (997 lines)
class NavScheduler:
    async def startup(self, app, conn)                       # line 387 — "Always start the scheduler (no cluster lock)" (416)
    async def create_jobs(self)                              # line 466 — SELECT * FROM troc.jobs WHERE enabled = true (473)
    def add_listeners(self)                                  # line 643 — EXECUTED / ERROR|MISSED / SUBMITTED / ADDED
    def job_success(self, event: JobExecutionEvent)          # line 726
    def job_status(self, event: JobExecutionEvent)           # line 759 — status + telegram notification
    def save_db_event(self, event_loop, event, job)          # line 836 — 1=ok 2=error 3=missed 4=max-instances
    async def update_task_status(self, event_loop, status)   # line 868 — UPDATE troc.jobs SET last_exec_time, next_run_time, job_status, job_state, traceback
# ~/Documents/navigator/flowtask/gunicorn_config.py — workers = 1 (single scheduler by deployment)
# ~/Documents/navigator/flowtask/flowtask/scheduler/handlers/manager.py — _patch_response: enabled flag → remove/re-add job
# ~/Documents/navigator/flowtask/flowtask/events/events/exec.py — LogExecution → InfluxDB TASK_EXEC_TABLE
# ~/Documents/navigator/flowtask/sdd/proposals/SDD_NAV-6365.md — duplicate execution_id on scheduler dispatch
```

### Does NOT Exist (Anti-Hallucination)
- ~~Any run/execution history table or model in `querysource/`~~: no `qs_scheduler_runs`, `job_runs` or similar.
- ~~Persisted pause state~~: `set_job_paused` only calls `AsyncIOScheduler.pause_job` / `resume_job` in-process.
- ~~`scheduled_run_time` passed to job callables~~: APScheduler 3.11 does not inject it. It exists only on `JobExecutionEvent` / `JobSubmissionEvent` for listeners.
- ~~`add_listener` / `EVENT_JOB_*` usage anywhere in `querysource/`~~: not used today.
- ~~`querysource/scheduler/handoff.py` on `dev`~~: it exists only in the FEAT-176 branches. The lead rejected that design on 2026-10-20.
- ~~A "run now" endpoint~~: the only POST is "sync slug" (`handlers/scheduler.py:271`).
- ~~Any `from notify` / async-notify usage in `querysource/`~~: the dependency is declared but unused.
- ~~Auto-migration / startup DDL~~: the repo ships SQL under `docs/sql/` and applies it manually; there is no `CREATE TABLE IF NOT EXISTS` in `querysource/`.
- ~~`wikitoolkit` CLI in this environment~~: it was not installed when researched (2026-10-20).

---

## Parallelism Assessment

- **Internal parallelism:** Moderate.
  1. DDL + `runs.py` ledger repository, which is the foundation.
  2. Job wrapper (claim/finalize/slot derivation) in `jobs.py` + `scheduler.py`.
  3. Durable pause in `scheduler.py` + `handlers/scheduler.py` PATCH.
  4. Read API + run-now in `handlers/scheduler.py` + `services.py`.
  5. Alerts callback in `notifications.py` + conf.
  6. Details collector in multi destinations.
  7. Frontend, in the other repo.

  Items 2, 3 and 4 share `scheduler.py` and `handlers/scheduler.py`. Items 5 and 6
  are independent after item 1. Item 7 only depends on the API contract.
- **Cross-feature independence:** It conflicts with **FEAT-176**
  (`feat-FEAT-176-query-store-management-and-migration`,
  `feat-FEAT-176-simple-query-move`). Its tasks TASK-915/916/917/918/926 modify
  `scheduler/jobs.py`, `scheduler/scheduler.py` and `handlers/scheduler.py` and
  introduce `handoff.py`. The lead's 2026-10-20 rejection means those tasks are
  likely re-scoped. This feature should land first, or FEAT-176's simplified move
  should be rebased onto it.
- **Recommended isolation:** `mixed`. The ledger, jobs wrapper, pause and API are
  sequential in one worktree because of shared files. Alerts and the destinations
  collector can use separate worktrees.
- **Rationale:** most tasks touch the same two scheduler files, so splitting them
  would only create merge conflicts. The notification and multi-destination edits
  live in separate files with a small interface (`notify` callback signature,
  collector API).

---

## Open Questions

- [ ] Claim policy when the primary is unreachable at fire time: fail-open (run, log, possible N runs) or fail-closed (skip + alert)? — *Owner: Juan Rodriguez / Jesus Lara*
- [ ] Alert channel and recipients (email via SES, Teams, Slack), and per-program routing vs. one ops channel? — *Owner: Juan Rodriguez*
- [ ] Schema for the central tables: `public` (next to `public.queries`) or a dedicated schema (e.g. `qs`/`navigator`)? — *Owner: Juan Rodriguez / DBA*
- [ ] Interval triggers: anchor `IntervalTrigger` to a deterministic `start_date` so slots align across workers, or claim on a coarser bucket? — *Owner: spec author*
- [ ] Should a scheduled fire skip when a manual run of the same job is still `running` (beyond `max_instances`)? — *Owner: Juan Rodriguez*
- [ ] Coordination with FEAT-176: does this feature land first and FEAT-176's simplified move consume durable pause + claim, or is FEAT-176 re-scoped to drop TASK-915/916/918? — *Owner: Jesus Lara*
- [ ] Frontend work: tracked as tasks in this SDD (cross-repo) or as a separate spec in `navigator-frontend-next`? — *Owner: Juan Rodriguez*
- [ ] Prod rollout prerequisite: fix the `navigator-api` QS pin drift (pinned 4.5.13 vs installed 5.1.5) before shipping, so a deploy doesn't silently revert? — *Owner: Juan Rodriguez*
- [x] Which jobs are recorded? — *Owner: Juan Rodriguez*: all kinds (query, multi, cache-refresh).
- [x] N-worker duplicate execution in scope? — *Owner: Juan Rodriguez*: yes, fix it in this feature.
- [x] UI scope? — *Owner: Juan Rodriguez*: per-job history, last-run column with failure badge, slug-inspector history, full error + ExecuteSQL details.
- [x] Retention? — *Owner: Juan Rodriguez*: no automatic purge.
- [x] Table location? — *Owner: Juan Rodriguez*: one central table in the primary DB with tenant/store columns.
- [x] Orphaned `running` rows? — *Owner: Juan Rodriguez*: mark as `abandoned`.
- [x] Permissions? — *Owner: Juan Rodriguez*: same as the current scheduler monitor (plus FEAT-160 grant for run-now on write multis).
- [x] Extras? — *Owner: Juan Rodriguez*: include "run now" button and failure alerts; durable pause/resume (pausing currently does nothing and jobs re-activate on their own).
