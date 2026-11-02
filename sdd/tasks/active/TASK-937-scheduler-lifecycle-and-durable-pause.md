# TASK-937: Scheduler lifecycle (history wiring, listeners, abandoned) and durable pause

**Feature**: FEAT-181 — Scheduler Execution History
**Spec**: `sdd/specs/scheduler-execution-history.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-936, TASK-934
**Assigned-to**: unassigned

---

## Context

Spec §3 Modules 4 (scheduler part) and 5. This task wires everything into
`startup`/`shutdown`:
- create `RunHistory` + `HistoryWriter` **before** job registration (the bindings
  capture `self._writer`);
- register `AlertCallback`;
- mark this host's dead-pid runs abandoned;
- add MISSED/MAX_INSTANCES listeners;
- register paused definitions paused.

It also adds `set_job_paused_durable`, used by PATCH (TASK-939).

---

## Scope

- `startup`, before the store loop (`stores = self._registry.stores() ...`):
  1. `self._history = RunHistory.from_conf()`; if set and
     `await self._history.ensure_indexes()` is False, log a WARNING and set
     `self._history = None`;
  2. if set, `self._writer = HistoryWriter(self._history)` and `.start()`;
  3. `self._alerts = AlertCallback(per_job=self._job_notify)` and
     `self.add_notification_callback(self._alerts)`, unless `QS_SCHEDULER_NOTIFY == "none"`;
  4. `self._writer.abandon_local(host, dead_pids)` for pids from this host's
     `running` documents. Use an `is_alive(pid)` check via `os.kill(pid, 0)`.
     FILL IN the source of candidate pids (a bounded read through `RunHistory`) or
     skip when unavailable.
- After registration, before `self._scheduler.start()`, add listeners:
  `self._scheduler.add_listener(self._on_job_skipped, EVENT_JOB_MISSED | EVENT_JOB_MAX_INSTANCES)`.
- `_on_job_skipped(event)`, a sync listener, calls `self._writer.skipped(...)` with
  `occurrence_id(event.job_id, event.scheduled_run_time)`, status `missed` /
  `skipped`, and slug/owner/kind from the job's kwargs.
- In `_register_query_row` / `_register_cache_row`:
  - record `attributes.scheduler.notify` into `self._job_notify[job_id]` when present;
  - when `attributes.scheduler.paused` is true, call `self.set_job_paused(job_id, True)`
    right after `add_job`.
- `async def set_job_paused_durable(job_id, paused, *, user, reason=None) -> bool`:
  1. resolve store + slug from the job's kwargs (`owner` envelope → `self._registry.resolve(owner["schema"])`);
  2. `await self._repository.set_schedule_paused(...)`;
  3. `self.set_job_paused(job_id, paused)`.

  It returns False when the job is unknown here.
- `shutdown`: `await self._writer.drain()` before stopping the scheduler.

**NOT in scope**: run now (TASK-938), HTTP (TASK-939).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/scheduler/scheduler.py` | MODIFY | Startup/shutdown wiring, listeners, paused registration, `set_job_paused_durable` |
| `tests/scheduler/test_scheduler_lifecycle.py` | CREATE | Lifecycle + durable pause tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from apscheduler.events import EVENT_JOB_MISSED, EVENT_JOB_MAX_INSTANCES, JobExecutionEvent  # apscheduler 3.11.2 (verified)
from querysource.scheduler.history import RunHistory, HistoryWriter, occurrence_id            # TASK-930/931/932
from querysource.scheduler.notifications import AlertCallback                                 # TASK-934
from querysource.conf import QS_SCHEDULER_NOTIFY                                               # TASK-929
```

### Existing Signatures to Use
```python
# querysource/scheduler/scheduler.py
class QSScheduler:
    def _register_query_row(self, row, *, store=None)          # line 282 (add_job at ~345 multi, ~369 query)
    def _register_cache_row(self, row, *, store=None)          # line 405 (add_job ~434)
    def set_job_paused(self, job_id, paused) -> bool           # line 596
    async def startup(self, app) -> None                       # line 730; stores loop at ~773; self._scheduler.start() at 829
    async def shutdown(self, app) -> None                      # line 836
    def add_notification_callback(self, callback) -> None      # line 851
    self._registry: TenantRegistry                             # resolve(schema) used at scheduler.py:428
