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

# Feature Specification: Scheduler Execution History

**Feature ID**: FEAT-181
**Date**: 2026-10-20
**Author**: Juan Rodriguez (drafted with Claude)
**Status**: draft
**Target version**: next minor release after FEAT-176 (no version bump prescribed)
**Brainstorm**: `sdd/proposals/scheduler-execution-history.brainstorm.md` (Option A)
**Frontend counterpart**: separate spec in `navigator-frontend-next`. It consumes the
API contract in §2 and lands after that repo's FEAT-252.

---

## 1. Motivation & Business Requirements

### Problem Statement

QSScheduler (`querysource/scheduler/`) records nothing about the jobs it runs. A
finished or failed run produces one log line and a `NotificationManager.notify`
call, and the only callback is logging-only. Concierge (`navigator-frontend-next`)
can only show the in-memory jobstore: registered jobs and `next_run_time`.

**Incident that prompted this (2026-10-17 → 2026-10-19).** The multi
`assembly_employee_detail_profile_insert` failed every day at 06:00
America/New_York with `canceling statement due to conflict with recovery`. Its
source query read from the RO replica, which has `max_standby_streaming_delay=30s`.
The target table was stale for three days. Nobody saw it until a business user
reported it, and the diagnosis needed SSH access to the prod gunicorn logs.

Three related defects are in scope:

1. **Each occurrence runs N times.** QSScheduler uses a per-process
   `MemoryJobStore` and starts in every gunicorn worker, so each occurrence runs
   once per worker. In prod that is 4×; the 2026-10-20 logs show 4×
   `ExecuteSQL: 3 statement(s) committed` for one 06:00 occurrence.
2. **Pause is not durable.** `PATCH …/jobs/{id}` pauses only the serving worker's
   in-memory job, and a restart re-registers it as active. In the user's words:
   *"pausarlas no hace nada, luego se vuelven a activar solas"* (pausing them does
   nothing; they turn themselves back on later).
3. **Failures are silent.** A failure only produces a WARNING log line.

### Goals

- Record **every** scheduler execution of every job kind (`query`, `multi`,
  `cache`) in Postgres, plus every on-demand run:
  - who ran it: `scheduled` or `manual` + user;
  - when: scheduled time, start, finish, duration;
  - the outcome: status, error, worker host/pid;
  - the details: e.g. `ExecuteSQL` status tags and the row counts `Table` wrote.
- Run **each scheduled occurrence exactly once** across all workers and hosts.
  The mechanism is a per-occurrence claim. It is **not** leader election and
  **not** a deployment change.
- Make **pause/resume durable**: it applies in every worker at the next fire
  and survives restarts.
- Add **Run now**: an endpoint that executes a job on demand and records it as a
  `manual` run.
- Send **one failure alert per failed occurrence** through a configurable
  async-notify channel. The default is Telegram.
- Mark runs orphaned by a dead worker as **`abandoned`**.
- Expose a read API for run history and last-run status, which the Concierge
  frontend spec consumes.

### Non-Goals (explicitly out of scope)

- **Concierge UI:** a separate spec in `navigator-frontend-next`; this spec only
  owns the API contract.
- **Automatic retention/purge** of run rows: decided "no purge".
- **Leader election / a single-worker scheduler.** Rejected in the brainstorm
  (Options B/C); flowtask's Redis leader lock was disabled for "design flaws".
- **Replacing `MemoryJobStore`** with a shared APScheduler SQL jobstore. Rejected
  (Option D): APScheduler 3.x does not support multiple schedulers on one
  jobstore.
- **Recording ad-hoc slug or multi runs from the regular query API.** Only
  scheduler jobs and the new run-now endpoint are recorded.
- **Changing FEAT-176's transient move pause** (`pause_slug_jobs`); see §7.

---

## 2. Architectural Design

### Overview

Option A, "the run ledger is the lock". It adds two tables in the primary DB, in
the `troc` schema next to flowtask's `troc.jobs`:

- **`troc.qs_scheduler_runs`** is the append-only run history. Its unique key
  `(job_id, scheduled_for)` makes the history row also the **single-execution
  claim**.
- **`troc.qs_scheduler_job_state`** is the durable per-job state: `paused`, who
  and when, and a denormalized last run.

**Fire-time flow.** FEAT-176 already gives each job callable a fire-time
`guard(slug, job_id)` (today `QSScheduler.definition_present`). FEAT-181 replaces
the bound guard with `QSScheduler.admit`, a superset of it that runs in every
worker:

1. The definition still exists in the store (FEAT-176 behavior, unchanged).
2. The job is not durably paused. If it is paused, also pause the local job and
   skip.
3. Compute `scheduled_for`: the latest fire time of the job's trigger that is ≤
   now. It is not wall-clock truncation.
4. Claim: `INSERT … ON CONFLICT (job_id, scheduled_for) DO NOTHING RETURNING
   run_id` on the primary.
   - A row back means this worker runs the occurrence. `admit` returns a
     `RunTicket`.
   - No row means another worker owns it, so skip.

The job body runs unchanged inside `tracked_run(ticket)`. That context manager
binds a per-run details collector (a `ContextVar`) and, on exit, finalizes the
row: `success|failed`, `finished_at`, `duration_ms`, `error`, `details`. It
also refreshes `job_state.last_*`. The existing `notification_manager.notify`
call stays where it is; because only the claimer reaches it, the new async-notify
callback sends one alert per failure.

**Fail-open, as decided and as flowtask does.** If the ledger is unreachable at
claim time:
- log at ERROR, schedule an alert, and **run the job untracked**;
- bookkeeping never blocks business execution;
- a duplicate during a primary outage is accepted.

If the tables are not deployed, the ledger is disabled at startup (one WARNING)
and the scheduler behaves exactly as today.

**Run now.** `POST /api/v1/qs/scheduler/jobs/{job_id}/run`:
1. Looks up the job in the serving worker's jobstore.
2. Enforces the same access as the scheduler view, plus the FEAT-160 grant for
   write-capable multis.
3. Returns `409` if a **manual** run of the job is still `running`.
4. Otherwise inserts a `manual` run (`scheduled_for = now()`, `requested_by`),
   starts the job callable with a pre-claimed ticket via `asyncio.create_task`,
   and returns `202 {run_id}`.

**Durable pause.** PATCH upserts `job_state.paused` (who, when, reason) and also
pauses or resumes the local in-memory job. Other workers honor it at their next
fire (admit, step 2). `startup` applies durable pauses after registering jobs.
`GET /jobs` reports the durable flag, so every worker answers the same.

**Missed / max-instances.** APScheduler listeners for `EVENT_JOB_MISSED` and
`EVENT_JOB_MAX_INSTANCES` record `missed` / `skipped` rows keyed by the event's
real `scheduled_run_time`, through the same `ON CONFLICT DO NOTHING`. This
follows flowtask's `add_listeners` and its status codes 3/4.

