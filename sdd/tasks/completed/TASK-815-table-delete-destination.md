# TASK-815: TableDeleteDestination (delete target rows by pk tuple)

**Feature**: FEAT-155 — MultiQuery TableDelete Destination
**Spec**: `sdd/specs/multi-tabledelete.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §2 and §3 Module 1. Slugs run on the read-only `PG_*` connection
(`querysource/conf.py:44`, `asyncpg_url`), so a pipeline cannot remove rows. This
task adds the MultiQuery Output step `TableDelete`. It deletes from `schema.table`
every row whose `pk` tuple appears in the pipeline DataFrame. It connects with the
full-access `DB*` DSN (`default_dsn`, `querysource/conf.py:32`) and runs everything
in one transaction on one connection. It is a clean redesign of Flowtask's
`TableDelete` component, not a port.

The folder scan in `querysource/queries/multi/destinations/__init__.py`
(`_scan_destinations`) picks the new class up automatically, so the catalog
(`ComponentRegistry`) lists it without further wiring. Registering the `"TableDelete"`
step name in the legacy `DESTINATION_REGISTRY` is TASK-816. The PBAC gate is TASK-817.

---

## Scope

- Create `querysource/queries/multi/destinations/table_delete.py` with
  `IDENTIFIER_RE` and `TableDeleteDestination(AbstractDestination)`:
  `_catalog`, `__init__`, `_key_frame`, `_delete_keys`, `run`, and the attribute `deleted_rows`.
- Implement the algorithm in spec §2 steps 1-4: identifier validation, NULL/duplicate key
  filtering, pk type introspection, `CREATE TEMP TABLE … ON COMMIT DROP`, raw asyncpg
  `copy_records_to_table`, `DELETE … USING`, and parsing the row count.
- Write every M1 unit test in spec §4 in `tests/test_destination_table_delete.py`.

**NOT in scope**:
- Registering the step name in `querysource/outputs/destinations/__init__.py` (TASK-816).
- The `WRITE_DESTINATIONS` PBAC gate in `querysource/queries/multi/__init__.py` (TASK-817).
- The live-Postgres integration test `test_tabledelete_postgres_roundtrip` (spec §4 Integration; optional, skipped without a DB).
- Range / `WHERE` deletes (FEAT-156 `ExecuteSQL`), and drivers other than PostgreSQL.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/destinations/table_delete.py` | CREATE | `TableDeleteDestination` + `IDENTIFIER_RE` |
| `tests/test_destination_table_delete.py` | CREATE | M1 unit tests (spec §4) |

---

## Codebase Contract (Anti-Hallucination)

Re-verified against HEAD `f8a32ae`.

### Verified Imports
```python
from querysource.outputs.destinations.abstract import AbstractDestination  # verified: querysource/outputs/destinations/abstract.py:19
from querysource.exceptions import DataNotFound, OutputError               # verified: querysource/exceptions.py:53 (DataNotFound), :104 (OutputError)
from querysource.conf import default_dsn                                   # verified: querysource/conf.py:32 (DB* full-access creds)
from asyncdb import AsyncDB                                                # verified: querysource/interfaces/connections.py:11; AsyncDB('pg', dsn=default_dsn) precedent at querysource/datasources/handlers/datasource.py:248
import re, uuid                                                            # stdlib
from typing import Union                                                   # stdlib
import pandas as pd                                                        # already used by querysource/queries/multi/destinations/table.py:33
```

