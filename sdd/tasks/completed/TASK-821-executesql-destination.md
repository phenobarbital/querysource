# TASK-821: `ExecuteSQLDestination` — pass-through MultiQuery Output step

**Feature**: FEAT-156 — MultiQuery ExecuteSQL Destination
**Spec**: `sdd/specs/multi-executesql.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-820
**Assigned-to**: unassigned

---

## Context

Spec §2 ("Destination") and §3 Module 3. `ExecuteSQLDestination` is a thin Output wrapper
over the shared executor from TASK-820 (`querysource/interfaces/guarded_sql.py`): validate
config in `__init__`, then `guard_statements` → `execute_guarded`, store the status tags in
`self.results`, and return the input data unchanged so `Table` can follow it.
Registering the `"ExecuteSQL"` step name and the PBAC write gate is TASK-822.

Note: `querysource/queries/multi/destinations/__init__.py` **folder-scans** this package, so as
soon as the file exists the class appears in the component catalog as `ExecuteSQLDestination`
(its `_catalog.display_name` renames it to `ExecuteSQL`) — the documentation-endpoint tests must keep passing.

---

## Scope

- Create `querysource/queries/multi/destinations/execute_sql.py` with `ExecuteSQLDestination(AbstractDestination)`:
  hand-written `_catalog` (mirrors `TableDestination._catalog`), config validation, `run()`.
- `GuardedSQLError` → `OutputError(str(err), category=err.category)`.
- Create `tests/test_destination_execute_sql.py` (spec §4 M3 rows).

**NOT in scope**:
- `DESTINATION_REGISTRY["ExecuteSQL"]` and `WRITE_DESTINATIONS` (TASK-822).
- Re-implementing guard/transaction logic — call TASK-820's functions only.
- Regenerating `generated/*.json` (no freshness gate in tests; the pre-commit hook is not installed in this clone).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/destinations/execute_sql.py` | CREATE | `ExecuteSQLDestination` |
| `tests/test_destination_execute_sql.py` | CREATE | Unit tests (init validation, blocked never connects, error wrap, pass-through) |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
import pandas as pd
from querysource.exceptions import OutputError                             # verified: querysource/exceptions.py:104
from querysource.outputs.destinations.abstract import AbstractDestination  # verified: querysource/outputs/destinations/abstract.py:19
from querysource.interfaces.guarded_sql import GuardedSQLError, execute_guarded, guard_statements  # created by TASK-820
```

### Existing Signatures to Use
```python
# querysource/outputs/destinations/abstract.py
class AbstractDestination(SchemaIntrospectable, ABC):              # line 19
    _category: str = "Destinations"                                # line 28
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:  # line 30 — sets self.data, self.logger
    @abstractmethod
    async def run(self) -> Union[dict, pd.DataFrame]:              # line 60-61

# querysource/exceptions.py:104
class OutputError(QueryException):
    def __init__(self, message: str = "", code: int | None = None, *,
                 step_name: str = None, category: str = None, **kwargs):  # line 114-122

# querysource/queries/multi/destinations/table.py
class TableDestination(AbstractDestination):  _catalog = {...}      # line 80 — keys: display_name, description,
                                                                    # usage, icon, attributes, json_schema, example
    def __init__(self, data, **kwargs) -> None:                     # line 220 — OutputError on bad config

# querysource/interfaces/guarded_sql.py (TASK-820)
class GuardedSQLError(QueryException): category: str
def guard_statements(sql: Union[str, List[str]]) -> List[str]: ...
async def execute_guarded(statements: List[str], *, timeout: float = 3600.0) -> List[str]: ...

# querysource/queries/multi/destinations/__init__.py:26-62  _scan_destinations(): imports every *.py here,
#   registers AbstractDestination subclasses by class name (DESTINATION_REGISTRY, line 73)
# querysource/queries/multi/_introspect.py:932-938  _catalog overrides name/description/usage/example/icon
```

### Does NOT Exist
- ~~`querysource.queries.multi.destinations.execute_sql`~~ — created here.
- ~~`AbstractDestination.results`~~ — not on the base class; define `self.results` in `__init__`.
- ~~Flowtask `ExecuteSQL` helpers (`TemplateSupport`, `QSSupport`, `_taskstore`, `file_sql`, `use_dataframe`)~~ — not in QuerySource.
- ~~`get_destination("ExecuteSQL")`~~ — does not resolve until TASK-822.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/queries/multi/destinations/execute_sql.py", "action": "CREATE"},
    {"path": "tests/test_destination_execute_sql.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/outputs/destinations/abstract.py#AbstractDestination",
    "sym:querysource/exceptions.py#OutputError",
    "sym:querysource/queries/multi/destinations/table.py#TableDestination._catalog"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Config errors raise `OutputError(..., category="data")` from `__init__` (before any I/O).
- `driver`: only PostgreSQL (`pg`, with aliases `postgres` / `postgresql`, case-insensitive).
- `timeout`: seconds, default 3600, number > 0; `bool` is rejected (it is an `int` subclass).
- `run()` never catches anything but `GuardedSQLError`; the MultiQS Output loop already wraps other exceptions.
- Use `self.logger` (set by `AbstractDestination.__init__`); log the status tags, not the SQL text.
- Use `kwargs.get("sql")` / `kwargs.get("driver", "pg")` / `kwargs.get("timeout", 3600)` literally —
  the introspector derives catalog attributes from `kwargs.get(...)` calls.

---

## Implementation Blueprint

### Steps (in order)
1. Write `execute_sql.py` from the block below and complete the `_catalog` FILL IN — *why*: the UI and documentation endpoint read it.
2. Write `tests/test_destination_execute_sql.py` and complete the FILL IN tests — *why*: spec §4 M3 rows.
3. Run the Validation Commands and `ruff check querysource/queries/multi/destinations/execute_sql.py tests/test_destination_execute_sql.py` — *why*: the folder scan puts this class in every catalog test.

### `querysource/queries/multi/destinations/execute_sql.py` (CREATE)
```python
"""ExecuteSQL destination: run guarded SQL on PostgreSQL (``DB*`` credentials), pass data through."""
from typing import List, Union

import pandas as pd

from querysource.exceptions import OutputError
from querysource.interfaces.guarded_sql import GuardedSQLError, execute_guarded, guard_statements
from querysource.outputs.destinations.abstract import AbstractDestination

SUPPORTED_DRIVERS = frozenset({"pg", "postgres", "postgresql"})
DEFAULT_TIMEOUT: float = 3600.0


class ExecuteSQLDestination(AbstractDestination):
    """Run guarded SQL statements on PostgreSQL (DB* credentials) in one transaction.

    Step name: ``ExecuteSQL``. Returns the input data unchanged.
    """

    _catalog = {
        "display_name": "ExecuteSQL",
        "icon": "terminal",
        # FILL IN: description, usage, attributes (sql / driver / timeout), json_schema, example —
        #          bounded by TableDestination._catalog shape (table.py:80-218) and the FILL IN checklist
    }

    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        """Read ``sql`` (str | list[str], required), ``driver`` (default ``pg``), ``timeout`` (s, default 3600).

        Raises:
            OutputError: missing/empty ``sql``, non-string items, unsupported driver, timeout <= 0.
        """
        super().__init__(data, **kwargs)
        sql = kwargs.get("sql")
        scripts: List[str] = [sql] if isinstance(sql, str) else sql if isinstance(sql, list) else []
        if not scripts or any(not isinstance(s, str) or not s.strip() for s in scripts):
            raise OutputError(
                "ExecuteSQL: 'sql' is required and must be a non-empty string or list of non-empty strings",
                category="data",
            )
        driver = str(kwargs.get("driver", "pg") or "pg").lower()
        if driver not in SUPPORTED_DRIVERS:
            raise OutputError(
                f"ExecuteSQL: unsupported driver '{driver}'. Supported: {', '.join(sorted(SUPPORTED_DRIVERS))}",
                category="data",
            )
        timeout = kwargs.get("timeout", 3600)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise OutputError(f"ExecuteSQL: 'timeout' must be a number of seconds > 0, got {timeout!r}", category="data")
        self._sql: List[str] = scripts
        self._driver: str = "pg"
        self._timeout: float = float(timeout)
        self.results: List[str] = []

    async def run(self) -> Union[dict, pd.DataFrame]:
        """``guard_statements`` → ``execute_guarded``; store ``self.results``.

        Returns:
            ``self.data`` unchanged.

        Raises:
            OutputError: wraps ``GuardedSQLError`` keeping its ``category``.
        """
        try:
            statements = guard_statements(self._sql)
            self.results = await execute_guarded(statements, timeout=self._timeout)
        except GuardedSQLError as err:
            raise OutputError(str(err), category=err.category) from err
        self.logger.info("ExecuteSQL: %d statement(s) committed: %s", len(self.results), self.results)
        return self.data
```
**Why this shape**: spec §2 "`ExecuteSQLDestination.run()`" steps 1–3 verbatim; `step_name` is left
unset because the MultiQS Output loop fills it (`queries/multi/__init__.py:850-851`). Do not change the class
name, file path, or `results` attribute — TASK-822 imports the class and tests read `results`.

### `tests/test_destination_execute_sql.py` (CREATE)
```python
"""Unit tests for ExecuteSQLDestination (FEAT-156, TASK-821)."""
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

import querysource.interfaces.guarded_sql as guarded_sql
import querysource.queries.multi.destinations.execute_sql as execute_sql
from querysource.exceptions import OutputError
from querysource.interfaces.guarded_sql import GuardedSQLError
from querysource.queries.multi.destinations.execute_sql import ExecuteSQLDestination

REFRESH_SQL = (
    "DELETE FROM wm_assembly.employee_detail_profile "
    "WHERE activity_date >= (date_trunc('month', CURRENT_DATE) - INTERVAL '5 months')::date "
    "AND activity_date < CURRENT_DATE"
)


@pytest.fixture
def data() -> pd.DataFrame:
    return pd.DataFrame({"employee_id": [1, 2]})


@pytest.mark.parametrize("kwargs", [{}, {"sql": ""}, {"sql": []}, {"sql": ["ok", 3]},
                                    {"sql": REFRESH_SQL, "driver": "mysql"},
                                    {"sql": REFRESH_SQL, "timeout": 0}, {"sql": REFRESH_SQL, "timeout": True}])
def test_execsql_init_validation(data, kwargs) -> None:
    with pytest.raises(OutputError) as exc:
        ExecuteSQLDestination(data, **kwargs)
    assert exc.value.category == "data"


async def test_execsql_passthrough(data, monkeypatch) -> None:
    monkeypatch.setattr(execute_sql, "guard_statements", lambda sql: [REFRESH_SQL])
    monkeypatch.setattr(execute_sql, "execute_guarded", AsyncMock(return_value=["DELETE 1234"]))
    dest = ExecuteSQLDestination(data, sql=REFRESH_SQL)
    assert await dest.run() is data
    assert dest.results == ["DELETE 1234"]


# FILL IN: test_execsql_blocked_never_connects, test_execsql_wraps_guarded_error — bounded by the FILL IN checklist
```

### FILL IN checklist
- [ ] `_catalog` — `description`, `usage` (mention one transaction, `DB*` credentials, blocked kinds, `BEGIN/COMMIT` rejected, chaining with `Table`), `attributes` for `sql` (`str | list`, required), `driver` (`str`, default `pg`), `timeout` (`float`, default 3600); `json_schema` with `sql` as `oneOf` string / array of strings, `driver` enum `["pg","postgres","postgresql"]`, `timeout` `{"type":"number","exclusiveMinimum":0,"default":3600}`, `required: ["sql"]`, `additionalProperties: false`; `example` = the spec §2 `ExecuteSQL` + `Table` Output JSON.
- [ ] `test_execsql_blocked_never_connects` — patch `guarded_sql.AsyncDB` with a `MagicMock`; `sql="DROP TABLE x"` → `OutputError(category="data")` and the mock was never called (skip when `not guarded_sql.HAS_RUST`).
- [ ] `test_execsql_wraps_guarded_error` — `execute_guarded` raises `GuardedSQLError("x", category="infra")` → `OutputError` with `category == "infra"` and `__cause__` is the original.

---

## Acceptance Criteria

- [ ] `ExecuteSQLDestination` exists at the blueprint path with the blueprint signatures and a populated `_catalog`.
- [ ] Blocked SQL raises `OutputError(category="data")` before `AsyncDB` is instantiated.
- [ ] `run()` returns the input object (`is`) and fills `results` with the status tags.
- [ ] Documentation-endpoint and destination-subpackage tests still pass (folder scan picks the class up).
- [ ] `ruff check` clean on both files.

## Validation Commands

- `pytest tests/test_destination_execute_sql.py -q`
- `pytest tests/test_destinations_documentation_endpoint.py -q`
- `pytest tests/test_multi_destinations_subpackage.py -q`
- `pytest tests/test_destination_table.py -q`

---

## Test Specification

See the `tests/test_destination_execute_sql.py` blueprint block and FILL IN checklist
(spec §4 M3 rows: `test_execsql_init_validation`, `test_execsql_blocked_never_connects`,
`test_execsql_wraps_guarded_error`, `test_execsql_passthrough`).

---

## Agent Instructions

When you pick up this task:

1. **Work in the feature worktree** — never on `base_branch`
   (`python -m scripts.sdd.ensure_worktree --slug multi-executesql --feature-id FEAT-156`)
2. **Read the spec** at the path listed above (§2 Destination, §3 Module 3).
3. **Check dependencies** — TASK-820 must be `"done"` in `sdd/tasks/index/multi-executesql.json`.
4. **Verify the Codebase Contract** before writing any code.
5. **Update status** in `sdd/tasks/index/multi-executesql.json` → `"in-progress"` (set `started_at`) and commit only that index file.
6. **Implement** from the blueprint; complete every `# FILL IN:`; never change a signature or path.
7. **Verify** all acceptance criteria — run the Validation Commands.
8. **Commit the code** — stage only the two listed files.
9. **Close the task** with `scripts/sdd/close_task.sh TASK-821 multi-executesql verified`.
10. **Fill in the Completion Note** below, then commit the staged SDD state.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: sdd-worker (sequential fallback)
**Date**: 2026-09-30
**Notes**: Created ExecuteSQLDestination with full _catalog and tests (11 pass). 3 failures in test_destinations_documentation_endpoint (2) and test_destination_table (1) are pre-existing: verified identical failures with execute_sql.py removed.

**Deviations from spec**: none
