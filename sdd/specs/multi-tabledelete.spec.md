---
type: feature
base_branch: dev
projects: [multiquery, outputs, auth]
tags: [multiquery, destinations, table-delete, flowtask-port, pbac]
---

# Feature Specification: MultiQuery TableDelete Destination

**Feature ID**: FEAT-155
**Date**: 2026-09-29
**Author**: Juan2coder (requested by Jesus Lara)
**Status**: approved
**Target version**: 5.2.0

---

## 1. Motivation & Business Requirements

### Problem Statement
Slugs run on QuerySource's **read-only** connection (`PG_*` credentials,
`asyncpg_url`, `querysource/conf.py:44`), so a slug cannot `DELETE` or `INSERT`
(observed: `cannot execute SELECT in a read-only transaction` when a
data-modifying CTE was run as a `db` slug). MultiQuery already writes through the
`Table` destination, which uses the full-access `DB*` credentials
(`sqlalchemy_url` / `async_default_dsn`, `querysource/conf.py:33-34`), but there
is no destination that **removes** rows. That makes "refresh a slice of a table" pipelines
impossible: `Table` only offers `append` / `upsert` / `truncate` / `drop`.

Flowtask already solves this with its `TableDelete` component
(`flowtask/flowtask/components/TableDelete.py`): it takes the primary-key
columns of the incoming DataFrame and deletes the matching rows from the target
table. Jesus Lara asked to port it (together with `ExecuteSQL`, FEAT-156) into
MultiQuery.

### Goals
- A new MultiQuery Output step `TableDelete` that deletes from
  `schema.table` every row whose `pk` tuple appears in the pipeline DataFrame,
  with the same semantics as the Flowtask component.
- Writes use the **same full-access `DB*` credentials that `Table` uses**, never the
  read-only `PG_*` ones.
- All-or-nothing: staging the keys and running the delete happen in **one transaction
  on one connection**, so a failure leaves the target table untouched.
- Pass-through: returns the input data unchanged, so a following `Table` step can
  re-insert the rows (delete-then-append pattern).
- A PBAC gate: a MultiQuery whose `Output` contains a write-capable destination
  requires the caller to hold the grant already used for the admin datasource
  (`datasource:use` on `pg_admin`, FEAT-091).
- Catalog entry (`_catalog`) so the Pipeline Editor lists and documents the step.

### Non-Goals (explicitly out of scope)
- Range / `WHERE`-predicate deletes (`DELETE … WHERE activity_date >= …`). That
  is `ExecuteSQL` (FEAT-156), which is the flexible escape hatch.
- Drivers other than PostgreSQL (Flowtask's component is PostgreSQL-only too;
  MySQL/BigQuery are follow-ups).
- A cross-step transaction spanning `TableDelete` + `Table` (see §7 Risks).
- Porting Flowtask's `multi` attribute mapping (per-name config per DataFrame of a dict).
  A `dict` of DataFrames is supported the way `Table` supports it: each frame is
  applied against the same target.

---

## 2. Architectural Design

### Overview
`TableDeleteDestination(AbstractDestination)` lives in
`querysource/queries/multi/destinations/table_delete.py` and is registered under
the step name `"TableDelete"`. Configuration:

```json
{"Output": [
  {"TableDelete": {"schema": "wm_assembly", "table": "employee_detail_profile",
                   "pk": ["associate_id", "activity_date"]}},
  {"Table": {"schema": "wm_assembly", "table": "employee_detail_profile",
             "method": "append"}}
]}
```

`run()` is a clean redesign of the Flowtask algorithm, not a line-by-line port.
The Flowtask version creates a *permanent* `<table>_deleted` table in the user
schema, uses two different engines, and swallows errors.

1. Validate `schema`, `table` and each `pk` name against the strict identifier rule
   `^[A-Za-z_][A-Za-z0-9_]*$` (anything else → `OutputError`, category `data`).
