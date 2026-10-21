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
**Status**: approved
**Approved by**: Juan Rodriguez, 2026-10-20 (DocumentDB history + fire-and-forget confirmed with Jesus Lara)
**Target version**: next minor release (no version bump prescribed)
**Brainstorm**: `sdd/proposals/scheduler-execution-history.brainstorm.md` (Option A). This
revision supersedes the brainstorm's Postgres storage after the lead's review (§8).
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

- Record every scheduler execution of every job kind (`query`, `multi`, `cache`)
  and every on-demand run in a **document store with a native TTL** (Amazon
  DocumentDB). Retention is **90 days** and is enforced by the store; there is no
  purge process.
- Recording is **fire-and-forget**: it never delays, blocks or fails a job.
- Run **each scheduled occurrence exactly once** across workers and hosts. The
  mechanism is a unique claim document; it is not leader election and not a
  deployment change.
- **Durable pause/resume**, stored on the slug definition itself
  (`attributes.scheduler.paused`), honored by every worker at fire time and after
  restarts.
- **Run now**: execute a job on demand, recorded as a `manual` run.
- **One failure alert per failed occurrence** through a configurable async-notify
  channel; the default is Telegram.
- Show orphaned runs as `abandoned`.
- A read API for run history and last-run status, which the Concierge frontend
  spec consumes.

### Non-Goals (explicitly out of scope)

- **Relational storage for run history.** Lead decision (2026-10-20): Postgres is
  not a log database and has no retention policy. No new Postgres tables, no
  Postgres purge job.
- **Concierge UI:** separate spec in `navigator-frontend-next`.
- **Leader election / a single-worker scheduler** (brainstorm Options B/C). Flowtask's
  Redis leader lock was disabled for "design flaws".
- **Replacing `MemoryJobStore`** with a shared APScheduler jobstore (Option D):
  unsupported for multiple schedulers in APScheduler 3.x.
- **Recording ad-hoc slug or multi runs from the regular query API.**
- **Changing FEAT-176's transient move pause** (`pause_slug_jobs`); see §7.

---

## 2. Architectural Design

### Overview

**Storage.** Run history lives in one DocumentDB collection, `qs_scheduler_runs`, in
`QS_SCHEDULER_HISTORY_DATABASE` (default `DOCUMENTDB_DATABASE`). One document is one
run. It has three indexes, created idempotently at scheduler startup:

- `_id`: the natural unique key. For scheduled runs it is the occurrence id
  `"<job_id>|<scheduled_for ISO-8601 UTC>"`; for manual runs it is a UUID4.
- A **TTL index** on `created_at` with
  `expireAfterSeconds = QS_SCHEDULER_HISTORY_TTL_DAYS × 86400` (default 90 days).
- `{job_id: 1, started_at: -1}` and `{slug: 1, started_at: -1}` for reads.

Nothing about history is stored in Postgres.

**Fire-and-forget.** Every history write except the claim goes through
`HistoryWriter`:

- a bounded `asyncio.Queue` per worker, drained by one background task over its own
  DocumentDB connection;
- callers use `put_nowait`; when the queue is full, the event is dropped, a counter
  is incremented and a rate-limited WARNING is logged;
- writer errors are logged and never propagate;
- on shutdown the queue is drained for at most `QS_SCHEDULER_HISTORY_DRAIN_TIMEOUT`.

So `finish`, `missed`/`skipped` and `abandoned` updates never touch the job's code
path.

**The claim: the only awaited write.** It is coordination, not logging. At fire
time, in every worker, the job's existing FEAT-176 `guard(slug, job_id)` is now
bound to `QSScheduler.admit`, which does:

1. **Definition and pause:** one read through the definition repository returns
   whether the slug exists (FEAT-176 `definition_present` semantics: a missing slug
   is skipped and its job removed) and whether `attributes.scheduler.paused` is
   true. If paused, the job is paused locally and skipped.
2. **Slot:** `scheduled_for` is the latest fire time of the job's trigger that is
   ≤ now, not wall-clock truncation.
3. **Claim:** `insert_one` of the run document with the occurrence `_id`, status
   `running`, bounded by `QS_SCHEDULER_CLAIM_TIMEOUT` (default 2 s).
   - Inserted: this worker runs the occurrence, and `admit` returns a `RunTicket`.
   - `DuplicateKeyError`: another worker owns it, so skip.
   - Any other error or timeout: **fail-open**. Log at ERROR, schedule an alert
     and run untracked (`True`). The job is never blocked by the history store.

The job body runs unchanged inside `tracked_run(ticket)`. It binds a per-run
details collector (`ContextVar`) and on exit **enqueues** the finish update
(`success|failed`, `finished_at`, `duration_ms`, `error`, `details`) without
awaiting it. The existing `notification_manager.notify` call stays where it is.
Only the claimer reaches it, so the new async-notify callback sends one alert per
failure.

If the history store is not configured, or `motor`/`pymongo` are not importable,
history is disabled at startup with one WARNING. The scheduler then behaves
exactly as today: no claim, no history, and the durable pause still works because
it lives on the definition.

**Durable pause.**
- `PATCH /jobs/{id}` writes `attributes.scheduler.paused` (+ `paused_by`,
  `paused_at`, `pause_reason`) atomically with `jsonb_set` on the slug's row in its
  store, via a new `DefinitionRepository.set_schedule_paused`, and applies the
  local pause.
- Other workers honor it at their next fire (admit, step 1).
- `startup` and `register_slug` register paused jobs in paused state.
- `DefinitionRepository.patch` preserves stored `attributes.scheduler.paused*` keys
  when an incoming `attributes` omits them, so an unrelated slug edit cannot
  silently resume a job.

**Run now.** `POST /api/v1/qs/scheduler/jobs/{job_id}/run`:
1. Looks up the job in the serving worker.
2. Enforces the scheduler view's access, plus the FEAT-160 grant for
   write-capable multis.
3. Returns `409` if a `manual` run of the job is still `running` (a bounded read).
4. Otherwise inserts a `manual` run document (awaited, the same claim path) and
   runs the job callable with that pre-claimed ticket via `asyncio.create_task`.
   It returns `202 {run_id}`.

**Missed / max-instances.** APScheduler listeners for `EVENT_JOB_MISSED` and
`EVENT_JOB_MAX_INSTANCES` enqueue a `missed`/`skipped` document (fire-and-forget)
keyed by the occurrence `_id`. The writer uses insert-if-absent, so duplicates from
several workers collapse. This follows flowtask's `add_listeners`.

**Abandoned.**
- Reads report `running` documents whose `started_at` is older than
  `QS_SCHEDULER_RUN_TIMEOUT` as `abandoned`, computed at read time, with no write.
- At startup, each worker enqueues an update marking its own host's `running`
  documents with dead pids as `abandoned`.

