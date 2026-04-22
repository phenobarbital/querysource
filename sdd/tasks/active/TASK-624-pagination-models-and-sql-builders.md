# TASK-624: Pagination Models and SQL Builders

**Feature**: querysource-slug-list-pagination
**Feature ID**: FEAT-090
**Spec**: `sdd/specs/querysource-slug-list-pagination.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

The Query Slug list endpoint (`GET /api/v1/management/queries`) currently returns
the entire `troc.queries` table (> 4,000 rows). This task creates the **pure,
SQL-injection-safe foundation** for pagination: Pydantic request / response
models and SQL builders for `SELECT COUNT(*)` and paged `SELECT`.

Implements spec Section 3 Modules 1 (parameter & response models) and 2 (SQL
builders).

This is the lowest-layer task — it does not touch HTTP code or the database.
All logic here is unit-testable in isolation.

---

## Scope

- Create `querysource/handlers/_pagination.py` containing:
  - Module-level constants: `DEFAULT_PAGE_SIZE = 50`, `MAX_PAGE_SIZE = 200`,
    `DEFAULT_SORT_FIELD = "updated_at"`, `DEFAULT_SORT_DIRECTION = "desc"`.
  - Allowlist constants derived from `QueryModel`:
    - `SORTABLE_COLUMNS`: scalar columns only (`query_slug`, `description`,
      `program_slug`, `provider`, `is_cached`, `created_at`, `updated_at`).
    - `SEARCHABLE_COLUMNS`: `("query_slug", "description", "program_slug")`.
    - `FILTERABLE_COLUMNS`: all keys of `QueryModel.columns(QueryModel)`.
  - Pydantic models: `PaginationParams`, `PaginationMeta`, `PaginatedResponse`.
  - `PaginationParams.from_query_string(qs: dict) -> PaginationParams`
    classmethod that parses a flat query-string dict (including `sort=<field>:<dir>`)
    and raises `ValueError` on unknown / unsafe columns.
  - Pure SQL-builder functions:
    - `build_where_clause(params: PaginationParams, extra_filters: dict) -> str`
    - `build_order_by(params: PaginationParams) -> str`
    - `build_count_sql(schema: str, table: str, where: str) -> str`
    - `build_page_sql(schema: str, table: str, fields: list[str], where: str, order_by: str, limit: int, offset: int) -> str`
  - All SQL builders must:
    - Validate **every** column identifier against the allowlists.
    - Route scalar values through `Entity.toSQL` + `Entity.quoteString` (same
      path `QueryManager.get_query_insert` uses at `manager.py:46-49`).
    - Reject unknown columns with `ValueError` (caller converts to HTTP 400).

**NOT in scope**:
- Modifying `QueryManager.get()` — that is TASK-625.
- Executing SQL against Postgres — this task produces strings only.
- Writing tests — see TASK-626 (though authoring a couple of tiny sanity checks
  inline while developing is allowed).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/_pagination.py` | CREATE | Models, allowlists, SQL builders |

---

## Codebase Contract (Anti-Hallucination)

> VERIFIED against `fix-rust-deploy` HEAD on 2026-04-22. Re-verify before edit.

### Verified Imports

```python
# Use verbatim:
from pydantic import BaseModel, Field, field_validator     # project already depends on pydantic >= 2
from typing import Literal, Optional

# In-project:
from querysource.models import QueryModel                  # verified: querysource/models.py:48
from querysource.types.validators import Entity            # verified: querysource/handlers/manager.py:16
```

### Existing Signatures to Use

```python
# querysource/models.py:48
class QueryModel(Model):
    query_slug: str                 # line 49
    description: str                # line 50
    program_slug: str               # line 81
    provider: str                   # line 74
    is_cached: bool                 # line 73
    created_at: datetime            # line 88
    updated_at: datetime            # line 94
    # ... (full list in spec §6)

    class Meta:                     # line 101
        driver = "pg"
        name = QS_QUERIES_TABLE     # from querysource.conf  (string)
        schema = QS_QUERIES_SCHEMA  # from querysource.conf  (string)

# Inherited from asyncdb.models.Model:
#   classmethod columns(cls) -> dict[str, Field]
```

