# TASK-930: DocumentDB run-history store (RunHistory, RunTicket, record_detail)

**Feature**: FEAT-181 — Scheduler Execution History
**Spec**: `sdd/specs/scheduler-execution-history.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-929
**Assigned-to**: unassigned

---

## Context

Spec §2 "Storage" and §3 Module 1. Run history lives in a DocumentDB collection
with a TTL index, never in Postgres (lead decision, 2026-10-20). This task creates
the store class:
- index bootstrap;
- the single awaited write (`claim`);
- bounded reads;
- the per-run details collector (`run_details` / `record_detail`).

The fire-and-forget writer is TASK-931.

---

## Scope

- Create `querysource/scheduler/history.py` with `RunStatus`, `RunTriggerType`,
  `RunTicket`, `RunHistory`, `run_details` and `record_detail`.
- `RunHistory.from_conf()` builds DocumentDB params like
  `interfaces/databases/mongo.py:DocumentDB` and returns `None` when
  `QS_SCHEDULER_HISTORY` is false or `motor`/`pymongo` are not importable.
- `ensure_indexes()` creates three indexes idempotently and never raises:
  - TTL on `created_at` (`expireAfterSeconds = ttl_days × 86400`);
  - `{job_id: 1, started_at: -1}`;
  - `{slug: 1, started_at: -1}`.
- `claim()` does `insert_one` with `_id = occurrence_id`, bounded by
  `claim_timeout`; it returns `None` on `DuplicateKeyError` and raises on any other
  error.
- Reads (`manual_running`, `last_runs`, `list_runs`, `get_run`) are bounded by
  `read_timeout`, and report `running` documents older than
  `QS_SCHEDULER_RUN_TIMEOUT` as `abandoned`.
- Unit tests with a fake Motor collection; no real DocumentDB.

**NOT in scope**: `HistoryWriter`, `tracked_run` (TASK-931), `scheduled_slot`
(TASK-932), any Postgres access.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/scheduler/history.py` | CREATE | RunTicket, RunHistory, run_details, record_detail |
| `tests/scheduler/test_history_store.py` | CREATE | Unit tests with a fake collection |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.conf import (                               # conf.py:251-263 (DocumentDB), TASK-929 keys
    DOCUMENTDB_HOSTNAME, DOCUMENTDB_PORT, DOCUMENTDB_USERNAME, DOCUMENTDB_PASSWORD,
    DOCUMENTDB_TLSFILE, DOCUMENTDB_USE_SSL,
    QS_SCHEDULER_HISTORY, QS_SCHEDULER_HISTORY_DATABASE, QS_SCHEDULER_HISTORY_COLLECTION,
    QS_SCHEDULER_HISTORY_TTL_DAYS, QS_SCHEDULER_CLAIM_TIMEOUT, QS_SCHEDULER_HISTORY_READ_TIMEOUT,
    QS_SCHEDULER_RUN_TIMEOUT, QS_SCHEDULER_ERROR_MAX,
)
from navconfig.logging import logging                        # pattern: querysource/scheduler/jobs.py:14
# Lazy (inside functions only — NOT installed in the local dev venv; present on prod):
import motor.motor_asyncio                                   # motor 3.7.0 (asyncdb extra "mongodb"); used by asyncdb/drivers/mongo.py:8
from pymongo.errors import DuplicateKeyError                 # pymongo 4.12.0
```

### Existing Signatures to Use
```python
# querysource/interfaces/databases/mongo.py:60-78 — DocumentDB connection params to mirror
#   {"host": DOCUMENTDB_HOSTNAME, "port": DOCUMENTDB_PORT, "username": ..., "password": ...,
#    "database": DOCUMENTDB_DATABASE, "dbtype": "documentdb", "ssl": True, "tlsCAFile": DOCUMENTDB_TLSFILE}
# querysource/queries/multi/destinations/dwh.py:244-246 — AsyncDB("mongo", params=params); conn[db][collection]
```

### Does NOT Exist
- ~~Any Postgres table for history (`troc.qs_scheduler_runs`)~~: forbidden by the spec. Do not create one.
- ~~`pymongo`/`motor` importable at module import time in dev~~: import lazily, and make `from_conf()` return `None` on `ImportError`.
- ~~`HistoryWriter`, `tracked_run`, `scheduled_slot`, `occurrence_id`~~: created by TASK-931 / TASK-932.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/scheduler/history.py", "action": "CREATE"},
    {"path": "tests/scheduler/test_history_store.py", "action": "CREATE"}
  ],
  "contract_symbols": []
}
```

---

## Implementation Notes

### Key Constraints
- Use one `motor.motor_asyncio.AsyncIOMotorClient` per `RunHistory`, created lazily
  on first use, with `serverSelectionTimeoutMS` ≈ `claim_timeout × 1000` so a dead
  cluster fails fast. Use Motor directly, not `AsyncDB("mongo")`, because the claim
  needs `insert_one` + `DuplicateKeyError` semantics and a long-lived client.