2. Every `pk` column must exist in the DataFrame. Rows with a NULL in any pk column
   are dropped, because `NULL = NULL` never matches, and the number dropped is logged. Duplicate key
   tuples are removed with `drop_duplicates`.
3. Open one asyncpg connection through asyncdb using `default_dsn` (the `DB*`
   credentials). Inside `raw.transaction()`:
   a. Introspect the target's pk column types:
      `SELECT a.attname, format_type(a.atttypid, a.atttypmod) FROM pg_attribute a
      WHERE a.attrelid = $1::regclass AND a.attname = ANY($2) AND NOT a.attisdropped`
      with `$1 = '"schema"."table"'`. A missing table or column raises `OutputError`
      (category `data`).
   b. `CREATE TEMP TABLE "_qs_del_<uuid8>" (<pk cols with introspected types>) ON COMMIT DROP`.
   c. `raw.copy_records_to_table(tmp, records=…, columns=pk)` to stage the keys.
      This is the raw asyncpg call. asyncdb's `copy_into_table` must not be used
      because it commits the open transaction (see §6).
   d. `DELETE FROM "schema"."table" t USING "_qs_del_x" d WHERE t."k1" = d."k1" AND …`.
      Parse the row count from the status string (`"DELETE 123"`).
4. Store the count on `self.deleted_rows` and log it. Return `self.data`.

Identifiers are always double-quoted after validation. Values never enter SQL
text; they travel only through `COPY` records.

### Component Diagram
```
MultiQS.query()
  ├─ _preflight_principal()  ──(Output has write step?)──→ enforce_principal(DATASOURCE,"pg_admin","datasource:use")
  └─ Output loop (__init__.py:805)
        get_destination("TableDelete") ─→ TableDeleteDestination(data=result, **cfg).run()
              └─ AsyncDB("pg", dsn=default_dsn) → raw asyncpg conn
                    └─ transaction: introspect → CREATE TEMP → COPY keys → DELETE … USING
        get_destination("Table") ─→ TableDestination.run()   (re-insert, optional)
```

### Integration Points
| Existing Component | Integration Type | Notes |
|---|---|---|
| `AbstractDestination` | extends | `run()` returns data pass-through |
| `DESTINATION_REGISTRY` (`outputs/destinations/__init__.py`) | registers | key `"TableDelete"`, same `try/except ImportError` block style |
| `MultiQS._preflight_principal` | modifies | adds write-destination gate |
| `querysource.conf.default_dsn` | uses | full-access `DB*` DSN (same creds as `Table`) |
| `asyncdb.AsyncDB` | uses | `pg` driver; `conn.engine()` exposes raw asyncpg connection |
| `ComponentRegistry` catalog | consumed by | `_catalog` dict, same shape as `TableDestination._catalog` |

### Data Models
No new Pydantic models; config arrives as `**kwargs` like every destination.

### New Public Interfaces
```python
class TableDeleteDestination(AbstractDestination):
    deleted_rows: int
    async def run(self) -> Union[dict, pd.DataFrame]: ...
```

---

## 3. Module Breakdown

#### Delegation-eligible modules

| Module | Eligible? | Decided patterns / exact contracts | Why not (if no) |
|---|---|---|---|
| M1: TableDeleteDestination | yes | algorithm §2 steps 1-4, identifier regex, temp table `ON COMMIT DROP`, raw asyncpg `copy_records_to_table`, `OutputError` categories | — |
| M2: Registry entry | yes | one `try/except ImportError` block, key `"TableDelete"` | — |
| M3: Write-destination PBAC gate | yes | `WRITE_DESTINATIONS` frozenset + one `enforce_principal` call | — |