**Abandoned runs.**
- At startup, each worker marks as `abandoned` the `running` rows of its own host
  whose pid is no longer alive.
- Run reads lazily mark `running` rows older than `QS_SCHEDULER_RUN_TIMEOUT` as
  `abandoned`.

**Interval triggers.** All workers must compute identical slots, so an `interval`
schedule without `start_date` gets a fixed anchor (`QS_SCHEDULER_INTERVAL_ANCHOR`,
default `2000-01-01T00:00:00` in the scheduler TZ), as flowtask does with
`start_date`.

### Component Diagram

```
APScheduler trigger (every worker)
        │
        ▼
job callable (jobs.py) ── guard(slug, job_id) ──► QSScheduler.admit(store, …)
        │                                          ├─ definition_present (FEAT-176)
        │                                          ├─ RunLedger.is_paused ──► troc.qs_scheduler_job_state
        │                                          ├─ scheduled_slot(trigger, now)
        │                                          └─ RunLedger.claim ───────► troc.qs_scheduler_runs (UNIQUE job_id, scheduled_for)
        │   RunTicket | True | False
        ▼
tracked_run(ticket) ── binds run_details ContextVar
        │      ├─ QS / MultiQS / cache body (unchanged)
        │      │     └─ ExecuteSQLDestination / TableDestination ─► record_detail(...)
        │      └─ on exit: RunLedger.finish(...) ─► runs + job_state.last_*
        ▼
notification_manager.notify ─► logging_callback + AlertCallback (async-notify → Telegram/email)

HTTP: SchedulerJobsView (GET adds last_run + durable paused; PATCH durable)
      SchedulerRunsView  (GET /runs, /runs/{run_id}, /jobs/{job_id}/runs)
      SchedulerRunNowView (POST /jobs/{job_id}/run) ─► QSScheduler.run_now
APScheduler listeners (MISSED / MAX_INSTANCES) ─► RunLedger.record_skipped
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `QSScheduler` (`scheduler/scheduler.py:128`) | modifies | New `admit`, `run_now`, `set_job_paused_durable`; `guard` binding switches from `definition_present` to `admit`; startup creates the ledger, applies durable pauses, marks abandoned, adds listeners; `_parse_trigger` interval anchor |
| `definition_present` (`scheduler.py:630`) | uses | Called first inside `admit`; unchanged |
| `scheduled_query_job` / `scheduled_multiqs_job` / `cache_refresh_job` (`jobs.py:105/154/230`) | modifies | Keep the guard's return value; run the body inside `tracked_run` when it is a `RunTicket` |
| `NotificationManager` (`notifications.py:21`) | extends | New async-notify `AlertCallback`, registered when `QS_SCHEDULER_NOTIFY != "none"` |
| `SchedulerJobsView` (`handlers/scheduler.py:74`) | modifies | GET overlays `paused` + `last_run`; PATCH becomes durable |
| `ExecuteSQLDestination.run` (`multi/destinations/execute_sql.py:150`) | modifies (1 line) | `record_detail("execute_sql", self.results)` |
| `TableDestination` (`multi/destinations/table.py:650,666`) | modifies (2 sites) | `record_detail("table", {...rows, schema, table})` |
| `QuerySource` route setup (`services.py:453-466`) | modifies | Register the runs/run-now routes inside the `ENABLE_QS_SCHEDULER` block |
| `conf.py` (`:388-391`) | extends | New `QS_SCHEDULER_*` keys (§6) |
| FEAT-176 move (`handlers/query_moves.py:95-112`) | unchanged, coexists | Its transient `pause_slug_jobs` / `set_job_paused` stays in-memory; see §7 |

### Data Models

```sql
-- docs/sql/qs_scheduler_runs.sql  (applied manually, repo convention)
CREATE TABLE troc.qs_scheduler_runs (
    run_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id         varchar NOT NULL,
    slug           varchar NOT NULL,
    kind           varchar NOT NULL,            -- query | multi | cache
    store_schema   varchar,                     -- owner envelope schema (NULL = legacy default)
    store_table    varchar,
    trigger        varchar NOT NULL,            -- scheduled | manual
    requested_by   varchar,                     -- user id / username for manual runs
    scheduled_for  timestamptz NOT NULL,
    started_at     timestamptz,
    finished_at    timestamptz,
    duration_ms    bigint,
    status         varchar NOT NULL,            -- running | success | failed | abandoned | missed | skipped
    error          text,                        -- "<ExcType>: <message>", truncated to QS_SCHEDULER_ERROR_MAX
    details        jsonb NOT NULL DEFAULT '{}'::jsonb,
    host           varchar,
    pid            integer,
    CONSTRAINT uq_qs_scheduler_runs_occurrence UNIQUE (job_id, scheduled_for)
);
CREATE INDEX ix_qs_scheduler_runs_job_started  ON troc.qs_scheduler_runs (job_id, started_at DESC);
CREATE INDEX ix_qs_scheduler_runs_slug_started ON troc.qs_scheduler_runs (slug, started_at DESC);
CREATE INDEX ix_qs_scheduler_runs_running      ON troc.qs_scheduler_runs (started_at) WHERE status = 'running';

CREATE TABLE troc.qs_scheduler_job_state (
    job_id        varchar PRIMARY KEY,
    slug          varchar NOT NULL,
    paused        boolean NOT NULL DEFAULT false,
    paused_by     varchar,
    paused_at     timestamptz,
    reason        text,
    last_run_id   uuid,
    last_status   varchar,
    last_run_at   timestamptz,
    updated_at    timestamptz NOT NULL DEFAULT now()
);
```

Manual runs never collide with scheduled slots, because `scheduled_for = now()`
carries microseconds.

```python
# querysource/scheduler/runs.py
RunStatus = Literal["running", "success", "failed", "abandoned", "missed", "skipped"]
RunTriggerType = Literal["scheduled", "manual"]

@dataclass(frozen=True, slots=True)
class RunTicket:
    run_id: str
    job_id: str
    slug: str
    kind: str
    scheduled_for: datetime
    trigger: RunTriggerType
    started_at: datetime