**Interval triggers.** Interval schedules without `start_date` get a fixed anchor
(`QS_SCHEDULER_INTERVAL_ANCHOR`), so every worker computes the same slots. This is
the flowtask `start_date` precedent.

### Component Diagram

```
APScheduler trigger (every worker)
        │
        ▼
job callable (jobs.py) ── guard(slug, job_id) ──► QSScheduler.admit(store, …)
        │                                          ├─ repo.schedule_state(store, slug) ─► Postgres (existing queries row: exists + attributes.scheduler.paused)
        │                                          ├─ scheduled_slot(trigger, now)
        │                                          └─ RunHistory.claim (awaited, ≤2 s, fail-open) ─► DocumentDB qs_scheduler_runs (_id = occurrence)
        │   RunTicket | True | False
        ▼
tracked_run(ticket) ── binds run_details ContextVar
        │      ├─ QS / MultiQS / cache body (unchanged)
        │      │     └─ ExecuteSQLDestination / TableDestination ─► record_detail(...)
        │      └─ on exit: HistoryWriter.put_nowait(finish) ──(background task)──► DocumentDB
        ▼
notification_manager.notify ─► logging_callback + AlertCallback (async-notify, scheduled task)

APScheduler listeners (MISSED / MAX_INSTANCES) ─► HistoryWriter.put_nowait(insert-if-absent)
HTTP: SchedulerJobsView (GET: paused from definition + last_run from DocumentDB; PATCH durable)
      SchedulerRunsView  (GET /runs, /runs/{run_id}, /jobs/{job_id}/runs)  — bounded reads
      SchedulerRunNowView (POST /jobs/{job_id}/run) ─► QSScheduler.run_now
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `QSScheduler` (`scheduler/scheduler.py:128`) | modifies | `admit`, `run_now`, `set_job_paused_durable`; `guard` binding switches from `definition_present` to `admit`; startup builds `RunHistory`/`HistoryWriter`, registers paused jobs paused, adds listeners; `_parse_trigger` interval anchor; shutdown drains the writer |
| `definition_present` (`scheduler.py:630`) | superseded | Kept for compatibility; `admit` reproduces its contract (skip + remove on missing, never block on a read error) |
| `DefinitionRepository` (`repositories/definitions.py`) | extends | New `schedule_state(store, slug)`, `set_schedule_paused(...)`; `patch` (`:518`) preserves `attributes.scheduler.paused*` |
| Job callables (`jobs.py:105/154/230`) | modifies | Keep the guard result; run the body in `tracked_run` when it is a `RunTicket` |
| `NotificationManager` (`notifications.py:21`) | extends | New `AlertCallback` |
| `SchedulerJobsView` (`handlers/scheduler.py:74`) | modifies | GET overlays `paused` + `last_run`; PATCH durable |
| `ExecuteSQLDestination` (`execute_sql.py:150`), `TableDestination` (`table.py:650,666`) | modifies | `record_detail(...)` |
| Route setup (`services.py:453-466`) | modifies | New views inside the `ENABLE_QS_SCHEDULER` block |
| `conf.py` (`:251-263` DocumentDB, `:388-391` scheduler) | extends | New `QS_SCHEDULER_*` keys (§6) |
| `pyproject.toml` | modifies | New optional extra `scheduler-history = ["asyncdb[mongodb]"]` (pymongo 4.12.0, motor 3.7.0, already installed on prod navigator-api) |

### Data Models

DocumentDB document, collection `qs_scheduler_runs`:

```python
{
  "_id": "multi_assembly_employee_detail_profile_insert|2026-10-20T10:00:00+00:00",  # or uuid4 hex for manual
  "job_id": str, "slug": str, "kind": "query" | "multi" | "cache",
  "store": {"schema": str | None, "table": str | None},
  "trigger": "scheduled" | "manual", "requested_by": str | None,
  "scheduled_for": datetime,           # tz-aware UTC
  "started_at": datetime | None, "finished_at": datetime | None, "duration_ms": int | None,
  "status": "running" | "success" | "failed" | "missed" | "skipped" | "abandoned",
  "error": str | None,                 # "<ExcType>: <message>", truncated to QS_SCHEDULER_ERROR_MAX
  "details": dict,                     # e.g. {"execute_sql": ["LOCK TABLE","DELETE 55229","INSERT 0 55439"], "table": [...]}
  "host": str, "pid": int,
  "created_at": datetime,              # TTL anchor (expireAfterSeconds = TTL days × 86400)
}
# indexes: TTL {created_at: 1}; {job_id: 1, started_at: -1}; {slug: 1, started_at: -1}
```

Slug definition (existing jsonb `attributes`, verified `jsonb` in prod `public.queries`
and `troc.queries`):

```json
{"scheduler": {"schedule_type": "daily", "schedule": {...},
               "paused": true, "paused_by": "jfrruffato", "paused_at": "2026-10-20T23:10:00Z",
               "pause_reason": "optional", "notify": "telegram"}}
```

```python
# querysource/scheduler/history.py
RunStatus = Literal["running", "success", "failed", "missed", "skipped", "abandoned"]
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
| `GET /api/v1/qs/scheduler/jobs` (modified) | each job gains `paused` (from the definition) and `last_run: {run_id, status, started_at, finished_at, duration_ms} \| null`; the envelope gains `history_available: bool` |
| `GET /api/v1/qs/scheduler/jobs/{job_id}/runs?status=&since=&until=&limit=50&offset=0` | `{runs, total, limit, offset}`, newest first |
| `GET /api/v1/qs/scheduler/runs?slug=&job_id=&status=&since=&until=&limit=&offset=` | same envelope, across jobs |
| `GET /api/v1/qs/scheduler/runs/{run_id}` | one run with full `error` + `details`; `404` |
| `POST /api/v1/qs/scheduler/jobs/{job_id}/run` | `202 {run_id, status: "running"}`; `404`; `409 {error_code: "run_in_progress", run_id}`; `503 {error_code: "history_unavailable"}` |
| `PATCH /api/v1/qs/scheduler/jobs/{job_id}` (modified) | same body/response; persisted on the definition; `404` if the slug no longer exists |

`limit` is capped at 200. History reads are bounded by `QS_SCHEDULER_HISTORY_READ_TIMEOUT`;
on timeout or when history is disabled they return `503 {"error_code": "history_unavailable"}`.
In that case `GET /jobs` still answers, with `last_run: null` and `history_available: false`.

---

## 3. Module Breakdown

#### Delegation-eligible modules

