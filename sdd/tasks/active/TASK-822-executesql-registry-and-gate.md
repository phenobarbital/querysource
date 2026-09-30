# TASK-822: Register `"ExecuteSQL"` and add it to the PBAC write gate

**Feature**: FEAT-156 — MultiQuery ExecuteSQL Destination
**Spec**: `sdd/specs/multi-executesql.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-821
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 4. Makes the `ExecuteSQL` Output step dispatchable (`get_destination("ExecuteSQL")`)
and puts it behind the same up-front PBAC gate as `TableDelete`: a MultiQuery whose `Output`
contains `ExecuteSQL` requires `datasource:use` on `pg_admin` before any child query runs.

> **CROSS-FEATURE BLOCKER — do not start this task until FEAT-155 (`multi-tabledelete`) is
> merged into `dev` and the FEAT-156 feature branch is rebased onto it.** Both anchors of this
> task — the `"TableDelete"` registry block in `querysource/outputs/destinations/__init__.py` and
> `WRITE_DESTINATIONS` in `querysource/queries/multi/__init__.py` — are introduced by FEAT-155
> (TASK-816 and TASK-817). They do **not** exist on `dev` today (`grep -c` = 0). FEAT-155 is in a
> different per-spec index, so it is not listed in `Depends-on`; the orchestrator must hold this
> task until the merge. If the anchors are still missing when you start, STOP and report — never
> invent `WRITE_DESTINATIONS` or the gate yourself.

---

## Scope

- `querysource/outputs/destinations/__init__.py`: new `try/except ImportError` block registering
  `DESTINATION_REGISTRY["ExecuteSQL"] = ExecuteSQLDestination`, right after the FEAT-155 `"TableDelete"` block.
- `querysource/queries/multi/__init__.py`: extend `WRITE_DESTINATIONS` to `frozenset({"TableDelete", "ExecuteSQL"})`.
- Write `tests/test_destination_execute_sql_registry.py` (`test_registry_has_executesql`, `test_preflight_gate_executesql`).

**NOT in scope**:
- The gate logic itself (`_output_step_names`, the `enforce_principal` call in `_preflight_principal`) — FEAT-155 TASK-817.
- Any change to `ExecuteSQLDestination` (TASK-821) or `guarded_sql.py` (TASK-820).
- Gating `Table` (spec FEAT-155 Open Question: not gated).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/outputs/destinations/__init__.py` | MODIFY | register `"ExecuteSQL"` after the `"TableDelete"` block |
| `querysource/queries/multi/__init__.py` | MODIFY | add `"ExecuteSQL"` to `WRITE_DESTINATIONS` |
| `tests/test_destination_execute_sql_registry.py` | CREATE | registry + preflight gate tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.queries.multi.destinations.execute_sql import ExecuteSQLDestination  # created by TASK-821
from querysource.outputs.destinations import DESTINATION_REGISTRY, get_destination    # verified: querysource/outputs/destinations/__init__.py:196,236
from querysource.queries.multi import MultiQS                                          # verified: tests/multi/test_multiqs_principal.py:9
from querysource.queries.multi import WRITE_DESTINATIONS                               # created by FEAT-155 TASK-817 (after merge)
from querysource.auth._resource_types import ResourceType                              # verified: querysource/auth/_resource_types.py:43 (DATASOURCE)
from querysource.auth.principal import QSPrincipal                                     # verified: tests/multi/test_multiqs_principal.py:7
import querysource.auth.enforcement as enforcement                                     # verified: querysource/auth/enforcement.py:22 (AccessDecision), :134 (enforce_principal)
from querysource.exceptions import QueryAccessDenied                                   # verified: querysource/exceptions.py:63
```

### Existing Signatures to Use
```python
# querysource/outputs/destinations/__init__.py (dev today)
DESTINATION_REGISTRY: dict[str, type[AbstractDestination]] = {...}     # line 196
# try/except blocks: ToSharepoint :203-209, ToS3 :211-217, Table :219-225, DWH :227-233
# after FEAT-155 TASK-816 (its blueprint, sdd/tasks/active/TASK-816-table-delete-registry.md:121-127), right after the "Table" block:
#   try:
#       from querysource.queries.multi.destinations.table_delete import TableDeleteDestination
#       DESTINATION_REGISTRY["TableDelete"] = TableDeleteDestination
#   except ImportError:
#       _pkg_logger.debug("TableDelete destination not available")
def get_destination(step_name: str) -> type[AbstractDestination]:      # line 236 (raises OutputError if unknown)

# querysource/queries/multi/__init__.py (dev today)
class MultiQS(BaseQuery):
    def __init__(self, slug=None, queries=None, files=None, query: dict | None = None, ..., *,
                 tenant=None, definition=None, principal: "QSPrincipal | None" = None, **kwargs)  # line 107-121
    # query= dict: pops 'queries'/'files'/'sources'; the rest (incl. "Output") stays in self._options  (line 141-150)
    async def _preflight_principal(self) -> None:                       # line 240 — no-op when principal is None