```python
# querysource/types/validators.py (Entity class)
#   Entity.toSQL(value, datatype, dbtype=None) -> str
#   Entity.quoteString(value: str, no_dblquoting: bool=False) -> str
# Usage pattern verified at querysource/handlers/manager.py:46-49
```

### Does NOT Exist

- ~~`QueryModel.paginate()`~~ — not a method. Do not invoke.
- ~~`QueryModel.all(limit=..., offset=...)`~~ — `asyncdb`'s pg driver `_all_`
  does NOT forward `limit` / `offset` (verified at
  `asyncdb/drivers/pg.py:1505-1519`).
- ~~`QueryModel.count()`~~ — does not exist on `asyncdb.models.Model`.
- ~~`QueryModel.Meta.columns`~~ — use `QueryModel.columns(QueryModel)` instead.
- ~~`from querysource.handlers.pagination import ...`~~ — NEW file is
  `_pagination.py` (underscore-prefixed, internal).
- ~~`pydantic.v1`~~ — this project uses Pydantic v2 (`field_validator`, not
  `validator`).

---

## Implementation Notes

### Suggested module skeleton

```python
# querysource/handlers/_pagination.py
"""Pagination helpers for the Query Slug management list endpoint.

Pure functions + Pydantic models. No aiohttp, no DB calls — this module is
unit-testable in isolation.
"""
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator

from querysource.models import QueryModel
from querysource.types.validators import Entity


SortDirection = Literal["asc", "desc"]

DEFAULT_PAGE_SIZE: int = 50
MAX_PAGE_SIZE: int = 200
DEFAULT_SORT_FIELD: str = "updated_at"
DEFAULT_SORT_DIRECTION: SortDirection = "desc"

_MODEL_COLUMNS: dict = QueryModel.columns(QueryModel)
FILTERABLE_COLUMNS: frozenset[str] = frozenset(_MODEL_COLUMNS.keys())
SORTABLE_COLUMNS: frozenset[str] = frozenset({
    "query_slug", "description", "program_slug", "provider",
    "is_cached", "created_at", "updated_at",
})
SEARCHABLE_COLUMNS: tuple[str, ...] = ("query_slug", "description", "program_slug")


class PaginationParams(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    sort_field: str = Field(default=DEFAULT_SORT_FIELD)
    sort_direction: SortDirection = Field(default=DEFAULT_SORT_DIRECTION)
    search: Optional[str] = Field(default=None, max_length=255)
    fields: Optional[list[str]] = Field(default=None)

    @field_validator("sort_field")
    @classmethod
    def _validate_sort(cls, v: str) -> str:
        if v not in SORTABLE_COLUMNS:
            raise ValueError(f"sort field not allowed: {v!r}")
        return v

    @field_validator("fields")
    @classmethod
    def _validate_fields(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return v
        unknown = [c for c in v if c not in FILTERABLE_COLUMNS]
        if unknown:
            raise ValueError(f"unknown field(s): {unknown}")
        return v

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @classmethod
    def from_query_string(cls, qs: dict) -> "PaginationParams":
        """Parse a flat query-string dict into PaginationParams.

        Understands:
          - page, page_size (ints)
          - sort=<field>[:asc|desc]
          - search (str)
          - fields (csv)
        """
        ...


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int


class PaginatedResponse(BaseModel):
    data: list[dict]
    meta: PaginationMeta


def build_where_clause(params: PaginationParams, extra_filters: dict) -> str:
    """Return a SQL WHERE clause (including leading 'WHERE ', or '' if empty).

    - extra_filters keys MUST be in FILTERABLE_COLUMNS or they are dropped.
    - Values go through Entity.toSQL / Entity.quoteString.
    - params.search matches SEARCHABLE_COLUMNS with ILIKE '%term%'.
    """
    ...


def build_order_by(params: PaginationParams) -> str:
    """Return 'ORDER BY <col> <DIR>' with validated column name."""
    ...


def build_count_sql(schema: str, table: str, where: str) -> str:
    """SELECT COUNT(*) FROM <schema>.<table> <where>."""
    ...


def build_page_sql(
    schema: str,
    table: str,
    fields: list[str],
    where: str,
    order_by: str,
    limit: int,
    offset: int,
) -> str:
    """SELECT ... FROM <schema>.<table> <where> <order_by> LIMIT ? OFFSET ?."""
    ...
```