```

### New Public Interfaces

HTTP (all registered only when `ENABLE_QS_SCHEDULER`):

| Method & path | Response |
|---|---|
| `GET /api/v1/qs/scheduler/jobs` (modified) | each job gains `paused: bool` (durable) and `last_run: {run_id, status, started_at, finished_at, duration_ms} \| null`; the envelope gains `history_enabled: bool` |
| `GET /api/v1/qs/scheduler/jobs/{job_id}/runs?status=&since=&until=&limit=50&offset=0` | `{runs: [...], total, limit, offset}`, newest first |
| `GET /api/v1/qs/scheduler/runs?slug=&job_id=&status=&since=&until=&limit=&offset=` | same envelope, across jobs (slug inspector) |
| `GET /api/v1/qs/scheduler/runs/{run_id}` | one run with full `error` + `details`; `404` if unknown |
| `POST /api/v1/qs/scheduler/jobs/{job_id}/run` | `202 {run_id, status: "running"}`; `404` unknown job; `409 {error_code: "run_in_progress", run_id}`; `503` scheduler or ledger unavailable |
| `PATCH /api/v1/qs/scheduler/jobs/{job_id}` (modified) | same body/response; now durable; `503` if the ledger is unavailable and the action cannot be persisted |

`limit` is capped at 200. Read endpoints return `503
{"error_code": "history_unavailable"}` when the ledger is disabled.

---

## 3. Module Breakdown

#### Delegation-eligible modules

| Module | Eligible? | Decided patterns / exact contracts | Why not (if no) |
|---|---|---|---|
| M1: DDL | yes | Exact DDL in §2 Data Models | — |
| M2: Run ledger | yes | Skeleton below; asyncpg via `AsyncDB("pg", dsn=default_dsn)` (pattern: `guarded_sql.py:execute_guarded`) | — |
| M3: Slot derivation + interval anchor | no | — | Correctness across 8 schedule types needs reasoning + property tests |
| M4: Admission + tracked jobs | no | — | Composes the FEAT-176 guard, fail-open semantics and concurrency |
| M5: Durable pause, startup, listeners | no | — | Interacts with FEAT-176 transient pause and per-worker state |
| M6: Run now | yes | Contract in §2 + skeleton | — |
| M7: Alerts | yes | async-notify; config keys §6; message format below | — |
| M8: Details collector hooks | yes | 3 one-line `record_detail` calls | — |
| M9: HTTP API | yes | Contract in §2; follows `SchedulerJobsView` conventions | — |
| M10: Docs + race test | no | — | Multi-process Postgres race test design |

### Module 1: DDL
- **Path**: `docs/sql/qs_scheduler_runs.sql` (new)
- **Responsibility**: create both tables and their indexes. It is idempotent
  (`IF NOT EXISTS`) and applied manually.
- **Depends on**: none

### Module 2: Run Ledger
- **Path**: `querysource/scheduler/runs.py` (new)
- **Responsibility**: all SQL for `troc.qs_scheduler_runs` / `troc.qs_scheduler_job_state`.
  Parameterized asyncpg against the **primary** (`default_dsn`); never the replica.
- **Depends on**: M1
- **Interface Skeleton**:
  ```python
  # querysource/scheduler/runs.py  (new)
  from querysource.conf import default_dsn  # verified: querysource/conf.py:32

  class RunLedger:
      """Run history + durable job state on the primary DB (troc schema)."""

      def __init__(self, dsn: str = default_dsn, *, schema: str = "troc") -> None: ...

      async def available(self) -> bool:
          """True when both tables exist (to_regclass); never raises."""

      async def claim(self, *, job_id: str, slug: str, kind: str, owner: Mapping[str, Any] | None,
                      scheduled_for: datetime, trigger: RunTriggerType = "scheduled",
                      requested_by: str | None = None) -> RunTicket | None:
          """INSERT … ON CONFLICT (job_id, scheduled_for) DO NOTHING RETURNING run_id.
          Returns None when another worker owns the occurrence. Raises on DB errors (caller decides fail-open)."""

      async def finish(self, ticket: RunTicket, *, status: RunStatus, error: str | None,
                       details: Mapping[str, Any]) -> None:
          """Finalize the run row and upsert job_state.last_*; never raises (logs)."""

      async def record_skipped(self, *, job_id: str, slug: str, kind: str, owner: Mapping[str, Any] | None,
                               scheduled_for: datetime, status: Literal["missed", "skipped"]) -> None:
          """Insert a terminal row for a missed/max-instances occurrence (ON CONFLICT DO NOTHING); never raises."""

      async def manual_running(self, job_id: str) -> str | None:
          """run_id of a still-running manual run of job_id, else None."""

      async def is_paused(self, job_id: str) -> bool: ...
      async def paused_job_ids(self) -> set[str]: ...
      async def set_paused(self, job_id: str, slug: str, *, paused: bool, user: str | None,
                           reason: str | None) -> None: ...

      async def last_runs(self, job_ids: Sequence[str]) -> dict[str, dict[str, Any]]: ...
      async def list_runs(self, *, job_id: str | None = None, slug: str | None = None,
                          status: str | None = None, since: datetime | None = None,
                          until: datetime | None = None, limit: int = 50, offset: int = 0
                          ) -> tuple[list[dict[str, Any]], int]: ...
      async def get_run(self, run_id: str) -> dict[str, Any] | None: ...

      async def mark_abandoned_local(self, host: str, is_alive: Callable[[int], bool]) -> int:
          """Mark this host's `running` rows whose pid is dead as abandoned; returns count."""
      async def mark_stale(self, timeout: timedelta) -> int:
          """Mark `running` rows older than timeout as abandoned; returns count."""

  # Per-run details collector
  run_details: ContextVar[dict[str, Any] | None]
  def record_detail(key: str, value: Any) -> None:
      """Append value under key in the active run's details; no-op outside a tracked run."""
  ```

### Module 3: Slot derivation + interval anchor
- **Path**: `querysource/scheduler/runs.py` (helper), `querysource/scheduler/scheduler.py` (`_parse_trigger`)
- **Responsibility**: `scheduled_slot` returns the latest fire time ≤ `now`
  for any APScheduler trigger. It searches within `lookback` and walks forward
  with `get_next_fire_time` (bounded iterations). If no fire time is found, it
  returns `now` truncated to the second and logs at DEBUG. `_parse_trigger` adds
  `start_date = QS_SCHEDULER_INTERVAL_ANCHOR` to interval schedules that lack one.
- **Depends on**: none
- **Interface Skeleton**:
  ```python
  # querysource/scheduler/runs.py
  def scheduled_slot(trigger: BaseTrigger, now: datetime, *, lookback: timedelta) -> datetime:
      """Latest trigger fire time <= now (tz-aware). Identical across workers for the same trigger config."""
  # querysource/scheduler/scheduler.py  (modifies _parse_trigger, verified: scheduler.py:220-221)
  #   if schedule_type == "interval": schedule.setdefault("start_date", <anchor in tz>) before IntervalTrigger(**schedule)
  ```

### Module 4: Admission + tracked jobs
- **Path**: `querysource/scheduler/scheduler.py`, `querysource/scheduler/jobs.py`, `querysource/scheduler/runs.py`
- **Responsibility**: `QSScheduler.admit` runs, in order:
  1. `definition_present`;
  2. the durable-pause check;
  3. `scheduled_slot`;
  4. `claim`.

  The guard binding at the three `partial(self.definition_present, store)` sites
  changes to `partial(self.admit, store)`.

  The jobs keep the guard result and wrap the body in `tracked_run(ticket, ledger)`:
  - it sets `run_details` and finalizes `success` / `failed`;
  - on a **failure path it re-raises**, so the existing `except` block in each
    job still logs and notifies;
  - the finalize and notify happen exactly once.

  If the ledger raises at claim, `admit` returns `True` (fail-open), logs at
  ERROR and schedules an alert.
- **Depends on**: M2, M3
- **Interface Skeleton**:
  ```python
  # querysource/scheduler/scheduler.py
  class QSScheduler:  # verified: scheduler.py:128
      _ledger: RunLedger | None          # set in startup when RunLedger.available()
      async def admit(self, store: QueryStore, slug: str, job_id: str | None) -> RunTicket | bool:
          """Fire-time gate. False = skip; True = run untracked (ledger off / fail-open); RunTicket = run tracked.
          Supersedes definition_present (verified: scheduler.py:630), which it calls first."""
  # querysource/scheduler/runs.py
  @asynccontextmanager
  async def tracked_run(ticket: RunTicket | bool, ledger: RunLedger | None) -> AsyncIterator[None]:
      """No-op unless ticket is a RunTicket; binds run_details; finish(success|failed) on exit; re-raises."""
  # querysource/scheduler/jobs.py  (modifies the 3 callables, verified: jobs.py:131,198,259)
  #   admission = await guard(slug, job_id) if guard is not None else True
  #   if not admission: return
  #   ... async with tracked_run(admission, ledger): <existing body inside `with _running(...)`>
  #   ledger reaches the job via a new kwarg `ledger: RunLedger | None = None` bound at registration.
  ```

### Module 5: Durable pause, startup reconciliation, listeners
- **Path**: `querysource/scheduler/scheduler.py`, `querysource/handlers/scheduler.py`
- **Responsibility**:
  - `set_job_paused_durable` writes `job_state` and applies the local pause.
  - PATCH calls it.
  - `startup`, after loading jobs, does three things:
    1. creates `RunLedger` if it is `available()`, otherwise disables it with one
       WARNING;
    2. pauses the locally registered jobs that are in `paused_job_ids()`;
    3. runs `mark_abandoned_local(socket.gethostname(), pid-alive check)`.
  - Registers listeners for `EVENT_JOB_MISSED | EVENT_JOB_MAX_INSTANCES`. They
    call `record_skipped` via `asyncio.create_task`, never by spinning up an event
    loop in a thread, which is the flowtask anti-pattern.
  - `register_slug` re-applies the durable pause to the jobs it registers.
- **Depends on**: M2, M4
- **Interface Skeleton**:
  ```python
  # querysource/scheduler/scheduler.py
  async def set_job_paused_durable(self, job_id: str, paused: bool, *, user: str | None,
                                   reason: str | None = None) -> bool:
      """Persist pause/resume (job_state) and apply locally; False when job_id is unknown here.
      Raises RuntimeError when the ledger is disabled (handler maps to 503)."""
  def _on_job_skipped(self, event: JobExecutionEvent) -> None:
      """Listener: MISSED -> 'missed', MAX_INSTANCES -> 'skipped'; schedules ledger.record_skipped."""
  # set_job_paused (verified: scheduler.py:596) stays in-memory — FEAT-176's move uses it transiently.
  ```

### Module 6: Run now
- **Path**: `querysource/scheduler/scheduler.py`
- **Responsibility**: start an on-demand run of a registered job. It creates the
  `manual` ticket and executes `job.func(**job.kwargs)` with
  `guard` replaced by a coroutine that returns the pre-claimed ticket. The job runs
  as a background task held in `self._manual_tasks`; shutdown cancels and finalizes
  them.
- **Depends on**: M2, M4
- **Interface Skeleton**:
  ```python
  async def run_now(self, job_id: str, *, user: str | None) -> RunTicket:
      """Raises KeyError (unknown job), RunInProgress(run_id) (manual run still running), RuntimeError (ledger off)."""
  class RunInProgress(Exception):
      run_id: str
  ```

### Module 7: Failure alerts
- **Path**: `querysource/scheduler/notifications.py`, `querysource/conf.py`
- **Responsibility**: `AlertCallback(job_id, slug, error)`, which is sync and
  matches the `NotificationManager.notify` contract (`notifications.py:37`).
  - It schedules an async send on the running loop.
  - Provider: per-job `attributes.scheduler.notify` (a map filled at
    registration), else `QS_SCHEDULER_NOTIFY`.
  - `telegram` uses `EVENT_CHAT_BOT` / `EVENT_CHAT_ID`; `email` uses the
    `DEFAULT_RECIPIENT` config; `none` disables alerts.
  - Message:
    `🛑 [<ENV> QSScheduler] <slug> (<job_id>) failed at <scheduled_for>: <error, ≤500 chars>` + `QS_SCHEDULER_ALERT_URL` link if set.
  - Send failures are logged and never raised.
  - `admit`'s fail-open path uses the same callback with a `⚠️` "history
    unavailable" message.
- **Depends on**: none (wired by M4/M5)
- **Interface Skeleton**:
  ```python
  class AlertCallback:
      def __init__(self, provider: str, *, per_job: Mapping[str, str] | None = None,
                   environment: str = "", link: str | None = None) -> None: ...
      def __call__(self, job_id: str, slug: str, error: Exception) -> None:
          """Schedule the send; never raises."""
  ```

### Module 8: Details collector hooks
- **Path**: `querysource/queries/multi/destinations/execute_sql.py`, `querysource/queries/multi/destinations/table.py`
- **Responsibility**:
  - `ExecuteSQLDestination` calls `record_detail("execute_sql", list(self.results))`
    after line 150.
  - `TableDestination` calls `record_detail("table", {"schema", "table", "rows", "method"})`
    at both "wrote %d rows" sites.
  - Outside a tracked run these calls are no-ops.
- **Depends on**: M2 (`record_detail`)

### Module 9: HTTP API
- **Path**: `querysource/handlers/scheduler.py`, `querysource/services.py`
- **Responsibility**:
  - `SchedulerJobsView.get` overlays `paused` and `last_run` with one
    `paused_job_ids()` and one `last_runs(ids)` call per request. With the ledger
    off, `paused` comes from the in-memory `next_run_time is None`.
  - `SchedulerJobsView.patch` becomes durable.
  - New `SchedulerRunsView` for the read endpoints and `SchedulerRunNowView` for
    POST run, both registered in the `ENABLE_QS_SCHEDULER` block of
    `services.py`.
  - Run-now applies `_enforce_scheduler_grant` (`handlers/scheduler.py:98`) for
    multi jobs.
  - `requested_by` / `paused_by` come from the request session user, using the
    same session access the existing views use.
- **Depends on**: M2, M5, M6

### Module 10: Docs + multi-process race test
- **Path**: `docs/QSSCHEDULER.md`, `tests/scheduler/…`, `tests/integration/test_scheduler_single_execution_postgres.py`
- **Responsibility**:
  - Document history, run now, durable pause, alerts and the interval anchor.
  - A disposable-Postgres test with ≥2 independent `QSScheduler` instances firing
    the same occurrence asserts exactly one `success` row and one body execution.
- **Depends on**: M1–M9

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_scheduled_slot_cron_daily` | M3 | daily 06:00 NY: slot is today 06:00 for `now` = 06:00:00.4 and 06:14 |
| `test_scheduled_slot_all_types` | M3 | cron/crontab/interval/hourly/daily/weekly/monthly/biweekly give the latest fire ≤ now; two "workers" with now ±3 s give the same slot |
| `test_scheduled_slot_frequent_cron` | M3 | `*/1` cron with a large lookback still returns the latest minute, not the earliest |
| `test_interval_anchor_default` | M3 | interval without `start_date` gets the anchor; with `start_date`, it is preserved |
| `test_claim_once` | M2 | two claims of the same `(job_id, scheduled_for)` → one ticket, one None |
| `test_finish_updates_last_run` | M2 | `finish` writes status/duration/details and `job_state.last_*` |
| `test_admit_skips_paused` | M4 | durable paused → returns False, local job paused, no row |
| `test_admit_definition_missing` | M4 | FEAT-176 behavior preserved: missing slug → False and job removed |
| `test_admit_fail_open` | M4 | ledger raises → True, ERROR log, alert scheduled, body runs |
| `test_admit_ledger_disabled` | M4 | no tables → True, legacy behavior, no alert spam |
| `test_tracked_run_failure_reraises` | M4 | body raises → row `failed` with error, existing `except` notifies once |
| `test_job_callables_untracked_when_true` | M4 | guard returns True → body runs, no ledger calls |
| `test_set_job_paused_durable` | M5 | persists, applies locally; survives a simulated restart (startup re-applies) |
| `test_startup_marks_abandoned_local` | M5 | dead pid on this host → `abandoned`; live pid untouched |
| `test_listener_missed_records_row` | M5 | `EVENT_JOB_MISSED` → one `missed` row (dedup across 2 listeners) |
| `test_run_now_manual_row_and_409` | M6 | creates `manual` run; second call while running → `RunInProgress` |
| `test_alert_callback_providers` | M7 | telegram/email/none/per-job override; send error is logged, not raised |
| `test_record_detail_noop_outside_run` | M8 | no active run → no error, nothing recorded |
| `test_execute_sql_details_recorded` | M8 | tracked multi run stores `['LOCK TABLE','DELETE n','INSERT 0 n']` in `details.execute_sql` |
| `test_jobs_view_overlay` | M9 | GET /jobs includes durable `paused` + `last_run`; `history_enabled` flag |
| `test_runs_endpoints` | M9 | filters, pagination cap 200, 404 run, 503 when ledger off |
| `test_run_now_endpoint_grant` | M9 | write-capable multi without FEAT-160 grant → 403; with grant → 202 |

