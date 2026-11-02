# TASK-936: QSScheduler.admit and tracked job callables

**Feature**: FEAT-181 — Scheduler Execution History
**Spec**: `sdd/specs/scheduler-execution-history.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-932, TASK-933
**Assigned-to**: unassigned

---

## Context

Spec §2 "The claim" and §3 Module 3. This is the core of single-execution.
FEAT-176 already gives each job a fire-time `guard(slug, job_id)` bound to
`definition_present`. `admit` supersedes it and runs, in every worker:
1. definition present + durable pause (one read);
2. `scheduled_slot`;
3. DocumentDB claim (bounded, fail-open).

The job callables keep the guard result and run their body inside `tracked_run`.

---

## Scope

- Add `QSScheduler._history: RunHistory | None = None`, `_writer: HistoryWriter | None = None`
  and `_alerts: AlertCallback | None = None` (initialized in `__init__`; set by TASK-937).
- Add `QSScheduler.admit(store, slug, job_id) -> RunTicket | bool`:
  1. `self._repository.schedule_state(store, slug)`:
     - missing → `remove_job(job_id)` + `False`;
     - paused → `set_job_paused(job_id, True)` + `False`;
     - read error → treat as present and not paused (FEAT-176 contract: never block).
  2. With no `_history`, return `True`.
  3. `slot = scheduled_slot(job.trigger, now, lookback=…)`.
  4. `claim(...)`:
     - a ticket → return it;
     - `None` → `False`;
     - any exception/timeout → log ERROR, `_alerts.warn_history_unavailable(...)`
       when set, and return `True` (fail-open).
- Replace the three `"guard": partial(self.definition_present, store),` bindings with
  `partial(self.admit, store)` and add `"writer": self._writer` to those kwargs.
- In `jobs.py`, for each of the 3 callables:
  - accept `writer: HistoryWriter | None = None`;
  - `admission = await guard(slug, job_id) if guard is not None else True`;
    `if not admission: return`;
  - wrap the existing body (inside `try`, around `with _running(...)`) in
    `async with tracked_run(admission, writer):`.
- Keep `definition_present` unchanged (FEAT-176 callers/tests).

**NOT in scope**: startup/listeners/durable PATCH (TASK-937), run now (TASK-938).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/scheduler/scheduler.py` | MODIFY | `admit`, attributes, guard bindings |
| `querysource/scheduler/jobs.py` | MODIFY | Keep admission; `tracked_run`; `writer` kwarg |
| `tests/scheduler/test_admission_and_tracking.py` | CREATE | Admit + job tracking tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.scheduler.history import (RunHistory, RunTicket, HistoryWriter, tracked_run,
                                           scheduled_slot, occurrence_id)        # TASK-930/931/932
from querysource.scheduler.notifications import NotificationManager            # scheduler.py:47
from querysource.conf import QS_SCHEDULER_SLOT_LOOKBACK                         # TASK-929
from functools import partial                                                    # scheduler.py:22
```

### Existing Signatures to Use
```python
# querysource/scheduler/scheduler.py
class QSScheduler:                                                             # line 128
    def __init__(self, loop=None)                                              # line 135; _repository at 153
    def remove_job(self, job_id: str) -> bool                                  # line 584
    def set_job_paused(self, job_id: str, paused: bool) -> bool                # line 596
    async def definition_present(self, store, slug, job_id) -> bool            # line 630 (keep; contract to mirror)
    # guard bindings: line 361 (multi), 384 (query), 451 (cache): "guard": partial(self.definition_present, store),
