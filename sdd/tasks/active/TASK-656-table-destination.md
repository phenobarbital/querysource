# TASK-656: Table Destination

**Feature**: FEAT-094 — MultiQuery New Destinations
**Spec**: `sdd/specs/multiquery-destinations.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-653
**Assigned-to**: unassigned

---

## Context

This task implements the `Table` destination component (called `TableDestination` in code to avoid name collision with the SQL concept). It writes a pandas DataFrame to a database table with configurable write modes: append, upsert, and truncate. Unlike the existing `TableOutput` (which is tightly coupled to SQLAlchemy and pandas `to_sql`), this destination uses asyncdb drivers and supports auto-table-creation with primary key specification.

The design draws from the `TableSource` pattern in ai-parrot (`parrot/tools/dataset_manager/sources/table.py`) for driver normalization and credential resolution, but the write logic leverages the existing `AbstractOutput` engine backends already in querysource.

Implements spec §3 Module 5.

---

## Scope

- Implement `TableDestination(AbstractDestination)` in `querysource/outputs/destinations/table.py`
- Support `driver` parameter: `pg`, `postgresql`, `postgres`, `mysql`, `bigquery`, `bq`
- Support `method` parameter:
  - `append`: insert rows (default)
  - `upsert`: INSERT ON CONFLICT UPDATE (delegates to engine's `db_upsert`)
  - `truncate`: TRUNCATE table then insert
- Support `pk` (primary key columns list) for upsert and auto-create
- Auto-table-creation: if the target table doesn't exist, create it from DataFrame dtypes
- Use the existing `AbstractOutput` engine backends (`PgOutput`, `BigQueryOutput`, etc.) for actual write operations
- Credential resolution via navconfig or explicit values
- Register `Table` in `DESTINATION_REGISTRY`
- Write unit tests

**NOT in scope**: DWH-specific drivers (DocumentDB, DynamoDB) — those are TASK-657. Modifying existing `TableOutput` class.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/outputs/destinations/table.py` | CREATE | TableDestination implementation |
| `querysource/outputs/destinations/__init__.py` | MODIFY | Register Table in DESTINATION_REGISTRY |
| `tests/test_destination_table.py` | CREATE | Unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# From TASK-653 (must be completed first)
from querysource.outputs.destinations.abstract import AbstractDestination  # created by TASK-653

# Existing TableOutput engines
from querysource.outputs.tables.TableOutput.postgres import PgOutput  # verified: querysource/outputs/tables/TableOutput/postgres.py
from querysource.outputs.tables.TableOutput.mysql import MysqlOutput  # verified: querysource/outputs/tables/TableOutput/mysql.py
from querysource.outputs.tables.TableOutput.bigquery import BigQueryOutput  # verified: querysource/outputs/tables/TableOutput/bigquery.py
from querysource.outputs.tables.TableOutput.abstract import AbstractOutput  # verified: querysource/outputs/tables/TableOutput/abstract.py:7

# Exceptions
from querysource.exceptions import OutputError, DataNotFound, DriverError  # verified: querysource/outputs/tables/TableOutput/table.py:5-9

# Logging
from navconfig.logging import logging  # verified: used across all modules

# AsyncDB for connections
from asyncdb import AsyncDB  # verified: querysource/connections.py usage pattern
```

### Existing Signatures to Use
```python
# querysource/outputs/tables/TableOutput/table.py:19
class TableOutput:
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:  # line 20
        self.flavor: str = kwargs.pop('flavor', 'postgresql')  # line 29
        self._truncate: bool = kwargs.get('truncate', False)  # line 30
    async def table_output(self, elem, datasource: pd.DataFrame):  # line 53
        # elem must have: .tablename, .schema, .pk, .if_exists, .foreign_key, .constraint
    async def run(self):  # line 118
        # Flavor-to-engine routing at lines 121-138

# querysource/outputs/tables/TableOutput/abstract.py:7
class AbstractOutput(metaclass=ABCMeta):
    def __init__(self, parent, dsn=None, do_update=True, only_update=False, external=False, **kwargs):  # line 13
    @property
    def is_external(self) -> bool:  # line 41
    @abstractmethod
    def connect(self):  # line 56
    @abstractmethod
    def db_upsert(self, table, conn, keys, data_iter):  # line 63
    @abstractmethod
    def write(self, table, schema, data, on_conflict='replace', pk=None):  # line 77
    @abstractmethod
    async def close(self):  # line 101

# querysource/outputs/tables/TableOutput/bigquery.py:9
class BigQueryOutput(BigQuery, AbstractOutput):
    async def db_upsert(self, table, schema, data, on_conflict='replace', pk=None, use_merge=None):  # line 38
    def connect(self):  # line 87
    async def close(self):  # line 100
```

### Does NOT Exist
- ~~`TableOutput.method`~~ — TableOutput has no `method` attribute; it uses `if_exists` for write mode
- ~~`TableOutput.auto_create()`~~ — no auto-create method exists on TableOutput
- ~~`AbstractOutput.truncate()`~~ — no truncate method on the abstract base
- ~~`querysource.outputs.destinations.table`~~ — does not exist yet; this task creates it
- ~~`asyncdb.AsyncDB.create_table()`~~ — AsyncDB has no direct create_table; use raw SQL DDL

---

## Implementation Notes

### Pattern to Follow

YAML configuration:
```yaml
- Table:
    driver: pg
    schema: troc
    table: stores
    method: append
    pk:
      - store_id