### Key Constraints

- **No SQL injection surface**: never interpolate a column or direction
  identifier that has not been matched against an allowlist.
- **No Python f-strings with untrusted values** for the literal portion of
  WHERE — use `Entity.toSQL(val, datatype)` and `Entity.quoteString(...)`.
- **Pure functions**: no aiohttp imports, no `await`, no DB connection.
- **Google-style docstrings**, strict type hints, PEP 8.
- Use `self.logger` pattern — but since this module has no class, prefer
  module-level `logger = logging.getLogger(__name__)` if logging is needed;
  most functions should simply raise `ValueError` on bad input.

### Pattern to Follow — safe value coercion

```python
# From querysource/handlers/manager.py:40-49
val = getattr(query, field.name)
_type = field.type
try:
    _dbtype = field.db_type()
except Exception:
    _dbtype = None
value = Entity.toSQL(val, _type, dbtype=_dbtype)
columns.append(name)
values.append(value)
values = ', '.join([Entity.quoteString(str(a), no_dblquoting=False) for a in values])
```

---

## Acceptance Criteria

- [ ] `querysource/handlers/_pagination.py` exists and is importable:
      `from querysource.handlers._pagination import PaginationParams, PaginationMeta, PaginatedResponse`
- [ ] `PaginationParams()` with no args yields `page=1, page_size=50,
      sort_field="updated_at", sort_direction="desc"`.
- [ ] `PaginationParams(page_size=9999)` raises `pydantic.ValidationError`.
- [ ] `PaginationParams(sort_field="password")` raises `pydantic.ValidationError`.
- [ ] `PaginationParams(fields=["query_slug", "bogus"])` raises
      `pydantic.ValidationError`.
- [ ] `PaginationParams.from_query_string({"sort": "query_slug:asc"})` yields
      `sort_field="query_slug", sort_direction="asc"`.
- [ ] `build_where_clause` drops keys not in `FILTERABLE_COLUMNS` (does not
      inject them into SQL).
- [ ] `build_page_sql(..., limit=50, offset=100)` output contains
      `"LIMIT 50 OFFSET 100"`.
- [ ] `build_order_by` rejects a column not in `SORTABLE_COLUMNS` (the model
      validator catches this earlier, but a defensive check inside the builder
      is acceptable).
- [ ] No linting errors: `ruff check querysource/handlers/_pagination.py`.
- [ ] Module contains no `print()` calls.

---

## Test Specification

> Full tests are authored in TASK-626. A couple of lightweight sanity tests
> while developing are fine but are not part of this task's scope.

---

## Agent Instructions

1. **Read the spec** at `sdd/specs/querysource-slug-list-pagination.spec.md`.
2. **Verify the Codebase Contract** above against current code:
   - `grep -n "class QueryModel" querysource/models.py`
   - `grep -n "def toSQL\|def quoteString" querysource/types/validators.py`
3. **Update index status** → `"in-progress"` with your session ID.
4. **Implement** `querysource/handlers/_pagination.py` per the skeleton above.
5. **Run a manual sanity check**:
   ```bash
   source .venv/bin/activate
   python -c "from querysource.handlers._pagination import PaginationParams; print(PaginationParams())"
   ```
6. **Move this file** to `sdd/tasks/completed/TASK-624-pagination-models-and-sql-builders.md`.
7. **Update index** → `"done"`.
8. **Fill in the Completion Note** below.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