### Integration Tests
| Test | Description |
|---|---|
| `test_single_execution_two_schedulers` | Disposable Postgres; 2 `QSScheduler` instances with the same job and a date trigger → exactly 1 body execution, 1 `success` row |
| `test_durable_pause_across_instances` | Pause via instance A's PATCH; instance B's next fire skips |
| `test_existing_scheduler_suites_green` | `tests/scheduler/`, `tests/test_scheduler_*.py`, `tests/tenants/test_query_move.py` still pass |

### Test Data / Fixtures
```python
@pytest.fixture
async def ledger(pg_dsn):          # disposable Postgres with docs/sql/qs_scheduler_runs.sql applied
    ...
@pytest.fixture
def fake_trigger_daily_0600_ny():  # CronTrigger(hour=6, minute=0, timezone="America/New_York")
    ...
```

---

## 5. Acceptance Criteria

- [ ] Every execution of `query`, `multi` and `cache` jobs is recorded in
  `troc.qs_scheduler_runs` with `scheduled_for`, `started_at`, `finished_at`,
  `duration_ms`, `status`, `error`, `details`, `host` and `pid`.
- [ ] With N ≥ 2 scheduler instances, each scheduled occurrence executes the job
  body **once** and produces **one** run row (integration test).
- [ ] Run rows and job state are written only to the **primary** (`default_dsn`),
  never to `asyncpg_url` / `PG_HOST`.