### Existing Signatures to Use
```python
# querysource/outputs/destinations/abstract.py
class AbstractDestination(SchemaIntrospectable, ABC):   # line 19
    _category: str = "Destinations"                      # line 28
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:  # line 30 — sets self.data and self.logger (QS.Output.<ClassName>)
    async def run(self) -> Union[dict, pd.DataFrame]:    # line 61 (abstract) — must return data unchanged
    async def close(self) -> None:                       # line 72 (optional override)

# querysource/exceptions.py:104
class OutputError(QueryException):
    def __init__(self, message: str = "", code: int | None = None, *,
                 step_name: str = None, category: str = None, **kwargs)  # lines 114-122; category "data" | "infra"

# querysource/queries/multi/destinations/table.py — pattern to mirror
class TableDestination(AbstractDestination):   # line 58
    _catalog = {...}                           # line 80 — keys: display_name, description, usage, icon, attributes, json_schema, example
    async def run(self) -> Union[dict, pd.DataFrame]:  # line 680 — dict/DataFrame frame selection + DataNotFound messages at :696-704

# asyncdb (.venv/lib/python3.11/site-packages/asyncdb)
AbstractDriver.engine = get_connection   # interfaces/abstract.py:69 — returns the raw asyncpg.Connection for "pg"
pg.connection()                          # drivers/pg.py:735 — returns self (`async with await db.connection() as conn`)
pg.copy_into_table(...)                  # drivers/pg.py:1203 — COMMITS an open asyncdb transaction first (:1211-1213) → DO NOT USE

# asyncpg 0.30.0 raw connection (reached only via conn.engine(); never import asyncpg)
Connection.transaction()                 # async context manager
Connection.fetch(query, *args) -> list[Record]
Connection.execute(query, *args) -> str  # status string, e.g. "DELETE 123"
Connection.copy_records_to_table(table_name, *, records, columns=None, schema_name=None, timeout=None, where=None)
```

### Catalog discovery (no wiring needed in this task)
- `querysource/queries/multi/destinations/__init__.py` `_scan_destinations()` imports every
  non-underscore `*.py` in the folder and registers each `AbstractDestination` subclass by class name.
- `querysource/queries/multi/_introspect.py:932` — `build_companion_catalog(cls) or getattr(cls, "_catalog", None)` overrides the introspected catalog entry.
- `tests/test_destinations_documentation_endpoint.py::test_every_real_destination_has_populated_schema`
  requires `_catalog["json_schema"]["properties"]` to be non-empty.