| Module | Eligible? | Decided patterns / exact contracts | Why not (if no) |
|---|---|---|---|
| M1: History store + writer | no | — | Queue/backpressure/shutdown semantics and DocumentDB TTL behaviour need judgment |
| M2: Slot derivation + interval anchor | no | — | Correctness across 8 schedule types needs reasoning + property tests |
| M3: Admission + tracked jobs | no | — | Composes the FEAT-176 guard, the claim and fail-open |
| M4: Durable pause on the definition | no | — | Atomic `jsonb_set`, `patch` preservation, interaction with FEAT-176 move |
| M5: Startup, shutdown, listeners, abandoned | no | — | Lifecycle ordering with FEAT-176 startup |
| M6: Run now | yes | Contract in §2 + skeleton | — |
| M7: Alerts | yes | async-notify; config §6; message below | — |
| M8: Details collector hooks | yes | 3 `record_detail` calls | — |
| M9: HTTP API | yes | Contract in §2; `SchedulerJobsView` conventions | — |
| M10: Packaging, docs, race test | no | — | Multi-process race test against a disposable MongoDB |

### Module 1: History store + fire-and-forget writer
- **Path**: `querysource/scheduler/history.py` (new)
- **Responsibility**: DocumentDB access for `qs_scheduler_runs`: index bootstrap,
  the awaited `claim`, bounded reads, and the `HistoryWriter` queue for every other
  write. Connection params are built like `interfaces/databases/mongo.py:DocumentDB`
  (`dbtype="documentdb"`, `ssl`, `tlsCAFile`) and opened with `AsyncDB("mongo", params=…)`
  (pattern `multi/destinations/dwh.py:244-246`). `motor`/`pymongo` are imported
  lazily; on `ImportError`, history is disabled.
- **Depends on**: none
- **Interface Skeleton**:
  ```python
  # querysource/scheduler/history.py  (new)
  class RunHistory:
      """DocumentDB-backed run history (TTL-retained). Only `claim` is awaited by jobs."""

      def __init__(self, params: Mapping[str, Any], *, database: str, collection: str = "qs_scheduler_runs",
                   ttl_days: int = 90, claim_timeout: float = 2.0, read_timeout: float = 3.0) -> None: ...

      @classmethod
      def from_conf(cls) -> "RunHistory | None":
          """Build from QS_SCHEDULER_HISTORY_* / DOCUMENTDB_* config; None when disabled or motor/pymongo missing."""

      async def ensure_indexes(self) -> bool:
          """Create TTL + read indexes idempotently; False (and disables history) on failure. Never raises."""

      async def claim(self, *, occurrence_id: str, job_id: str, slug: str, kind: str,
                      owner: Mapping[str, Any] | None, scheduled_for: datetime,
                      trigger: RunTriggerType = "scheduled", requested_by: str | None = None) -> RunTicket | None:
          """insert_one with _id=occurrence_id, bounded by claim_timeout.
          Returns None on DuplicateKeyError (another worker owns it). Raises on any other error/timeout
          (caller applies fail-open)."""

      async def manual_running(self, job_id: str) -> str | None: ...
      async def last_runs(self, job_ids: Sequence[str]) -> dict[str, dict[str, Any]]: ...
      async def list_runs(self, *, job_id: str | None = None, slug: str | None = None, status: str | None = None,
                          since: datetime | None = None, until: datetime | None = None,
                          limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]: ...
      async def get_run(self, run_id: str) -> dict[str, Any] | None: ...
      # reads are bounded by read_timeout; they map `running` older than QS_SCHEDULER_RUN_TIMEOUT to `abandoned`.

  class HistoryWriter:
      """Per-worker bounded queue + one background drain task. Fire-and-forget: callers never await a write."""
      def __init__(self, history: RunHistory, *, maxsize: int = 1000) -> None: ...
      def start(self) -> None: ...
      def finish(self, ticket: RunTicket, *, status: RunStatus, error: str | None, details: Mapping[str, Any]) -> None:
          """Enqueue the terminal update; drops + counts when full; never raises."""
      def skipped(self, *, occurrence_id: str, job_id: str, slug: str, kind: str, owner: Mapping[str, Any] | None,
                  scheduled_for: datetime, status: Literal["missed", "skipped"]) -> None:
          """Enqueue insert-if-absent; never raises."""
      def abandon_local(self, host: str, dead_pids: Iterable[int]) -> None: ...
      async def drain(self, timeout: float) -> None:
          """Flush pending writes on shutdown, bounded by timeout; never raises."""
      dropped: int

  run_details: ContextVar[dict[str, Any] | None]
  def record_detail(key: str, value: Any) -> None:
      """Append value under key in the active run's details; no-op outside a tracked run."""
  ```

### Module 2: Slot derivation + interval anchor
- **Path**: `querysource/scheduler/history.py` (helper), `querysource/scheduler/scheduler.py` (`_parse_trigger`)
- **Responsibility**: `scheduled_slot` returns the latest fire time ≤ `now` for any
  APScheduler trigger. It searches back within `lookback` and walks forward with
  `get_next_fire_time` (bounded). `occurrence_id` formats `job_id|<slot UTC ISO>`.
  `_parse_trigger` adds `start_date = QS_SCHEDULER_INTERVAL_ANCHOR` to interval
  schedules that lack one.
- **Depends on**: none
- **Interface Skeleton**:
  ```python
  def scheduled_slot(trigger: BaseTrigger, now: datetime, *, lookback: timedelta) -> datetime:
      """Latest trigger fire time <= now (tz-aware); identical across workers for the same trigger config."""
  def occurrence_id(job_id: str, slot: datetime) -> str: ...
  # querysource/scheduler/scheduler.py  (modifies _parse_trigger, verified: scheduler.py:220-221)
  ```

### Module 3: Admission + tracked jobs
- **Path**: `querysource/scheduler/scheduler.py`, `querysource/scheduler/jobs.py`, `querysource/scheduler/history.py`
- **Responsibility**: `QSScheduler.admit` runs, in order:
  1. `repo.schedule_state` (missing → skip + `remove_job`; paused → local pause +
     skip; read error → treat as present and not paused, as FEAT-176's guard
     never blocks);
  2. `scheduled_slot`;
  3. `claim`.

  The three `partial(self.definition_present, store)` bindings become
  `partial(self.admit, store)`. The jobs keep the guard result and wrap the
  existing body in `tracked_run(admission, writer)`, which re-raises so the
  existing `except` still logs and notifies once.
- **Depends on**: M1, M2, M4 (`schedule_state`)
- **Interface Skeleton**:
  ```python
  # querysource/scheduler/scheduler.py
  class QSScheduler:  # verified: scheduler.py:128
      _history: RunHistory | None
      _writer: HistoryWriter | None
      async def admit(self, store: QueryStore, slug: str, job_id: str | None) -> RunTicket | bool:
          """False = skip; True = run untracked (history off / fail-open); RunTicket = run tracked."""
  # querysource/scheduler/history.py
  @asynccontextmanager
  async def tracked_run(admission: RunTicket | bool, writer: HistoryWriter | None) -> AsyncIterator[None]:
      """No-op unless admission is a RunTicket; binds run_details; enqueues finish(success|failed); re-raises."""
  # querysource/scheduler/jobs.py  (modifies the 3 callables, verified: jobs.py:131,198,259)
  #   admission = await guard(slug, job_id) if guard is not None else True
  #   if not admission: return
  #   async with tracked_run(admission, writer): <existing body, still inside `with _running(...)`>
  #   writer reaches the job via a new kwarg `writer: HistoryWriter | None = None` bound at registration.
  ```

