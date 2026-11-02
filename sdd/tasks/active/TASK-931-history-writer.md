# TASK-931: Fire-and-forget HistoryWriter and tracked_run

**Feature**: FEAT-181 — Scheduler Execution History
**Spec**: `sdd/specs/scheduler-execution-history.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-930
**Assigned-to**: unassigned

---

## Context

Spec §2 "Fire-and-forget" and §3 Modules 1/3. The lead requires that recording never
affects code execution. Every history write except the claim goes through a bounded
per-worker queue drained by one background task. `tracked_run` wraps a job body,
binds `run_details`, and enqueues the finish update without awaiting it.

---

## Scope

- Add `HistoryWriter` to `querysource/scheduler/history.py`:
  - a bounded `asyncio.Queue` and one drain task;
  - `finish`, `skipped` and `abandon_local` are **sync** enqueue methods;
  - `start()` and `drain(timeout)`;
  - a `dropped` counter with a rate-limited WARNING.
- Add `tracked_run(admission, writer)`, an async context manager:
  - it is a no-op unless `admission` is a `RunTicket`;
  - on success it enqueues `finish(success)`; on exception it enqueues
    `finish(failed, error)` and **re-raises**.
- Add the corresponding `RunHistory` write methods used by the drain task:
  `_apply_finish`, `_apply_skipped` (insert-if-absent), `_apply_abandon_local`.
- Tests proving the writer never blocks or raises.

**NOT in scope**: wiring into jobs/scheduler (TASK-936/937), slot helpers (TASK-932).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/scheduler/history.py` | MODIFY | Append HistoryWriter, tracked_run, RunHistory write helpers |
| `tests/scheduler/test_history_writer.py` | CREATE | Never-blocks / errors-swallowed / drain tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.scheduler.history import RunHistory, RunTicket, RunStatus, run_details, truncate_error, logger  # created by TASK-930
from querysource.conf import QS_SCHEDULER_HISTORY_QUEUE, QS_SCHEDULER_HISTORY_DRAIN_TIMEOUT                      # TASK-929
from contextlib import asynccontextmanager       # stdlib
```

### Existing Signatures to Use
```python
# querysource/scheduler/history.py (TASK-930)
class RunHistory:
    host: str; pid: int
    def _collection(self) -> Any
@dataclass(frozen=True, slots=True)
class RunTicket: run_id, job_id, slug, kind, scheduled_for, trigger, started_at
```

### Does NOT Exist
- ~~`asyncio.create_task` per write~~: the spec forbids one task per write. Use the single drain task.
- ~~An awaited write inside `tracked_run`~~: `finish` must be an enqueue only.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/scheduler/history.py", "action": "MODIFY"},
    {"path": "tests/scheduler/test_history_writer.py", "action": "CREATE"}
  ],
  "contract_symbols": []
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Append the blocks below to the end of `history.py`. *Why:* `HistoryWriter` depends
   on `RunHistory`, which is defined above.
2. Implement the drain loop. Each event is a `(kind, payload)` tuple applied by the
   matching `RunHistory._apply_*` coroutine with its own short timeout; exceptions
   are logged and swallowed. *Why:* the lead's fire-and-forget requirement.
3. Write tests with a `RunHistory` stub whose `_apply_*` methods hang or raise.

### `querysource/scheduler/history.py` (MODIFY)
```python
# occurrences: 1 (verified after TASK-930: grep -c 'def truncate_error' querysource/scheduler/history.py)
# AFTER — append at end of file
class HistoryWriter:
    """Per-worker bounded queue + one background drain task. Callers never await a write."""

    def __init__(self, history: RunHistory, *, maxsize: int = QS_SCHEDULER_HISTORY_QUEUE) -> None:
        self._history = history
        self._queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(maxsize=maxsize)
        self._task: asyncio.Task | None = None
        self.dropped = 0

    def start(self) -> None:
        """Start the drain task on the running loop (idempotent)."""
        # FILL IN: create the task once via asyncio.get_running_loop().create_task(self._run())
        raise NotImplementedError

    def _put(self, kind: str, payload: dict[str, Any]) -> None:
        """Enqueue without blocking; drop + count when full; never raises."""
        try:
            self._queue.put_nowait((kind, payload))
        except asyncio.QueueFull:
            self.dropped += 1
            # FILL IN: rate-limited logger.warning (e.g. on 1st and every 100th drop)

    def finish(self, ticket: RunTicket, *, status: RunStatus, error: str | None,
               details: Mapping[str, Any]) -> None:
        """Enqueue the terminal update for ``ticket``."""
        self._put("finish", {"ticket": ticket, "status": status, "error": error, "details": dict(details),
                             "finished_at": datetime.now(timezone.utc)})

    def skipped(self, *, occurrence_id: str, job_id: str, slug: str, kind: str,
                owner: Mapping[str, Any] | None, scheduled_for: datetime,
                status: Literal["missed", "skipped"]) -> None:
        """Enqueue an insert-if-absent terminal document."""
        self._put("skipped", {"occurrence_id": occurrence_id, "job_id": job_id, "slug": slug, "kind": kind,
                              "owner": owner, "scheduled_for": scheduled_for, "status": status})

    def abandon_local(self, host: str, dead_pids: Sequence[int]) -> None:
        """Enqueue: mark this host's `running` docs with dead pids as abandoned."""
        self._put("abandon_local", {"host": host, "dead_pids": list(dead_pids)})

    async def _run(self) -> None:
        """Drain forever; every apply is bounded and its errors are logged, never raised."""
        # FILL IN: loop get() → dispatch to self._history._apply_<kind>(**payload) inside
        #   asyncio.wait_for(..., self._history.read_timeout); except Exception → logger.error; task_done()
        raise NotImplementedError

    async def drain(self, timeout: float = QS_SCHEDULER_HISTORY_DRAIN_TIMEOUT) -> None:
        """Flush pending writes on shutdown, bounded by timeout; then cancel the task. Never raises."""
        # FILL IN: wait_for(self._queue.join(), timeout) swallowing TimeoutError; cancel self._task
        raise NotImplementedError
