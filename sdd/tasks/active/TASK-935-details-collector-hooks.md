# TASK-935: Record ExecuteSQL / Table details into the active run

**Feature**: FEAT-181 — Scheduler Execution History
**Spec**: `sdd/specs/scheduler-execution-history.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-930
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 8. During the 2026-10-17 incident the useful facts were the
`ExecuteSQL` status tags (`DELETE 55229`, `INSERT 0 55439`) and the rows `Table`
wrote. Those destinations will now report them to the active run through
`record_detail`, which is a no-op outside a tracked run.

---

## Scope

- In `ExecuteSQLDestination.run`, after `self.results = await execute_guarded(...)`,
  call `record_detail("execute_sql", list(self.results))`.
- In `TableDestination`, at both "wrote %d rows" log sites, call
  `record_detail("table", {"schema", "table", "rows", "method"})`.
- Tests: with `run_details` set, details are captured; without it, nothing breaks.

**NOT in scope**: binding `run_details` (TASK-931 `tracked_run`).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/destinations/execute_sql.py` | MODIFY | `record_detail` after results |
| `querysource/queries/multi/destinations/table.py` | MODIFY | `record_detail` at both write sites |
| `tests/scheduler/test_details_hooks.py` | CREATE | Capture / no-op tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.scheduler.history import record_detail, run_details   # created by TASK-930
```

### Existing Signatures to Use
```python
# querysource/queries/multi/destinations/execute_sql.py
class ExecuteSQLDestination(AbstractDestination):
    self.results: List[str]                                         # line 137
    # line 150: self.results = await execute_guarded(statements, timeout=self._timeout)
# querysource/queries/multi/destinations/table.py
class TableDestination(AbstractDestination):                        # line 58
    # self._schema, self._table, self._method attributes (used at lines 650-670)
    # lines 649-654 and 665-670: self.logger.info("TableDestination: wrote %d rows → %s.%s", len(df), self._schema, self._table)
```

### Does NOT Exist
- ~~A run id or ticket inside destinations~~: destinations only call `record_detail`; they never know about runs.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/queries/multi/destinations/execute_sql.py", "action": "MODIFY"},
    {"path": "querysource/queries/multi/destinations/table.py", "action": "MODIFY"},
    {"path": "tests/scheduler/test_details_hooks.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/queries/multi/destinations/execute_sql.py#ExecuteSQLDestination",
    "sym:querysource/queries/multi/destinations/table.py#TableDestination"
  ]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Import `record_detail` at module top in both destinations. *Why:* `history.py` has no
   heavy imports (Motor is lazy), so a top-level import is safe.
2. Add the calls below.

### `querysource/queries/multi/destinations/execute_sql.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '            self.results = await execute_guarded(statements, timeout=self._timeout)' querysource/queries/multi/destinations/execute_sql.py)
# AFTER line 150
            record_detail("execute_sql", list(self.results))
```

### `querysource/queries/multi/destinations/table.py` (MODIFY)
```python
# occurrences: 2 (verified: grep -c '"TableDestination: wrote %d rows → %s.%s",' querysource/queries/multi/destinations/table.py)
# FILL IN: disambiguate — the anchor appears at 650 (pandas `to_sql` path, after `await asyncio.to_thread(df.to_sql, ...)`)
#   and 666 (external `db_upsert` path, after `await engine.db_upsert(...)`). After EACH full
#   `self.logger.info(... self._table,\n)` call (ending lines 654 and 670) insert:
                record_detail("table", {"schema": self._schema, "table": self._table,
                                        "rows": len(df), "method": self._method})
```

### FILL IN checklist
- [ ] Both Table sites hooked (pandas path and db_upsert path).
- [ ] Tests: set `run_details` to `{}` with a token, call the code path with mocks, assert the captured dict; reset the token.

---

## Acceptance Criteria

- [ ] Inside a tracked run, `details.execute_sql` holds the status tags list and `details.table` holds one entry per write.
- [ ] Outside a tracked run, the destinations behave exactly as before.
- [ ] Existing destination tests still pass.

---

## Validation Commands

- `pytest tests/scheduler/test_details_hooks.py -q`

---

## Agent Instructions

1. Work in the feature worktree; check TASK-930 is done.
2. Verify both anchors before editing.
3. Set `in-progress`; implement; run the Validation Commands.
4. Close with `scripts/sdd/close_task.sh TASK-935 scheduler-execution-history verified`.

---

## Completion Note

*(Agent fills this in when done)*
