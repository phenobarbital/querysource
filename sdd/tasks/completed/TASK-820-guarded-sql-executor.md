# TASK-820: Shared guarded-SQL executor (`querysource/interfaces/guarded_sql.py`)

**Feature**: FEAT-156 — MultiQuery ExecuteSQL Destination
**Spec**: `sdd/specs/multi-executesql.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-819
**Assigned-to**: unassigned

---

## Context

Spec §2 ("Shared guarded-SQL executor") and §3 Module 2b. The guard call and the
one-transaction execution live in a module that has **no dependency on MultiQuery**, so
that `ExecuteSQLDestination` (TASK-821) and FEAT-157's pre/post-hooks reuse the same code.
It imports `sql_guard` and `HAS_RUST` from `querysource.qs_parsers` (re-exported by TASK-819).

---

## Scope

- Create `querysource/interfaces/guarded_sql.py` with `GuardedSQLError`, `guard_statements`, `execute_guarded`.
- `guard_statements`: normalise `str | list[str]`, validate, fail closed without Rust, run `sql_guard`
  per script, flatten in order; nothing reaches the database when anything is blocked.
- `execute_guarded`: `AsyncDB("pg", dsn=default_dsn)` → raw asyncpg connection → one
  `raw.transaction()` → `SET LOCAL statement_timeout` → each statement with `raw.execute`, return status tags.
- Create `tests/test_guarded_sql.py` (spec §4 M2b rows) with a mocked `AsyncDB` (no live DB).

**NOT in scope**:
- `ExecuteSQLDestination` (TASK-821); registry / PBAC gate (TASK-822).
- `{placeholder}` substitution, BigQuery/MySQL, running statements as one multi-statement string (spec Non-Goals / §2).
- Any import from `querysource.queries.multi` (spec AC: reusable by FEAT-157).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/interfaces/guarded_sql.py` | CREATE | `GuardedSQLError`, `guard_statements`, `execute_guarded` |
| `tests/test_guarded_sql.py` | CREATE | Unit tests with mocked `AsyncDB` / raw connection |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from asyncdb import AsyncDB                          # verified: querysource/interfaces/connections.py:11
from querysource.conf import default_dsn             # verified: querysource/conf.py:32  (DB* credentials)
from querysource.exceptions import QueryException    # verified: querysource/exceptions.py:6
from querysource.qs_parsers import HAS_RUST          # verified: querysource/qs_parsers/__init__.py:16,24,26
from querysource.qs_parsers import sql_guard         # created by TASK-819 (explicit re-export)
```

### Existing Signatures to Use
```python
# querysource/exceptions.py:6
class QueryException(Exception):
    default_code: int = 500
    def __init__(self, message: str, code: int | None = None, **kwargs):  # line 16 — sets .message, .code
    def __str__(self): return f"{self.message!s}"                          # line 26

# querysource/conf.py
default_dsn = f'postgres://{DBUSER}:{DBPWD}@{DBHOST}:{DBPORT}/{DBNAME}'   # line 32 — USE THIS
asyncpg_url = f'postgres://{PG_USER}:{PG_PWD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}'  # line 44 — NEVER use here

# asyncdb 2.15.9 (installed, .venv/lib/python3.11/site-packages/asyncdb)
# interfaces/abstract.py:66-69   def get_connection(self): return self._connection ; engine = get_connection
# drivers/pg.py:735              async def connection(self)  → returns self (driver) after connecting
# interfaces/abstract.py:148-157 DriverContextManager.__aenter__/__aexit__ (closes the connection on exit)
# pattern: querysource/connections.py:97  `async with await self._redis.connection() as conn:`