### Module 1: TableDeleteDestination
- **Path**: `querysource/queries/multi/destinations/table_delete.py` (new)
- **Responsibility**: Delete target rows matching the DataFrame's pk tuples, in one transaction, using `DB*` credentials.
- **Depends on**: existing `AbstractDestination`, `asyncdb`, `querysource.conf`
- **Interface Skeleton**:
  ```python
  # querysource/queries/multi/destinations/table_delete.py  (new)
  import re
  from typing import List, Union
  import pandas as pd
  from querysource.conf import default_dsn               # verified: querysource/conf.py:32
  from querysource.exceptions import DataNotFound, OutputError  # verified: querysource/exceptions.py:53,104
  from querysource.outputs.destinations.abstract import AbstractDestination  # verified: querysource/outputs/destinations/abstract.py:19

  IDENTIFIER_RE: re.Pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

  class TableDeleteDestination(AbstractDestination):
      """Delete rows of ``schema.table`` whose ``pk`` tuple is present in the pipeline data.

      Step name: ``TableDelete``. PostgreSQL only. Returns the input data unchanged.
      """
      _catalog: dict  # display_name "TableDelete", icon "trash", attributes schema/table/pk, json_schema, example

      def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
          """Read ``schema`` (default ``public``), ``table`` (required), ``pk`` (required, non-empty list).

          Raises:
              OutputError: missing ``table``/``pk`` or an identifier failing ``IDENTIFIER_RE``.
          """

      def _key_frame(self, df: pd.DataFrame) -> pd.DataFrame:
          """Return ``df[pk]`` without NULL-key rows and duplicates.

          Raises:
              OutputError: a ``pk`` column is absent from ``df`` (category ``data``).
          """

      async def _delete_keys(self, keys: pd.DataFrame) -> int:
          """Stage ``keys`` in a temp table and run ``DELETE … USING`` in one transaction.

          Returns:
              Number of deleted rows.
          Raises:
              OutputError: table/column not found, or any database error (rolled back).
          """

      async def run(self) -> Union[dict, pd.DataFrame]:
          """Apply the delete for a DataFrame or each DataFrame of a dict; set ``deleted_rows``.

          Returns:
              ``self.data`` unchanged.
          Raises:
              DataNotFound: data is empty / every frame is empty.
              OutputError: on validation or database failure.
          """
  ```

### Module 2: Destination registry entry
- **Path**: `querysource/outputs/destinations/__init__.py` (modify)
- **Responsibility**: Register `"TableDelete"` → `TableDeleteDestination`.
- **Depends on**: Module 1
- **Interface Skeleton**:
  ```python
  # modifies querysource/outputs/destinations/__init__.py:227 (after the "Table" block, verified: :219-225)
  try:
      from querysource.queries.multi.destinations.table_delete import TableDeleteDestination
      DESTINATION_REGISTRY["TableDelete"] = TableDeleteDestination
  except ImportError:
      _pkg_logger.debug("TableDelete destination not available")
  ```

### Module 3: Write-destination PBAC gate
- **Path**: `querysource/queries/multi/__init__.py` (modify)
- **Responsibility**: Before any child runs, when `self._options["Output"]` contains a
  step listed in `WRITE_DESTINATIONS`, call
  `enforce_principal(self._principal, ResourceType.DATASOURCE, "pg_admin", "datasource:use", tenant=…, logger=…)`.
  If the principal is None, the existing early `return` applies, so PBAC-off behaviour does not change.
- **Depends on**: none (only knows step *names*)
- **HTTP path (added at task review)**: `QueryHandler` builds `MultiQS(...)` without `principal=` (`querysource/handlers/multi.py:493`), so `_preflight_principal` never fires for API callers. The same gate is therefore also added in `QueryHandler._preflight_multiquery` (`handlers/multi.py:29`). A new keyword-only `write_access: bool = False` triggers `_enforce_pbac(request, ResourceType.DATASOURCE, "pg_admin", "datasource:use")`, computed at the call site from the inline payload's `Output` against `WRITE_DESTINATIONS`. Stored multi slugs are not inspected at HTTP level, because their `Output` comes from the stored definition.
- **Interface Skeleton**:
  ```python
  # querysource/queries/multi/__init__.py  (modifies _preflight_principal, verified: :239; insert before `if has_raw_child:` :271)
  WRITE_DESTINATIONS: frozenset[str] = frozenset({"TableDelete"})  # module level; FEAT-156 adds "ExecuteSQL"

  def _output_step_names(output: object) -> set[str]:
      """Return the step names of an ``Output`` list (``[{name: cfg}, …]``); ignore malformed entries."""
  ```

