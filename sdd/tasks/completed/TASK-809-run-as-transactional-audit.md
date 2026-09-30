# TASK-809: Run-as assignment + append-only audit in the same transaction

**Feature**: FEAT-159 — OneDrive Source for MultiQS
**Spec**: `sdd/specs/onedrive-multiqs-source.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-808
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 6, part two (G5, S6). This module is **not
delegation-eligible**: it changes tenant write semantics. `patch()` and
`upsert()` gain `run_as_actor` and `request_info`. When a write creates,
changes or removes `attributes.scheduler`, the run-as column is set, changed
or cleared, and **one** audit row is written in the **same transaction** as the
definition write. Edits that leave the schedule alone touch neither the column
nor the audit table.

---

## Scope

- Add the `RunAsChange` frozen dataclass and `_run_as_audit_table(store)`,
  which returns `{schema}.{table}_run_as_audit`, quoted.
- Add `_apply_run_as(conn, store, slug, previous, current, actor, request_info) -> RunAsChange | None`.
- Restructure `patch()` and `upsert()` to run inside one connection
  transaction. Lock and read the previous `attributes` (`SELECT attributes
  FROM … WHERE query_slug=$1 FOR UPDATE`), perform the existing write, call
  `_apply_run_as`, then commit. Roll back on any error. Keep all existing
  return values and error translation.
- Write unit tests with a transaction-aware mock connection.

**NOT in scope**: handlers passing `run_as_actor` (TASK-811) and the DDL (TASK-810).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/repositories/definitions.py` | MODIFY | transactional run-as + audit |
| `tests/tenants/test_run_as_repository_audit.py` | CREATE | unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.repositories.definitions import RUN_AS_COLUMN, _is_missing_run_as_column   # TASK-808
from querysource.tenants import quote_identifier, QueryIdentity, QueryStore                  # verified import
import json                                                                                    # request_info → jsonb
```

### Existing Signatures to Use
```python
# asyncdb.drivers.pg.pg (the object yielded by connection_factory):
async def transaction(self): ...   # starts self._connection.transaction(); returns self   (pg.py:1069)
async def commit(self): ...        # pg.py:1076
async def rollback(self): ...      # pg.py:1083
async def fetch_one(self, sentence, *args) # raises on error (the repository relies on this for writes)
async def execute(self, sentence, *args)   # returns (result, error) tuple — DO NOT use for writes here
# querysource/repositories/definitions.py
async def upsert(self, identity, data) -> tuple[Mapping, bool]   # :331-397 (single `async with await self.connection_factory() as conn: row = await conn.fetch_one(sql, *values)` :381-383)
async def patch(self, identity, data) -> Mapping                 # :399-474 (fetch_one inside connection :459-461)
```

### Does NOT Exist
- ~~`DefinitionRepository.transaction()` helper~~: write the begin/commit/rollback inline around the existing `async with` body.
- ~~Audit rows for non-schedule edits~~: forbidden.
- ~~`conn.execute` for the audit insert~~: use `fetch_one("INSERT … RETURNING audit_id", …)` so failures raise.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/repositories/definitions.py", "action": "MODIFY"},
    {"path": "tests/tenants/test_run_as_repository_audit.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/repositories/definitions.py#DefinitionRepository.upsert",
    "sym:querysource/repositories/definitions.py#DefinitionRepository.patch"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Decision table (`prev = (previous_attributes or {}).get("scheduler")`, `cur = (current_attributes or {}).get("scheduler")`):
  - `prev is None and cur` + actor: operation `set` (or `change` if the column was already non-null)
  - `prev and cur and prev != cur` + actor: operation `change` (skip when the stored value already equals actor → `None`)
  - `prev and not cur` + actor: operation `clear` (column → NULL, audit `new_user_id` NULL)
  - any change with **no actor** (a sessionless caller): leave the column untouched, write no audit row, and log a WARNING.
    This is required because `changed_by` is `NOT NULL` and an anonymous identity change must never happen.
  - otherwise: `None`
- `current_attributes` comes from the **RETURNING** row of the write, not the payload.
- Un-migrated store (a missing column or audit table raised by the SELECT or
  UPDATE): log a WARNING and return `None` **without failing the definition
  write**. Use a SAVEPOINT (`conn.fetch_one("SAVEPOINT qs_run_as")` / `ROLLBACK
  TO SAVEPOINT`) around the run-as statements so the outer transaction survives.
- The audit insert columns are `query_slug, old_user_id, new_user_id,
  operation, changed_by, request_info` (see the spec §2 DDL).
- The new keyword-only params default to `None`, so every existing caller and
  test is unaffected.

---

## Implementation Blueprint

### Steps (in order)
1. Add `RunAsChange` and `_run_as_audit_table` — *why*: fixed names from spec §3 M6.
2. Implement `_apply_run_as` following the decision table — *why*: S6.
3. Wrap the `patch`/`upsert` bodies in the transaction and call `_apply_run_as` after the write.
4. Write the tests with a mock conn that records `transaction`/`commit`/`rollback` and SQL.

### `querysource/repositories/definitions.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '    async def delete(self, identity: QueryIdentity) -> bool:' …) — :476
# BEFORE — insert above it (after TASK-808's get_run_as):
    def _run_as_audit_table(self, store: QueryStore) -> str:
        """Quoted {schema}.{table}_run_as_audit for this store."""
        return f"{quote_identifier(store.schema)}.{quote_identifier(f'{store.table}_run_as_audit')}"

    async def _apply_run_as(self, conn: Any, store: QueryStore, slug: str, previous: Any,
                            current: Any, actor: int | None,
                            request_info: Mapping[str, Any] | None) -> "RunAsChange | None":
        """Set/change/clear run-as + one audit row inside the caller's transaction."""
        # FILL IN: decision table (Key Constraints); SAVEPOINT qs_run_as; SELECT current RUN_AS_COLUMN;
        #          UPDATE … SET RUN_AS_COLUMN; INSERT audit … RETURNING audit_id; RELEASE SAVEPOINT;
        #          missing column/table → ROLLBACK TO SAVEPOINT, WARNING, return None — bounded by AC "un-migrated store"