### Does NOT Exist
- ~~`querysource.queries.multi.destinations.table_delete`~~ — created by this task.
- ~~`flowtask.components.TableDelete`~~ — must NOT be imported; QuerySource does not depend on flowtask.
- ~~`asyncpg` as a declared dependency / any `import asyncpg` in `querysource/`~~ — reach asyncpg only through `conn.engine()`. Classify driver errors by exception class name (as `classify_output_error` does in `querysource/queries/multi/__init__.py:52`).
- ~~asyncdb `copy_into_table` inside a transaction~~ — it commits first (`pg.py:1211-1213`).
- ~~`PgOutput(dsn=...)` honoring a custom DSN~~ — `PgOutput.__init__` overwrites it (`querysource/outputs/tables/TableOutput/postgres.py:305`).
- ~~`asyncpg_url` / `PG_*` for writes~~ — read-only; use `default_dsn`.
- ~~A principal/session on destinations~~ — destinations receive only `data` + config kwargs.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/queries/multi/destinations/table_delete.py", "action": "CREATE"},
    {"path": "tests/test_destination_table_delete.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/outputs/destinations/abstract.py#AbstractDestination",
    "sym:querysource/exceptions.py#OutputError",
    "sym:querysource/exceptions.py#DataNotFound",
    "sym:querysource/conf.py#default_dsn",
    "sym:querysource/queries/multi/destinations/table.py#TableDestination"
  ]
}
```

---

## Implementation Notes

### Pattern to Follow
Mirror `TableDestination` (`querysource/queries/multi/destinations/table.py`) for the
`_catalog` shape, `self.logger` usage, frame selection in `run()`, and
`OutputError(..., category="data"|"infra") from err`.

### Key Constraints
- Async throughout. One connection and one transaction: introspect, `CREATE TEMP`, `COPY` and `DELETE` all happen inside `raw.transaction()`.
- Connect with `AsyncDB("pg", dsn=default_dsn)`, never `asyncpg_url` (AC: DB* creds).
- Identifiers (`schema`, `table`, each `pk`) must match `IDENTIFIER_RE`, then always appear double-quoted. DataFrame values travel only through `COPY` records and never enter SQL text.
- The temp table name is `_qs_del_<uuid4().hex[:8]>` (lowercase, valid identifier) and is `ON COMMIT DROP`. No permanent table is ever created.
- `copy_records_to_table(tmp, records=…, columns=pk)`: pass the bare temp name (asyncpg quotes it) and no `schema_name`, because temp tables live in `pg_temp`.
- `run()` returns `self.data` (the same object). `self.deleted_rows` is the sum over frames.
- Use `self.logger` only; never `print`.

### References in Codebase
- `querysource/queries/multi/destinations/table.py` — pattern (catalog, run, errors).
- `querysource/datasources/handlers/datasource.py:248` — `AsyncDB('pg', dsn=default_dsn)`.
- `querysource/queries/multi/__init__.py:46-66` — class-name based data/infra classification.

---

## Implementation Blueprint

The file is about 190 lines, so it is split into three consecutive blocks (part 1/3 to 3/3). Write them in order into the same file. Each block is under 80 lines.

### Steps (in order)
1. Write part 1 (module header, `IDENTIFIER_RE`, class and `_catalog`). *Why*: the catalog must have populated `json_schema.properties`, or the documentation-endpoint test fails, because the folder scan auto-registers the class.
2. Write part 2 (`__init__`, `_key_frame`, `_to_native`). *Why*: validation must happen at construction time, so bad config fails before any connection opens.
3. Write part 3 (`_delete_keys`, `run`). *Why*: this is the transactional core. It keeps staging and delete atomic (AC: one transaction).
4. Write `tests/test_destination_table_delete.py` with a mocked raw connection. *Why*: unit tests must not need a live database.
5. Run the Validation Commands and `ruff check querysource/queries/multi/destinations/table_delete.py`.

### `querysource/queries/multi/destinations/table_delete.py` (CREATE, part 1/3)
```python
"""
TableDeleteDestination.

MultiQuery Output step ``TableDelete``: delete the rows of ``schema.table``
whose primary-key tuple appears in the pipeline DataFrame (PostgreSQL only).
Uses the full-access ``DB*`` DSN and runs in a single transaction.
"""
import re
import uuid
from typing import Union

import pandas as pd
from asyncdb import AsyncDB

from querysource.conf import default_dsn
from querysource.exceptions import DataNotFound, OutputError
from querysource.outputs.destinations.abstract import AbstractDestination

IDENTIFIER_RE: re.Pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Driver exception class names meaning "the target table/column/schema is
# wrong" (a config/data problem, HTTP 422) rather than an infra failure.
_DATA_ERROR_NAMES = frozenset({
    "UndefinedTableError", "UndefinedColumnError", "InvalidSchemaNameError",
    "DataError", "IntegrityError",
})

_PK_TYPES_SQL = (
    "SELECT a.attname, format_type(a.atttypid, a.atttypmod) "
    "FROM pg_attribute a "
    "WHERE a.attrelid = $1::regclass AND a.attname = ANY($2) "
    "AND NOT a.attisdropped"
)


