# TASK-942: Single-execution and durable-pause integration test

**Feature**: FEAT-181 — Scheduler Execution History
**Spec**: `sdd/specs/scheduler-execution-history.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-939, TASK-940
**Assigned-to**: unassigned

---

## Context

Spec §4 Integration Tests and §5 AC. This task proves the headline guarantees end to
end:
- N ≥ 2 independent `QSScheduler` instances run one occurrence once and write one
  document;
- a durable pause set through one instance stops the other;
- with the history store hanging, jobs still run (fire-and-forget + fail-open).

---

## Scope

- Create `tests/integration/test_scheduler_single_execution.py`:
  - **Storage:** use a disposable MongoDB when available (`QS_TEST_MONGO_URL` env, e.g.
    a local `mongo` container). Otherwise fall back to a minimal in-process async fake
    collection that honours `_id` uniqueness, and **skip** the real-Mongo variants.
  - `test_single_execution_two_schedulers`: 2 schedulers, the same job with a
    `DateTrigger` a second ahead, a counter in the job body → counter == 1 and one
    `success` document.
  - `test_durable_pause_across_instances`: fake repository shared by both instances;
    `set_job_paused_durable` on A → B's next `admit` returns False.
  - `test_history_hang_does_not_block`: `claim` hangs → job body runs after ~2 s
    (fail-open); writer hangs → job finishes promptly.
- No Postgres; the repository is a fake.

**NOT in scope**: production code changes.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/integration/test_scheduler_single_execution.py` | CREATE | Multi-instance integration tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from apscheduler.triggers.date import DateTrigger                        # apscheduler 3.11.2
from querysource.scheduler.scheduler import QSScheduler                  # scheduler.py:128
from querysource.scheduler.history import RunHistory, HistoryWriter     # TASK-930/931
```

### Existing Signatures to Use
```python
# QSScheduler.admit(store, slug, job_id), set_job_paused_durable(job_id, paused, *, user, reason=None)  (TASK-936/937)
# DefinitionRepository.schedule_state / set_schedule_paused (TASK-933) — fake them in tests
# tests/test_scheduler_handler_integration.py — StubQSScheduler pattern (in-memory AsyncIOScheduler)
```

### Does NOT Exist
- ~~A shared test MongoDB in CI~~: gate real-Mongo tests on `QS_TEST_MONGO_URL` and skip otherwise.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "tests/integration/test_scheduler_single_execution.py", "action": "CREATE"}
  ],
  "contract_symbols": ["sym:querysource/scheduler/scheduler.py#QSScheduler"]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Build two `QSScheduler` instances. Set `_repository` to a shared fake and
   `_history` to a shared store (real or fake), and create `_scheduler` via
   `_create_scheduler()`. Do not call `startup()`, because it needs the app.
2. Register the same job id on both with `add_job(..., kwargs={"guard": partial(sch.admit, store), "writer": sch._writer, ...})`.
3. Start both, wait for the fire, assert.

### `tests/integration/test_scheduler_single_execution.py` (CREATE)
```python
"""FEAT-181 integration: one occurrence runs once across scheduler instances."""
import asyncio
import os
from functools import partial

import pytest

REAL_MONGO = os.environ.get("QS_TEST_MONGO_URL")

# FILL IN: FakeRunsCollection (async insert_one honouring _id uniqueness via a dict + DuplicateKeyError stand-in,
#   update_one/find_one), FakeRepository(schedule_state/set_schedule_paused over a shared dict),
#   fixture `two_schedulers` wiring both instances to the same fakes.

async def test_single_execution_two_schedulers(two_schedulers):
    # FILL IN: shared counter; DateTrigger(now + 1 s) on both; await ~2 s; assert counter == 1 and one success doc
    ...

async def test_durable_pause_across_instances(two_schedulers):
    ...

async def test_history_hang_does_not_block(two_schedulers):
    ...
```

### FILL IN checklist
- [ ] Fakes + fixture.
- [ ] Three tests; real-Mongo variants gated by `QS_TEST_MONGO_URL`.

---

## Acceptance Criteria

- [ ] All three tests pass with the in-process fake; real-Mongo variants pass when `QS_TEST_MONGO_URL` is set.
- [ ] Runtime < 30 s.

---

## Validation Commands

- `pytest tests/integration/test_scheduler_single_execution.py -q`

---

## Agent Instructions

1. Work in the feature worktree; check TASK-939 and TASK-940 are done.
2. Close with `scripts/sdd/close_task.sh TASK-942 scheduler-execution-history verified`.

---

## Completion Note

*(Agent fills this in when done)*