# and at module level (below RUN_AS_COLUMN):
@dataclass(frozen=True)
class RunAsChange:
    query_slug: str
    old_user_id: int | None
    new_user_id: int | None
    operation: str  # "set" | "change" | "clear"
```
Signature changes, which are fixed by the spec skeleton:
```python
    async def upsert(self, identity: QueryIdentity, data: Mapping[str, Any], *,
                     run_as_actor: int | None = None,
                     request_info: Mapping[str, Any] | None = None) -> tuple[Mapping[str, Any], bool]:
    async def patch(self, identity: QueryIdentity, data: Mapping[str, Any], *,
                    run_as_actor: int | None = None,
                    request_info: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
# body: `async with await self.connection_factory() as conn:` → await conn.transaction(); try:
#   previous = SELECT attributes … FOR UPDATE (None for a new row) ; row = <existing write> ;
#   await self._apply_run_as(conn, store, slug, previous, row["attributes"], run_as_actor, request_info)
#   await conn.commit()  except: await conn.rollback(); raise
# FILL IN: keep the existing `_translate_write_error` handling around the whole block
```
**Why**: one transaction covers the definition row, the column and the audit
row (S6). The savepoint keeps un-migrated stores writable.

### FILL IN checklist
- [ ] `_apply_run_as` decision table + savepoint + un-migrated tolerance
- [ ] `upsert` transactional restructure (return value unchanged)
- [ ] `patch` transactional restructure (no-op path unchanged)

---

## Acceptance Criteria

- [ ] created → `set`, changed → `change`, removed → `clear`, with exactly one audit INSERT each, in the same transaction.
- [ ] A `description`-only edit issues no run-as UPDATE and no audit INSERT.
- [ ] An audit INSERT failure rolls back the definition write (`rollback` awaited, `commit` not).
- [ ] An un-migrated store: the definition write still commits, and a WARNING is logged.
- [ ] Existing write tests pass unchanged.

## Validation Commands

- `pytest tests/tenants/test_run_as_repository_audit.py -q`
- `pytest tests/tenants/test_tenant_repository_writes.py -q`

---

## Test Specification

```python
# tests/tenants/test_run_as_repository_audit.py
import pytest


class _TxConn:
    """Records SQL; supports transaction/commit/rollback and scripted fetch_one results."""
    ...  # FILL IN


async def test_run_as_set_change_clear_audited():
    ...  # FILL IN: three scenarios, assert UPDATE + INSERT audit + commit order


async def test_run_as_untouched_on_non_schedule_edit():
    ...  # FILL IN


async def test_audit_failure_rolls_back_definition():
    ...  # FILL IN


async def test_unmigrated_store_still_commits():
    ...  # FILL IN
```

---

## Agent Instructions

1. Work in the feature worktree (`python -m scripts.sdd.ensure_worktree --slug onedrive-multiqs-source --feature-id FEAT-159`).
2. Read the spec. Check that every `Depends-on` task is `"done"` in `sdd/tasks/index/onedrive-multiqs-source.json`.
3. Verify the Codebase Contract before writing code.
4. Set the task to `"in-progress"` in the index (with `started_at`), then implement from the blueprint and complete every `# FILL IN:`.
5. Run the Validation Commands. Commit only the listed files.
6. Close with `scripts/sdd/close_task.sh TASK-809 onedrive-multiqs-source verified`, then fill in the Completion Note.

---

## Completion Note

Seat: sonnet (native) · Attempts: 1. Transactional run-as audit. Deviations: lowercase 'for update'; getattr for transaction/commit/rollback to keep existing mock-based tests passing. Merge-tier: 33 passed.