# after FEAT-155 TASK-817 (its blueprint, sdd/tasks/active/TASK-817-write-destinations-pbac-gate.md:154, :184-188):
WRITE_DESTINATIONS: frozenset[str] = frozenset({"TableDelete"})          # module level
#   _preflight_principal: if any Output step name ∈ WRITE_DESTINATIONS →
#   await enforce_principal(self._principal, ResourceType.DATASOURCE, "pg_admin", "datasource:use", tenant=…, logger=…)

# querysource/auth/enforcement.py
class AccessDecision: allowed: bool; pbac_enabled: bool                 # line 22-26
async def enforce_principal(principal, resource_type, resource_name, action, *, tenant=None, logger=None)  # line 134
```

### Does NOT Exist
- ~~`WRITE_DESTINATIONS`~~ and ~~`DESTINATION_REGISTRY["TableDelete"]`~~ on `dev` today — FEAT-155 creates them (verify after merge).
- ~~`ResourceType.DESTINATION` / `ResourceType.OUTPUT`~~ — only SLUG, DATASOURCE, DRIVER, RAW_QUERY.
- ~~`DESTINATION_REGISTRY["ExecuteSQL"]`~~ — added by this task (the folder-scanned registry in
  `querysource/queries/multi/destinations/__init__.py` only knows the class name `ExecuteSQLDestination`).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/outputs/destinations/__init__.py", "action": "MODIFY"},
    {"path": "querysource/queries/multi/__init__.py", "action": "MODIFY"},
    {"path": "tests/test_destination_execute_sql_registry.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/outputs/destinations/__init__.py#DESTINATION_REGISTRY",
    "sym:querysource/outputs/destinations/__init__.py#get_destination",
    "sym:querysource/queries/multi/__init__.py#MultiQS._preflight_principal",
    "sym:querysource/auth/enforcement.py#enforce_principal",
    "sym:querysource/auth/enforcement.py#AccessDecision"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Rebase first; then re-run both `grep -c` anchor checks — each must print `1`. If FEAT-155 shaped
  the `TableDelete` block differently (e.g. multi-line `debug(...)`), insert after **its whole
  `except ImportError:` body** and before `def get_destination(` (verified today: 1 occurrence).
- Keep the registry block in the same style as the neighbours (`try` / assignment / `except ImportError` / `_pkg_logger.debug`).
- One-token change in `WRITE_DESTINATIONS`. TASK-817's blueprint puts the comment `# (FEAT-091) at pre-flight. FEAT-156 adds "ExecuteSQL".` on the line above it; reword it to `… FEAT-156 added "ExecuteSQL".` if present (no other change).

---

## Implementation Blueprint

### Steps (in order)
1. Confirm FEAT-155 is merged into `dev` and rebase the feature branch — *why*: both anchors come from FEAT-155.
2. Add the registry block — *why*: `get_destination("ExecuteSQL")` must resolve in the MultiQS Output loop (`queries/multi/__init__.py:846`).
3. Extend `WRITE_DESTINATIONS` — *why*: spec AC — a principal without `datasource:use` on `pg_admin` is denied up-front.
4. Write the tests, run the Validation Commands and `ruff check` on the three files — *why*: pins both integrations.

### `querysource/outputs/destinations/__init__.py` (MODIFY)
```python
# occurrences: 1 (created by FEAT-155 TASK-816 — verify after FEAT-155 is merged; occurrences expected 1)
#   verify: grep -c 'DESTINATION_REGISTRY\["TableDelete"\] = TableDeleteDestination' querysource/outputs/destinations/__init__.py
#   (dev today: 0)
# AFTER — insert below the whole try/except block that contains
#   `    DESTINATION_REGISTRY["TableDelete"] = TableDeleteDestination`
#   and above `def get_destination(step_name: str) -> type[AbstractDestination]:` (dev today: :236, occurrences 1)

try:
    from querysource.queries.multi.destinations.execute_sql import ExecuteSQLDestination
    DESTINATION_REGISTRY["ExecuteSQL"] = ExecuteSQLDestination
except ImportError:
    _pkg_logger.debug(
        "ExecuteSQL destination not available"
    )
```
**Why**: spec Module 4 — one registry block after `TableDelete`, same style as `Table` / `DWH`.

### `querysource/queries/multi/__init__.py` (MODIFY)
```python
# occurrences: 1 (created by FEAT-155 TASK-817 — verify after FEAT-155 is merged; occurrences expected 1)
#   verify: grep -c 'WRITE_DESTINATIONS: frozenset\[str\] = frozenset({"TableDelete"})' querysource/queries/multi/__init__.py
#   (dev today: 0)
# REPLACE `WRITE_DESTINATIONS: frozenset[str] = frozenset({"TableDelete"})` with:
WRITE_DESTINATIONS: frozenset[str] = frozenset({"TableDelete", "ExecuteSQL"})
```
**Why**: spec Module 4 — `ExecuteSQL` joins the FEAT-155 write gate; no gate logic changes.

### `tests/test_destination_execute_sql_registry.py` (CREATE)
```python
"""Registry + PBAC write-gate tests for the ExecuteSQL step (FEAT-156, TASK-822)."""
from unittest.mock import AsyncMock

import pytest

import querysource.auth.enforcement as enforcement
from querysource.auth._resource_types import ResourceType
from querysource.auth.principal import QSPrincipal
from querysource.exceptions import QueryAccessDenied
from querysource.outputs.destinations import get_destination
from querysource.queries.multi import WRITE_DESTINATIONS, MultiQS
from querysource.queries.multi.destinations.execute_sql import ExecuteSQLDestination


def _pipeline() -> dict:
    return {
        "queries": {"profile": {"query": "SELECT 1"}},
        "Output": [{"ExecuteSQL": {"sql": "DELETE FROM wm_assembly.t WHERE d < CURRENT_DATE"}}],
    }


def test_registry_has_executesql() -> None:
    assert get_destination("ExecuteSQL") is ExecuteSQLDestination
    assert "ExecuteSQL" in WRITE_DESTINATIONS


async def test_preflight_gate_executesql(monkeypatch) -> None:
    mock = AsyncMock(return_value=enforcement.AccessDecision(allowed=True, pbac_enabled=True))
    monkeypatch.setattr(enforcement, "enforce_principal", mock)
    mqs = MultiQS(query=_pipeline(), principal=QSPrincipal(user_id="35", groups=("ops",)))
    await mqs._preflight_principal()
    assert any(
        call.args[1:4] == (ResourceType.DATASOURCE, "pg_admin", "datasource:use")
        for call in mock.await_args_list
    )


# FILL IN: test_preflight_gate_executesql_denied (deny → QueryAccessDenied from _preflight_principal)
#          — bounded by tests/multi/test_multiqs_principal.py `deny` fixture pattern
```

### FILL IN checklist
- [ ] `test_preflight_gate_executesql_denied` — `enforce_principal` patched with `AsyncMock(side_effect=QueryAccessDenied())` → `await mqs._preflight_principal()` raises `QueryAccessDenied`.
- [ ] If FEAT-155 passes the gate arguments by keyword instead of positionally, adapt the `call.args[1:4]` assertion to the merged call shape (do not change the gate).

---

## Acceptance Criteria

- [ ] FEAT-155 merged into `dev` and the feature branch rebased before any edit.
- [ ] `get_destination("ExecuteSQL") is ExecuteSQLDestination`.
- [ ] `WRITE_DESTINATIONS == frozenset({"TableDelete", "ExecuteSQL"})`.
- [ ] A MultiQuery with an `ExecuteSQL` Output step calls `enforce_principal(…, DATASOURCE, "pg_admin", "datasource:use")` up-front; a deny raises `QueryAccessDenied`.
- [ ] HTTP path covered with no extra code: FEAT-155's handler `write_access` reads `WRITE_DESTINATIONS`, so an inline `ExecuteSQL` Output now requires `pg_admin` there too. Confirm, after the FEAT-155 rebase, by re-running FEAT-155's `pytest tests/test_multiquery_write_gate_http.py -q`. It is not in Validation Commands because that file comes from another feature.
- [ ] Existing destination / principal tests still pass; `ruff check` clean on the three files.

## Validation Commands

- `pytest tests/test_destination_execute_sql_registry.py -q`
- `pytest tests/test_multiqs_destination_dispatch.py -q`
- `pytest tests/test_multi_destinations_subpackage.py -q`
- `pytest tests/multi/test_multiqs_principal.py -q`

---

## Test Specification

See the `tests/test_destination_execute_sql_registry.py` blueprint block above (spec §4 M4 rows:
`test_registry_has_executesql`, `test_preflight_gate_executesql`).

---

## Agent Instructions

When you pick up this task:

1. **Confirm FEAT-155 is merged into `dev`** and rebase the FEAT-156 feature branch onto it. Not merged → do not start; report the blocker.
2. **Work in the feature worktree** — never on `base_branch`
   (`python -m scripts.sdd.ensure_worktree --slug multi-executesql --feature-id FEAT-156`)
3. **Read the spec** at the path listed above (§3 Module 4, Worktree Strategy "Cross-feature").
4. **Check dependencies** — TASK-821 must be `"done"` in `sdd/tasks/index/multi-executesql.json`.
5. **Verify the Codebase Contract** — both FEAT-155 anchors must now `grep -c` to `1`; update the contract first if FEAT-155 shaped them differently.
6. **Update status** in `sdd/tasks/index/multi-executesql.json` → `"in-progress"` (set `started_at`) and commit only that index file.
7. **Implement** from the blueprint; complete every `# FILL IN:`.
8. **Verify** all acceptance criteria — run the Validation Commands.
9. **Commit the code** — stage only the three listed files.
10. **Close the task** with `scripts/sdd/close_task.sh TASK-822 multi-executesql verified`, then fill in the Completion Note and commit the staged SDD state.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
