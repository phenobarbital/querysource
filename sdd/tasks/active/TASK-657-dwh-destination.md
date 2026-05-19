# TASK-657: DWH Destination

**Feature**: FEAT-094 — MultiQuery New Destinations
**Spec**: `sdd/specs/multiquery-destinations.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-653, TASK-656
**Assigned-to**: unassigned

---

## Context

This task implements the `DWH` (Data Warehouse) destination component. It extends the `Table` destination concept for data warehouse targets that use asyncdb drivers directly: BigQuery, DocumentDB, and DynamoDB. These drivers have unique write semantics that differ from standard relational databases — DynamoDB uses `put_item`, DocumentDB uses MongoDB-style operations, and BigQuery has dataset-level operations.

Implements spec §3 Module 6.

---

## Scope

- Implement `DWHDestination(AbstractDestination)` in `querysource/outputs/destinations/dwh.py`
- Support `driver` parameter: `bigquery`, `documentdb`, `dynamodb`
- Support write modes:
  - `append`: insert all rows
  - `upsert`: update existing rows, insert new ones
  - `truncate`: clear target then insert
- Use asyncdb drivers directly for each backend:
  - BigQuery: `AsyncDB('bigquery', params={...})` — dataset.table writes
  - DocumentDB: `AsyncDB('mongo', params={...})` with SSL/TLS — collection writes
  - DynamoDB: `AsyncDB('dynamodb', params={...})` — table writes with `put_item`
- Schema inference from DataFrame dtypes for auto-table-creation
- Credential resolution via navconfig or explicit values
- Register `DWH` in `DESTINATION_REGISTRY`
- Write unit tests with mocked asyncdb calls

**NOT in scope**: Relational database drivers (pg, mysql) — those are handled by TASK-656 Table destination.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/outputs/destinations/dwh.py` | CREATE | DWHDestination implementation |
| `querysource/outputs/destinations/__init__.py` | MODIFY | Register DWH in DESTINATION_REGISTRY |
| `tests/test_destination_dwh.py` | CREATE | Unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# From TASK-653 (must be completed first)
from querysource.outputs.destinations.abstract import AbstractDestination  # created by TASK-653

# Existing DWH-related engines (reference, may reuse logic)
from querysource.outputs.tables.TableOutput.bigquery import BigQueryOutput  # verified: querysource/outputs/tables/TableOutput/bigquery.py:9
from querysource.outputs.tables.TableOutput.documentdb import DocumentDBOutput  # verified: querysource/outputs/tables/TableOutput/documentdb.py
from querysource.interfaces.databases.bigquery import BigQuery  # verified: querysource/interfaces/databases/bigquery.py

# AsyncDB
from asyncdb import AsyncDB  # verified: querysource/connections.py usage pattern

# Exceptions
from querysource.exceptions import OutputError, DriverError  # verified: querysource/outputs/tables/TableOutput/table.py:5-9

# Logging
from navconfig.logging import logging  # verified: used across all modules
```

### Existing Signatures to Use
```python
# querysource/outputs/tables/TableOutput/bigquery.py:9
class BigQueryOutput(BigQuery, AbstractOutput):
    def __init__(self, parent, dsn=None, do_update=True, only_update=False, external=True, **kwargs):  # line 17
    async def db_upsert(self, table, schema, data, on_conflict='replace', pk=None, use_merge=None):  # line 38
    def connect(self):  # line 87
    async def close(self):  # line 100

# AsyncDB usage pattern (from querysource/connections.py):
# db = AsyncDB('bigquery', params={'credentials': Path(...), 'project_id': '...'})
# async with await db.connection() as conn:
#     result = await conn.query(sql)

# querysource/datasources/drivers/bigquery.py — bigqueryDriver
# querysource/datasources/drivers/documentdb.py — documentdbDriver (uses mongo driver)
# querysource/datasources/drivers/dynamodb.py — dynamodbDriver
```

### Does NOT Exist
- ~~`querysource.outputs.destinations.dwh`~~ — does not exist yet; this task creates it
- ~~`AsyncDB.create_table()`~~ — no direct table creation method on AsyncDB
- ~~`DynamoDB.upsert()`~~ — DynamoDB has `put_item`, not upsert; upsert must be emulated
- ~~`DocumentDBOutput.truncate()`~~ — no truncate method; must execute `deleteMany({})` directly
- ~~`querysource.interfaces.databases.dynamodb`~~ — does not exist; use asyncdb driver directly

---

## Implementation Notes

### Pattern to Follow

YAML configuration:
```yaml
- DWH:
    driver: bigquery
    schema: analytics
    table: daily_metrics
    method: upsert
    pk:
      - date
      - store_id
    credentials:
      project_id: BIGQUERY_PROJECT_ID
      credentials: BIGQUERY_CREDENTIALS
