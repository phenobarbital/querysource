# TASK-938: QSScheduler.run_now (on-demand manual runs)

**Feature**: FEAT-181 — Scheduler Execution History
**Spec**: `sdd/specs/scheduler-execution-history.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-937
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 6. Operators need "Run now" from Concierge. A manual run is claimed
like a scheduled one (awaited insert, `_id` = uuid4, `trigger=manual`). It then runs
the job's own callable with a pre-claimed ticket in the serving worker. A second
manual run while one is `running` is refused (409); scheduled fires are never skipped
because of it.

---

## Scope

- Add `class RunInProgress(Exception)` with `run_id` (module level, `scheduler.py`).
- `async def run_now(self, job_id, *, user) -> RunTicket`:
  1. unknown job → `KeyError`;
  2. `self._history is None` → `RuntimeError("history unavailable")`;
  3. `await self._history.manual_running(job_id)` → `RunInProgress(run_id)`;
  4. claim with `occurrence_id = uuid4().hex`, `trigger="manual"`, `requested_by=user`,
     `scheduled_for=now`;
  5. `kwargs = {**job.kwargs, "guard": <async fn returning ticket>, "writer": self._writer}`;
     `task = create_task(job.func(**kwargs))`, held in `self._manual_tasks`
     (discard on done);
  6. return the ticket.
- `shutdown`: cancel pending manual tasks. Their `tracked_run` enqueues `failed`
  because `CancelledError` is a `BaseException`.

**NOT in scope**: HTTP endpoint (TASK-939).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/scheduler/scheduler.py` | MODIFY | `RunInProgress`, `run_now`, `_manual_tasks`, shutdown cancel |
| `tests/scheduler/test_run_now.py` | CREATE | Manual run + 409 + shutdown tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from uuid import uuid4                                              # stdlib
from querysource.scheduler.history import RunHistory, RunTicket    # TASK-930
```

### Existing Signatures to Use
```python
# querysource/scheduler/scheduler.py (after TASK-936/937)
class QSScheduler:
    _history: RunHistory | None; _writer: HistoryWriter | None
    async def admit(self, store, slug, job_id) -> RunTicket | bool
    async def shutdown(self, app) -> None                    # line ~836
# job kwargs (registration): slug, notification_manager, owner, job_id, guard, writer[, run_as_user_id, identity_auth]
# APScheduler Job: .func, .kwargs, .id
# RunHistory.claim(*, occurrence_id, job_id, slug, kind, owner, scheduled_for, trigger, requested_by) -> RunTicket | None
# RunHistory.manual_running(job_id) -> str | None
```

### Does NOT Exist
- ~~`AsyncIOScheduler.modify_job(...).run_now`~~: APScheduler has no run-now API. Call `job.func(**kwargs)` directly.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/scheduler/scheduler.py", "action": "MODIFY"},
    {"path": "tests/scheduler/test_run_now.py", "action": "CREATE"}
  ],
  "contract_symbols": ["sym:querysource/scheduler/scheduler.py#QSScheduler"]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Add `RunInProgress` near the module's other top-level helpers.
2. Add `self._manual_tasks: set[asyncio.Task] = set()` in `__init__`.
3. Insert `run_now` after `set_job_paused_durable` (TASK-937).
4. Extend `shutdown` to cancel and await the manual tasks (bounded).

### `querysource/scheduler/scheduler.py` (MODIFY)
```python
# AFTER set_job_paused_durable (added by TASK-937; verify with grep -c 'async def set_job_paused_durable')
    async def run_now(self, job_id: str, *, user: str | None) -> RunTicket:
        """Start an on-demand run of a registered job.

        Raises:
            KeyError: unknown job in this worker.
            RunInProgress: a manual run of job_id is still running.
            RuntimeError: history unavailable (cannot record a manual run).
        """
        job = self._scheduler.get_job(job_id) if self._scheduler is not None else None
        if job is None:
            raise KeyError(job_id)
        if self._history is None:
            raise RuntimeError("history unavailable")
        running = await self._history.manual_running(job_id)
        if running:
            raise RunInProgress(running)
        kwargs = dict(job.kwargs or {})
        ticket = await self._history.claim(
            occurrence_id=uuid4().hex, job_id=job_id, slug=kwargs.get("slug"), kind=_kind_of(job_id),
            owner=kwargs.get("owner"), scheduled_for=datetime.now(timezone.utc),
            trigger="manual", requested_by=user,
        )
        # FILL IN: ticket None is impossible for a uuid _id — raise RuntimeError if it happens;
        #   async def _preclaimed(_slug, _job_id): return ticket; kwargs["guard"] = _preclaimed;
        #   kwargs["writer"] = self._writer; task = asyncio.get_running_loop().create_task(job.func(**kwargs));
        #   self._manual_tasks.add(task); task.add_done_callback(self._manual_tasks.discard); return ticket
        ...
```
```python
class RunInProgress(Exception):
    """A manual run of the job is still running."""

    def __init__(self, run_id: str) -> None:
        super().__init__(run_id)
        self.run_id = run_id
```

### FILL IN checklist
- [ ] `_preclaimed` guard + task bookkeeping.
- [ ] Shutdown: cancel + `asyncio.wait(tasks, timeout=…)`.
- [ ] Tests: manual run executes the job func with the ticket; second call → `RunInProgress`; unknown → `KeyError`; history None → `RuntimeError`.

---

## Acceptance Criteria

- [ ] `run_now` returns a `manual` ticket and the job body runs once, tracked.
- [ ] Concurrent manual run → `RunInProgress(run_id)`.
- [ ] Scheduled `admit` is unaffected by a running manual run.
- [ ] Shutdown cancels pending manual runs; they finish as `failed`.

---

## Validation Commands

- `pytest tests/scheduler/test_run_now.py -q`

---

## Agent Instructions

1. Work in the feature worktree; check TASK-937 is done.
2. Set `in-progress`; implement; run the Validation Commands.
3. Close with `scripts/sdd/close_task.sh TASK-938 scheduler-execution-history verified`.

---

## Completion Note

*(Agent fills this in when done)*