# rust (TASK-818/819) — Python view
def sql_guard(sql: str) -> list[str]: ...   # raises ValueError("statement <n>: <kind> is not allowed: …" | "unterminated …")
```
`raw = conn.engine()` is an `asyncpg.Connection`: `raw.transaction()` is an async context manager
(commits on clean exit, rolls back on exception) and `await raw.execute(sql)` returns the status
tag string (e.g. `"DELETE 1234"`).

### Does NOT Exist
- ~~`querysource.interfaces.guarded_sql` / `GuardedSQLError`~~ — created by this task.
- ~~`asyncdb` `pg.transaction()` for this feature~~ — exists (`drivers/pg.py:1069`) but the spec uses the **raw asyncpg** transaction.
- ~~`QueryException(..., category=...)`~~ — `QueryException` has no `category`; `GuardedSQLError` stores it itself.
- ~~`querysource/interfaces/__init__.py` exports~~ — the file is empty (0 bytes); import the module path directly.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/interfaces/guarded_sql.py", "action": "CREATE"},
    {"path": "tests/test_guarded_sql.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/exceptions.py#QueryException",
    "sym:querysource/conf.py#default_dsn",
    "sym:querysource/qs_parsers/__init__.py#HAS_RUST"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- **Fail closed**: `HAS_RUST` False, or `sql_guard` not importable (extension built before FEAT-156) → `GuardedSQLError(category="infra")`, nothing executed.
- `guard_statements` runs **every** script through the guard before returning, so `execute_guarded` is never called with a partially guarded batch.
- Statements run one by one (never joined into one string) inside ONE transaction; `SET LOCAL statement_timeout = <int ms>` first (the int is computed by us, never user text).
- Any exception from connect / execute → `GuardedSQLError(category="infra")` chained with `from err`; the `async with raw.transaction()` exit rolls back.
- Logging via `logging.getLogger(__name__)`; never log full statement text at INFO (it may carry data) — log the index and status tag.
- Google-style docstrings, strict type hints; no `print`.

---

## Implementation Blueprint

### Steps (in order)
1. Write `querysource/interfaces/guarded_sql.py` from the block below — *why*: it is fully decided by spec §2.
2. Write `tests/test_guarded_sql.py` and complete the FILL IN tests — *why*: pins the fail-closed and one-transaction contract.
3. Run the Validation Commands, `ruff check querysource/interfaces/guarded_sql.py tests/test_guarded_sql.py`, and `grep -n "queries.multi" querysource/interfaces/guarded_sql.py` (must print nothing) — *why*: spec AC (reusable by FEAT-157).

### `querysource/interfaces/guarded_sql.py` (CREATE)
```python
"""Guarded SQL execution on PostgreSQL with the full-access ``DB*`` credentials (FEAT-156).

Shared by the MultiQuery ``ExecuteSQL`` destination and the FEAT-157 hooks. It must never
import from ``querysource.queries.multi``.
"""
from __future__ import annotations

import logging
from typing import Callable, List, Optional, Union

from asyncdb import AsyncDB

from querysource.conf import default_dsn
from querysource.exceptions import QueryException
from querysource.qs_parsers import HAS_RUST

try:
    from querysource.qs_parsers import sql_guard as _sql_guard
except ImportError:  # extension missing, or built before FEAT-156
    _sql_guard = None

sql_guard: Optional[Callable[[str], List[str]]] = _sql_guard
logger = logging.getLogger(__name__)


class GuardedSQLError(QueryException):
    """Guard rejection (category ``data``) or missing extension / DB failure (category ``infra``)."""

    def __init__(self, message: str, *, category: str) -> None:
        """Store ``category`` (``"data"`` | ``"infra"``); ``data`` maps to HTTP 422, ``infra`` to 500."""
        super().__init__(message, code=422 if category == "data" else 500)
        self.category = category


def guard_statements(sql: Union[str, List[str]]) -> List[str]:
    """Return the flattened, guard-approved statements of every script, in order.

    Args:
        sql: One script or a list of scripts.

    Returns:
        The top-level statements, in order.

    Raises:
        GuardedSQLError: empty input or non-str items (``data``); ``HAS_RUST`` False (``infra``);
            a blocked or unparsable statement (``data``, the guard's message).
    """
    scripts = [sql] if isinstance(sql, str) else sql
    if not isinstance(scripts, list) or not scripts:
        raise GuardedSQLError("sql must be a non-empty string or list of strings", category="data")
    if any(not isinstance(script, str) or not script.strip() for script in scripts):
        raise GuardedSQLError("every sql item must be a non-empty string", category="data")
    if not HAS_RUST or sql_guard is None:
        raise GuardedSQLError(
            "SQL guard unavailable (qs_parsers Rust extension not installed); refusing to execute SQL",
            category="infra",
        )
    statements: List[str] = []
    for index, script in enumerate(scripts, start=1):
        try:
            statements.extend(sql_guard(script))
        except ValueError as err:
            raise GuardedSQLError(f"script {index}: {err}", category="data") from err
    if not statements:
        raise GuardedSQLError("sql contains no executable statements", category="data")
    return statements