### Module 4: Durable pause on the definition
- **Path**: `querysource/repositories/definitions.py`, `querysource/scheduler/scheduler.py`, `querysource/handlers/scheduler.py`
- **Responsibility**:
  - `schedule_state` is one cheap `SELECT` returning presence + `paused`; it
    replaces `exists` in the fire path.
  - `set_schedule_paused` is an atomic
    `UPDATE … SET attributes = jsonb_set(…, '{scheduler,paused}', …)` that also
    writes `paused_by` / `paused_at` / `pause_reason` and does not touch other keys.
  - `patch` carries stored `scheduler.paused*` keys forward when the incoming
    `attributes` omits them.
  - `QSScheduler.set_job_paused_durable` resolves the job's store + slug from its
    kwargs (`owner` envelope), calls `set_schedule_paused`, then applies the local
    pause.
  - PATCH calls it.
  - `_register_query_row` / `_register_cache_row` register a paused row's jobs and
    pause them immediately (`set_job_paused`).
- **Depends on**: none
- **Interface Skeleton**:
  ```python
  # querysource/repositories/definitions.py
  async def schedule_state(self, store: QueryStore, slug: str) -> tuple[bool, bool]:
      """(present, paused) for slug in store; replaces exists() on the scheduler fire path."""
  async def set_schedule_paused(self, store: QueryStore, slug: str, *, paused: bool,
                                user: str | None, reason: str | None = None) -> bool:
      """Atomic jsonb_set of attributes.scheduler.paused(+by/at/reason); False when the slug is missing."""
  # querysource/scheduler/scheduler.py
  async def set_job_paused_durable(self, job_id: str, paused: bool, *, user: str | None,
                                   reason: str | None = None) -> bool:
      """Persist on the definition, then pause/resume locally; False when job_id is unknown here."""
  # set_job_paused (verified: scheduler.py:596) stays in-memory — FEAT-176's move uses it transiently.
  ```

### Module 5: Startup, shutdown, listeners, abandoned
- **Path**: `querysource/scheduler/scheduler.py`
- **Responsibility**:
  - Before `self._scheduler.start()`, startup does:
    1. `RunHistory.from_conf()` → `ensure_indexes()`; on failure, history is
       disabled with one WARNING;
    2. start `HistoryWriter`;
    3. `abandon_local(socket.gethostname(), <dead pids>)`;
    4. register listeners for `EVENT_JOB_MISSED | EVENT_JOB_MAX_INSTANCES`, which
       call `writer.skipped(...)`, a sync enqueue, so there is no loop/thread juggling.
  - Shutdown cancels manual-run tasks (enqueued as `failed` "worker shutdown") and
    calls `writer.drain(QS_SCHEDULER_HISTORY_DRAIN_TIMEOUT)`.
- **Depends on**: M1, M3

### Module 6: Run now
- **Path**: `querysource/scheduler/scheduler.py`
- **Responsibility**: `run_now` checks `manual_running` (→ `RunInProgress`) and
  claims a `manual` ticket (`_id` = uuid4). It then runs `job.func(**job.kwargs)`
  with `guard` replaced by a coroutine returning that ticket, as a task held in
  `self._manual_tasks`.
- **Depends on**: M1, M3
- **Interface Skeleton**:
  ```python
  async def run_now(self, job_id: str, *, user: str | None) -> RunTicket:
      """Raises KeyError (unknown job), RunInProgress(run_id), RuntimeError (history unavailable)."""
  class RunInProgress(Exception):
      run_id: str
  ```

### Module 7: Failure alerts
- **Path**: `querysource/scheduler/notifications.py`, `querysource/conf.py`
- **Responsibility**: `AlertCallback(job_id, slug, error)` is sync, matching
  `NotificationManager.notify` (`notifications.py:37`), and schedules an async send
  with `create_task`.
  - Provider: per-job `attributes.scheduler.notify` (a map filled at registration),
    else `QS_SCHEDULER_NOTIFY` (`telegram` | `email` | `slack` | `none`).
  - Telegram uses `EVENT_CHAT_BOT` / `EVENT_CHAT_ID`, the same keys as flowtask.
  - Message:
    `🛑 [<ENV> QSScheduler] <slug> (<job_id>) failed at <scheduled_for>: <error ≤500 chars>` + `QS_SCHEDULER_ALERT_URL`.
  - The fail-open path reuses it with a `⚠️ history unavailable` message, rate-limited
    to one per `QS_SCHEDULER_ALERT_COOLDOWN` seconds.
  - Send failures are logged, never raised.
- **Depends on**: none
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
  - `record_detail("execute_sql", list(self.results))` after `execute_sql.py:150`.
  - `record_detail("table", {"schema", "table", "rows", "method"})` at both
    "wrote %d rows" sites.
  - These are no-ops outside a tracked run.
- **Depends on**: M1 (`record_detail`)

### Module 9: HTTP API
- **Path**: `querysource/handlers/scheduler.py`, `querysource/services.py`
- **Responsibility**:
  - `SchedulerJobsView.get` overlays `paused` from the job's definition. To avoid
    N queries it reads the in-memory paused state, which startup and register keep
    in sync with the definition. `last_run` comes from one bounded `last_runs(ids)`
    call.
  - `patch` → `set_job_paused_durable`.
  - New `SchedulerRunsView` and `SchedulerRunNowView`, registered in the
    `ENABLE_QS_SCHEDULER` block.
  - Run-now applies `_enforce_scheduler_grant` (`handlers/scheduler.py:98`) for
    multi jobs.
  - `requested_by` / `paused_by` come from the session user.
- **Depends on**: M1, M4, M6

### Module 10: Packaging, docs, race test
- **Path**: `pyproject.toml`, `docs/QSSCHEDULER.md`, `tests/…`
- **Responsibility**:
  - Optional extra `scheduler-history = ["asyncdb[mongodb]"]`.
  - Docs cover history, TTL, fire-and-forget, run now, durable pause and the
    interval anchor.
  - The race test runs ≥2 `QSScheduler` instances against a disposable MongoDB
    (`mongo` container, or `mongomock-motor` if the container is unavailable) and
    asserts one body execution and one document per occurrence.