class TableDeleteDestination(AbstractDestination):
    """Delete rows of ``schema.table`` whose ``pk`` tuple is present in the pipeline data.

    Step name: ``TableDelete``. PostgreSQL only. Returns the input data unchanged.
    """

    _catalog = {
        "display_name": "TableDelete",
        "description": (
            "Delete rows from a PostgreSQL table whose primary-key values "
            "appear in the pipeline DataFrame."
        ),
        "usage": (
            "Place before a ``Table`` step (``method: append``) to refresh a "
            "slice of a table: ``TableDelete`` removes every target row whose "
            "``pk`` tuple is in the data, then ``Table`` re-inserts them. Rows "
            "with a NULL key are skipped. Not atomic across steps: the delete "
            "commits before the next step runs, so if ``Table`` fails re-run "
            "the pipeline (delete-then-append is idempotent). Rows missing "
            "from the source are not deleted; use ``ExecuteSQL`` for range deletes."
        ),
        "icon": "trash",
        "attributes": [
            {"name": "schema", "type": "str", "required": False, "default": "public",
             "description": "Target schema."},
            {"name": "table", "type": "str", "required": True, "default": "",
             "description": "Target table name."},
            {"name": "pk", "type": "list", "required": True, "default": [],
             "description": "Key column names matched against the DataFrame (composite allowed)."},
        ],
        "json_schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "title": "TableDelete",
            "description": "Delete rows of a table matching the DataFrame's key tuples.",
            "properties": {
                "schema": {"type": "string", "default": "public"},
                "table": {"type": "string"},
                "pk": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            },
            "required": ["table", "pk"],
            "additionalProperties": False,
        },
        "example": (
            '{\n  "Output": [\n'
            '    {"TableDelete": {"schema": "wm_assembly", "table": "employee_detail_profile",\n'
            '                     "pk": ["associate_id", "activity_date"]}},\n'
            '    {"Table": {"schema": "wm_assembly", "table": "employee_detail_profile",\n'
            '               "method": "append"}}\n'
            '  ]\n}'
        ),
    }
```
**Why this shape**: `_catalog` mirrors `TableDestination._catalog` (table.py:80) key for key, because the introspector cannot recover the attribute types. The `usage` text must document the "not atomic across steps" risk (spec §7). `IDENTIFIER_RE` is a public module name fixed by the spec skeleton; do not rename it.

### `querysource/queries/multi/destinations/table_delete.py` (CREATE, part 2/3)
```python
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        """Read ``schema`` (default ``public``), ``table`` (required), ``pk`` (required, non-empty list).

        Raises:
            OutputError: missing ``table``/``pk`` or an identifier failing ``IDENTIFIER_RE``.
        """
        super().__init__(data, **kwargs)
        self._schema: str = kwargs.get("schema") or "public"
        self._table: str = kwargs.get("table") or ""
        pk = kwargs.get("pk") or []
        self._pk: list[str] = [pk] if isinstance(pk, str) else list(pk)
        self.deleted_rows: int = 0
        # FILL IN: raise OutputError(..., category="data") when table is empty or pk is empty — bounded by test_init_requires_table_and_pk
        # FILL IN: validate schema, table and every pk name with IDENTIFIER_RE.fullmatch; raise OutputError(category="data") naming the bad identifier — bounded by test_init_rejects_bad_identifiers (AC: identifiers validated)

    @staticmethod
    def _quote(identifier: str) -> str:
        """Return ``identifier`` double-quoted (already validated by ``IDENTIFIER_RE``)."""
        return f'"{identifier}"'

    def _key_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        """Return ``df[pk]`` without NULL-key rows and duplicates.

        Raises:
            OutputError: a ``pk`` column is absent from ``df`` (category ``data``).
        """
        missing = [col for col in self._pk if col not in df.columns]
        if missing:
            raise OutputError(
                f"TableDelete: pk column(s) not in data: {', '.join(missing)}",
                category="data",
            )
        # FILL IN: keys = df[self._pk].dropna(); log the number of dropped NULL-key rows with self.logger.info when > 0; then keys.drop_duplicates() — bounded by test_key_frame_drops_nulls_and_dupes (spec §2 step 2)
        return keys

    @staticmethod
    def _to_native(value: object) -> object:
        """Convert a pandas/numpy scalar to a Python native accepted by asyncpg COPY."""
        # FILL IN: pd.Timestamp -> value.to_pydatetime(); numpy scalar (hasattr(value, "item")) -> value.item(); else value unchanged — bounded by spec §7 "Convert pandas values to Python natives"
        return value