- All datetimes are tz-aware UTC. `created_at` is the TTL anchor.
- `error` is truncated to `QS_SCHEDULER_ERROR_MAX` characters.
- Reads never raise to callers on a timeout: they raise `HistoryUnavailable`, which
  the HTTP layer maps to 503.

---

## Implementation Blueprint

### Steps (in order)
1. Create `history.py` with the block below. *Why:* it fixes the names every later
   task imports.
2. Implement each `FILL IN`. *Why:* the Motor calls and filters are the
   judgement-bearing part.
3. Write the tests against a fake collection object exposing async `insert_one`,
   `find`, `create_index` and `aggregate`. *Why:* there is no DocumentDB in CI.

### `querysource/scheduler/history.py` (CREATE)
```python
"""QSScheduler run history on DocumentDB (FEAT-181).

Logs are not relational data: runs live in a TTL-indexed DocumentDB collection.
Only ``RunHistory.claim`` is ever awaited by job code; every other write goes
through the fire-and-forget ``HistoryWriter`` (TASK-931).
"""
from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from navconfig.logging import logging

from querysource.conf import (
    DOCUMENTDB_HOSTNAME, DOCUMENTDB_PORT, DOCUMENTDB_USERNAME, DOCUMENTDB_PASSWORD,
    DOCUMENTDB_TLSFILE, DOCUMENTDB_USE_SSL,
    QS_SCHEDULER_HISTORY, QS_SCHEDULER_HISTORY_DATABASE, QS_SCHEDULER_HISTORY_COLLECTION,
    QS_SCHEDULER_HISTORY_TTL_DAYS, QS_SCHEDULER_CLAIM_TIMEOUT, QS_SCHEDULER_HISTORY_READ_TIMEOUT,
    QS_SCHEDULER_RUN_TIMEOUT, QS_SCHEDULER_ERROR_MAX,
)

logger = logging.getLogger("QSScheduler.History")

RunStatus = Literal["running", "success", "failed", "missed", "skipped", "abandoned"]
RunTriggerType = Literal["scheduled", "manual"]

run_details: ContextVar[dict[str, Any] | None] = ContextVar("qs_run_details", default=None)


class HistoryUnavailable(RuntimeError):
    """History store disabled, unreachable or too slow for a bounded read."""


@dataclass(frozen=True, slots=True)
class RunTicket:
    """An admitted run: the claim document exists in the history store."""

    run_id: str
    job_id: str
    slug: str
    kind: str
    scheduled_for: datetime
    trigger: RunTriggerType
    started_at: datetime


def record_detail(key: str, value: Any) -> None:
    """Append ``value`` under ``key`` in the active run's details; no-op outside a tracked run."""
    details = run_details.get()
    if details is None:
        return
    details.setdefault(key, []).append(value)


def truncate_error(exc: BaseException) -> str:
    """Return ``"<ExcType>: <message>"`` truncated to QS_SCHEDULER_ERROR_MAX."""
    return f"{type(exc).__name__}: {exc}"[:QS_SCHEDULER_ERROR_MAX]
```
**Why this shape**: these names are fixed by spec §3 M1. `record_detail` appends to
lists because one multi can run several `ExecuteSQL`/`Table` steps.

```python
# (continued in the same file)
class RunHistory:
    """DocumentDB-backed run history (TTL-retained). Only ``claim`` is awaited by jobs."""

    def __init__(self, params: Mapping[str, Any], *, database: str, collection: str = "qs_scheduler_runs",
                 ttl_days: int = 90, claim_timeout: float = 2.0, read_timeout: float = 3.0) -> None:
        self._params = dict(params)
        self._database = database
        self._collection_name = collection
        self._ttl_days = ttl_days
        self.claim_timeout = claim_timeout
        self.read_timeout = read_timeout
        self._client: Any = None
        self.host = socket.gethostname()
        self.pid = os.getpid()

    @classmethod
    def from_conf(cls) -> RunHistory | None:
        """Build from config; None when disabled or motor/pymongo are missing (logs one WARNING)."""
        # FILL IN: return None if not QS_SCHEDULER_HISTORY; try `import motor.motor_asyncio` and
        #   `import pymongo` → on ImportError log WARNING "history disabled: install querysource[scheduler-history]"
        #   and return None; else build params mirroring interfaces/databases/mongo.py:67-78 — bounded by spec §2 Storage
        raise NotImplementedError

    def _collection(self) -> Any:
        """Lazily create the Motor client and return the runs collection."""
        # FILL IN: AsyncIOMotorClient(host, port, username, password, tls=DOCUMENTDB_USE_SSL,
        #   tlsCAFile=str(DOCUMENTDB_TLSFILE), retryWrites=False, serverSelectionTimeoutMS=int(claim_timeout*1000))
        #   — DocumentDB requires retryWrites=False
        raise NotImplementedError

    async def ensure_indexes(self) -> bool:
        """Create TTL + read indexes idempotently; False on failure. Never raises."""
        # FILL IN: create_index([("created_at", 1)], expireAfterSeconds=ttl_days*86400, name="ttl_created_at");
        #   create_index([("job_id", 1), ("started_at", -1)]); create_index([("slug", 1), ("started_at", -1)])
        raise NotImplementedError

    async def claim(self, *, occurrence_id: str, job_id: str, slug: str, kind: str,
                    owner: Mapping[str, Any] | None, scheduled_for: datetime,
                    trigger: RunTriggerType = "scheduled", requested_by: str | None = None) -> RunTicket | None:
        """insert_one with _id=occurrence_id, bounded by claim_timeout.

        Returns None on DuplicateKeyError (another worker owns it); raises on any other error/timeout.
        """
        # FILL IN: build the spec §2 document (status "running", started_at/created_at=now UTC, host, pid,
        #   store={"schema","table"} from owner); asyncio.wait_for(insert_one(doc), self.claim_timeout);
        #   except DuplicateKeyError → None; return RunTicket(...)
        raise NotImplementedError
```
**Why**: the claim is the only awaited write (spec §2). Callers apply fail-open, so it
must raise rather than hide errors. `retryWrites=False` is mandatory on DocumentDB.

