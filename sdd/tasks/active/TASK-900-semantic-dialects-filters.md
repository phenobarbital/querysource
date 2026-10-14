# TASK-900: Driver→dialect map and filter validation/rendering

**Feature**: FEAT-175 — Querysource Semantic Layer (Ossie models compiled by Sidemantic)
**Spec**: `sdd/specs/semantic-layer.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-897
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 2 and §7 driver→dialect table. `dialects.py` maps a QuerySource datasource driver to
a sqlglot/Sidemantic dialect (or rejects non-SQL drivers). `filters.py` validates caller and
governance filters — structured `{field, op, value}` and predicate strings — against the model's
dimensions and renders them to Sidemantic filter strings with sqlglot literal quoting (AC-13).

---

## Scope

- Implement `DRIVER_DIALECTS` and `dialect_for_driver()` per the §7 table.
- Implement `FilterValidator` with `render()`; `sqlglot` is imported **lazily** inside `filters.py` functions; `ImportError` → `SemanticCompilerUnavailable`.
- Tests `tests/semantic/test_dialects.py`, `tests/semantic/test_filters.py`.

**NOT in scope**: applying filters in the compiler (TASK-903), governance filter collection (TASK-902).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/semantic/dialects.py` | CREATE | driver → dialect |
| `querysource/semantic/filters.py` | CREATE | `FilterValidator` |
| `tests/semantic/test_dialects.py` | CREATE | dialect table tests |
| `tests/semantic/test_filters.py` | CREATE | filter tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.semantic.models import FILTER_OPS, CatalogField, SemanticFilter   # TASK-897
from querysource.semantic.exceptions import (SemanticRequestError, SemanticResourceError,
                                             SemanticCompilerUnavailable)        # TASK-897
from querysource.datasources.drivers import SUPPORTED   # verified: querysource/datasources/drivers/__init__.py:30 (tests only)
import sqlglot                       # 30.20.0 installed transitively; declared in the `semantic` extra (TASK-898) — LAZY import only
from sqlglot import exp              # sqlglot expression tree (exp.Column, exp.Func, exp.Subquery, exp.Select, exp.Literal)
```

### Existing Signatures to Use
```python
# querysource/datasources/drivers/__init__.py:30 — SUPPORTED: dict keyed by driver name
#   ('pg', 'redis', 'oracle', 'jdbc', 'sqlserver', ... — iterate the real keys in the test)
```

### Does NOT Exist
- ~~A dialect attribute on `BaseDriver`~~ — dialect is derived only from the driver name via `DRIVER_DIALECTS`.
- ~~`sqlglot` imported at module top-level~~ — forbidden (AC-3: base package import must not need it).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/semantic/dialects.py", "action": "CREATE"},
    {"path": "querysource/semantic/filters.py", "action": "CREATE"},
    {"path": "tests/semantic/test_dialects.py", "action": "CREATE"},
    {"path": "tests/semantic/test_filters.py", "action": "CREATE"}
  ],
  "contract_symbols": []
}
```

---

## Implementation Notes

### Key Constraints
- Non-SQL drivers (`arangodb, cassandra, delta, documentdb, elastic, hazel, iceberg, influx, mongo, redis, rethink, salesforce, scylladb`) → `SemanticResourceError` (400).
- Unknown/`jdbc` → `('ansi', '<warning>')`.
- String filters: every `exp.Column` must name a known dimension (qualified or not), no `exp.Subquery`/`exp.Select`, functions only from `ALLOWED_FUNCTIONS`.
- Structured values rendered as sqlglot literals (`exp.Literal.string`, `exp.convert`) so `"1 OR 1=1"` stays a quoted string.
- More than `max_filters` filters → `SemanticRequestError`.

---

## Implementation Blueprint

### Steps (in order)
1. Write `dialects.py` (pure table) — *why*: no dependencies, trivially testable.
2. Write `filters.py` with lazy sqlglot import helper — *why*: AC-3.
3. Write tests; run validation.

### `querysource/semantic/dialects.py` (CREATE)
```python
"""Driver → SQL dialect registry (FEAT-175 §7)."""
from __future__ import annotations

from .exceptions import SemanticResourceError

DRIVER_DIALECTS: dict[str, str] = {
    "pg": "postgres", "pg_admin": "postgres", "bigquery": "bigquery",
    "mysql": "mysql", "mariadb": "mysql", "sqlserver": "tsql",
    "oracle": "oracle", "sqlite": "sqlite",
}
NON_SQL_DRIVERS: frozenset[str] = frozenset({
    "arangodb", "cassandra", "delta", "documentdb", "elastic", "hazel", "iceberg",
    "influx", "mongo", "redis", "rethink", "salesforce", "scylladb",
})


def dialect_for_driver(driver: str) -> tuple[str, str | None]:
    """Return (dialect, warning). Non-SQL drivers raise SemanticResourceError (400)."""
    # FILL IN: lookup; NON_SQL → raise SemanticResourceError(f"... non-SQL datasource ..."); jdbc/unknown →
    # ("ansi", f"dialect not mapped for driver {driver}; ANSI SQL generated") — bounded by AC-5
```