- [ ] Pausing via PATCH stops the job in **every** instance at its next fire and
  **after restart**; resume reverses both.
- [ ] `POST …/jobs/{job_id}/run` returns `202 {run_id}`, records `trigger=manual`
  with `requested_by`, and returns `409` while a manual run of that job is
  `running`.
- [ ] A scheduled fire is **not** skipped because a manual run is in progress.
- [ ] One failure alert is sent per failed occurrence, not one per worker. The
  provider comes from `QS_SCHEDULER_NOTIFY` (default `telegram`) and can be
  overridden per job with `attributes.scheduler.notify`.
- [ ] **Fail-open:** if the ledger is unreachable at fire time, the job still runs;
  an ERROR log and an alert are emitted.
- [ ] If the tables are not deployed, the scheduler behaves exactly as before
  (no history, no guard) and logs one WARNING at startup.
- [ ] Runs orphaned by a dead worker are marked `abandoned`: at startup for the
  same host, and on read after `QS_SCHEDULER_RUN_TIMEOUT` for any host.
- [ ] `missed` and max-instances occurrences are recorded once each.
- [ ] Interval schedules without `start_date` are anchored to
  `QS_SCHEDULER_INTERVAL_ANCHOR`, so slots are identical across workers.
- [ ] Multi runs store `ExecuteSQL` status tags and `Table` row counts in
  `details`.
- [ ] `GET /api/v1/qs/scheduler/jobs` returns durable `paused` + `last_run`;
  the history endpoints match the §2 contract.
- [ ] Run rows are never purged automatically (no retention job exists).
- [ ] FEAT-176 behavior is preserved: the fire-time existence guard still removes
  stale jobs, and the move's transient pause/rollback still works
  (`tests/tenants/test_query_move.py` green).
- [ ] `docs/sql/qs_scheduler_runs.sql` creates both tables in `troc` and is
  documented in `docs/QSSCHEDULER.md`.
- [ ] `ruff check` clean; all unit and integration tests in §4 pass; existing
  scheduler suites stay green.

---

## 6. Codebase Contract