- **Depends on**: M1–M9
- **Exclusive**: `pyproject.toml` / `uv.lock` change (`parallel: false`).

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_writer_never_blocks` | M1 | `finish()` returns immediately while the store hangs; queue full → dropped counter + one WARNING |
| `test_writer_errors_swallowed` | M1 | store raises on every write → job unaffected, errors logged |
| `test_writer_drain_on_shutdown` | M1 | pending writes flushed within timeout; timeout respected when the store hangs |
| `test_claim_duplicate_returns_none` | M1 | second insert with same occurrence `_id` → None |
| `test_ensure_indexes_ttl` | M1 | TTL index on `created_at` with `expireAfterSeconds = 90×86400`; idempotent |
| `test_history_disabled_without_motor` | M1 | `ImportError` → `from_conf()` None, one WARNING |
| `test_scheduled_slot_all_types` | M2 | cron/crontab/interval/hourly/daily/weekly/monthly/biweekly → latest fire ≤ now; workers ±3 s agree |
| `test_scheduled_slot_frequent_cron` | M2 | `*/1` cron with large lookback → latest minute |
| `test_interval_anchor_default` | M2 | anchor added when `start_date` missing; preserved otherwise |
| `test_admit_claim_once` | M3 | two admits same occurrence → one ticket, one False |
| `test_admit_paused_skips` | M3 | paused definition → False, local job paused, no claim |
| `test_admit_missing_definition` | M3 | FEAT-176 behaviour: False + job removed |
| `test_admit_fail_open_timeout` | M3 | claim exceeds 2 s → True, ERROR log, alert scheduled, body runs |
| `test_tracked_run_failure_reraises` | M3 | body raises → finish(`failed`) enqueued, existing `except` notifies once |
| `test_set_schedule_paused_atomic` | M4 | only `scheduler.paused*` keys change; other attributes intact |
| `test_patch_preserves_pause` | M4 | `patch` with attributes lacking `scheduler.paused` keeps the stored pause |
| `test_pause_survives_restart` | M4 | paused definition → job registered paused on startup |
| `test_listener_missed_enqueues_once` | M5 | MISSED on 2 listeners → one document |
| `test_startup_abandon_local` | M5 | dead pid on this host → enqueued abandon; live pid untouched |
| `test_run_now_manual_and_409` | M6 | manual ticket created; second call while running → `RunInProgress` |
| `test_alert_callback_providers` | M7 | telegram/email/none/per-job override; send error logged; cooldown honoured |
| `test_record_detail_noop_outside_run` | M8 | no active run → no error |
| `test_execute_sql_details_recorded` | M8 | tracked multi stores `['LOCK TABLE','DELETE n','INSERT 0 n']` |
| `test_jobs_view_overlay_and_degraded` | M9 | `paused` + `last_run`; history down → `last_run: null`, `history_available: false` |
| `test_runs_endpoints` | M9 | filters, cap 200, 404, 503 `history_unavailable`, abandoned computed at read |
| `test_run_now_endpoint_grant` | M9 | write-capable multi without FEAT-160 grant → 403; with grant → 202 |

### Integration Tests
| Test | Description |
|---|---|
| `test_single_execution_two_schedulers` | Disposable MongoDB; 2 `QSScheduler` instances, same job, date trigger → 1 body execution, 1 `success` document |
| `test_durable_pause_across_instances` | PATCH on instance A; instance B's next fire skips; both restart paused |
| `test_existing_scheduler_suites_green` | `tests/scheduler/`, `tests/test_scheduler_*.py`, `tests/tenants/test_query_move.py` pass |

---

## 5. Acceptance Criteria

- [ ] Every execution of `query`, `multi` and `cache` jobs, and every manual run,
  produces one document in DocumentDB `qs_scheduler_runs` with `scheduled_for`,
  `started_at`, `finished_at`, `duration_ms`, `status`, `error`, `details`, `host`
  and `pid`.
- [ ] **No Postgres table is created or written for run history.** The only Postgres
  write is the pause flag on the existing slug definition.
- [ ] **Retention is a DocumentDB TTL index** on `created_at` (default 90 days,
  `QS_SCHEDULER_HISTORY_TTL_DAYS`). No purge job, cron or startup cleanup exists.
- [ ] **Fire-and-forget.** A job never awaits a history write other than the claim.
  With the history store hanging or failing, jobs run and finish with the same
  outcome and latency (claim bounded by `QS_SCHEDULER_CLAIM_TIMEOUT`), and no
  exception from history reaches job code (tests `test_writer_never_blocks`,
  `test_writer_errors_swallowed`, `test_admit_fail_open_timeout`).
- [ ] With N ≥ 2 scheduler instances, each scheduled occurrence executes once and
  produces one document (integration test).
- [ ] **Fail-open**: if the claim fails or times out, the job runs untracked; ERROR
  log + rate-limited alert.
- [ ] If history is not configured or `motor`/`pymongo` are missing, the scheduler
  behaves as before plus durable pause, and logs one WARNING.
- [ ] Pausing via PATCH persists in `attributes.scheduler.paused`, stops the job in
  every instance at its next fire and after restart, and survives an unrelated slug
  edit through `DefinitionRepository.patch`. Resume reverses all of it.
- [ ] `POST …/jobs/{job_id}/run` → `202 {run_id}`, `trigger=manual`,
  `requested_by`; `409` while a manual run of that job is `running`. A scheduled
  fire is not skipped because of a manual run.
- [ ] One alert per failed occurrence, provider from `QS_SCHEDULER_NOTIFY`
  (default `telegram`) or per-job `attributes.scheduler.notify`.
- [ ] `missed` / max-instances occurrences recorded once; stale `running` shown as
  `abandoned`.
- [ ] Interval schedules without `start_date` anchored to `QS_SCHEDULER_INTERVAL_ANCHOR`.
- [ ] Multi runs store `ExecuteSQL` tags and `Table` row counts in `details`.
- [ ] `GET /api/v1/qs/scheduler/jobs` returns `paused` + `last_run` and degrades to
  `history_available: false`; history endpoints match §2.
- [ ] FEAT-176 behaviour preserved: a missing slug is skipped and its job removed at
  fire time; the move's transient pause/rollback still works
  (`tests/tenants/test_query_move.py` green).
- [ ] `ruff check` clean; §4 tests pass; existing scheduler suites stay green.

---

## 6. Codebase Contract

> Verified against `20db6f5` (`origin/dev`, includes FEAT-176's simple query move).

### Verified Imports
```python
from querysource.conf import (DOCUMENTDB_HOSTNAME, DOCUMENTDB_PORT, DOCUMENTDB_DATABASE, DOCUMENTDB_USERNAME,
                              DOCUMENTDB_PASSWORD, DOCUMENTDB_TLSFILE, DOCUMENTDB_USE_SSL)   # conf.py:251-263