```python
# (continued) bounded reads
    async def manual_running(self, job_id: str) -> str | None:
        """run_id of a still-running manual run of job_id (not older than RUN_TIMEOUT), else None."""
        raise NotImplementedError  # FILL IN: find_one({"job_id", "trigger": "manual", "status": "running", "started_at" >= cutoff})

    async def last_runs(self, job_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        """Latest run per job_id (aggregate $match/$sort/$group), bounded by read_timeout."""
        raise NotImplementedError  # FILL IN

    async def list_runs(self, *, job_id: str | None = None, slug: str | None = None, status: str | None = None,
                        since: datetime | None = None, until: datetime | None = None,
                        limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        """Newest-first page + total; limit capped at 200."""
        raise NotImplementedError  # FILL IN

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        raise NotImplementedError  # FILL IN

    @staticmethod
    def _present(doc: Mapping[str, Any]) -> dict[str, Any]:
        """Serialize a document for the API; report stale `running` as `abandoned`."""
        # FILL IN: run_id=_id; datetimes → isoformat; status "running" with started_at older than
        #   timedelta(seconds=QS_SCHEDULER_RUN_TIMEOUT) → "abandoned" (computed, no write)
        raise NotImplementedError
```
**Why**: every read raises `HistoryUnavailable` on timeout or error, which the HTTP
layer maps to 503 (spec §2). "Abandoned" is computed at read time, with no write
(spec §2 "Abandoned").

### FILL IN checklist
- [ ] `from_conf`: switch + lazy imports + params; bounded by spec §2 Storage.
- [ ] `_collection`: Motor client options incl. `retryWrites=False`.
- [ ] `ensure_indexes`: 3 indexes, idempotent, never raises.
- [ ] `claim`: document shape (spec §2 Data Models), timeout, `DuplicateKeyError` → None.
- [ ] reads: filters, cap 200, timeout → `HistoryUnavailable`, `_present` abandoned mapping.
- [ ] tests (see below).

---

## Acceptance Criteria

- [ ] `from querysource.scheduler.history import RunHistory, RunTicket, record_detail, run_details, HistoryUnavailable` works without motor installed.
- [ ] `from_conf()` returns `None` (one WARNING) when motor/pymongo are missing.
- [ ] Duplicate claim → `None`; other errors raise; claim bounded by `claim_timeout`.
- [ ] TTL index uses `expireAfterSeconds = ttl_days × 86400`.
- [ ] No Postgres access anywhere in the module.
- [ ] `ruff check querysource/scheduler/history.py` clean; tests pass.

---

## Validation Commands

- `pytest tests/scheduler/test_history_store.py -q`

---

## Test Specification

```python
# tests/scheduler/test_history_store.py
import pytest
from querysource.scheduler.history import RunHistory, record_detail, run_details

# FILL IN: FakeCollection with async insert_one (raise a DuplicateKeyError stand-in on repeated _id;
#   patch the module's DuplicateKeyError reference), find/aggregate/create_index recording calls.

def test_record_detail_noop_outside_run():
    record_detail("execute_sql", ["X"])  # must not raise
    assert run_details.get() is None

# test_claim_duplicate_returns_none, test_claim_timeout_raises, test_ensure_indexes_ttl,
# test_history_disabled_without_motor (monkeypatch builtins __import__ / sys.modules), test_present_abandoned
```

---

## Agent Instructions

1. Work in the feature worktree (`python -m scripts.sdd.ensure_worktree --slug scheduler-execution-history --feature-id FEAT-181`).
2. Check `Depends-on` (TASK-929) is done in `sdd/tasks/index/scheduler-execution-history.json`.
3. Verify the Codebase Contract first.
4. Set `in-progress` in the index (commit only the index).
5. Implement from the blueprint; complete every `# FILL IN:`.
6. Run the Validation Commands; commit only this task's files.
7. Close with `scripts/sdd/close_task.sh TASK-930 scheduler-execution-history verified`; fill the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*
