# TASK-650: SourceTable Component

**Feature**: FEAT-093 — MultiQuery New Sources
**Spec**: `sdd/specs/multiquery-new-sources.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-644
**Assigned-to**: unassigned

---

## Context

Implements Spec Module 7: SourceTable. Connects to a database via `asyncdb.AsyncDB`,
executes `SELECT * FROM schema.table [WHERE filters]`, and returns the result as a
pandas DataFrame. Adapted from the ai-parrot `TableSource` pattern.

---

## Scope

- Create `SourceTable` class inheriting from `ThreadSource`.
- Accept `driver`, `schema`, `table`, and optional `filter` dict from config.
- Normalize driver aliases: `postgresql`/`postgres` → `pg`, `bq` → `bigquery`, `mariadb` → `mysql`.
- Validate table and schema names against SQL identifier regex.
- Build a `SELECT * FROM schema.table WHERE ...` query from the filter dict.
- Execute via `asyncdb.AsyncDB` with pandas output format.
- Support optional `dsn` or `credentials` dict for connection parameters.
- Write unit tests.

**NOT in scope**: Custom SQL queries, schema prefetch, column restrictions.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/sources/table.py` | CREATE | SourceTable implementation |
| `tests/test_source_table.py` | CREATE | Unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from .base import ThreadSource                # created by TASK-644
import pandas as pd                            # verified: file.py:8
import re                                      # standard library
```

### Existing Signatures to Use
```python
# ThreadSource base (created by TASK-644):
class ThreadSource(threading.Thread, ABC):
    def __init__(self, name: str, options: dict, request: web.Request, queue: asyncio.Queue): ...
    def resolve_credential(self, key: str, value: str) -> str: ...
    async def fetch(self) -> pd.DataFrame: ...  # abstract — implement this
    def run(self) -> None: ...  # inherited

# asyncdb.AsyncDB — database connection:
# from asyncdb import AsyncDB
# db = AsyncDB(driver, dsn=dsn)  OR  db = AsyncDB(driver, params=credentials_dict)
# async with await db.connection() as conn:
#     conn.output_format('pandas')
#     result, errors = await conn.query(sql)
# result is a pd.DataFrame when output_format is 'pandas'
```

### Reference: ai-parrot TableSource
```python
# ai-parrot/packages/ai-parrot/src/parrot/tools/dataset_manager/sources/table.py
# Key patterns to adapt:
#   - DRIVER_ALIASES = {"postgresql": "pg", "postgres": "pg", "bq": "bigquery", "mariadb": "mysql"}
#   - SQL_IDENTIFIER_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')
#   - Filter dict → WHERE clause: {"active": True, "type": "store"} → "WHERE active = true AND type = 'store'"
#   - AsyncDB usage: AsyncDB(driver, params=...) → conn.output_format('pandas') → conn.query(sql)
```

### Does NOT Exist
- ~~`querysource.queries.multi.sources.table`~~ — this task creates it
- ~~`querysource.datasources.table`~~ — no such module (datasources/drivers/ is for named DB drivers, not table sources)
- ~~`querysource.utils.sql`~~ — no SQL utility module exists
- ~~`ThreadSource.get_connection()`~~ — no connection method on the base class

---

## Implementation Notes

### Pattern to Follow

```python
import re
from asyncdb import AsyncDB

DRIVER_ALIASES = {
    "postgresql": "pg",
    "postgres": "pg",
    "bq": "bigquery",
    "mariadb": "mysql",
}

SQL_IDENTIFIER_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')


class SourceTable(ThreadSource):
    def __init__(self, name, options, request, queue):
        super().__init__(name, options, request, queue)
        driver = options.get('driver', 'pg')
        self._driver = DRIVER_ALIASES.get(driver, driver)
        self._schema = options.get('schema')
        self._table = options.get('table')
        self._filter = options.get('filter', {})
        self._dsn = options.get('dsn')
        self._credentials = options.get('credentials', {})
        # Validate identifiers
        if self._table and not SQL_IDENTIFIER_RE.match(self._table):
            raise ValueError(f"Invalid table name: {self._table}")
        if self._schema and not SQL_IDENTIFIER_RE.match(self._schema):
            raise ValueError(f"Invalid schema name: {self._schema}")

    def _build_where(self) -> str:
        if not self._filter:
            return ""
        clauses = []
        for col, val in self._filter.items():
            if not SQL_IDENTIFIER_RE.match(col):
                raise ValueError(f"Invalid column name in filter: {col}")
            if isinstance(val, bool):
                clauses.append(f"{col} = {str(val).lower()}")
            elif isinstance(val, (int, float)):
                clauses.append(f"{col} = {val}")
            elif isinstance(val, str):
                safe_val = val.replace("'", "''")
                clauses.append(f"{col} = '{safe_val}'")
            elif val is None:
                clauses.append(f"{col} IS NULL")
        return " WHERE " + " AND ".join(clauses)

    async def fetch(self) -> pd.DataFrame:
        table_ref = f"{self._schema}.{self._table}" if self._schema else self._table
        sql = f"SELECT * FROM {table_ref}{self._build_where()}"

        if self._dsn:
            db = AsyncDB(self._driver, dsn=self._dsn)
        elif self._credentials:
            resolved = {k: self.resolve_credential(k, v) for k, v in self._credentials.items()}
            db = AsyncDB(self._driver, params=resolved)
        else:
            db = AsyncDB(self._driver)

        async with await db.connection() as conn:
            conn.output_format('pandas')
            result, errors = await conn.query(sql)
            if errors:
                raise RuntimeError(f"Query error: {errors}")
        return result