---

## 4. Test Specification

### Unit Tests (`tests/test_destination_table_delete.py`)
| Test | Module | Description |
|---|---|---|
| `test_init_requires_table_and_pk` | M1 | missing `table` or empty `pk` → `OutputError` |
| `test_init_rejects_bad_identifiers` | M1 | `table="t; drop"`, `pk=["a b"]`, `schema='x"y'` → `OutputError` |
| `test_key_frame_missing_column` | M1 | pk column not in df → `OutputError` |
| `test_key_frame_drops_nulls_and_dupes` | M1 | NULL-key rows and duplicate tuples removed |
| `test_run_empty_dataframe` | M1 | empty df → `DataNotFound` |
| `test_run_passthrough` | M1 | mocked `_delete_keys`; returned object `is` input; `deleted_rows` set |
| `test_run_dict_of_frames` | M1 | each non-empty frame deleted, counts summed |
| `test_delete_sql_shape` | M1 | mocked asyncpg conn: temp table `ON COMMIT DROP`, `copy_records_to_table` (not `copy_into_table`), quoted `DELETE … USING`, all inside `transaction()` |
| `test_delete_error_wrapped` | M1 | asyncpg error → `OutputError`, transaction context exited with exception |
| `test_registry_has_tabledelete` | M2 | `get_destination("TableDelete") is TableDeleteDestination` |
| `test_preflight_gate_enforced` | M3 | Output with `TableDelete` + principal → `enforce_principal` called with `(DATASOURCE, "pg_admin", "datasource:use")`; deny → `QueryAccessDenied` before any child runs |
| `test_preflight_gate_skipped` | M3 | Output with only `Table` → no extra call; principal None → no call |

### Integration Tests
| Test | Description |
|---|---|
| `test_tabledelete_postgres_roundtrip` | (skipped without a live `DB*` Postgres) create table with rows, run `TableDelete` for a subset of keys, assert the rest remain; composite pk |

### Test Data / Fixtures
```python
@pytest.fixture
def keys_df():
    return pd.DataFrame({"associate_id": ["A1", "A2", None, "A1"],
                         "activity_date": pd.to_datetime(["2026-09-01"] * 4)})
```

---

## 5. Acceptance Criteria

- [ ] `pytest tests/test_destination_table_delete.py -v` passes.
- [ ] Existing destination tests still pass: `pytest tests/test_destination_table.py tests/test_multiqs_destination_dispatch.py tests/test_multi_destinations_subpackage.py -v`.
- [ ] `ruff check querysource/queries/multi/destinations/table_delete.py querysource/queries/multi/__init__.py querysource/outputs/destinations/__init__.py` is clean.
- [ ] `TableDelete` connects with `default_dsn` (`DB*`), never `asyncpg_url` (`PG_*`).
- [ ] Temp-table creation, key staging and `DELETE` run in one transaction; the temp table is `ON COMMIT DROP`, and no permanent table is ever created.
- [ ] No DataFrame value is interpolated into SQL text; identifiers are validated with `IDENTIFIER_RE` and double-quoted.
- [ ] A MultiQuery with a `TableDelete` output step is denied up-front (before any child query runs) for a principal without `datasource:use` on `pg_admin`.
- [ ] `run()` returns the input object unchanged, and a following `Table` step receives the full data.
- [ ] `GET` catalog/documentation endpoint lists `TableDelete` with its attributes (`tests/test_destinations_documentation_endpoint.py` still green).

---

## 6. Codebase Contract