```
**Why**: validation lives in `__init__`, as it does in `TableDestination.__init__` (table.py:220-240), so a misconfigured step fails before any connection opens. A string `pk` is normalized into a one-item list because Flowtask configs sometimes pass a bare string. NULL keys are dropped because `NULL = NULL` never matches in SQL.

### `querysource/queries/multi/destinations/table_delete.py` (CREATE, part 3/3)
```python
    async def _delete_keys(self, keys: pd.DataFrame) -> int:
        """Stage ``keys`` in a temp table and run ``DELETE … USING`` in one transaction.

        Returns:
            Number of deleted rows.
        Raises:
            OutputError: table/column not found, or any database error (rolled back).
        """
        target = f"{self._quote(self._schema)}.{self._quote(self._table)}"
        tmp = f"_qs_del_{uuid.uuid4().hex[:8]}"
        records = [
            tuple(self._to_native(v) for v in row)
            for row in keys.itertuples(index=False, name=None)
        ]
        db = AsyncDB("pg", dsn=default_dsn)
        try:
            async with await db.connection() as conn:
                raw = conn.engine()
                async with raw.transaction():
                    rows = await raw.fetch(_PK_TYPES_SQL, target, self._pk)
                    types = {r[0]: r[1] for r in rows}
                    # FILL IN: if any pk name is missing from types -> raise OutputError(category="data") naming the column(s) — bounded by spec §2 step 3a
                    cols = ", ".join(f"{self._quote(c)} {types[c]}" for c in self._pk)
                    await raw.execute(
                        f"CREATE TEMP TABLE {self._quote(tmp)} ({cols}) ON COMMIT DROP"
                    )
                    await raw.copy_records_to_table(tmp, records=records, columns=self._pk)
                    match = " AND ".join(
                        f"t.{self._quote(c)} = d.{self._quote(c)}" for c in self._pk
                    )
                    status = await raw.execute(
                        f"DELETE FROM {target} t USING {self._quote(tmp)} d WHERE {match}"
                    )
        except OutputError:
            raise
        except Exception as err:
            # FILL IN: category = "data" if type(err).__name__ in _DATA_ERROR_NAMES else "infra"; self.logger.error(...); raise OutputError(f"TableDelete: {err}", category=category) from err — bounded by test_delete_error_wrapped
            raise
        # FILL IN: parse the count from status ("DELETE 123" -> 123; int(status.split()[-1]), 0 on a malformed status) — bounded by spec §2 step 3d
        return deleted

    async def run(self) -> Union[dict, pd.DataFrame]:
        """Apply the delete for a DataFrame or each DataFrame of a dict; set ``deleted_rows``.

        Returns:
            ``self.data`` unchanged.
        Raises:
            DataNotFound: data is empty / every frame is empty.
            OutputError: on validation or database failure.
        """
        # FILL IN: build `frames` exactly like TableDestination.run (table.py:694-709): dict -> non-empty DataFrames or DataNotFound("TableDelete: all DataFrames in dict are empty."); DataFrame -> DataNotFound when empty; other type -> OutputError(category="data") — bounded by test_run_empty_dataframe, test_run_dict_of_frames
        total = 0
        for df in frames:
            keys = self._key_frame(df)
            if keys.empty:
                self.logger.info("TableDelete: no non-NULL keys for %s.%s", self._schema, self._table)
                continue
            total += await self._delete_keys(keys)
        self.deleted_rows = total
        self.logger.info(
            "TableDelete: deleted %s row(s) from %s.%s", total, self._schema, self._table
        )
        return self.data
```
**Why**: every statement runs on the one raw asyncpg connection inside `raw.transaction()`. An exception therefore rolls back both the staging and the delete, and the `ON COMMIT DROP` temp table disappears either way. Use `copy_records_to_table` on the raw connection, never asyncdb's `copy_into_table`, which commits first (pg.py:1211). `$1::regclass` receives the quoted `"schema"."table"` text, so a missing table raises inside the transaction and is classified as data by its class name. Do not change the method names or signatures; the tests patch `_delete_keys`.

### `tests/test_destination_table_delete.py` (CREATE)
```python
"""Unit tests for TableDeleteDestination (FEAT-155, TASK-815). No real database."""
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from querysource.exceptions import DataNotFound, OutputError
from querysource.queries.multi.destinations import table_delete as td
from querysource.queries.multi.destinations.table_delete import TableDeleteDestination