```

Key implementation flow:
1. `__init__`: Parse driver, schema, table, method, pk from kwargs. Normalize driver name (`postgresql` → `pg`, `bq` → `bigquery`).
2. `run()`:
   a. Map driver to existing engine class (same mapping as `TableOutput.run()` lines 121-138)
   b. If `method == 'truncate'`: execute `TRUNCATE TABLE schema.table` via the engine connection
   c. Create engine instance, connect
   d. For external engines (BigQuery, etc.): call `engine.db_upsert(table, schema, data, on_conflict, pk)`
   e. For SQLAlchemy engines (PgOutput, MysqlOutput): use pandas `to_sql` with appropriate method
   f. Close engine
   g. Return original `self.data`

### Driver Normalization
```python
DRIVER_MAP = {
    'pg': 'postgresql',
    'postgresql': 'postgresql',
    'postgres': 'postgresql',
    'mysql': 'mysql',
    'mariadb': 'mysql',
    'bigquery': 'bigquery',
    'bq': 'bigquery',
}
```

### Key Constraints
- Reuse existing engine classes — do NOT reimplement database write logic
- The `method` parameter maps to: `append` → `if_exists='append'`, `upsert` → standard upsert behavior, `truncate` → TRUNCATE then append
- Auto-create: when the table doesn't exist and engine is SQLAlchemy-based, `to_sql` with `if_exists='append'` auto-creates. For external engines, this may need explicit handling.
- `run()` must return original data unchanged

### References in Codebase
- `querysource/outputs/tables/TableOutput/table.py` — existing engine routing logic to follow
- `querysource/outputs/tables/TableOutput/postgres.py` — PgOutput engine example

---

## Acceptance Criteria

- [ ] `TableDestination` class exists at `querysource/outputs/destinations/table.py`
- [ ] Supports `driver` parameter with normalization (pg, postgresql, postgres, mysql, bigquery, bq)
- [ ] Supports `method` parameter: append, upsert, truncate
- [ ] `pk` list used for upsert conflict resolution
- [ ] Reuses existing engine classes (PgOutput, MysqlOutput, BigQueryOutput)
- [ ] Registered in `DESTINATION_REGISTRY` under `"Table"`
- [ ] Returns original DataFrame after write (pass-through)
- [ ] All tests pass: `pytest tests/test_destination_table.py -v`
- [ ] No linting errors: `ruff check querysource/outputs/destinations/table.py`

---

## Test Specification

```python
# tests/test_destination_table.py
import pytest
import pandas as pd
from unittest.mock import AsyncMock, patch, MagicMock

from querysource.outputs.destinations.table import TableDestination


@pytest.fixture
def sample_df():
    return pd.DataFrame({
        "store_id": [1, 2, 3],
        "name": ["A", "B", "C"],
        "revenue": [100.0, 200.0, 300.0],
    })


@pytest.fixture
def pg_table_config():
    return {
        "driver": "pg",
        "schema": "troc",
        "table": "stores",
        "method": "append",
        "pk": ["store_id"],
    }


class TestTableDestination:
    def test_initialization(self, sample_df, pg_table_config):
        dest = TableDestination(data=sample_df, **pg_table_config)
        assert dest.data is sample_df
        assert dest._table == "stores"
        assert dest._schema == "troc"
        assert dest._method == "append"

    def test_driver_normalization(self, sample_df):
        dest = TableDestination(data=sample_df, driver="postgres", schema="public", table="t")
        assert dest._normalized_driver == "postgresql"

        dest2 = TableDestination(data=sample_df, driver="bq", schema="ds", table="t")
        assert dest2._normalized_driver == "bigquery"

    def test_invalid_driver_raises(self, sample_df):
        with pytest.raises(OutputError):
            TableDestination(data=sample_df, driver="unknown_db", schema="s", table="t")

    def test_invalid_method_raises(self, sample_df):
        with pytest.raises(OutputError):
            TableDestination(data=sample_df, driver="pg", schema="s", table="t", method="delete")

    @pytest.mark.asyncio
    async def test_run_returns_original_data(self, sample_df, pg_table_config):
        dest = TableDestination(data=sample_df, **pg_table_config)
        with patch.object(dest, "_write_to_table", new_callable=AsyncMock):
            result = await dest.run()
            assert result is sample_df
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiquery-destinations.spec.md` for full context
2. **Check dependencies** — verify TASK-653 is in `sdd/tasks/completed/`
3. **Read the existing TableOutput code** at `querysource/outputs/tables/TableOutput/table.py` to understand engine routing
4. **Verify the Codebase Contract** — confirm AbstractDestination and engine classes exist
5. **Update status** in `sdd/tasks/index/multiquery-destinations.json` → `"in-progress"`
6. **Implement** following the scope, codebase contract, and notes above
7. **Verify** all acceptance criteria are met
8. **Move this file** to `sdd/tasks/completed/TASK-656-table-destination.md`
9. **Update index** → `"done"`
10. **Fill in the Completion Note** below

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