async def execute_guarded(statements: List[str], *, timeout: float = 3600.0) -> List[str]:
    """Execute ``statements`` in ONE transaction on ``default_dsn`` and return their status tags.

    Args:
        statements: Output of :func:`guard_statements`.
        timeout: Per-statement timeout in seconds (``SET LOCAL statement_timeout``).

    Returns:
        One asyncpg status tag per statement (``"DELETE 1234"``, ``"INSERT 0 900"``).

    Raises:
        GuardedSQLError: ``timeout <= 0`` (``data``); any connection or statement error (``infra``);
            the transaction is rolled back.
    """
    if timeout <= 0:
        raise GuardedSQLError("timeout must be > 0 seconds", category="data")
    if not statements:
        return []
    timeout_ms = int(timeout * 1000)
    results: List[str] = []
    current = 0
    try:
        db = AsyncDB("pg", dsn=default_dsn)
        async with await db.connection() as conn:
            raw = conn.engine()
            async with raw.transaction():
                await raw.execute(f"SET LOCAL statement_timeout = {timeout_ms}")
                for current, statement in enumerate(statements, start=1):
                    status = await raw.execute(statement)
                    logger.debug("guarded_sql: statement %d/%d -> %s", current, len(statements), status)
                    results.append(status)
    except Exception as err:  # every DB/driver failure is infra; the transaction was rolled back
        where = f"statement {current}" if current else "connection"
        raise GuardedSQLError(f"guarded SQL failed at {where}: {err}", category="infra") from err
    logger.info("guarded_sql: committed %d statement(s): %s", len(results), results)
    return results
```
**Why this shape**: mirrors spec §2 points 1–2 literally. `sql_guard` is a module attribute so tests
can monkeypatch it and `HAS_RUST`; the `try/except ImportError` keeps this module importable (and
failing closed) on a stale extension. Do not change the public names or signatures — TASK-821 and FEAT-157 import them.

### `tests/test_guarded_sql.py` (CREATE)
```python
"""Unit tests for querysource.interfaces.guarded_sql (FEAT-156, TASK-820)."""
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock

import pytest

import querysource.interfaces.guarded_sql as guarded_sql
from querysource.conf import default_dsn
from querysource.interfaces.guarded_sql import GuardedSQLError, execute_guarded, guard_statements


class _Tx:
    """Async context manager recording enter/exit (and the exception type) into ``log``."""

    def __init__(self, log: List[Any]) -> None:
        self.log = log

    async def __aenter__(self) -> "_Tx":
        self.log.append("tx_enter")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self.log.append(("tx_exit", exc_type))
        return False


@pytest.fixture
def fake_db(monkeypatch):
    """Patch AsyncDB; return (asyncdb_factory, raw_conn, log)."""
    log: List[Any] = []
    raw = MagicMock()
    raw.transaction = MagicMock(side_effect=lambda: _Tx(log))

    async def _execute(sql: str) -> str:
        log.append(sql)
        return "SET" if sql.startswith("SET LOCAL") else f"{sql.split()[0].upper()} 1"

    raw.execute = AsyncMock(side_effect=_execute)
    conn = MagicMock()
    conn.engine = MagicMock(return_value=raw)
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    db = MagicMock()
    db.connection = AsyncMock(return_value=conn)
    factory = MagicMock(return_value=db)
    monkeypatch.setattr(guarded_sql, "AsyncDB", factory)
    return factory, raw, log


def test_guard_statements_blocked() -> None:
    with pytest.raises(GuardedSQLError) as exc:
        guard_statements("DROP TABLE x")
    assert exc.value.category == "data"


def test_guard_statements_no_rust_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(guarded_sql, "HAS_RUST", False)
    with pytest.raises(GuardedSQLError) as exc:
        guard_statements("DELETE FROM t")
    assert exc.value.category == "infra"