### Verified Imports
```python
from querysource.outputs.destinations.abstract import AbstractDestination  # verified: querysource/outputs/destinations/abstract.py:19
from querysource.exceptions import OutputError, DataNotFound              # verified: querysource/exceptions.py:104, :53
from querysource.conf import default_dsn                                  # verified: querysource/conf.py:32 (DB* creds)
from asyncdb import AsyncDB                                               # verified: querysource/interfaces/connections.py:11
from querysource.auth._resource_types import ResourceType                 # verified: querysource/queries/multi/__init__.py:247 (local import inside _preflight_principal)
from querysource.auth.enforcement import enforce_principal                # verified: querysource/queries/multi/__init__.py:248 (local import)
```

### Existing Class Signatures
```python
# querysource/outputs/destinations/abstract.py
class AbstractDestination(SchemaIntrospectable, ABC):   # line 19
    _category: str = "Destinations"                      # line 28
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:  # line 30 — sets self.data, self.logger
    def resolve_credentials(self, credentials: dict) -> dict:  # line 36
    async def run(self) -> Union[dict, pd.DataFrame]:    # line 61 (abstract)
    async def close(self) -> None:                       # line 72

# querysource/queries/multi/destinations/table.py
class TableDestination(AbstractDestination):   # line 58 — `_catalog` dict pattern at :79; run() at :680

# querysource/exceptions.py
class OutputError(QueryException):             # line 104 — __init__(message="", …, step_name=None, category=None)

# querysource/queries/multi/__init__.py
async def _preflight_principal(self) -> None:  # line 239 — returns early when self._principal is None
#   enforce_principal(self._principal, ResourceType.X, name, action, tenant=self._tenant_selector, logger=self._logger)
await self._preflight_principal()              # line 364 — self._options (incl. "Output") already populated here
_output = self._options.pop('Output', None)    # line 717
destination_cls = get_destination(step_name); obj = destination_cls(data=result, **component); result = await obj.run()  # lines 816-818

# querysource/auth/_resource_types.py
ResourceType.DATASOURCE  # line 43

# asyncdb (installed .venv/lib/python3.11/site-packages/asyncdb)
AbstractDriver.engine = get_connection   # interfaces/abstract.py:69 — returns raw asyncpg.Connection for pg
pg.execute(sentence, *args) -> [result, error]   # drivers/pg.py:937
pg.copy_into_table(...)  # drivers/pg.py:1203 — COMMITS an open asyncdb transaction first → DO NOT USE here
```

### Integration Points
| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `TableDeleteDestination` | `DESTINATION_REGISTRY` | dict assignment | `querysource/outputs/destinations/__init__.py:196-225` |
| `TableDeleteDestination` | MultiQS Output loop | `get_destination(step_name)` | `querysource/queries/multi/__init__.py:816` |
| write gate | `enforce_principal` | await call | `querysource/queries/multi/__init__.py:252-256` (pattern) |
| `pg_admin` grant semantics | FEAT-091 docstring | policy name | `querysource/datasources/drivers/pg_admin.py:1-10` |

### Does NOT Exist (Anti-Hallucination)
- ~~`querysource.queries.multi.destinations.table_delete`~~ — created by this feature.
- ~~`TableDelete` in any QuerySource registry~~. The Flowtask class (`flowtask.components.TableDelete`) must NOT be imported. QuerySource does not depend on flowtask.
- ~~`PgOutput(dsn=...)` honoring a custom DSN~~. `PgOutput.__init__` overwrites `dsn` with `sqlalchemy_url`/`async_default_dsn` (`querysource/outputs/tables/TableOutput/postgres.py:305`).
- ~~A destination-level PBAC hook~~. Destinations receive only `data` + config kwargs, with no principal or session, so the gate must live in `_preflight_principal`.
- ~~`ResourceType.DESTINATION` / `ResourceType.OUTPUT`~~. Only SLUG, DATASOURCE, DRIVER and RAW_QUERY exist (`_resource_types.py:42-45`).