```

### Driver-Specific Write Strategies

**BigQuery:**
- Use existing `BigQueryOutput` engine or `AsyncDB('bigquery', params={...})`
- `append`: load_table_from_dataframe with `WRITE_APPEND`
- `upsert`: use MERGE statement (BigQueryOutput already supports `use_merge=True`)
- `truncate`: `WRITE_TRUNCATE` disposition

**DocumentDB:**
- Use `AsyncDB('mongo', params={host, port, username, password, database, ssl: True, tlsCAFile: ...})`
- Convert DataFrame to list of dicts: `data.to_dict(orient='records')`
- `append`: `insert_many(records)`
- `upsert`: `update_one({pk_filter}, {"$set": record}, upsert=True)` for each record
- `truncate`: `delete_many({})` then `insert_many`

**DynamoDB:**
- Use `AsyncDB('dynamodb', params={region, access_key, secret_key})`
- Convert DataFrame to list of dicts
- `append`: batch `put_item` operations
- `upsert`: same as append (DynamoDB `put_item` is inherently upsert)
- `truncate`: scan + batch delete, then batch put

### Key Constraints
- Each driver should handle `ImportError` gracefully — DWH drivers are optional dependencies
- Handle large DataFrames by batching (e.g., 500 records at a time for DynamoDB)
- `run()` must return original data unchanged
- Log the number of records written and the target table for observability

### References in Codebase
- `querysource/outputs/tables/TableOutput/bigquery.py` — existing BigQuery write logic
- `querysource/outputs/tables/TableOutput/documentdb.py` — existing DocumentDB write logic
- `querysource/datasources/drivers/dynamodb.py` — DynamoDB driver config
- `querysource/datasources/drivers/documentdb.py` — DocumentDB driver config

---

## Acceptance Criteria

- [ ] `DWHDestination` class exists at `querysource/outputs/destinations/dwh.py`
- [ ] Supports `driver` parameter: bigquery, documentdb, dynamodb
- [ ] Supports `method` parameter: append, upsert, truncate
- [ ] BigQuery writes use dataset.table format via existing BigQueryOutput or asyncdb
- [ ] DocumentDB writes convert DataFrame to documents and use MongoDB operations
- [ ] DynamoDB writes batch `put_item` operations
- [ ] Missing driver packages raise clear error (not cryptic ImportError)
- [ ] Registered in `DESTINATION_REGISTRY` under `"DWH"`
- [ ] Returns original DataFrame after write (pass-through)
- [ ] All tests pass: `pytest tests/test_destination_dwh.py -v`
- [ ] No linting errors: `ruff check querysource/outputs/destinations/dwh.py`

---

## Test Specification

```python
# tests/test_destination_dwh.py
import pytest
import pandas as pd
from unittest.mock import AsyncMock, patch, MagicMock

from querysource.outputs.destinations.dwh import DWHDestination
from querysource.exceptions import OutputError


@pytest.fixture
def sample_df():
    return pd.DataFrame({
        "date": ["2025-01-01", "2025-01-02"],
        "store_id": [1, 2],
        "metric": [100.0, 200.0],
    })


@pytest.fixture
def bigquery_config():
    return {
        "driver": "bigquery",
        "schema": "analytics",
        "table": "daily_metrics",
        "method": "append",
        "pk": ["date", "store_id"],
        "credentials": {
            "project_id": "test-project",
            "credentials": "/path/to/creds.json",
        },
    }


class TestDWHDestination:
    def test_initialization(self, sample_df, bigquery_config):
        dest = DWHDestination(data=sample_df, **bigquery_config)
        assert dest.data is sample_df
        assert dest._driver == "bigquery"

    def test_invalid_driver(self, sample_df):
        with pytest.raises(OutputError):
            DWHDestination(data=sample_df, driver="redis", schema="s", table="t")

    def test_valid_dwh_drivers(self, sample_df):
        for driver in ("bigquery", "documentdb", "dynamodb"):
            dest = DWHDestination(data=sample_df, driver=driver, schema="s", table="t")
            assert dest._driver == driver

    @pytest.mark.asyncio
    async def test_run_returns_original_data(self, sample_df, bigquery_config):
        dest = DWHDestination(data=sample_df, **bigquery_config)
        with patch.object(dest, "_write_to_dwh", new_callable=AsyncMock):
            result = await dest.run()
            assert result is sample_df
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiquery-destinations.spec.md` for full context
2. **Check dependencies** — verify TASK-653 and TASK-656 are in `sdd/tasks/completed/`
3. **Read existing DWH engines**: `querysource/outputs/tables/TableOutput/bigquery.py` and `documentdb.py`
4. **Read asyncdb driver configs**: `querysource/datasources/drivers/bigquery.py`, `documentdb.py`, `dynamodb.py`
5. **Verify the Codebase Contract** — confirm imports and signatures still match
6. **Update status** in `sdd/tasks/index/multiquery-destinations.json` → `"in-progress"`
7. **Implement** following the scope, codebase contract, and notes above
8. **Verify** all acceptance criteria are met
9. **Move this file** to `sdd/tasks/completed/TASK-657-dwh-destination.md`
10. **Update index** → `"done"`
11. **Fill in the Completion Note** below

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