from querysource.conf import ENABLE_QS_SCHEDULER, QS_SCHEDULER_TIMEZONE, QS_SCHEDULER_MAX_INSTANCES, QS_SCHEDULER_COALESCE  # conf.py:388-391
from querysource.scheduler.notifications import NotificationManager   # notifications.py:21
from querysource.scheduler.jobs import in_flight                       # jobs.py:43
from querysource.repositories import DefinitionRepository              # scheduler.py:40
from querysource.tenants import QueryStore, TenantOwnerEnvelope        # scheduler.py:49-55
from apscheduler.events import EVENT_JOB_MISSED, EVENT_JOB_MAX_INSTANCES, JobExecutionEvent  # apscheduler 3.11.2
from apscheduler.triggers.base import BaseTrigger                      # scheduler.py:30
from apscheduler.triggers.interval import IntervalTrigger              # scheduler.py:32
from asyncdb import AsyncDB                                            # AsyncDB("mongo", params=...) — dwh.py:244
# NOT importable in the local dev venv today (no asyncdb[mongodb]); installed on prod navigator-api:
from pymongo.errors import DuplicateKeyError                           # pymongo 4.12.0 (asyncdb extra "mongodb")
import motor.motor_asyncio                                             # motor 3.7.0 (asyncdb extra "mongodb"); asyncdb/drivers/mongo.py:8
from notify import Notify                                              # async-notify: 2.0.0 in uv.lock, 1.6.0 on prod — Notify(provider: str, *args, **kwargs)
```

### Existing Class Signatures
```python
# querysource/scheduler/jobs.py
def in_flight(store: Any, slug: str) -> bool                             # line 43
@contextmanager
def _running(owner, slug)                                                # line 55-56
async def scheduled_query_job(slug, notification_manager=None, *, owner=None, job_id=None,
                              guard: Callable[[str, str | None], Awaitable[bool]] | None = None, **kwargs)   # line 105; guard 131; _running 136; notify 147
async def scheduled_multiqs_job(..., guard=None, run_as_user_id=None, identity_auth=None, **kwargs)        # line 154; guard 198; _running 203; notify 223
async def cache_refresh_job(..., guard=None, **kwargs)                                                      # line 230; guard 259; _running 264; notify 275

# querysource/scheduler/scheduler.py
class QSScheduler:                                                       # line 128
    _notification_manager (line 140); _repository: DefinitionRepository | None (line 153)
    def _parse_trigger(self, schedule_type: str, schedule: dict)        # line 205; interval 220-221
    def _register_query_row(...)                                         # line 282; guard binding 361, 384
    def _register_cache_row(...)                                         # line 405; guard binding 451
    def remove_job(self, job_id: str) -> bool                            # line 584
    def set_job_paused(self, job_id: str, paused: bool) -> bool          # line 596 (in-memory)
    def pause_slug_jobs(self, slug, store) -> list                       # line 612 (FEAT-176 transient)
    async def definition_present(self, store, slug, job_id) -> bool      # line 630; exists() at 640
    async def register_slug(self, slug, *, tenant=None) -> dict          # line 657
    async def startup(self, app) -> None                                 # line 730; start() 829; app["qs_scheduler"] 834
    async def shutdown(self, app) -> None                                # line 836
    def add_notification_callback(self, callback) -> None                # line 851

# querysource/repositories/definitions.py
class DefinitionRepository:  connection_factory (line 116/120; services.py:522-525 → connection.definition_connection)
    async def exists(self, store: QueryStore, slug: str) -> bool         # line 188 (SELECT 1)
    async def patch(self, identity, data, *, run_as_actor=None, request_info=None) -> Mapping[str, Any]   # line 518 (SET <col> = $n — replaces whole attributes)
    async def schedulable(self, store) -> tuple[Mapping[str, Any], ...]  # line 358

# querysource/interfaces/databases/mongo.py
class DocumentDB(MongoDB):                                               # line 60; params dbtype="documentdb", ssl, tlsCAFile (67-78)

# querysource/queries/multi/destinations/dwh.py
#   AsyncDB("mongo", params=params) (244); collection = conn[params["db"]][self._table] (246)

# querysource/handlers/scheduler.py
class SchedulerJobsView(BaseView):                                       # line 74
    _enforce_scheduler_grant (98); _serialize_job (157); get (202); post (271); delete (343); patch (374; set_job_paused at 409)

# querysource/scheduler/notifications.py
class NotificationManager: notify(self, job_id: str, slug: str, error: Exception) -> None   # lines 21, 37