### Edit Sites (Blueprint Anchors)
Verified against: `2a9f19b`

| File | Action | Verbatim anchor line | Verified at | Occurrences |
|---|---|---|---|---|
| `querysource/queries/multi/destinations/table_delete.py` | CREATE | — | — | — |
| `querysource/outputs/destinations/__init__.py` | MODIFY | `    DESTINATION_REGISTRY["Table"] = TableDestination` | `__init__.py:221` | 1 |
| `querysource/queries/multi/__init__.py` | MODIFY | `        if has_raw_child:` | `__init__.py:271` | 1 |
| `tests/test_destination_table_delete.py` | CREATE | — | — | — |

---

## 7. Implementation Notes & Constraints

### Patterns to Follow
- Mirror `TableDestination` for `_catalog` shape, logging (`self.logger`), and `OutputError(..., category="data"|"infra")`.
- Use asyncdb for the connection: `db = AsyncDB("pg", dsn=default_dsn)`; `async with await db.connection() as conn: raw = conn.engine()`. Then use asyncpg's `raw.transaction()`, `raw.fetch`, `raw.execute`, `raw.copy_records_to_table` on that single connection.
- Convert pandas values to Python natives for `COPY` (`Timestamp` → `datetime`, `NaT`/`NaN` are already filtered out). The column order follows `pk`.

### Known Risks / Gotchas
- **Not atomic across steps**: `TableDelete` commits before the following `Table` step
  runs. If `Table` fails, the deleted rows are gone until the pipeline is re-run.
  Delete-then-append is idempotent, so a re-run repairs it. Document this in the catalog usage text.
- `pk` semantics = "rows present in the DataFrame". Rows that disappeared from the source
  are **not** deleted; range refreshes need `ExecuteSQL` (FEAT-156).
- asyncdb `copy_into_table` commits the open transaction (`pg.py:1211-1213`). Use raw asyncpg only.
- Large key sets: `COPY` into a temp table scales. Do not fall back to `IN (…)` lists.
- Output only runs when the pipeline result is non-empty (`__init__.py:797`), so an empty source means nothing is deleted. This is intended.

### External Dependencies
None new (`asyncdb`, `asyncpg`, `pandas` already required).

---

## 8. Open Questions

- [x] Grant for write destinations — *Resolved by Juan2coder (2026-09-30), spec default accepted*: `datasource:use` on `pg_admin` (existing FEAT-091 grant). A dedicated `datasource:write` action would be a navigator-auth follow-up.
- [x] Gate the existing `Table` destination too? — *Resolved by Juan2coder (2026-09-30), spec default accepted*: no. `WRITE_DESTINATIONS` = `{"TableDelete"}` only, so current `Table` pipelines are unchanged.
- [x] MySQL / BigQuery support — *Resolved by Juan2coder (2026-09-30), spec default accepted*: follow-up; PostgreSQL only in this feature.

---

## 9. Design Research Cross-Check

> Model: — · Status: skipped (no accepted exploration document; spec scaffolded from a direct request)

| # | Suggestion (kind) | Disposition | Reason | Landed in |
|---|---|---|---|---|

Summary: **0** confirmed · **0** rejected · **0** escalated.

---

## Worktree Strategy
- Isolation: one feature worktree `.claude/worktrees/feat-FEAT-155-multi-tabledelete`.
- Module graph: M2 → M1 (imports `TableDeleteDestination`). M3 is independent, so it runs concurrently with M1.
- Shared files: none within this feature.
- Exclusive resources: none (no Rust/Cython rebuild).
- Cross-feature: FEAT-156 (`multi-executesql`) also edits `querysource/outputs/destinations/__init__.py` and extends `WRITE_DESTINATIONS`. **Merge FEAT-155 first.**

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-09-29 | Juan2coder | Initial draft |
| 0.2 | 2026-09-30 | Juan2coder | Task review: write gate also enforced in the HTTP handler (`_preflight_multiquery`, `write_access`) |
