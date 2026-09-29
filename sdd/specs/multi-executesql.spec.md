---
type: feature
base_branch: dev
projects: [multiquery, outputs, rust-parsers, auth]
tags: [multiquery, destinations, execute-sql, flowtask-port, sql-guard]
---

# Feature Specification: MultiQuery ExecuteSQL Destination

**Feature ID**: FEAT-156
**Date**: 2026-09-29
**Author**: Juan2coder (requested by Jesus Lara)
**Status**: draft
**Target version**: 5.2.0

---

## 1. Motivation & Business Requirements

### Problem Statement
Maintenance SQL cannot be run from QuerySource today:
- **Slugs are read-only.** They run on the `PG_*` connection (`asyncpg_url`,
  `querysource/conf.py:44`).
- **Slugs are single-statement.** The pg provider executes with `asyncpg.fetch()`
  (prepared statement), so `BEGIN; DELETE …; INSERT …; COMMIT;` fails with
  `cannot insert multiple commands into a prepared statement`.

The concrete driver is the `wm_assembly.employee_detail_profile` refresh: delete the last 5
months and re-insert them from a CTE. It needs a range `DELETE`, which neither `Table`
nor `TableDelete` (FEAT-155) can express.

Flowtask solves this with `ExecuteSQL` (`flowtask/flowtask/components/ExecuteSQL.py`),
which runs one or more SQL scripts on the full-access connection. Jesus Lara asked to port
it to MultiQuery. The only safety requirement is that **no destructive DDL** (`DROP …`)
may run, and it should be enforced by QuerySource's Rust extension.

### Goals
- A new MultiQuery Output step `ExecuteSQL` that runs one SQL string or a list of SQL
  strings on PostgreSQL with the **full-access `DB*` credentials** (`default_dsn`, the same
  tier `Table` writes with).
- A Rust guard (`sql_guard`) in the `_qs_parsers` extension that splits a script into
  top-level statements with a real lexer and rejects destructive / privilege-changing statements
  **before anything is sent to the database**.
- All statements of one `ExecuteSQL` step run inside **one transaction** (all-or-nothing).
- Pass-through: returns the input data unchanged, so it can be chained with `Table`,
  for example `ExecuteSQL` (range DELETE) followed by `Table` (append).
- Fail closed: if the Rust extension is unavailable, `ExecuteSQL` refuses to run.
- The same PBAC write gate as FEAT-155 (`WRITE_DESTINATIONS`).

### Non-Goals (explicitly out of scope)
- Flowtask's `file_sql` (QS has no task storage), `pattern` / `masks` / `use_template`
  variable substitution, and `use_dataframe` per-row formatting. Per-row formatting is an
  injection vector. Dates are computed in SQL (`CURRENT_DATE`, …).
- BigQuery / MySQL (Flowtask supports BigQuery; follow-up).
- Running SQL *before* the sources (a "pre-hook"). `ExecuteSQL` is an Output step, so it runs
  after the pipeline produced data.
- Detecting destructive behaviour hidden inside server-side functions
  (`SELECT my_func()` that executes `DROP` dynamically). See §7 Risks.
- A general SQL AST/grammar parser. The guard is a lexer plus a statement classifier.

---

## 2. Architectural Design

### Overview

**Rust guard (`rust/src/sql_guard.rs`).**

*Lexer.* It walks the script byte-wise and tracks these states:
- `'…'` literals, with `''` as the escape;
- `E'…'` literals, with backslash escapes;
- `"…"` identifiers;
- dollar-quoted bodies `$tag$…$tag$`;
- `-- …` line comments;
- `/* … */` block comments, which can be nested in PostgreSQL.

A `;` outside those states ends a statement. Empty or comment-only statements are
discarded. An unterminated literal, identifier, dollar body or comment raises `ValueError`
(fail closed).

*Classifier.* For each statement, it reads the keyword tokens outside
literals and comments, uppercased:

| Statement starts with | Verdict |
|---|---|
| `DROP` | blocked `drop` |
| `TRUNCATE` | blocked `truncate` |
| `ALTER` and any later top-level token is `DROP` (`ALTER TABLE … DROP COLUMN/CONSTRAINT`) | blocked `alter_drop` |
| `DO` | blocked `do_block`, because an anonymous code block can run dynamic DDL |
| `GRANT` / `REVOKE` | blocked `privilege` |
| `CREATE`/`ALTER` followed by `ROLE`/`USER`/`GROUP` | blocked `role` |
| `COPY` containing the token `PROGRAM` | blocked `copy_program` |
| `BEGIN`, `START`, `COMMIT`, `END`, `ROLLBACK`, `SAVEPOINT`, `RELEASE`, `ABORT` | blocked `transaction_control`, because the component owns the transaction |
| anything else (`WITH`, `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `CREATE TABLE/INDEX/VIEW`, `ALTER … ADD`, `CALL`, `REFRESH MATERIALIZED VIEW`, `ANALYZE`, …) | allowed |

The `pyfunction` `sql_guard(sql: str) -> list[str]` returns the allowed statements in
order. It raises `ValueError("statement <n>: <kind> is not allowed: <first 80 chars>")`
on the first blocked statement. Because a data-modifying CTE starts with `WITH`, the
user's single-statement refresh query is allowed.

**Destination (`querysource/queries/multi/destinations/execute_sql.py`).**
`ExecuteSQLDestination(AbstractDestination)` is registered as `"ExecuteSQL"`.

Config:
- `sql`: str or list[str], required.
- `driver`: `"pg"`, the only one supported for now.
- `timeout`: seconds, default 3600. Applied with `SET LOCAL statement_timeout`.

`run()`:
1. `HAS_RUST` is False → `OutputError` ("ExecuteSQL requires the Rust extension").
2. Normalize `sql` to a list, then run `sql_guard` on each script and flatten the statements
   in order. `ValueError` → `OutputError(category="data")`. **Nothing reaches the database
   when any statement is blocked.**
3. `AsyncDB("pg", dsn=default_dsn)`, then `raw = conn.engine()`. Inside
   `raw.transaction()`: `SET LOCAL statement_timeout = <ms>`, then
   `status = await raw.execute(stmt)` for each statement. The status tag
   (`"DELETE 1234"`, `"INSERT 0 900"`) is appended to `self.results`.
4. Log the results. Return `self.data`.

Statements are executed one by one, not as a single multi-statement string. That way the
exact guarded strings are what runs, and each status tag is attributable.

Example (the employee_detail_profile refresh):
```json
{"queries": {"profile": {"query": "WITH params AS (…) SELECT … FROM activity_days ad …"}},
 "Output": [
   {"ExecuteSQL": {"sql": "DELETE FROM wm_assembly.employee_detail_profile WHERE activity_date >= (date_trunc('month', CURRENT_DATE) - INTERVAL '5 months')::date AND activity_date < CURRENT_DATE"}},
   {"Table": {"schema": "wm_assembly", "table": "employee_detail_profile", "method": "append"}}
 ]}
```

### Component Diagram
```
MultiQS.query()
  ├─ _preflight_principal()  ── "ExecuteSQL" ∈ WRITE_DESTINATIONS ──→ enforce_principal(DATASOURCE,"pg_admin","datasource:use")
  └─ Output loop
        ExecuteSQLDestination.run()
          ├─ _qs_parsers.sql_guard(script)  (Rust: lex → split → classify; raises on blocked)
          └─ AsyncDB("pg", dsn=default_dsn) → raw asyncpg conn
                └─ transaction: SET LOCAL statement_timeout → execute(stmt₁) … execute(stmtₙ)
        TableDestination.run()   (optional re-insert)
```

### Integration Points
| Existing Component | Integration Type | Notes |
|---|---|---|
| `_qs_parsers` PyO3 module (`rust/src/lib.rs`) | extends | new `mod sql_guard;` + `wrap_pyfunction!(sql_guard::sql_guard, m)` |
| `querysource/qs_parsers/__init__.py` | modifies | explicit re-export of `sql_guard` in both import branches |
| `AbstractDestination` | extends | pass-through `run()` |
| `DESTINATION_REGISTRY` | registers | key `"ExecuteSQL"` |
| `WRITE_DESTINATIONS` (FEAT-155) | extends | add `"ExecuteSQL"` |
| `querysource.conf.default_dsn` | uses | `DB*` credentials |

### Data Models
None new.

### New Public Interfaces
```python
# Python view of the Rust function
def sql_guard(sql: str) -> list[str]: ...   # raises ValueError on blocked / unterminated input

class ExecuteSQLDestination(AbstractDestination):
    results: list[str]
    async def run(self) -> Union[dict, pd.DataFrame]: ...
```

---

## 3. Module Breakdown

#### Delegation-eligible modules

| Module | Eligible? | Decided patterns / exact contracts | Why not (if no) |
|---|---|---|---|
| M1: Rust `sql_guard` | yes | lexer states, classification table §2, `ValueError` message format, pure-Rust core + thin pyfunction | — |
| M2: PyO3 registration + re-export | yes | one `mod`, one `add_function`, two re-export lines | — |
| M3: ExecuteSQLDestination | yes | run() steps 1-4, one transaction, `SET LOCAL statement_timeout` | — |
| M4: registry + write gate | yes | one registry block; add `"ExecuteSQL"` to `WRITE_DESTINATIONS` | — |

### Module 1: Rust SQL guard
- **Path**: `rust/src/sql_guard.rs` (new)
- **Responsibility**: Lex and split a PostgreSQL script into top-level statements, then reject destructive / privilege / transaction-control statements.
- **Depends on**: nothing (std + pyo3 already in `rust/Cargo.toml`, no new crates)
- **Interface Skeleton**:
  ```rust
  // rust/src/sql_guard.rs  (new)
  use pyo3::exceptions::PyValueError;
  use pyo3::prelude::*;

  /// Why a statement was rejected.
  #[derive(Debug, Clone, Copy, PartialEq, Eq)]
  pub enum BlockedKind { Drop, Truncate, AlterDrop, DoBlock, Privilege, Role, CopyProgram, TransactionControl }

  /// Split `sql` into top-level statements (trimmed, non-empty, comment-only dropped).
  /// Err(String) on unterminated literal / identifier / dollar-quote / block comment.
  pub fn split_statements(sql: &str) -> Result<Vec<String>, String>;

  /// Upper-cased keyword tokens of one statement, skipping literals, identifiers and comments.
  fn keyword_tokens(stmt: &str) -> Vec<String>;

  /// Classify one statement; None = allowed.
  pub fn classify(stmt: &str) -> Option<BlockedKind>;

  /// Pure-Rust entry point used by the pyfunction and by `cargo test`.
  pub fn guard(sql: &str) -> Result<Vec<String>, String>;

  /// Python: sql_guard(sql: str) -> list[str]; raises ValueError.
  #[pyfunction]
  #[pyo3(signature = (sql))]
  pub fn sql_guard(sql: &str) -> PyResult<Vec<String>>;
  ```
  `#[cfg(test)] mod tests` covers every table row in §2, plus these cases: `DROP` inside a
  literal or comment is allowed; `$fn$ DROP $fn$` is allowed as a literal body; mixed case
  `dRoP`; `;` inside a literal does not split; unterminated literal → Err; the user's full
  data-modifying CTE → allowed.

### Module 2: PyO3 registration and Python re-export
- **Path**: `rust/src/lib.rs`, `querysource/qs_parsers/__init__.py` (modify)
- **Responsibility**: Expose `sql_guard` to Python.
- **Depends on**: M1
- **Interface Skeleton**:
  ```rust
  // rust/src/lib.rs — add `mod sql_guard;` to the mod list (verified: :8-23)
  // and after the SafeDict block (verified: :57):
  m.add_function(wrap_pyfunction!(sql_guard::sql_guard, m)?)?;
  ```
  ```python
  # querysource/qs_parsers/__init__.py — beside the safe_format_map_validated re-exports (verified: :15, :23)
  from ._qs_parsers import sql_guard  # noqa: F401   (in-wheel branch)
  from _qs_parsers import sql_guard  # noqa: F401    (local-dev branch)
  ```

### Module 3: ExecuteSQLDestination
- **Path**: `querysource/queries/multi/destinations/execute_sql.py` (new)
- **Responsibility**: Guard, then execute the SQL in one transaction with `DB*` credentials. Pass the data through.
- **Depends on**: M2
- **Interface Skeleton**:
  ```python
  # querysource/queries/multi/destinations/execute_sql.py  (new)
  from typing import List, Union
  import pandas as pd
  from querysource.conf import default_dsn  # verified: querysource/conf.py:32
  from querysource.exceptions import OutputError  # verified: querysource/exceptions.py:104
  from querysource.outputs.destinations.abstract import AbstractDestination  # verified: querysource/outputs/destinations/abstract.py:19
  from querysource.qs_parsers import HAS_RUST  # verified: querysource/qs_parsers/__init__.py:16,26

  class ExecuteSQLDestination(AbstractDestination):
      """Run guarded SQL statements on PostgreSQL (DB* credentials) in one transaction.

      Step name: ``ExecuteSQL``. Returns the input data unchanged.
      """
      _catalog: dict  # display_name "ExecuteSQL", icon "terminal", attributes sql/driver/timeout, json_schema, example

      def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
          """Read ``sql`` (str | list[str], required), ``driver`` (default ``pg``), ``timeout`` (s, default 3600).

          Raises:
              OutputError: missing/empty ``sql``, non-string items, unsupported driver, timeout <= 0.
          """

      def _guarded_statements(self) -> List[str]:
          """Run ``sql_guard`` over every script and return the flattened statements.

          Raises:
              OutputError: Rust extension missing (category ``infra``) or a statement blocked /
                  unparsable (category ``data``, message from the guard).
          """

      async def _execute(self, statements: List[str]) -> List[str]:
          """Execute ``statements`` in one transaction and return their status tags.

          Raises:
              OutputError: any database error (the transaction is rolled back).
          """

      async def run(self) -> Union[dict, pd.DataFrame]:
          """Guard, execute, store ``self.results``.

          Returns:
              ``self.data`` unchanged.
          """
  ```

### Module 4: Registry entry + write gate
- **Path**: `querysource/outputs/destinations/__init__.py`, `querysource/queries/multi/__init__.py` (modify)
- **Responsibility**: Register `"ExecuteSQL"` and add it to `WRITE_DESTINATIONS`.
- **Depends on**: M3; **FEAT-155 merged** (it introduces `WRITE_DESTINATIONS`)
- **Interface Skeleton**:
  ```python
  # querysource/outputs/destinations/__init__.py — new try/except block after the "TableDelete" block (FEAT-155)
  from querysource.queries.multi.destinations.execute_sql import ExecuteSQLDestination
  DESTINATION_REGISTRY["ExecuteSQL"] = ExecuteSQLDestination
  # querysource/queries/multi/__init__.py
  WRITE_DESTINATIONS: frozenset[str] = frozenset({"TableDelete", "ExecuteSQL"})
  ```

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `cargo test --no-default-features sql_guard` | M1 | Rust unit tests listed under M1 |
| `test_sql_guard_blocks_*` (`tests/test_sql_guard.py`) | M2 | each blocked kind via Python raises `ValueError` |
| `test_sql_guard_allows_dml_and_cte` | M2 | the full employee_detail_profile data-modifying CTE → 1 statement |
| `test_sql_guard_ignores_keywords_in_literals` | M2 | `SELECT 'drop table x'`, `-- DROP`, `/* DROP */`, `$$DROP$$` allowed |
| `test_sql_guard_splits` | M2 | `DELETE …; INSERT …;` → 2 statements, `';'` inside literal not split |
| `test_sql_guard_unterminated` | M2 | `SELECT 'abc` → `ValueError` |
| `test_execsql_init_validation` (`tests/test_destination_execute_sql.py`) | M3 | missing sql, bad driver, timeout ≤ 0 → `OutputError` |
| `test_execsql_blocked_never_connects` | M3 | `DROP TABLE x` → `OutputError`; `AsyncDB` never instantiated |
| `test_execsql_no_rust_fails_closed` | M3 | `HAS_RUST=False` patched → `OutputError` |
| `test_execsql_runs_in_one_transaction` | M3 | mocked raw conn: `SET LOCAL statement_timeout` first, statements in order, inside `transaction()`; `results` = status tags |
| `test_execsql_db_error_wrapped` | M3 | asyncpg error on stmt 2 → `OutputError`, transaction exited with exception |
| `test_execsql_passthrough` | M3 | return value `is` input data |
| `test_registry_has_executesql` | M4 | `get_destination("ExecuteSQL")` |
| `test_preflight_gate_executesql` | M4 | `ExecuteSQL` in Output triggers the `pg_admin` gate |

### Integration Tests
| Test | Description |
|---|---|
| `test_executesql_postgres_roundtrip` | (skipped without live `DB*` Postgres) `DELETE` range + `INSERT … SELECT` commit together; a failing 2nd statement rolls back the 1st |

### Test Data / Fixtures
```python
@pytest.fixture
def refresh_sql() -> str:
    return ("DELETE FROM wm_assembly.employee_detail_profile "
            "WHERE activity_date >= (date_trunc('month', CURRENT_DATE) - INTERVAL '5 months')::date "
            "AND activity_date < CURRENT_DATE")
```

---

## 5. Acceptance Criteria

- [ ] `cd rust && cargo test --no-default-features` passes (includes `sql_guard` tests).
- [ ] Extension rebuilt (`maturin develop` from `rust/`) and `python -c "from querysource.qs_parsers import sql_guard"` works.
- [ ] `pytest tests/test_sql_guard.py tests/test_destination_execute_sql.py tests/test_rust_parsers.py -v` passes.
- [ ] Existing destination tests still pass (`tests/test_destination_table.py`, `tests/test_multiqs_destination_dispatch.py`, `tests/test_destinations_documentation_endpoint.py`).
- [ ] `ruff check` is clean on the new and modified Python files.
- [ ] Every statement kind in the §2 blocked table is rejected **before any database connection is opened**.
- [ ] Keywords inside string literals, quoted identifiers, dollar-quoted bodies and comments never trigger a block, and never cause a split.
- [ ] All statements of one `ExecuteSQL` step commit together or not at all.
- [ ] `ExecuteSQL` connects with `default_dsn` (`DB*`) and never `asyncpg_url` (`PG_*`).
- [ ] With `HAS_RUST = False`, `ExecuteSQL` raises `OutputError` and executes nothing.
- [ ] A MultiQuery with an `ExecuteSQL` output step is denied up-front for a principal without `datasource:use` on `pg_admin`.

---

## 6. Codebase Contract

### Verified Imports
```python
from querysource.outputs.destinations.abstract import AbstractDestination  # verified: querysource/outputs/destinations/abstract.py:19
from querysource.exceptions import OutputError                            # verified: querysource/exceptions.py:104
from querysource.conf import default_dsn                                  # verified: querysource/conf.py:32
from querysource.qs_parsers import HAS_RUST                               # verified: querysource/qs_parsers/__init__.py:16,26
from asyncdb import AsyncDB                                               # verified: querysource/interfaces/connections.py:11
# after M2:
from querysource.qs_parsers import sql_guard                              # created by this feature
```

### Existing Class Signatures
```python
# querysource/outputs/destinations/abstract.py
class AbstractDestination(SchemaIntrospectable, ABC):   # line 19
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:  # line 30
    async def run(self) -> Union[dict, pd.DataFrame]:    # line 61 (abstract)

# querysource/qs_parsers/__init__.py — `from ._qs_parsers import *` (line 12) + explicit
# `safe_format_map_validated` re-export (lines 15, 23); HAS_RUST (lines 16, 24, 26)

# rust/src/lib.rs
#[pymodule] fn _qs_parsers(m: &Bound<'_, PyModule>) -> PyResult<()>   # line 35
m.add_function(wrap_pyfunction!(safe_dict::safe_format_map_validated, m)?)?;  # line 57
// mod list lines 8-23; pyo3 = "0.29", no SQL-grammar crate in rust/Cargo.toml

# asyncdb (installed)
AbstractDriver.engine = get_connection   # interfaces/abstract.py:69 → raw asyncpg.Connection
pg.transaction()/commit()/rollback()     # drivers/pg.py:1069/1076/1083 (asyncdb-level; this feature uses raw asyncpg transaction instead)
```

### Integration Points
| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `sql_guard` | `_qs_parsers` module | `wrap_pyfunction!` | `rust/src/lib.rs:57` (pattern) |
| `ExecuteSQLDestination` | `DESTINATION_REGISTRY` | dict assignment | `querysource/outputs/destinations/__init__.py:196-225` |
| `ExecuteSQLDestination` | MultiQS Output loop | `get_destination(step_name)` | `querysource/queries/multi/__init__.py:816` |
| write gate | `WRITE_DESTINATIONS` | frozenset membership | created by FEAT-155 |

### Does NOT Exist (Anti-Hallucination)
- ~~A SQL grammar/AST parser in `rust/`~~. The existing `*_parser.rs` modules build and format WHERE/ORDER/LIMIT fragments, and `safe_dict.rs` checks **values** for injection markers (`safe_dict.rs:17-33`). None of them classify statements. `sql_guard.rs` is new.
- ~~`sqlparser` / `sqlglot` dependencies~~. Neither is present in `rust/Cargo.toml` or `pyproject.toml`, and this feature adds none.
- ~~`querysource.queries.multi.destinations.execute_sql`~~ — created here.
- ~~Flowtask imports (`flowtask.components.ExecuteSQL`, `TemplateSupport`, `QSSupport`, `_taskstore`)~~ — not available in QuerySource.
- ~~`WRITE_DESTINATIONS`~~ does not exist until FEAT-155 is merged.

### Edit Sites (Blueprint Anchors)
Verified against: `2a9f19b`

| File | Action | Verbatim anchor line | Verified at | Occurrences |
|---|---|---|---|---|
| `rust/src/sql_guard.rs` | CREATE | — | — | — |
| `rust/src/lib.rs` | MODIFY | `mod safe_dict;` | `lib.rs:20` | 1 |
| `rust/src/lib.rs` | MODIFY | `    m.add_function(wrap_pyfunction!(safe_dict::safe_format_map_validated, m)?)?;` | `lib.rs:57` | 1 |
| `querysource/qs_parsers/__init__.py` | MODIFY | `    from ._qs_parsers import safe_format_map_validated  # noqa: F401` | `__init__.py:15` | 1 |
| `querysource/qs_parsers/__init__.py` | MODIFY | `        from _qs_parsers import safe_format_map_validated  # noqa: F401` | `__init__.py:23` | 1 |
| `querysource/queries/multi/destinations/execute_sql.py` | CREATE | — | — | — |
| `querysource/outputs/destinations/__init__.py` | MODIFY | `    DESTINATION_REGISTRY["TableDelete"] = TableDeleteDestination` (from FEAT-155) | (after FEAT-155) | 1 |
| `querysource/queries/multi/__init__.py` | MODIFY | `WRITE_DESTINATIONS: frozenset[str] = frozenset({"TableDelete"})` (from FEAT-155) | (after FEAT-155) | 1 |
| `tests/test_sql_guard.py` | CREATE | — | — | — |
| `tests/test_destination_execute_sql.py` | CREATE | — | — | — |

---

## 7. Implementation Notes & Constraints

### Patterns to Follow
- Rust: pure-Rust core (`guard`, `split_statements`, `classify`) and a thin `#[pyfunction]` wrapper. Map `Err(String)` to `PyValueError::new_err`. Tests run with `cargo test --no-default-features` (see `rust/Cargo.toml` features comment).
- Python destination mirrors `TableDestination` for `_catalog`, `self.logger`, `OutputError` categories.
- Connection: `AsyncDB("pg", dsn=default_dsn)` → `async with await db.connection() as conn: raw = conn.engine()` → `async with raw.transaction(): …`.

### Known Risks / Gotchas
- **The guard is lexical, not semantic.** It cannot see DDL executed inside functions or
  procedures (`SELECT f()`, `CALL p()`, `dblink_exec`). Mitigations: the PBAC write gate,
  and the fact that `DO` blocks (the ad-hoc way to run dynamic SQL) are blocked.
- **Not atomic across Output steps.** `ExecuteSQL` commits before the following `Table` step
  runs. If `Table` fails, the deleted range stays deleted until a re-run, and the
  delete-then-append is idempotent. For true atomicity, write the whole refresh as a
  single `ExecuteSQL` data-modifying CTE.
- Scripts copied from psql often contain `BEGIN;` / `COMMIT;`. These are rejected with a
  clear message (`transaction_control`) instead of being silently stripped.
- `TRUNCATE` is blocked even though `Table(method=truncate)` exists; the explicit `Table`
  method stays the sanctioned way.
- Changing `rust/src/*.rs` requires rebuilding the extension before pytest sees
  `sql_guard` (exclusive resource, see Worktree Strategy).

### External Dependencies
None new.

---

## 8. Open Questions

- [ ] Should `CALL` (stored procedures) be allowed? It is currently allowed, on the grounds that procedures are pre-reviewed server code. — *Owner: Jesus Lara*
- [ ] Should `CREATE … ` / `ALTER … ADD` DDL be allowed at all, or only DML (`WITH/SELECT/INSERT/UPDATE/DELETE/MERGE`)? The spec follows the request and blocks only destructive DDL. — *Owner: Jesus Lara*
- [ ] Is `datasource:use` on `pg_admin` the right grant (shared with FEAT-155)? — *Owner: Jesus Lara*
- [ ] Future: optional `{placeholder}` substitution from pipeline conditions via `safe_format_map_validated`? — *Owner: Juan2coder*

---

## 9. Design Research Cross-Check

> Model: — · Status: skipped (no accepted exploration document; spec scaffolded from a direct request)

| # | Suggestion (kind) | Disposition | Reason | Landed in |
|---|---|---|---|---|

Summary: **0** confirmed · **0** rejected · **0** escalated.

---

## Worktree Strategy
- Isolation: one feature worktree `.claude/worktrees/feat-FEAT-156-multi-executesql`.
- Module graph: M2 → M1 (registers `sql_guard`). M3 → M2 (imports `sql_guard`). M4 → M3 (imports `ExecuteSQLDestination`).
- Shared files: `rust/src/lib.rs` (M2 only); none shared between modules.
- Exclusive resources: rebuilding the Rust extension (`maturin develop`) is required after M1/M2 and before the M3 tests, so M2 is `parallel: false`.
- Cross-feature: **FEAT-155 (`multi-tabledelete`) must be merged first**. It creates `WRITE_DESTINATIONS` and the `TableDelete` registry block that M4 anchors on.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-09-29 | Juan2coder | Initial draft |