# querysource/queries/multi/destinations/execute_sql.py — self.results set at line 150
# querysource/queries/multi/destinations/table.py — "wrote %d rows" at 650, 666
```

### Integration Points
| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `QSScheduler.admit` | job callables | `guard=partial(self.admit, store)` | `scheduler.py:361,384,451` |
| `QSScheduler.admit` | `DefinitionRepository.schedule_state` | replaces `exists()` on fire path | `scheduler.py:640` |
| job callables | `tracked_run` | `async with` around the existing `_running` body | `jobs.py:136,203,264` |
| `set_job_paused_durable` | PATCH | replaces `set_job_paused` call | `handlers/scheduler.py:409` |
| `AlertCallback` | `NotificationManager` | `add_notification_callback` | `scheduler.py:851` |
| `record_detail` | destinations | direct call | `execute_sql.py:150`, `table.py:650,666` |
| New views | router | `add_view` in scheduler block | `services.py:458-466` |

### Configuration References (new, `querysource/conf.py` after line 391)
| Key | Default | Purpose |
|---|---|---|
| `QS_SCHEDULER_HISTORY` | `true` | Master switch for run history |
| `QS_SCHEDULER_HISTORY_DATABASE` | `DOCUMENTDB_DATABASE` | DocumentDB database |
| `QS_SCHEDULER_HISTORY_COLLECTION` | `qs_scheduler_runs` | Collection |
| `QS_SCHEDULER_HISTORY_TTL_DAYS` | `90` | TTL index retention |
| `QS_SCHEDULER_HISTORY_QUEUE` | `1000` | Writer queue size per worker |
| `QS_SCHEDULER_HISTORY_DRAIN_TIMEOUT` | `5` (s) | Shutdown flush bound |
| `QS_SCHEDULER_CLAIM_TIMEOUT` | `2.0` (s) | Claim bound before fail-open |
| `QS_SCHEDULER_HISTORY_READ_TIMEOUT` | `3.0` (s) | API read bound |
| `QS_SCHEDULER_RUN_TIMEOUT` | `21600` (s) | `running` older → reported `abandoned` |
| `QS_SCHEDULER_SLOT_LOOKBACK` | `86400` (s) | `scheduled_slot` window |
| `QS_SCHEDULER_INTERVAL_ANCHOR` | `2000-01-01T00:00:00` | Interval `start_date` default |
| `QS_SCHEDULER_ERROR_MAX` | `4000` | Max chars of `error` |
| `QS_SCHEDULER_NOTIFY` | `telegram` | `telegram` \| `email` \| `slack` \| `none` |
| `QS_SCHEDULER_ALERT_COOLDOWN` | `600` (s) | Rate limit for "history unavailable" alerts |
| `EVENT_CHAT_BOT` / `EVENT_CHAT_ID` | — | Telegram (same keys as flowtask `conf.py:313-314`) |
| `QS_SCHEDULER_ALERT_URL` | — | Optional Concierge link |

### Does NOT Exist (Anti-Hallucination)
- ~~Any run-history store in `querysource/`~~: created here, in DocumentDB.
- ~~Postgres tables `troc.qs_scheduler_runs` / `troc.qs_scheduler_job_state`~~: dropped from the design (lead, 2026-10-20). Do not create them.
- ~~Persisted pause state~~: `set_job_paused` / `pause_slug_jobs` are in-memory only.
- ~~`DefinitionRepository.schedule_state` / `set_schedule_paused`~~: introduced here; `patch` replaces whole columns today.
- ~~`scheduled_run_time` passed to job callables~~: only on APScheduler events.
- ~~`add_listener` / `EVENT_JOB_*` usage in `querysource/`~~: none today.
- ~~`pymongo` / `motor` in the local dev venv~~: not installed; `asyncdb[mongodb]` is not a dependency yet (prod has them).
- ~~`EVENT_CHAT_BOT` / `EVENT_CHAT_ID` in `querysource/conf.py`~~: flowtask-only today.
- ~~`QSScheduler.admit`, `run_now`, `set_job_paused_durable`, `RunHistory`, `HistoryWriter`, `RunTicket`, `tracked_run`, `record_detail`, `scheduled_slot`, `occurrence_id`, `AlertCallback`, `SchedulerRunsView`, `SchedulerRunNowView`~~: introduced by this spec.

### Edit Sites (Blueprint Anchors)

Verified against: `20db6f5`

| File | Action | Verbatim anchor line | Verified at | Occurrences |
|---|---|---|---|---|
| `querysource/scheduler/history.py` | CREATE | — | — | — |
| `querysource/scheduler/scheduler.py` | MODIFY | `                return IntervalTrigger(**schedule)` | `scheduler.py:221` | 1 |
| `querysource/scheduler/scheduler.py` | MODIFY | `"guard": partial(self.definition_present, store),` | `scheduler.py:361,384,451` | 3 (replace all) |
| `querysource/scheduler/scheduler.py` | MODIFY | `    async def definition_present(self, store: QueryStore, slug: str, job_id: str \| None) -> bool:` | `scheduler.py:630` | 1 (add `admit`, `set_job_paused_durable`, `run_now` after it) |
| `querysource/scheduler/scheduler.py` | MODIFY | `        self._scheduler.start()` | `scheduler.py:829` | 1 |
| `querysource/scheduler/jobs.py` | MODIFY | `    if guard is not None and not await guard(slug, job_id):` | `jobs.py:131,198,259` | 3 |
| `querysource/repositories/definitions.py` | MODIFY | `    async def exists(self, store: QueryStore, slug: str) -> bool:` | `definitions.py:188` | 1 (add `schedule_state` after) |
| `querysource/repositories/definitions.py` | MODIFY | `    async def patch(` | `definitions.py:518` | 1 (preserve pause keys; add `set_schedule_paused`) |
| `querysource/scheduler/notifications.py` | MODIFY | `class NotificationManager:` | `notifications.py:21` | 1 |
| `querysource/handlers/scheduler.py` | MODIFY | `        if not scheduler.set_job_paused(job_id, action == "pause"):` | `handlers/scheduler.py:409` | 1 |
| `querysource/handlers/scheduler.py` | MODIFY | `    def _serialize_job(self, job: Job) -> dict:` | `handlers/scheduler.py:157` | 1 |
| `querysource/services.py` | MODIFY | `'/api/v1/qs/scheduler/jobs/{job_id}', SchedulerJobsView` | `services.py:464` | 1 |
| `querysource/conf.py` | MODIFY | `QS_SCHEDULER_COALESCE = config.getboolean('QS_SCHEDULER_COALESCE', fallback=True)` | `conf.py:391` | 1 |
| `querysource/queries/multi/destinations/execute_sql.py` | MODIFY | `            self.results = await execute_guarded(statements, timeout=self._timeout)` | `execute_sql.py:150` | 1 |
| `querysource/queries/multi/destinations/table.py` | MODIFY | `"TableDestination: wrote %d rows → %s.%s",` (with the `self.logger.info(` line above, 649/665) | `table.py:650,666` | 2 |
| `pyproject.toml` | MODIFY | `[project.optional-dependencies]` | `pyproject.toml:121` | 1 |
| `docs/QSSCHEDULER.md` | MODIFY | (append section) | — | — |
| `tests/scheduler/test_history_store.py` | CREATE | — | — | — |
| `tests/scheduler/test_scheduled_slot.py` | CREATE | — | — | — |
| `tests/scheduler/test_admission_and_tracking.py` | CREATE | — | — | — |
| `tests/scheduler/test_durable_pause.py` | CREATE | — | — | — |
| `tests/scheduler/test_alerts.py` | CREATE | — | — | — |
| `tests/handlers/test_scheduler_runs_api.py` | CREATE | — | — | — |
| `tests/integration/test_scheduler_single_execution.py` | CREATE | — | — | — |

---

## 7. Implementation Notes & Constraints

### Patterns to Follow
- **Fire-and-forget means enqueue, not `create_task` per write.** One bounded
  queue and one drain task per worker keep memory bounded and order per run
  (insert before finish). Never `await` a history write in job code except the
  claim.
- **Logs are not relational data.** Run history goes only to DocumentDB with a TTL
  index. Postgres holds only configuration (the pause flag on the definition).
- **Guard composition.** `admit` extends FEAT-176's guard: same "missing slug →
  skip + remove" and "read error never blocks" contract.
- **Atomic attribute writes.** Use `jsonb_set` for the pause. Never
  read-modify-write `attributes`, because `patch` replaces the whole column.
- **Listeners are sync.** They only call `HistoryWriter` enqueue methods, never
  `new_event_loop()` + thread pool (the flowtask `save_db_event` anti-pattern).
- **Run identity.** `_id` is generated at claim and passed down (flowtask NAV-6365
  lesson).
- **Repo conventions.** `self.logger` / module `logger`, never `print`; Google
  docstrings; strict type hints; config via navconfig in `conf.py`.

### Known Risks / Gotchas
- **DocumentDB TTL is approximate.** The TTL monitor deletes expired documents in
  the background, with no exact-time guarantee. That is acceptable for history;
  readers must not assume a document is gone at exactly 90 days.
- **Claim latency on every fire.** One DocumentDB round-trip per worker per fire,
  bounded at 2 s. If DocumentDB is slow or down, fail-open runs the job (possible N
  duplicates during the outage, accepted, flowtask precedent) and alerts are
  rate-limited.
- **Queue overflow drops events.** Under a write storm or a long outage, finish
  events can be dropped, leaving claim documents in `running`. Reads show them as
  `abandoned` after `QS_SCHEDULER_RUN_TIMEOUT`, and the `dropped` counter is
  logged.
- **Slot derivation correctness.** A wrong `scheduled_for` defeats dedup or wrongly
  skips. Mitigation: latest fire ≤ now, property tests over all 8 types, and the
  interval anchor. Adding the anchor changes the phase of existing interval jobs:
  document it.
- **Pause clobbering.** Any writer that replaces `attributes` wholesale could drop
  the pause. `DefinitionRepository.patch` preserves it; the frontend spec must also
  send it back unchanged.
- **FEAT-176 transient vs durable pause.**
  - The move's `pause_slug_jobs` / rollback stay in-memory and must not touch
    `attributes.scheduler.paused`.
  - Because the durable pause lives on the definition row, it **moves with the
    slug** to the destination store and is re-applied by `register_slug`.
- **Dependency.** `asyncdb[mongodb]` is a new optional extra. History is disabled
  if it is missing, so environments without it keep working.
- **async-notify version skew.** Prod has 1.6.0 and `uv.lock` has 2.0.0. Use only
  the `Notify(provider, **kwargs)` + `async with … send(recipient=, message=)` surface
  common to both (flowtask's 1.6.x usage), and verify on 2.0.0 in tests.
- **Clock/timezone.** tz-aware UTC datetimes everywhere.

### External Dependencies
| Package | Version | Reason |
|---|---|---|
| `asyncdb[mongodb]` (`pymongo`, `motor`) | pymongo 4.12.0, motor 3.7.0 (pinned by asyncdb 2.15.x; present on prod) | DocumentDB history + claim |
| `apscheduler` | 3.11.2 (installed) | triggers, listeners |
| `async-notify` | ≥1.6 (prod 1.6.0; lock 2.0.0) | alerts; already a dependency |

---

## Worktree Strategy

- **Isolation:** one feature worktree, `feat-FEAT-181-scheduler-execution-history`.
- **Module dependency graph:**
  - M3 → M1 (`RunHistory`, `HistoryWriter`, `tracked_run`), M3 → M2
    (`scheduled_slot`), M3 → M4 (`schedule_state`).
  - M5 → M1 and M5 → M3.
  - M6 → M1 and M6 → M3.
  - M8 → M1 (`record_detail`).
  - M9 → M1, M4, M6.
  - M10 → all.
  - M7 has no edges.
  - Concurrent from the start: M1, M2, M4, M7. M8 can run after M1.
- **Shared files:**
  - `querysource/scheduler/scheduler.py` (M2, M3, M4, M5, M6) and
    `querysource/handlers/scheduler.py` (M4, M9): their tasks are serialized.
  - `querysource/scheduler/history.py` (M1, M2, M3): M2/M3 append helpers after M1.
- **Exclusive resources:** M10's `pyproject.toml` + `uv.lock` change
  (`parallel: false`).
- **Cross-feature dependencies:** FEAT-176's simple move is merged
  (`origin/dev` `a242603`). The frontend spec lands after `navigator-frontend-next`
  FEAT-252.

---

## 8. Open Questions

- [x] Which jobs are recorded? — *Resolved in brainstorm*: all kinds (query, multi, cache-refresh).
- [x] N-worker duplicate execution in scope? — *Resolved in brainstorm*: yes, fix it in this feature.
- [x] UI scope? — *Resolved in brainstorm*: per-job history, last-run column with failure badge, slug-inspector history, full error + ExecuteSQL details (delivered by the frontend spec).
- [x] Orphaned `running` rows? — *Resolved in brainstorm*: show as `abandoned`.
- [x] Permissions? — *Resolved in brainstorm*: same as the current scheduler monitor (plus FEAT-160 grant for run-now on write multis).
- [x] Extras? — *Resolved in brainstorm*: include "run now" and failure alerts; durable pause/resume.
- [x] Claim policy when the store is unreachable at fire time? — *Resolved in brainstorm*: fail-open, like flowtask; run, log, alert.
- [x] Alert channel? — *Resolved in brainstorm*: async-notify, `QS_SCHEDULER_NOTIFY` default `telegram`, flowtask's `EVENT_CHAT_BOT`/`EVENT_CHAT_ID`, per-job override.
- [x] Interval triggers slot alignment? — *Resolved in brainstorm*: deterministic `start_date` anchor.
- [x] Skip a scheduled fire while a manual run is `running`? — *Resolved in brainstorm*: no; only a second manual run gets `409`.
- [x] Frontend tracked here or in the frontend repo? — *Resolved in brainstorm*: separate spec in `navigator-frontend-next`.
- [x] `navigator-api` QS pin drift a prerequisite? — *Resolved in brainstorm*: no.
- [x] Coordination with FEAT-176? — *Resolved in brainstorm*: FEAT-176/FEAT-252 land first; this spec builds on FEAT-176's merged `guard`.
- [x] **Storage for run history** — *Resolved 2026-10-20 by Jesus Lara (lead), supersedes the brainstorm's "central Postgres table in `troc`"*: never a relational DB for logging. Use DocumentDB (or DynamoDB/BigQuery) with a native TTL retention policy. Chosen: DocumentDB `qs_scheduler_runs`, TTL 90 days.
- [x] **Retention** — *Resolved 2026-10-20 by Jesus Lara, supersedes the brainstorm's "no automatic purge"*: 90-day TTL enforced by the store; no purge process.
- [x] **Recording must be fire-and-forget** — *Resolved 2026-10-20 by Jesus Lara*: recording never affects code execution. Implemented with `HistoryWriter` (queue, never awaited); the only awaited write is the claim (coordination, bounded at 2 s, fail-open).
- [x] **Single-execution claim** — *Resolved 2026-10-20 by Juan Rodriguez*: unique insert in DocumentDB (`_id` = occurrence id), awaited ≤2 s, fail-open.
- [x] **Where the durable pause lives** — *Resolved 2026-10-20 by Juan Rodriguez*: on the slug definition, `attributes.scheduler.paused` (flowtask `troc.jobs.enabled` precedent); no new tables.
- [ ] Verify at task time that navigator-api's vault exposes `EVENT_CHAT_BOT` / `EVENT_CHAT_ID` and the `DOCUMENTDB_*` keys for QS (on prod `.env` only one of those keys is set; the rest may come from vault). — *Owner: Juan Rodriguez*
- [x] Does the lead's "fire-and-forget" cover the awaited ≤2 s claim? — *Resolved 2026-10-20*: yes. The claim is coordination, not logging; it is bounded at 2 s and fail-open, so it never blocks execution.

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
| 0.1 | 2026-10-20 | Juan Rodriguez (with Claude) | Initial draft from brainstorm Option A (Postgres ledger), rebased on FEAT-176 simple move |
| 0.2 | 2026-10-20 | Juan Rodriguez (with Claude) | Lead review: history moved to DocumentDB with 90-day TTL; fire-and-forget writer; claim = unique DocumentDB insert (fail-open); durable pause moved to `attributes.scheduler.paused`; no Postgres tables |
| 1.0 | 2026-10-20 | Juan Rodriguez | Approved; awaited ≤2 s fail-open claim confirmed as compatible with fire-and-forget |