> Verified against `c98ea72` (dev, rebased onto `origin/dev` `a242603`, which
> includes FEAT-176's simple query move).

### Verified Imports
```python
from querysource.conf import default_dsn, ENABLE_QS_SCHEDULER, QS_SCHEDULER_TIMEZONE, QS_SCHEDULER_MAX_INSTANCES, QS_SCHEDULER_COALESCE  # conf.py:32,388-391
from querysource.scheduler.notifications import NotificationManager   # notifications.py:21
from querysource.scheduler.jobs import in_flight                       # jobs.py:43 (used by handlers/query_moves.py:19)
from querysource.interfaces.guarded_sql import execute_guarded         # guarded_sql.py:71 — primary-DB connection pattern
from querysource.repositories import DefinitionRepository              # scheduler.py:40
from querysource.tenants import QueryStore, TenantOwnerEnvelope        # scheduler.py:49-55
from apscheduler.events import EVENT_JOB_MISSED, EVENT_JOB_MAX_INSTANCES, JobExecutionEvent  # apscheduler 3.11.2; JobExecutionEvent(code, job_id, jobstore, scheduled_run_time, retval=None, exception=None, traceback=None)
from apscheduler.triggers.base import BaseTrigger                      # scheduler.py:30
from apscheduler.triggers.interval import IntervalTrigger              # scheduler.py:32
from asyncdb import AsyncDB                                            # guarded_sql.py (AsyncDB("pg", dsn=default_dsn))
from notify import Notify                                              # async-notify 2.0.0 (uv.lock:659-660); Notify(provider: str, *args, **kwargs)
from notify.models import Actor, Chat                                  # async-notify 2.0.0 — field names: verify at task time (flowtask uses 1.6.1: Chat(chat_id, chat_name), Actor(name, account))
```

### Existing Class Signatures
```python
# querysource/scheduler/jobs.py
_IN_FLIGHT: dict[tuple, int] = {}                                        # line 33
def in_flight(store: Any, slug: str) -> bool                             # line 43
@contextmanager
def _running(owner: TenantOwnerEnvelope | None, slug: str)               # line 55-56
async def _revalidate_owner(query_obj, owner, slug) -> None              # line 70
async def scheduled_query_job(slug, notification_manager=None, *, owner=None, job_id=None,
                              guard: Callable[[str, str | None], Awaitable[bool]] | None = None, **kwargs) -> None   # line 105; guard check line 131; `with _running` 136; notify 147
async def scheduled_multiqs_job(slug, notification_manager=None, *, owner=None, job_id=None, guard=None,
                                run_as_user_id=None, identity_auth=None, **kwargs) -> None   # line 154; guard 198; _running 203; notify 223
async def cache_refresh_job(slug, notification_manager=None, *, owner=None, job_id=None, guard=None, **kwargs) -> None   # line 230; guard 259; _running 264; notify 275

# querysource/scheduler/scheduler.py
class QSScheduler:                                                       # line 128
    def __init__(self, loop=None)                                        # line 135; _notification_manager line 140; _repository line 153
    def _create_scheduler(self) -> AsyncIOScheduler                      # line 155 (MemoryJobStore, AsyncIOExecutor)
    def _parse_trigger(self, schedule_type: str, schedule: dict) -> BaseTrigger | None   # line 205; interval branch 220-221
    def _register_query_row(...)                                         # line 282; guard bound at 361 (multi) and 384 (query)
    def _register_cache_row(...)                                         # line 405; guard bound at 451
    def _slug_job_ids(self, slug, *, store=None) -> list                 # line 473
    def remove_job(self, job_id: str) -> bool                            # line 584
    def set_job_paused(self, job_id: str, paused: bool) -> bool          # line 596 (in-memory only)
    def pause_slug_jobs(self, slug: str, store: QueryStore) -> list      # line 612 (FEAT-176, transient)
    def remove_slug_jobs(self, slug: str, store: QueryStore) -> list     # line 626
    async def definition_present(self, store: QueryStore, slug: str, job_id: str | None) -> bool   # line 630 (never blocks on error)
    async def register_slug(self, slug: str, *, tenant: str | None = None) -> dict   # line 657
    def setup(self, app) -> None                                         # line 721
    async def startup(self, app) -> None                                 # line 730; _repository from app 755; schedulable() 794; start() 829; app["qs_scheduler"] 834
    async def shutdown(self, app) -> None                                # line 836
    def add_notification_callback(self, callback: Callable) -> None      # line 851

# querysource/scheduler/notifications.py
class NotificationManager:                                               # line 21
    def notify(self, job_id: str, slug: str, error: Exception) -> None   # line 37 (sync; callbacks(job_id=, slug=, error=))

# querysource/handlers/scheduler.py
class SchedulerJobsView(BaseView):                                       # line 74
    def _get_scheduler(self) -> QSScheduler | None                       # line 88
    async def _enforce_scheduler_grant(self, slug: str, tenant: str | None) -> None   # line 98 (enforce_request_pbac at 149)
    def _serialize_job(self, job: Job) -> dict                           # line 157
    async def get(self) -> web.Response                                  # line 202
    async def post(self) -> web.Response                                 # line 271 (sync slug)
    async def delete(self) -> web.Response                               # line 343
    async def patch(self) -> web.Response                                # line 374; set_job_paused call line 409

# querysource/repositories/definitions.py
async def exists(self, store: QueryStore, slug: str) -> bool             # line 188
async def schedulable(self, store: QueryStore) -> tuple[Mapping[str, Any], ...]   # line 358

# querysource/queries/multi/destinations/execute_sql.py
class ExecuteSQLDestination(AbstractDestination):  self.results: List[str]   # line 137; set at 150

# querysource/queries/multi/destinations/table.py
class TableDestination(AbstractDestination):                             # line 58; "wrote %d rows" logs at 650 and 666; run() line 680
```

### Integration Points
| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `QSScheduler.admit` | `definition_present` | direct await | `scheduler.py:630` |
| `QSScheduler.admit` | job callables | `guard=partial(self.admit, store)` (replaces 3 bindings) | `scheduler.py:361,384,451` |
| job callables | `tracked_run` | `async with` around the existing `with _running(...)` body | `jobs.py:136,203,264` |
| `AlertCallback` | `NotificationManager` | `add_notification_callback` | `scheduler.py:851` |
| `record_detail` | `ExecuteSQLDestination.run` | call after results | `execute_sql.py:150` |
| `record_detail` | `TableDestination` | call at both write logs | `table.py:650,666` |
| `SchedulerRunsView` / `SchedulerRunNowView` | app router | `add_view` in scheduler block | `services.py:458-466` |
| `set_job_paused_durable` | PATCH | replaces `set_job_paused` call | `handlers/scheduler.py:409` |

### Configuration References (new, `querysource/conf.py` after line 391)
| Key | Default | Purpose |
|---|---|---|
| `QS_SCHEDULER_HISTORY_SCHEMA` | `troc` | Schema of the two tables |
| `QS_SCHEDULER_RUN_TIMEOUT` | `21600` (s) | `running` older than this → `abandoned` on read |
| `QS_SCHEDULER_SLOT_LOOKBACK` | `86400` (s) | `scheduled_slot` search window |
| `QS_SCHEDULER_INTERVAL_ANCHOR` | `2000-01-01T00:00:00` | Default `start_date` for interval triggers (scheduler TZ) |
| `QS_SCHEDULER_ERROR_MAX` | `4000` | Max chars stored in `error` |
| `QS_SCHEDULER_NOTIFY` | `telegram` | `telegram` \| `email` \| `slack` \| `none` |
| `EVENT_CHAT_BOT` / `EVENT_CHAT_ID` | — | Telegram bot/chat (same keys as flowtask `conf.py:313-314`) |
| `QS_SCHEDULER_ALERT_URL` | — | Optional Concierge link in alerts |

### Does NOT Exist (Anti-Hallucination)
- ~~Any run/execution table or model in `querysource/`~~: the first ones are created here.
- ~~Persisted pause state~~: `set_job_paused` and `pause_slug_jobs` are in-memory only.
- ~~`scheduled_run_time` passed to job callables~~: APScheduler 3.11 exposes it only on events.
- ~~`add_listener` / `EVENT_JOB_*` usage in `querysource/`~~: not used anywhere today.
- ~~A "run now" endpoint~~: the only POST is slug sync (`handlers/scheduler.py:271`).
- ~~`from notify` usage in `querysource/`~~: async-notify is declared (`pyproject.toml:71`) but unused.
- ~~`EVENT_CHAT_BOT` / `EVENT_CHAT_ID` / `DEFAULT_RECIPIENT` in `querysource/conf.py`~~: they exist only in flowtask's conf.
- ~~Startup DDL / auto-migrations~~: DDL is applied manually from `docs/sql/`.
- ~~`querysource/scheduler/handoff.py`~~: the lead rejected that FEAT-176 design; it is not on dev.
- ~~`QSScheduler.admit`, `run_now`, `set_job_paused_durable`, `RunLedger`, `RunTicket`, `tracked_run`, `record_detail`, `scheduled_slot`, `AlertCallback`, `SchedulerRunsView`, `SchedulerRunNowView`~~: introduced by this spec.

### Edit Sites (Blueprint Anchors)

Verified against: `c98ea72`

| File | Action | Verbatim anchor line | Verified at | Occurrences |
|---|---|---|---|---|
| `docs/sql/qs_scheduler_runs.sql` | CREATE | — | — | — |
| `querysource/scheduler/runs.py` | CREATE | — | — | — |
| `querysource/scheduler/scheduler.py` | MODIFY | `                return IntervalTrigger(**schedule)` | `scheduler.py:221` | 1 |
| `querysource/scheduler/scheduler.py` | MODIFY | `"guard": partial(self.definition_present, store),` | `scheduler.py:361,384,451` | 3 (replace all three) |
| `querysource/scheduler/scheduler.py` | MODIFY | `    async def definition_present(self, store: QueryStore, slug: str, job_id: str \| None) -> bool:` | `scheduler.py:630` | 1 (insert `admit`, `set_job_paused_durable`, `run_now` after it) |
| `querysource/scheduler/scheduler.py` | MODIFY | `        self._scheduler.start()` | `scheduler.py:829` | 1 (ledger init, pause reapply, abandoned, listeners before it) |
| `querysource/scheduler/jobs.py` | MODIFY | `    if guard is not None and not await guard(slug, job_id):` | `jobs.py:131,198,259` | 3 (one per callable) |
| `querysource/scheduler/notifications.py` | MODIFY | `class NotificationManager:` | `notifications.py:21` | 1 (add `AlertCallback` before it) |
| `querysource/handlers/scheduler.py` | MODIFY | `        if not scheduler.set_job_paused(job_id, action == "pause"):` | `handlers/scheduler.py:409` | 1 |
| `querysource/handlers/scheduler.py` | MODIFY | `    def _serialize_job(self, job: Job) -> dict:` | `handlers/scheduler.py:157` | 1 |
| `querysource/services.py` | MODIFY | `'/api/v1/qs/scheduler/jobs/{job_id}', SchedulerJobsView` | `services.py:464` | 1 |
| `querysource/conf.py` | MODIFY | `QS_SCHEDULER_COALESCE = config.getboolean('QS_SCHEDULER_COALESCE', fallback=True)` | `conf.py:391` | 1 |
| `querysource/queries/multi/destinations/execute_sql.py` | MODIFY | `            self.results = await execute_guarded(statements, timeout=self._timeout)` | `execute_sql.py:150` | 1 |
| `querysource/queries/multi/destinations/table.py` | MODIFY | `"TableDestination: wrote %d rows → %s.%s",` (context: the `self.logger.info(` above it at 649 and 665) | `table.py:650,666` | 2 (hook both) |
| `docs/QSSCHEDULER.md` | MODIFY | (append section) | — | — |
| `tests/scheduler/test_run_ledger.py` | CREATE | — | — | — |
| `tests/scheduler/test_scheduled_slot.py` | CREATE | — | — | — |
| `tests/scheduler/test_admission_and_tracking.py` | CREATE | — | — | — |
| `tests/scheduler/test_alerts.py` | CREATE | — | — | — |
| `tests/handlers/test_scheduler_runs_api.py` | CREATE | — | — | — |
| `tests/integration/test_scheduler_single_execution_postgres.py` | CREATE | — | — | — |

---

## 7. Implementation Notes & Constraints

### Patterns to Follow
- **Primary-DB connection.** Use `AsyncDB("pg", dsn=default_dsn)` with a
  short-lived connection per operation, as `execute_guarded` does
  (`guarded_sql.py:71`). Use parameterized queries only; never `str.format`, which
  is the flowtask `update_task_status` anti-pattern.
- **Guard composition.** Extend FEAT-176's guard, don't add a parallel gate.
  `admit` calls `definition_present` first, so a missing slug is still removed
  from the worker.
- **Async listeners.** APScheduler listeners are sync. Hand work off with
  `asyncio.get_running_loop().create_task(...)`; never `new_event_loop()` + thread
  pool (the flowtask `save_db_event` anti-pattern).
- **Run identity.** The run id is generated at claim (`run_id` PK) and passed
  down. It is never stored on the job definition (flowtask NAV-6365 lesson).
- **Notify contract.** Keep `notify(job_id, slug, error)` sync and its call sites
  in `jobs.py` unchanged. `tracked_run` re-raises so that path still fires once.
- **Repo conventions.** `self.logger` / module `logger`, never `print`; Google
  docstrings; strict type hints; config via navconfig in `conf.py`.

### Known Risks / Gotchas
- **Slot derivation correctness.** A wrong `scheduled_for` either defeats dedup
  (different slots per worker) or wrongly skips (collides with a previous slot).
  Mitigation: latest fire ≤ now; property tests over all 8 types; the interval
  anchor.
- **Interval triggers anchored per worker.** Without the anchor, each worker's
  `IntervalTrigger` anchors at its own registration time. The anchor is
  mandatory, and adding it changes the phase of existing interval jobs: document
  it in `docs/QSSCHEDULER.md`.
- **Fail-open duplicates.** During a primary outage, up to N runs per occurrence
  can happen. This was accepted (2026-10-20, flowtask precedent). The targets of
  the known write multis are idempotent (`DELETE`+`INSERT` under `LOCK TABLE`).
- **FEAT-176 transient pause vs durable pause.**
  - The move's `pause_slug_jobs` / rollback `set_job_paused(…, False)` stay
    in-memory and must not write `job_state`; otherwise a rollback could resume a
    job a user paused durably.
  - `admit` re-checks the durable state at every fire, so a durably paused job
    remains paused after a move. `register_slug` re-applies it on the destination.
  - Job ids differ per store (`qsj2-…`), so a durable pause on the origin id does
    not follow the moved slug automatically. The spec accepts this;
    re-pausing on the destination is a frontend concern, documented.
- **Manual run executes in the serving worker.** A long manual run ties up that
  worker's event loop no more than a scheduled run does today. Shutdown cancels
  and finalizes it as `failed` ("worker shutdown").
- **Clock/timezone.** Use tz-aware datetimes everywhere (`timestamptz`). Display
  is the frontend's concern. The incident's `inserted_at` confusion came from a
  naive `timestamp`.
- **Tables missing.** The ledger is disabled at startup. History endpoints return
  `503 history_unavailable` and the frontend shows a hint.
- **Row volume without purge.** Roughly jobs × runs/day. Indexes cover reads.
  Revisit if it grows (no retention by decision).

### External Dependencies
| Package | Version | Reason |
|---|---|---|
| `apscheduler` | 3.11.2 (installed) | triggers, listeners, `scheduled_run_time` on events |
| `asyncdb` / `asyncpg` | existing | primary-DB writes |
| `async-notify` | 2.0.0 (`uv.lock`), already in `pyproject.toml:71` | alerts; no new dependency |

---

## Worktree Strategy

- **Isolation:** one feature worktree, `feat-FEAT-181-scheduler-execution-history`;
  the `sdd-coder` engine gives each task a sub-worktree.
- **Module dependency graph:**
  - M2 → M1 (its SQL targets M1's tables).
  - M4 → M2 (`RunLedger`, `RunTicket`, `tracked_run`) and M4 → M3
    (`scheduled_slot`).
  - M5 → M2 and M5 → M4 (`admit` / `_ledger`).
  - M6 → M2 and M6 → M4 (pre-claimed ticket into the job callables).
  - M7 has no edge; M4/M5 only register it.
  - M8 → M2 (`record_detail`).
  - M9 → M2, M5, M6.
  - M10 → all.
  - Concurrent once M2 lands: M3, M7, M8.
- **Shared files:**
  - `querysource/scheduler/scheduler.py` (M3, M4, M5, M6) and
    `querysource/handlers/scheduler.py` (M5, M9). Their tasks are serialized.
  - `querysource/scheduler/runs.py` (M2, M3, M4): M3/M4 append helpers after M2.
- **Exclusive resources:** none. There is no lockfile, extension or migration
  change, and the DDL is a docs file.
- **Cross-feature dependencies:** **FEAT-176** (query-store-management-and-migration
  simple move) is merged on `origin/dev` at `a242603`, and this spec builds on its
  `guard` / `definition_present` / `pause_slug_jobs`. The frontend counterpart
  lands after `navigator-frontend-next` FEAT-252.

---

## 8. Open Questions

- [x] Which jobs are recorded? — *Resolved in brainstorm*: all kinds (query, multi, cache-refresh).
- [x] N-worker duplicate execution in scope? — *Resolved in brainstorm*: yes, fix it in this feature.
- [x] UI scope? — *Resolved in brainstorm*: per-job history, last-run column with failure badge, slug-inspector history, full error + ExecuteSQL details. The UI is delivered by the frontend spec.
- [x] Retention? — *Resolved in brainstorm*: no automatic purge.
- [x] Table location? — *Resolved in brainstorm*: one central table in the primary DB with tenant/store columns.
- [x] Orphaned `running` rows? — *Resolved in brainstorm*: mark as `abandoned`.
- [x] Permissions? — *Resolved in brainstorm*: same as the current scheduler monitor (plus FEAT-160 grant for run-now on write multis).
- [x] Extras? — *Resolved in brainstorm*: include "run now" button and failure alerts; durable pause/resume (pausing currently does nothing and jobs re-activate on their own).
- [x] Claim policy when the primary is unreachable at fire time? — *Resolved in brainstorm*: fail-open, like flowtask (2026-10-20). Run the job anyway, log loudly and send an alert; bookkeeping never blocks execution.
- [x] Alert channel and recipients? — *Resolved in brainstorm*: async-notify with configurable provider (`QS_SCHEDULER_NOTIFY`, default `telegram`), reusing flowtask's `EVENT_CHAT_BOT`/`EVENT_CHAT_ID`/`DEFAULT_RECIPIENT` keys, plus per-job override `attributes.scheduler.notify`.
- [x] Schema for the central tables? — *Resolved in brainstorm*: `troc` (`troc.qs_scheduler_runs`, `troc.qs_scheduler_job_state`), next to flowtask's `troc.jobs` in the same prod primary.
- [x] Interval triggers slot alignment? — *Resolved in brainstorm*: deterministic `start_date` anchor (flowtask precedent); default anchor when missing.
- [x] Skip a scheduled fire while a manual run is `running`? — *Resolved in brainstorm*: no; only a second manual run gets `409`; APScheduler max-instances rejections are recorded as `skipped`.
- [x] Frontend tracked here or in the frontend repo? — *Resolved in brainstorm*: separate spec in `navigator-frontend-next` consuming this API contract (TASK-457 pattern).
- [x] `navigator-api` QS pin drift a prerequisite? — *Resolved in brainstorm*: no, not blocking; handled separately.
- [x] Coordination with FEAT-176? — *Resolved in brainstorm*: FEAT-176 (backend) and FEAT-252 (frontend) land first; this feature rebases onto them. FEAT-176's simple move is already merged on `origin/dev` (`a242603`), and this spec builds on its `guard`.
- [ ] Verify at task time that navigator-api's vault exposes `EVENT_CHAT_BOT` / `EVENT_CHAT_ID` for QS (only flowtask reads them today). — *Owner: Juan Rodriguez*
- [ ] Should a durable pause follow a slug when FEAT-176 moves it to another store (job id changes)? This spec keeps them separate (see §7); revisit if operators need it. — *Owner: Juan Rodriguez*

---

## 9. Design Research Cross-Check

> Model: — · Status: skipped (exploration doc `**Status**: exploration`, not `accepted`) · Transcript: —

| # | Suggestion (kind) | Disposition | Reason | Landed in |
|---|---|---|---|---|

Summary: **0** confirmed · **0** rejected · **0** escalated.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-10-20 | Juan Rodriguez (with Claude) | Initial draft from brainstorm Option A, rebased on FEAT-176 simple move |