```

### Key Constraints
- `asyncdb` is already a project dependency — no optional import needed.
- SQL identifier validation is critical to prevent injection — every table, schema, and filter column name must match `^[a-zA-Z_][a-zA-Z0-9_]*$`.
- Filter values: strings get single-quote-escaped, booleans become lowercase, numbers are literal, None becomes `IS NULL`.
- If no dsn or credentials provided, `AsyncDB` will use its default connection (from navconfig).

### References in Codebase
- `ai-parrot/.../sources/table.py` — reference implementation (lines 113-497)
- `querysource/interfaces/connections.py` — how querysource uses AsyncDB (lines 113-164)

---

## Acceptance Criteria

- [ ] `SourceTable` class at `querysource/queries/multi/sources/table.py`
- [ ] Inherits from `ThreadSource`
- [ ] Normalizes driver aliases (postgresql → pg, bq → bigquery, mariadb → mysql)
- [ ] Validates table/schema names against SQL identifier regex
- [ ] Builds WHERE clause from filter dict with proper escaping
- [ ] Connects via AsyncDB and returns DataFrame
- [ ] Supports dsn, credentials, or default connection
- [ ] Unit tests pass: `pytest tests/test_source_table.py -v`
- [ ] No linting errors

---

## Test Specification

```python
# tests/test_source_table.py
import asyncio
import pytest
from querysource.queries.multi.sources.table import SourceTable, DRIVER_ALIASES, SQL_IDENTIFIER_RE
from querysource.queries.multi.sources.base import ThreadSource


class TestSourceTable:
    def test_inherits_thread_source(self):
        assert issubclass(SourceTable, ThreadSource)

    def test_driver_normalization(self):
        options = {"driver": "postgresql", "table": "users"}
        source = SourceTable("tbl_test", options, None, asyncio.Queue())
        assert source._driver == "pg"

    def test_driver_normalization_bq(self):
        options = {"driver": "bq", "table": "users"}
        source = SourceTable("tbl_test", options, None, asyncio.Queue())
        assert source._driver == "bigquery"

    def test_invalid_table_name_raises(self):
        with pytest.raises(ValueError, match="Invalid table name"):
            SourceTable("test", {"driver": "pg", "table": "'; DROP TABLE--"}, None, asyncio.Queue())

    def test_invalid_schema_name_raises(self):
        with pytest.raises(ValueError, match="Invalid schema name"):
            SourceTable("test", {"driver": "pg", "table": "t", "schema": "1bad"}, None, asyncio.Queue())

    def test_build_where_empty(self):
        source = SourceTable("test", {"driver": "pg", "table": "t"}, None, asyncio.Queue())
        assert source._build_where() == ""

    def test_build_where_bool(self):
        source = SourceTable("test", {"driver": "pg", "table": "t", "filter": {"active": True}}, None, asyncio.Queue())
        assert "active = true" in source._build_where()

    def test_build_where_string_escaping(self):
        source = SourceTable("test", {"driver": "pg", "table": "t", "filter": {"name": "O'Brien"}}, None, asyncio.Queue())
        where = source._build_where()
        assert "O''Brien" in where

    def test_build_where_null(self):
        source = SourceTable("test", {"driver": "pg", "table": "t", "filter": {"deleted_at": None}}, None, asyncio.Queue())
        assert "IS NULL" in source._build_where()

    def test_sql_identifier_regex(self):
        assert SQL_IDENTIFIER_RE.match("valid_name")
        assert SQL_IDENTIFIER_RE.match("_private")
        assert not SQL_IDENTIFIER_RE.match("123bad")
        assert not SQL_IDENTIFIER_RE.match("no spaces")
        assert not SQL_IDENTIFIER_RE.match("semi;colon")
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiquery-new-sources.spec.md` for full context
2. **Check dependencies** — verify TASK-644 is completed
3. **Read `querysource/interfaces/connections.py`** for AsyncDB usage patterns in querysource
4. **Verify the Codebase Contract** — confirm ThreadSource exists, asyncdb.AsyncDB signature
5. **Implement** following the scope and notes above
6. **Verify** all acceptance criteria are met
7. **Move this file** to `sdd/tasks/completed/TASK-650-source-table.md`
8. **Update index** → `"done"`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