# querysource/scheduler/jobs.py
async def scheduled_query_job(slug, notification_manager=None, *, owner=None, job_id=None, guard=None, **kwargs)  # line 105; guard check 131; _running 136
async def scheduled_multiqs_job(..., guard=None, run_as_user_id=None, identity_auth=None, **kwargs)               # line 154; guard 198; _running 203
async def cache_refresh_job(..., guard=None, **kwargs)                                                           # line 230; guard 259; _running 264
# querysource/repositories/definitions.py — schedule_state(store, slug) -> (present, paused)   (TASK-933)
```

### Does NOT Exist
- ~~`_alerts.warn_history_unavailable`~~ before TASK-934: it exists after TASK-934 (`AlertCallback`). Guard with `if self._alerts is not None`.
- ~~`scheduled_run_time` in job kwargs~~: derive the slot from the job's trigger. Use `self._scheduler.get_job(job_id).trigger`.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/scheduler/scheduler.py", "action": "MODIFY"},
    {"path": "querysource/scheduler/jobs.py", "action": "MODIFY"},
    {"path": "tests/scheduler/test_admission_and_tracking.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/scheduler/scheduler.py#QSScheduler",
    "sym:querysource/scheduler/jobs.py#scheduled_query_job",
    "sym:querysource/scheduler/jobs.py#scheduled_multiqs_job",
    "sym:querysource/scheduler/jobs.py#cache_refresh_job"
  ]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Add the three attributes at the end of `__init__`. *Why:* `admit` reads them before
   startup wiring exists.
2. Insert `admit` after `definition_present`.
3. Replace the 3 guard bindings and add `"writer": self._writer`. The writer is
   resolved at registration time; TASK-937 creates it **before** registration.
4. Edit the 3 callables identically.

### `querysource/scheduler/scheduler.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '    async def definition_present(self, store: QueryStore, slug: str, job_id: str | None) -> bool:' querysource/scheduler/scheduler.py)
# AFTER the definition_present method (verified: querysource/scheduler/scheduler.py:630-655)
    async def admit(self, store: QueryStore, slug: str, job_id: str | None) -> RunTicket | bool:
        """Fire-time gate. False = skip; True = run untracked (history off / fail-open); RunTicket = tracked.

        Supersedes definition_present: same "missing → skip + remove" and "read error never blocks" contract.
        """
        if self._repository is not None:
            try:
                present, paused = await self._repository.schedule_state(store, slug)
            except Exception as exc:  # noqa: BLE001 - the guard must not block a job
                self.logger.warning("QSScheduler: schedule_state failed for '%s': %s", slug, exc)
                present, paused = True, False
            if not present:
                # FILL IN: log like definition_present (scheduler.py:650) and remove_job(job_id); return False
                ...
            if paused:
                # FILL IN: set_job_paused(job_id, True) so this worker stops waking up; return False
                ...
        if self._history is None or job_id is None:
            return True
        job = self._scheduler.get_job(job_id) if self._scheduler is not None else None
        if job is None:
            return True
        now = datetime.now(timezone.utc)
        slot = scheduled_slot(job.trigger, now, lookback=timedelta(seconds=QS_SCHEDULER_SLOT_LOOKBACK))
        try:
            ticket = await self._history.claim(
                occurrence_id=occurrence_id(job_id, slot), job_id=job_id, slug=slug,
                kind=_kind_of(job_id), owner=(job.kwargs or {}).get("owner"), scheduled_for=slot,
            )
        except Exception as exc:  # noqa: BLE001 - fail-open: history never blocks execution
            # FILL IN: logger.error("QSScheduler: claim failed for %s, running untracked: %s", job_id, exc);
            #   if self._alerts: self._alerts.warn_history_unavailable(job_id, slug); return True
            ...
        return ticket if ticket is not None else False
```
Add a module-level `_kind_of(job_id) -> str` helper. It returns
`multi` / `cache` / `query` from the id prefix, and **FILL IN** the qualified
`qsj2-` shape by mirroring `_kind_from_id` in `handlers/scheduler.py:42`. Also add
`from datetime import timezone` (`datetime` and `timedelta` are already imported at
line 21).

```python
# occurrences: 3 (verified: grep -c '"guard": partial(self.definition_present, store),' querysource/scheduler/scheduler.py)
# REPLACE all three (lines 361, 384, 451) with:
                    "guard": partial(self.admit, store),
                    "writer": self._writer,
# (match each site's indentation: 20 spaces at 361, 16 at 384 and 451)
```

### `querysource/scheduler/jobs.py` (MODIFY)
```python
# occurrences: 3 (verified: grep -c '    if guard is not None and not await guard(slug, job_id):' querysource/scheduler/jobs.py)
# In EACH of the 3 callables: add the kwarg `writer: HistoryWriter | None = None,` after `guard`,
# then REPLACE the two lines
#     if guard is not None and not await guard(slug, job_id):
#         return
# with
    admission = await guard(slug, job_id) if guard is not None else True
    if not admission:
        return
# and change `with _running(owner, slug):` (lines 136, 203, 264) to
        with _running(owner, slug):
            async with tracked_run(admission, writer):
                <existing body, re-indented one level>
```
**Why**: `tracked_run` re-raises, so the existing `except Exception` (log + `notify`)
still runs exactly once. The import goes under `TYPE_CHECKING` for `HistoryWriter`;
import `tracked_run` normally.

### FILL IN checklist
- [ ] Missing/paused branches in `admit` (logs + side effects).
- [ ] Fail-open branch (ERROR log + rate-limited alert).
- [ ] `_kind_of` incl. `qsj2-` ids.
- [ ] Re-indent the three bodies without changing behaviour.
- [ ] Tests below.

---

## Acceptance Criteria

- [ ] Two `admit` calls for the same occurrence: one `RunTicket`, one `False`.
- [ ] Paused definition → `False`, local job paused, no claim attempted.
- [ ] Missing definition → `False` + job removed (FEAT-176 behaviour).
- [ ] Claim raising/timing out → `True`, ERROR logged, alert hook called.
- [ ] A body exception → finish(`failed`) enqueued, and the existing `notify` called once.
- [ ] Existing `tests/scheduler/test_jobs_no_principal.py` and `tests/tenants/test_query_move.py` pass.

---

## Validation Commands

- `pytest tests/scheduler/test_admission_and_tracking.py -q`
- `pytest tests/scheduler/test_jobs_no_principal.py -q`
- `pytest tests/tenants/test_query_move.py -q`

---

## Agent Instructions

1. Work in the feature worktree; check TASK-932 and TASK-933 are done.
2. Re-run the `grep -c` for every anchor; stop and report if a count differs.
3. Set `in-progress`; implement; complete every `# FILL IN:`; run the Validation Commands.
4. Close with `scripts/sdd/close_task.sh TASK-936 scheduler-execution-history verified`.

---

## Completion Note

*(Agent fills this in when done)*