```
**Why**: spec §3 M1 skeleton. The sync `_put` is what makes callers fire-and-forget,
and a single task keeps memory bounded and per-run ordering.

```python
# (continued) tracked_run
@asynccontextmanager
async def tracked_run(admission: RunTicket | bool, writer: HistoryWriter | None):
    """No-op unless admission is a RunTicket; binds run_details; enqueues finish; re-raises."""
    if not isinstance(admission, RunTicket) or writer is None:
        yield
        return
    token = run_details.set({})
    try:
        yield
    except BaseException as exc:
        writer.finish(admission, status="failed", error=truncate_error(exc), details=run_details.get() or {})
        raise
    else:
        writer.finish(admission, status="success", error=None, details=run_details.get() or {})
    finally:
        run_details.reset(token)
```
**Why**: re-raising keeps the jobs' existing `except` path (log + `notify`) firing
exactly once (spec §7 "Notify contract").

Add to `RunHistory` (inside the class, after `get_run`):
```python
    async def _apply_finish(self, ticket: RunTicket, status: str, error: str | None,
                            details: dict[str, Any], finished_at: datetime) -> None:
        # FILL IN: update_one({"_id": ticket.run_id}, {"$set": {status, error, details, finished_at,
        #   duration_ms=int((finished_at - ticket.started_at).total_seconds()*1000)}})
        raise NotImplementedError

    async def _apply_skipped(self, **payload: Any) -> None:
        # FILL IN: update_one({"_id": occurrence_id}, {"$setOnInsert": {...doc, created_at=now}}, upsert=True)
        #   — insert-if-absent so several workers' listeners collapse to one document
        raise NotImplementedError

    async def _apply_abandon_local(self, host: str, dead_pids: list[int]) -> None:
        # FILL IN: update_many({"host": host, "pid": {"$in": dead_pids}, "status": "running"},
        #   {"$set": {"status": "abandoned", "finished_at": now}})
        raise NotImplementedError
```

### FILL IN checklist
- [ ] `start` / `_run` / `drain`: bounded, never raising.
- [ ] Rate-limited drop warning.
- [ ] `_apply_*` Motor updates (insert-if-absent for skipped).
- [ ] Tests below.

---

## Acceptance Criteria

- [ ] `writer.finish(...)` returns immediately even when the store hangs (spec AC "Fire-and-forget").
- [ ] Store errors never propagate out of the writer.
- [ ] Queue full → event dropped, `dropped` incremented, WARNING logged (rate-limited).
- [ ] `tracked_run` re-raises the body's exception and enqueues exactly one finish.
- [ ] `drain` respects its timeout.

---

## Validation Commands

- `pytest tests/scheduler/test_history_writer.py -q`

---

## Test Specification

```python
# tests/scheduler/test_history_writer.py
# test_writer_never_blocks: _apply_finish sleeps 10 s → writer.finish() returns < 10 ms
# test_writer_errors_swallowed: _apply_finish raises → no exception, error logged
# test_queue_full_drops: maxsize=1, enqueue 3 → dropped == 2
# test_tracked_run_success_and_failure: success enqueues "success"; raising body re-raises + enqueues "failed"
# test_tracked_run_noop_for_bool: admission True → no enqueue
# test_drain_timeout: hanging store → drain(0.1) returns within ~0.2 s
```

---

## Agent Instructions

1. Work in the feature worktree; check TASK-930 is done in the index.
2. Verify the Codebase Contract (TASK-930's names exist in `history.py`).
3. Set `in-progress`; implement; complete every `# FILL IN:`.
4. Run the Validation Commands; commit only this task's files.
5. Close with `scripts/sdd/close_task.sh TASK-931 scheduler-execution-history verified`.

---

## Completion Note

*(Agent fills this in when done)*