CFG = {"schema": "wm_assembly", "table": "employee_detail_profile",
       "pk": ["associate_id", "activity_date"]}


@pytest.fixture
def keys_df():
    return pd.DataFrame({"associate_id": ["A1", "A2", None, "A1"],
                         "activity_date": pd.to_datetime(["2026-09-01"] * 4)})


@pytest.fixture
def fake_raw():
    """Mocked raw asyncpg connection; `transaction()` is an async context manager."""
    raw = MagicMock()
    raw.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
    raw.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    raw.fetch = AsyncMock(return_value=[("associate_id", "character varying"),
                                        ("activity_date", "date")])
    raw.execute = AsyncMock(side_effect=["CREATE TABLE", "DELETE 2"])
    raw.copy_records_to_table = AsyncMock(return_value="COPY 2")
    return raw


@pytest.fixture
def patched_db(fake_raw):
    # FILL IN: patch td.AsyncDB so `async with await db.connection() as conn` yields conn with conn.engine() -> fake_raw; yield the AsyncDB mock
    ...


def test_init_requires_table_and_pk(keys_df):
    # FILL IN: missing table -> OutputError; pk=[] -> OutputError
    ...


@pytest.mark.parametrize("override", [{"table": "t; drop"}, {"pk": ["a b"]}, {"schema": 'x"y'}])
def test_init_rejects_bad_identifiers(keys_df, override):
    # FILL IN: TableDeleteDestination(data=keys_df, **{**CFG, **override}) raises OutputError
    ...


def test_key_frame_missing_column(keys_df):
    # FILL IN: pk=["nope"] -> _key_frame raises OutputError with category "data"
    ...


def test_key_frame_drops_nulls_and_dupes(keys_df):
    # FILL IN: _key_frame(keys_df) has 2 rows (A1, A2): the None row and the duplicate A1 are removed
    ...


async def test_run_empty_dataframe():
    # FILL IN: empty DataFrame with the pk columns -> DataNotFound
    ...


async def test_run_passthrough(keys_df):
    # FILL IN: patch.object(TableDeleteDestination, "_delete_keys", AsyncMock(return_value=2)); result is keys_df; deleted_rows == 2
    ...


async def test_run_dict_of_frames(keys_df):
    # FILL IN: {"a": keys_df, "b": keys_df, "c": empty df}; _delete_keys mocked to 2 -> awaited twice; deleted_rows == 4
    ...


async def test_delete_sql_shape(keys_df, fake_raw, patched_db):
    # FILL IN: run(); assert AsyncDB called with ("pg", dsn=td.default_dsn); CREATE statement contains "ON COMMIT DROP" and '"associate_id" character varying';
    #          copy_records_to_table awaited with columns=CFG["pk"]; no copy_into_table use; DELETE contains 'FROM "wm_assembly"."employee_detail_profile" t USING'
    #          and 't."associate_id" = d."associate_id"'; transaction().__aenter__ awaited; deleted_rows == 2; no DataFrame value appears in any executed SQL text
    ...


async def test_delete_error_wrapped(keys_df, fake_raw, patched_db):
    # FILL IN: fake_raw.copy_records_to_table raises a RuntimeError -> run() raises OutputError (__cause__ is the error);
    #          transaction().__aexit__ awaited with a non-None exc_type (rolled back)
    ...