async def test_execute_guarded_uses_default_dsn(fake_db) -> None:
    factory, _raw, _log = fake_db
    await execute_guarded(["DELETE FROM t"])
    factory.assert_called_once_with("pg", dsn=default_dsn)


# FILL IN: test_guard_statements_flattens, test_guard_statements_rejects_bad_input,
#          test_execute_guarded_one_transaction, test_execute_guarded_db_error — bounded by the FILL IN checklist
```

### FILL IN checklist
- [ ] `test_guard_statements_flattens` — `["DELETE FROM t WHERE a = 1; INSERT INTO t VALUES (1)", "UPDATE t SET a = 2"]` → the 3 statements in order.
- [ ] `test_guard_statements_rejects_bad_input` — `""`, `[]`, `["ok", 3]`, `None` → `GuardedSQLError(category="data")`.
- [ ] `test_execute_guarded_one_transaction` — `log == ["tx_enter", "SET LOCAL statement_timeout = 5000", stmt1, stmt2, ("tx_exit", None)]` for `timeout=5`; returns `["DELETE 1", "INSERT 1"]`.
- [ ] `test_execute_guarded_db_error` — `raw.execute` raises `asyncpg.exceptions.PostgresError("boom")` on stmt 2 → `GuardedSQLError(category="infra")`, message contains `statement 2`, last log entry is `("tx_exit", PostgresError)`.
- [ ] Skip the guard-calling tests with `pytest.mark.skipif(not guarded_sql.HAS_RUST, …)` where they need the real extension (`test_guard_statements_blocked`, `…_flattens`).

---

## Acceptance Criteria

- [ ] `querysource/interfaces/guarded_sql.py` exists with the blueprint's public names and signatures.
- [ ] It imports nothing from `querysource.queries.multi` (`grep -n "queries.multi" querysource/interfaces/guarded_sql.py` is empty).
- [ ] A blocked statement raises `GuardedSQLError(category="data")` and `AsyncDB` is never instantiated.
- [ ] `HAS_RUST = False` → `GuardedSQLError(category="infra")`, nothing executed.
- [ ] `execute_guarded` connects with `default_dsn`, runs `SET LOCAL statement_timeout` first and every statement inside one `raw.transaction()`.
- [ ] `ruff check querysource/interfaces/guarded_sql.py tests/test_guarded_sql.py` is clean.

## Validation Commands

- `pytest tests/test_guarded_sql.py -q`
- `pytest tests/test_rust_parsers.py -q`

---

## Test Specification

See the `tests/test_guarded_sql.py` blueprint block and FILL IN checklist above
(spec §4 M2b rows: `test_guard_statements_flattens`, `test_guard_statements_blocked`,
`test_guard_statements_no_rust_fails_closed`, `test_execute_guarded_one_transaction`,
`test_execute_guarded_db_error`, `test_execute_guarded_uses_default_dsn`).

---

## Agent Instructions

When you pick up this task:

1. **Work in the feature worktree** — never on `base_branch`
   (`python -m scripts.sdd.ensure_worktree --slug multi-executesql --feature-id FEAT-156`)
2. **Read the spec** at the path listed above (§2 executor contract, §3 Module 2b).
3. **Check dependencies** — TASK-819 must be `"done"` in `sdd/tasks/index/multi-executesql.json`
   and `python -c "from querysource.qs_parsers import sql_guard"` must work (rebuilt extension).
4. **Verify the Codebase Contract** before writing any code.
5. **Update status** in `sdd/tasks/index/multi-executesql.json` → `"in-progress"` (set `started_at`) and commit only that index file.
6. **Implement** from the blueprint; complete every `# FILL IN:`; never change a public signature.
7. **Verify** all acceptance criteria — run the Validation Commands.
8. **Commit the code** — stage only the two listed files.
9. **Close the task** with `scripts/sdd/close_task.sh TASK-820 multi-executesql verified`.
10. **Fill in the Completion Note** below, then commit the staged SDD state.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: sdd-worker (sequential fallback)
**Date**: 2026-09-30
**Notes**: Created guarded_sql.py per blueprint and tests/test_guarded_sql.py (10 tests pass with mocked AsyncDB). ruff clean. The grep for "queries.multi" matches only the module docstring sentence forbidding the import, no actual import.

**Deviations from spec**: none