# querysource/repositories/definitions.py — set_schedule_paused(store, slug, *, paused, user, reason) (TASK-933)
# APScheduler: AsyncIOScheduler.add_listener(callback, mask); JobExecutionEvent(code, job_id, jobstore, scheduled_run_time, ...)
```

### Does NOT Exist
- ~~Thread/new-event-loop DB writes from listeners~~: the flowtask anti-pattern. Listeners only enqueue.
- ~~A persisted pause in `set_job_paused`~~: it stays in-memory (FEAT-176's move uses it transiently). Durable is the new method.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/scheduler/scheduler.py", "action": "MODIFY"},
    {"path": "tests/scheduler/test_scheduler_lifecycle.py", "action": "CREATE"}
  ],
  "contract_symbols": ["sym:querysource/scheduler/scheduler.py#QSScheduler"]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Add `self._job_notify: dict[str, str] = {}` in `__init__`.
2. Insert the history/alerts block in `startup` **before** registration. *Why:*
   `add_job` kwargs capture `self._writer` by value.
3. Hook the paused flag + notify map in both register methods.
4. Add listeners right before `self._scheduler.start()`.
5. Add `_on_job_skipped`, `set_job_paused_durable`; patch `shutdown`.

### `querysource/scheduler/scheduler.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '        self._scheduler.start()' querysource/scheduler/scheduler.py)
# BEFORE `        self._scheduler.start()` (verified: querysource/scheduler/scheduler.py:829)
        self._scheduler.add_listener(self._on_job_skipped, EVENT_JOB_MISSED | EVENT_JOB_MAX_INSTANCES)
```
```python
# INSERT in startup() BEFORE the line `        stores = self._registry.stores() or (self._registry.resolve(),)`
# occurrences: 1 (verify: grep -c 'stores = self._registry.stores() or (self._registry.resolve(),)' querysource/scheduler/scheduler.py)
        self._history = RunHistory.from_conf()
        if self._history is not None and not await self._history.ensure_indexes():
            self.logger.warning("QSScheduler: run history disabled (DocumentDB indexes unavailable)")
            self._history = None
        if self._history is not None:
            self._writer = HistoryWriter(self._history)
            self._writer.start()
            # FILL IN: abandon_local(host, dead pids) — bounded; skip on any error
        if QS_SCHEDULER_NOTIFY.lower() != "none":
            self._alerts = AlertCallback(per_job=self._job_notify)
            self.add_notification_callback(self._alerts)
```
```python
# AFTER set_job_paused (verified: querysource/scheduler/scheduler.py:596-610)
    def _on_job_skipped(self, event: JobExecutionEvent) -> None:
        """Listener: MISSED → 'missed', MAX_INSTANCES → 'skipped'; enqueue only (fire-and-forget)."""
        # FILL IN: return if self._writer is None; job = self._scheduler.get_job(event.job_id) (may be None → return);
        #   status = "missed" if event.code == EVENT_JOB_MISSED else "skipped"; self._writer.skipped(...)
        ...

    async def set_job_paused_durable(self, job_id: str, paused: bool, *, user: str | None,
                                     reason: str | None = None) -> bool:
        """Persist pause/resume on the slug definition, then apply locally; False when job_id is unknown here."""
        # FILL IN: job lookup; slug/owner from job.kwargs; store = self._registry.resolve(owner["schema"]) when owner
        #   else self._registry.resolve(); ok = await self._repository.set_schedule_paused(store, slug, paused=…, user=…, reason=…);
        #   if not ok → return False (slug gone); return self.set_job_paused(job_id, paused)
        ...
```
In `shutdown` (`scheduler.py:836`), first:
`if self._writer is not None: await self._writer.drain()`.

In `_register_query_row` and `_register_cache_row`, right after each `add_job(...)`:
```python
        # FILL IN: sched = (row.get("attributes") or {}).get("scheduler") or {} (attributes may be str JSON — reuse
        #   the parsing the method already does for scheduler_def); if sched.get("notify"): self._job_notify[job_id] = sched["notify"];
        #   if sched.get("paused"): self.set_job_paused(job_id, True) — only valid once the scheduler exists (it does: created before the loop)
```

### FILL IN checklist
- [ ] abandon_local candidate pids (bounded) + `os.kill(pid, 0)` alive check.
- [ ] `_on_job_skipped` body.
- [ ] `set_job_paused_durable` body.
- [ ] Paused/notify hooks in both register methods (3 add_job sites).
- [ ] Tests below.

---

## Acceptance Criteria

- [ ] History disabled (no motor or index failure) → one WARNING; jobs still register and run.
- [ ] Paused definition at startup → job registered with `next_run_time is None`.
- [ ] `set_job_paused_durable` persists via the repository and pauses locally; unknown job → False.
- [ ] MISSED event on two listeners → one `skipped()` enqueue each, collapsed by the store.
- [ ] Shutdown drains the writer within its timeout.

---

## Validation Commands

- `pytest tests/scheduler/test_scheduler_lifecycle.py -q`
- `pytest tests/test_scheduler_core.py -q`

---

## Agent Instructions

1. Work in the feature worktree; check TASK-936 and TASK-934 are done.
2. Re-verify anchor counts; set `in-progress`; implement; run the Validation Commands.
3. Close with `scripts/sdd/close_task.sh TASK-937 scheduler-execution-history verified`.

---

## Completion Note

*(Agent fills this in when done)*