```
**Why**: patching `td.AsyncDB` at the module attribute keeps the test DB-free. The fixture `keys_df` is the one given in spec §4. The asserts on the SQL text back the AC "no value interpolated / identifiers quoted".

### FILL IN checklist
- [ ] `table_delete.py::TableDeleteDestination.__init__`: required `table`/`pk` and identifier validation (test_init_requires_table_and_pk, test_init_rejects_bad_identifiers).
- [ ] `table_delete.py::TableDeleteDestination._key_frame`: `dropna`, log the count, `drop_duplicates` (test_key_frame_drops_nulls_and_dupes).
- [ ] `table_delete.py::TableDeleteDestination._to_native`: `Timestamp`/numpy to native (spec §7).
- [ ] `table_delete.py::TableDeleteDestination._delete_keys`: missing pk column check, error classification and wrapping, status parsing (test_delete_sql_shape, test_delete_error_wrapped).
- [ ] `table_delete.py::TableDeleteDestination.run`: frame selection like `TableDestination.run` (test_run_empty_dataframe, test_run_dict_of_frames).
- [ ] `tests/test_destination_table_delete.py`: the `patched_db` fixture and every test body.

---

## Acceptance Criteria

- [ ] `pytest tests/test_destination_table_delete.py -v` passes (all 9 M1 tests from spec §4).
- [ ] Existing destination tests still pass (`tests/test_destination_table.py`, `tests/test_multi_destinations_subpackage.py`, `tests/test_destinations_documentation_endpoint.py`).
- [ ] `ruff check querysource/queries/multi/destinations/table_delete.py` is clean.
- [ ] `TableDelete` connects with `default_dsn` (`DB*`), never `asyncpg_url` (`PG_*`).
- [ ] Temp-table creation, key staging and `DELETE` run in one transaction; the temp table is `ON COMMIT DROP`, and no permanent table is ever created.
- [ ] No DataFrame value is interpolated into SQL text; identifiers are validated with `IDENTIFIER_RE` and double-quoted.
- [ ] `run()` returns the input object unchanged (`is`), and `deleted_rows` holds the total.
- [ ] The catalog lists `TableDeleteDestination` with populated `json_schema.properties`.

## Validation Commands

- `pytest tests/test_destination_table_delete.py -q`
- `pytest tests/test_destination_table.py -q`
- `pytest tests/test_multi_destinations_subpackage.py -q`
- `pytest tests/test_destinations_documentation_endpoint.py -q`

---

## Test Specification

See the `tests/test_destination_table_delete.py` blueprint block above. It scaffolds the nine M1 tests from spec §4:
`test_init_requires_table_and_pk`, `test_init_rejects_bad_identifiers`,
`test_key_frame_missing_column`, `test_key_frame_drops_nulls_and_dupes`,
`test_run_empty_dataframe`, `test_run_passthrough`, `test_run_dict_of_frames`,
`test_delete_sql_shape`, `test_delete_error_wrapped`. `pytest.ini` sets
`asyncio_mode = auto`, so the async tests need no marker.

---

## Agent Instructions

When you pick up this task:

1. **Work in the feature worktree**, never on `base_branch`
   (`python -m scripts.sdd.ensure_worktree --slug multi-tabledelete --feature-id FEAT-155`).
2. **Read the spec** at the path listed above for full context.
3. **Check dependencies**: every `Depends-on` task must be `"done"` in
   `sdd/tasks/index/multi-tabledelete.json` (none for this task).
4. **Verify the Codebase Contract** before writing any code. Re-grep every import and signature. If something changed, update the contract first. Never use a symbol that is not listed without verifying it.
5. **Update status** in `sdd/tasks/index/multi-tabledelete.json` to `"in-progress"` (set `started_at`) and commit only that index file.
6. **Implement** starting from the Implementation Blueprint blocks. Complete every `# FILL IN:` and never change a signature or path the blueprint fixes.
7. **Verify** all acceptance criteria by running the Validation Commands.
8. **Commit the code**, staging only the files this task lists (never `git add .` / `-A`).
9. **Close the task** with `scripts/sdd/close_task.sh TASK-815 multi-tabledelete verified`. Never move the file by hand.
10. **Fill in the Completion Note** below, then commit the staged SDD state.

---

## Completion Note

Implemented TableDeleteDestination per blueprint; 9 M1 tests pass, ruff clean.

**Completed by**: sdd-worker (fallback sequential)
**Date**: 2026-09-30
**Notes**: Pre-existing failures unrelated: tests/test_destinations_documentation_endpoint.py (2 tests, TableOutputAdapter/migrated destinations) fail on base too.

**Deviations from spec**: none
