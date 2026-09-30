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
**Status**: approved
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
| `SET`/`RESET` with a `ROLE` or `AUTHORIZATION` token (`SET ROLE`, `SET SESSION AUTHORIZATION`, `RESET ROLE`) | blocked `role` (added at task review) |
| `SET [SESSION\|LOCAL]` of `standard_conforming_strings`, `backslash_quote`, `escape_string_warning`, `statement_timeout`, `lock_timeout`, `idle_in_transaction_session_timeout`, `transaction_timeout` | blocked `setting` (other `SET`, e.g. `SET search_path`, allowed) |
| `RESET` (any parameter, incl. `RESET ALL`) other than the `role` cases above | blocked `setting` (0.4) |
| `SET TRANSACTION …` / `SET SESSION CHARACTERISTICS …`, `PREPARE TRANSACTION` | blocked `transaction_control` |
| `CREATE [OR REPLACE] [CONSTRAINT] [TRUSTED] [PROCEDURAL] FUNCTION \| PROCEDURE \| TRIGGER \| EXTENSION \| RULE \| AGGREGATE \| OPERATOR \| LANGUAGE \| EVENT TRIGGER \| TRANSFORM \| CAST` (also as a `CREATE SCHEMA` element); `ALTER FUNCTION \| PROCEDURE \| ROUTINE \| EXTENSION …` | blocked `executable_object` (0.4) — user-defined code would bypass every other rule |
| `ALTER SYSTEM …`; `ALTER DATABASE … SET\|RESET …` (`ALTER ROLE\|USER … SET\|RESET` stays `role`) | blocked `setting` (0.5) — changes settings of future sessions |
| `LOAD '…'`; `ALTER EVENT TRIGGER …` | blocked `executable_object` (0.5) |
| `CREATE\|ALTER SERVER`, `CREATE\|ALTER FOREIGN DATA WRAPPER`, `CREATE\|ALTER FOREIGN TABLE`, `CREATE\|ALTER USER MAPPING`, `IMPORT FOREIGN SCHEMA` | blocked `foreign_access` (0.5) — reaching other servers would bypass the guard |
| `COPY` (any direction or target) | blocked `copy` (0.4, replaces `copy_program`) — no maintenance use, and `COPY … FROM STDIN` would hang the simple protocol |
| `BEGIN`, `START`, `COMMIT`, `END`, `ROLLBACK`, `SAVEPOINT`, `RELEASE`, `ABORT` | blocked `transaction_control`, because the component owns the transaction |
| anything else (`WITH`, `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `CREATE TABLE/INDEX/VIEW/MATERIALIZED VIEW/SCHEMA/SEQUENCE`, `ALTER … ADD`, `CALL`, `REFRESH MATERIALIZED VIEW`, `ANALYZE`, …) | allowed, unless the call rule below matches |

*Function-call rule (0.4).* Anywhere in a statement (outside literals and comments), an
identifier immediately followed by `(` (whitespace and comments ignored), case-insensitive
and optionally schema-qualified (`pg_catalog.set_config(`), is blocked `dangerous_function`
when it names one of: `set_config`, `dblink`, `dblink_exec`, `dblink_connect`, `dblink_open`,
`dblink_send_query`, `pg_read_file`, `pg_read_binary_file`, `pg_ls_dir`, `pg_stat_file`,
`lo_import`, `lo_export`, `lo_from_bytea`, `lo_put`, `pg_file_write`, `pg_terminate_backend`,
`pg_cancel_backend`, `pg_reload_conf`, `pg_rotate_logfile`. A quoted identifier used as the
function name (`"set_config"(`) and a `U&"…"` identifier (default escapes decoded) count as
calls; a column or table with such a name and no `(` after it, or the name inside a literal,
is allowed. A `U&"…"` identifier followed by `UESCAPE` cannot be decoded reliably and is
blocked `unparsable`, as is any statement the lexer cannot read.

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

**Shared guarded-SQL executor (`querysource/interfaces/guarded_sql.py`).** The guard and the
execution logic live in a module that **does not depend on MultiQuery destinations**. FEAT-157
(`multi-source-hooks`) reuses it for the pre/post-hooks. Its contract:
1. `guard_statements(sql)`:
   - Normalizes `sql` (str | list[str]) to a list.
   - Runs `sql_guard` on each script and returns the flattened statements in order.
   - `HAS_RUST` False → `GuardedSQLError(category="infra")`.
   - `ValueError` from the guard → `GuardedSQLError(category="data")`.
   - **Nothing reaches the database when any statement is blocked.**
2. `execute_guarded(statements, timeout=…)`:
   - Connects with `AsyncDB("pg", dsn=default_dsn)`, then `raw = conn.engine()`.
   - Inside `raw.transaction()`: `SET LOCAL statement_timeout = <ms>`, then
     `status = await raw.execute(stmt)` for each statement.
   - Returns the status tags (`"DELETE 1234"`, `"INSERT 0 900"`).
   - Any database error → `GuardedSQLError(category="infra")`, and the transaction is rolled back.

Statements are executed one by one, not as a single multi-statement string. That way the
exact guarded strings are what runs, and each status tag is attributable.

`ExecuteSQLDestination.run()`:
1. Call `guard_statements(self._sql)`, then `execute_guarded(...)`, and store the tags in
   `self.results`.
2. `GuardedSQLError` → `OutputError(str(err), category=err.category)`.
3. Log the results. Return `self.data`.

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
          └─ interfaces/guarded_sql.py   (shared; reused by FEAT-157 hooks)
               ├─ guard_statements() → _qs_parsers.sql_guard(script)  (Rust: lex → split → classify)
               └─ execute_guarded()  → AsyncDB("pg", dsn=default_dsn) → raw asyncpg conn
                     └─ transaction: SET LOCAL statement_timeout → execute(stmt₁) … execute(stmtₙ)
        TableDestination.run()   (optional re-insert)
```

### Integration Points
| Existing Component | Integration Type | Notes |
|---|---|---|
| `_qs_parsers` PyO3 module (`rust/src/lib.rs`) | extends | new `mod sql_guard;` + `wrap_pyfunction!(sql_guard::sql_guard, m)` |
| `querysource/qs_parsers/__init__.py` | modifies | explicit re-export of `sql_guard` in both import branches |
| `QueryException` | extends | new `GuardedSQLError` in `interfaces/guarded_sql.py` |
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

# querysource/interfaces/guarded_sql.py
class GuardedSQLError(QueryException): category: str
def guard_statements(sql: str | list[str]) -> list[str]: ...
async def execute_guarded(statements: list[str], *, timeout: float = 3600.0) -> list[str]: ...

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
| M2b: Shared guarded-SQL executor | yes | `GuardedSQLError`, `guard_statements`, `execute_guarded` contracts in §2; one transaction, `SET LOCAL statement_timeout` | — |
| M3: ExecuteSQLDestination | yes | thin wrapper over M2b; `GuardedSQLError` → `OutputError(category=…)` | — |
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

### Module 2b: Shared guarded-SQL executor
- **Path**: `querysource/interfaces/guarded_sql.py` (new)
- **Responsibility**: Guard SQL with the Rust guard and execute it on PostgreSQL with `DB*` credentials in one transaction. It has no dependency on MultiQuery, and FEAT-157 hooks use it.
- **Depends on**: M2
- **Interface Skeleton**:
  ```python
  # querysource/interfaces/guarded_sql.py  (new)
  from typing import List, Union
  from asyncdb import AsyncDB                      # verified: querysource/interfaces/connections.py:11
  from querysource.conf import default_dsn         # verified: querysource/conf.py:32
  from querysource.exceptions import QueryException  # verified: querysource/exceptions.py:6
  from querysource.qs_parsers import HAS_RUST      # verified: querysource/qs_parsers/__init__.py:16,26

  class GuardedSQLError(QueryException):
      """Guard rejection (category ``data``) or missing extension / DB failure (category ``infra``)."""
      def __init__(self, message: str, *, category: str) -> None: ...

  def guard_statements(sql: Union[str, List[str]]) -> List[str]:
      """Return the flattened, guard-approved statements of every script, in order.

      Raises:
          GuardedSQLError: empty input or non-str items (``data``); ``HAS_RUST`` False (``infra``);
              a blocked or unparsable statement (``data``, the guard's message).
      """

  async def execute_guarded(statements: List[str], *, timeout: float = 3600.0) -> List[str]:
      """Execute ``statements`` in ONE transaction on ``default_dsn`` and return their status tags.

      Raises:
          GuardedSQLError: any connection or statement error (``infra``); the transaction is rolled back.
      """
  ```

### Module 3: ExecuteSQLDestination
- **Path**: `querysource/queries/multi/destinations/execute_sql.py` (new)
- **Responsibility**: A thin Output wrapper over M2b. Pass the data through.
- **Depends on**: M2b
- **Interface Skeleton**:
  ```python
  # querysource/queries/multi/destinations/execute_sql.py  (new)
  from typing import List, Union
  import pandas as pd
  from querysource.exceptions import OutputError  # verified: querysource/exceptions.py:104
  from querysource.interfaces.guarded_sql import GuardedSQLError, execute_guarded, guard_statements  # created by M2b
  from querysource.outputs.destinations.abstract import AbstractDestination  # verified: querysource/outputs/destinations/abstract.py:19

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

      async def run(self) -> Union[dict, pd.DataFrame]:
          """``guard_statements`` → ``execute_guarded``; store ``self.results``.

          Returns:
              ``self.data`` unchanged.
          Raises:
              OutputError: wraps ``GuardedSQLError`` keeping its ``category``.
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
| `test_guard_statements_flattens` (`tests/test_guarded_sql.py`) | M2b | list of scripts → statements in order |
| `test_guard_statements_blocked` | M2b | `DROP TABLE x` → `GuardedSQLError(category="data")` |
| `test_guard_statements_no_rust_fails_closed` | M2b | `HAS_RUST=False` patched → `GuardedSQLError(category="infra")` |
| `test_execute_guarded_one_transaction` | M2b | mocked raw conn: `SET LOCAL statement_timeout` first, statements in order, inside `transaction()`; returns status tags |
| `test_execute_guarded_db_error` | M2b | asyncpg error on stmt 2 → `GuardedSQLError(category="infra")`, transaction exited with exception |
| `test_execute_guarded_uses_default_dsn` | M2b | `AsyncDB` built with `default_dsn`, never `asyncpg_url` |
| `test_execsql_init_validation` (`tests/test_destination_execute_sql.py`) | M3 | missing sql, bad driver, timeout ≤ 0 → `OutputError` |
| `test_execsql_blocked_never_connects` | M3 | `DROP TABLE x` → `OutputError(category="data")`; `AsyncDB` never instantiated |
| `test_execsql_wraps_guarded_error` | M3 | `GuardedSQLError` → `OutputError` with the same `category` |
| `test_execsql_passthrough` | M3 | return value `is` input data; `results` = status tags |
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
- [ ] Extension rebuilt and staged into the worktree's `querysource/qs_parsers/` (`maturin build` + copy, as in `make stage-rust`; never `maturin develop` into the shared venv), and `python -c "from querysource.qs_parsers import sql_guard"` works.
- [ ] `pytest tests/test_sql_guard.py tests/test_guarded_sql.py tests/test_destination_execute_sql.py tests/test_rust_parsers.py -v` passes.
- [ ] `querysource/interfaces/guarded_sql.py` imports nothing from `querysource.queries.multi` (reusable by FEAT-157).
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
from querysource.exceptions import QueryException                         # verified: querysource/exceptions.py:6
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
- ~~`querysource.interfaces.guarded_sql` / `GuardedSQLError`~~ — created here (M2b).
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
| `querysource/interfaces/guarded_sql.py` | CREATE | — | — | — |
| `querysource/queries/multi/destinations/execute_sql.py` | CREATE | — | — | — |
| `querysource/outputs/destinations/__init__.py` | MODIFY | `    DESTINATION_REGISTRY["TableDelete"] = TableDeleteDestination` (from FEAT-155) | (after FEAT-155) | 1 |
| `querysource/queries/multi/__init__.py` | MODIFY | `WRITE_DESTINATIONS: frozenset[str] = frozenset({"TableDelete"})` (from FEAT-155) | (after FEAT-155) | 1 |
| `tests/test_sql_guard.py` | CREATE | — | — | — |
| `tests/test_guarded_sql.py` | CREATE | — | — | — |
| `tests/test_destination_execute_sql.py` | CREATE | — | — | — |

---

## 7. Implementation Notes & Constraints

### Patterns to Follow
- Rust: pure-Rust core (`guard`, `split_statements`, `classify`) and a thin `#[pyfunction]` wrapper. Map `Err(String)` to `PyValueError::new_err`. Tests run with `cargo test --no-default-features` (see `rust/Cargo.toml` features comment).
- Python destination mirrors `TableDestination` for `_catalog`, `self.logger`, `OutputError` categories.
- Connection: `AsyncDB("pg", dsn=default_dsn)` → `async with await db.connection() as conn: raw = conn.engine()` → `async with raw.transaction(): …`.

### Known Risks / Gotchas
- **The guard is lexical, not semantic.** It cannot see DDL executed inside *pre-existing*
  functions or procedures (`SELECT f()`, `CALL p()`). Mitigations: the PBAC write gate;
  `DO` blocks are blocked; since 0.4 the script cannot install its own code (`CREATE
  FUNCTION/PROCEDURE/TRIGGER/RULE/EXTENSION/…` → `executable_object`) nor call the
  configuration/escape functions (`set_config`, `dblink*`, file and large-object access,
  backend signalling → `dangerous_function`).
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

- [x] Allow `CALL`? — *Resolved by Juan2coder (2026-09-30), spec default accepted*: yes, allowed (procedures are pre-reviewed server code).
- [x] Allow non-destructive DDL (`CREATE …`, `ALTER … ADD`)? — *Resolved by Juan2coder (2026-09-30), spec default accepted*: yes. Only the §2 blocked table is rejected (destructive DDL, privileges, roles, `DO`, `COPY`, transaction control, and since 0.4 executable objects, `RESET` and dangerous function calls).
- [x] Grant — *Resolved by Juan2coder (2026-09-30), spec default accepted*: `datasource:use` on `pg_admin`, shared with FEAT-155.
- [x] Could arbitrary SQL bypass the `DROP` block via user-defined code? — *Resolved by Jesus Lara (2026-09-30), guard hardening approved*: yes, so the guard now blocks executable objects (`CREATE FUNCTION/PROCEDURE/TRIGGER/EXTENSION/RULE/AGGREGATE/OPERATOR/LANGUAGE/EVENT TRIGGER/TRANSFORM/CAST`, `ALTER FUNCTION/PROCEDURE/ROUTINE/EXTENSION`), all `RESET`, all `COPY`, and calls to configuration/escape functions (§2). Existing procedures stay callable via `CALL`.
- [x] `{placeholder}` substitution — *Resolved by Juan2coder (2026-09-30), spec default accepted*: follow-up, not in this feature.

---

## 9. Design Research Cross-Check

> Model: — · Status: skipped (no accepted exploration document; spec scaffolded from a direct request)

| # | Suggestion (kind) | Disposition | Reason | Landed in |
|---|---|---|---|---|

Summary: **0** confirmed · **0** rejected · **0** escalated.

---

## Worktree Strategy
- Isolation: one feature worktree `.claude/worktrees/feat-FEAT-156-multi-executesql`.
- Module graph: M2 → M1 (registers `sql_guard`). M2b → M2 (imports `sql_guard`). M3 → M2b (imports `guard_statements`/`execute_guarded`). M4 → M3 (imports `ExecuteSQLDestination`).
- Shared files: `rust/src/lib.rs` (M2 only); none shared between modules.
- Exclusive resources: rebuilding the Rust extension (`maturin develop`) is required after M1/M2 and before the M3 tests, so M2 is `parallel: false`.
- Cross-feature: **FEAT-155 (`multi-tabledelete`) must be merged first**. It creates `WRITE_DESTINATIONS` and the `TableDelete` registry block that M4 anchors on. **FEAT-157 (`multi-source-hooks`) depends on this feature** (M1, M2, M2b).

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-09-29 | Juan2coder | Initial draft |
| 0.2 | 2026-09-30 | Juan2coder | Extract guard + executor into `querysource/interfaces/guarded_sql.py` for reuse by FEAT-157 hooks |
| 0.3 | 2026-09-30 | Juan2coder | Task review: block `SET ROLE` / `SET SESSION AUTHORIZATION`; write gate also enforced on the HTTP handler (via FEAT-155 `write_access`) |
| 0.4 | 2026-09-30 | Juan2coder | Guard hardening approved by Jesus Lara: block executable objects, RESET, COPY, dangerous function calls |
| 0.5 | 2026-09-30 | Juan2coder | Guard follow-up (same approval): block `ALTER SYSTEM`, `ALTER DATABASE … SET/RESET` (`setting`), `LOAD`, `ALTER EVENT TRIGGER` (`executable_object`), foreign-data access (`foreign_access`) |