### `querysource/semantic/filters.py` (CREATE)
```python
"""Validation and rendering of semantic filters (FEAT-175 M2)."""
from __future__ import annotations

from typing import Any

from .exceptions import SemanticCompilerUnavailable, SemanticRequestError
from .models import FILTER_OPS, CatalogField, SemanticFilter

ALLOWED_FUNCTIONS: frozenset[str] = frozenset(
    {"lower", "upper", "date_trunc", "coalesce", "abs", "round", "length"}
)


def _sqlglot() -> Any:
    """Lazy sqlglot import; missing → SemanticCompilerUnavailable (501)."""
    try:
        import sqlglot  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - exercised without the extra
        raise SemanticCompilerUnavailable() from exc
    return sqlglot


class FilterValidator:
    """Validates caller and governance filters against the model catalog."""

    def __init__(self, dimensions: dict[str, CatalogField], *, dialect: str, max_filters: int) -> None:
        self._dimensions = dimensions   # keyed by qualified "model.field"
        self._dialect = dialect
        self._max_filters = max_filters

    def resolve_field(self, name: str) -> str:
        """Qualify `name` against the dimensions; unknown/ambiguous → SemanticRequestError (lists names)."""
        # FILL IN: exact qualified match, else unique suffix match on ".name" — bounded by AC-14

    def render(self, filters: list[SemanticFilter | str]) -> list[str]:
        """Return Sidemantic filter strings; raises SemanticRequestError."""
        if len(filters) > self._max_filters:
            raise SemanticRequestError(f"Too many filters: {len(filters)} > {self._max_filters}")
        return [self._render_structured(f) if isinstance(f, SemanticFilter) else self._render_string(f)
                for f in filters]

    def _render_structured(self, flt: SemanticFilter) -> str:
        # FILL IN: field = resolve_field; op ∈ FILTER_OPS; build sqlglot expression with literals
        # (in/not in need list, between needs 2-item list, is null/is not null need value None);
        # return expression.sql(dialect=self._dialect) — bounded by AC-13

    def _render_string(self, text: str) -> str:
        # FILL IN: sqlglot.parse_one(text, dialect=self._dialect) (ParseError → 400); reject any
        # exp.Subquery/exp.Select/exp.Command; every exp.Column → resolve_field and rewrite to qualified
        # name; every exp.Func not in ALLOWED_FUNCTIONS (by lower-cased sql_name/key) → 400 — bounded by AC-13
```
**Why this shape**: signatures fixed by spec §3 M2; `resolve_field` is an addition reused by the
compiler for name qualification of filter columns.

### FILL IN checklist
- [ ] `dialect_for_driver` — table semantics; AC-5
- [ ] `FilterValidator.resolve_field` — qualification; AC-14
- [ ] `_render_structured` / `_render_string` — allow-lists and quoting; AC-13
- [ ] Test bodies

---

## Acceptance Criteria

- [ ] Every `SUPPORTED` key maps, warns ANSI, or raises for non-SQL (AC-5).
- [ ] Each `FILTER_OPS` renders; unknown field / bad op → 400; subquery / `pg_sleep` → 400; `lower(region) = 'es'` accepted; `"1 OR 1=1"` value stays quoted (AC-13).
- [ ] `import querysource.semantic.filters` does not import sqlglot.

---

## Validation Commands
- `pytest tests/semantic/test_dialects.py -q`
- `pytest tests/semantic/test_filters.py -q`

---

## Test Specification

```python
# tests/semantic/test_filters.py
import pytest
from querysource.semantic.filters import FilterValidator
from querysource.semantic.models import CatalogField, SemanticFilter, FILTER_OPS
from querysource.semantic.exceptions import SemanticRequestError

DIMS = {"orders.region": CatalogField(name="orders.region", kind="dimension", type="categorical"),
        "orders.order_date": CatalogField(name="orders.order_date", kind="dimension", type="time")}


@pytest.fixture
def validator():
    return FilterValidator(DIMS, dialect="postgres", max_filters=4)


def test_filter_structured_ops_and_unknown_dimension(validator):
    # FILL IN: one valid value per op renders; field='nope' → 400


def test_filter_string_rejects_subquery_and_function(validator):
    for bad in ("region = (SELECT 1)", "pg_sleep(1) = 1"):
        with pytest.raises(SemanticRequestError):
            validator.render([bad])
    assert validator.render(["lower(region) = 'es'"])


def test_filter_string_literal_quoting(validator):
    out = validator.render([SemanticFilter(field="region", op="=", value="1 OR 1=1")])
    assert "'1 OR 1=1'" in out[0]
```
```python
# tests/semantic/test_dialects.py
from querysource.datasources.drivers import SUPPORTED
# FILL IN: test_dialect_for_driver_table — iterate SUPPORTED keys; each maps, warns, or raises SemanticResourceError
```

---

## Agent Instructions

1. Work in the feature worktree; TASK-897 must be done.
2. Implement from the blueprint; run validation; commit only listed files.
3. Close with `scripts/sdd/close_task.sh TASK-900 semantic-layer verified`.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
