# TASK-796: DNF filter translation for Parquet sources

**Feature**: FEAT-158 — Parquet Sources for MultiQS (local, S3, GCS over fsspec)
**Spec**: `sdd/specs/parquet-multiqs-source.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 2 and the §2 filter grammar. YAML `filters` are **DNF lists only**: a flat list of
`[col, op, value]` means AND, and a list of such lists means OR of ANDs. Operators come from an
allowlist, and nothing is parsed from a string or evaluated. This pure module converts them into a
`pyarrow.compute.Expression` that `ParquetSource` (TASK-797) pushes into the dataset scan. This task
also creates the `parquet` subpackage with a docstring-only `__init__.py`. TASK-801 fills in its exports.

---

## Scope

- Create `querysource/queries/multi/sources/parquet/__init__.py` (module docstring only: no imports, no `__all__`).
- Implement `ALLOWED_OPERATORS`, `build_filter_expression(filters)` and `filter_columns(filters)` in `filters.py`.
- Write `tests/test_source_parquet_filters.py`.

**NOT in scope**: the source classes; exporting anything from the subpackage `__init__` (TASK-801).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/sources/parquet/__init__.py` | CREATE | Package marker (docstring only) |
| `querysource/queries/multi/sources/parquet/filters.py` | CREATE | DNF → pyarrow expression |
| `tests/test_source_parquet_filters.py` | CREATE | Unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# Third-party, verified in .venv (pyarrow 25.0.1) on 2026-09-30:
import pyarrow.compute as pc   # pc.field(name) == v, .isin([...]), .is_null(), &, |, ~
```
Verified behaviour: `(pc.field('c') == 'US') & pc.field('a').isin([1, 3])` works with
`pyarrow.dataset.Dataset.count_rows(filter=...)` and `.to_table(filter=...)`. `pc.field('a').is_null()` works too.

### Existing Signatures to Use
None. This is a new, dependency-free module.

### Does NOT Exist
- ~~`querysource.queries.multi.sources.parquet`~~: created by this task.
- ~~Any existing filter-expression helper in querysource~~: there is none. Don't look for one to reuse.
- ~~`pyarrow.parquet.filters_to_expression`~~: don't use it. It accepts operators outside our allowlist, and the translation must stay explicit.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/queries/multi/sources/parquet/__init__.py", "action": "CREATE"},
    {"path": "querysource/queries/multi/sources/parquet/filters.py", "action": "CREATE"},
    {"path": "tests/test_source_parquet_filters.py", "action": "CREATE"}
  ],
  "contract_symbols": []
}
```

---

## Implementation Notes

### Key Constraints
- Import pyarrow **inside the functions**, not at module level, because importing
  `querysource.queries.multi.sources` must never import pyarrow eagerly (spec §5 AC).
- Operator normalization: lower-case the op and collapse internal whitespace (`"NOT  IN"` → `"not in"`), then check it against `ALLOWED_OPERATORS`.
- `=` and `==` are equivalent.
- Error messages must name the offending entry's index and the operator, but never echo the whole filter value list (it may be large).

---

## Implementation Blueprint

### Steps (in order)
1. Create `parquet/__init__.py` with a docstring only — *why*: TASK-801 owns the exports, and an empty init keeps imports lazy.
2. Write `filters.py` from the block below and complete the FILL INs — *why*: it implements the resolved "DNF lists only" decision.
3. Write the tests — *why*: this module is the security boundary for user-supplied filters.

### `querysource/queries/multi/sources/parquet/__init__.py` (CREATE)
```python
"""Parquet sources for MultiQS (FEAT-158): local filesystem, S3 and GCS over fsspec."""
```
**Why**: it's a package marker only. TASK-801 adds the exports.

### `querysource/queries/multi/sources/parquet/filters.py` (CREATE)
```python
"""DNF filter translation for Parquet sources (FEAT-158).

Filters are DNF lists only: a flat list of ``[col, op, value]`` entries is an AND,
and a list of such lists is an OR of ANDs. Operators come from
:data:`ALLOWED_OPERATORS`. Nothing is ever parsed from a string or evaluated.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pyarrow.compute as pc

ALLOWED_OPERATORS: frozenset[str] = frozenset({
    "=", "==", "!=", "<", "<=", ">", ">=",
    "in", "not in", "is null", "is not null",
})
_NULL_OPERATORS = frozenset({"is null", "is not null"})
_LIST_OPERATORS = frozenset({"in", "not in"})


def _is_conjunction(filters: list) -> bool:
    """Return True when *filters* is a flat AND list (its first item is a predicate)."""
    # FILL IN: a predicate is a list/tuple whose first element is a str (column name);
    #          an OR-of-ANDs item is a list whose first element is itself a list/tuple.
    #          Bounded by: spec §2 filter grammar.
    raise NotImplementedError


def _predicate(entry: Any, position: str) -> "pc.Expression":
    """Convert one ``[col, op, value]`` (or ``[col, "is null"]``) entry.

    Raises:
        ValueError: malformed entry, unknown operator, non-list value for
            ``in``/``not in``, or a value given to a null operator.
    """
    import pyarrow.compute as pc  # noqa: PLC0415

    # FILL IN: validate shape (list/tuple of len 2 or 3, str column), normalize op
    #          (lower, collapse whitespace), check ALLOWED_OPERATORS, then map:
    #          = / == → field == v; != → field != v; < <= > >=; in → field.isin(list);
    #          not in → ~field.isin(list); is null → field.is_null();
    #          is not null → ~field.is_null(). `position` (e.g. "filters[1][0]") goes
    #          into every ValueError message. Bounded by: spec §2 grammar, §5 AC (no eval).
    raise NotImplementedError


def build_filter_expression(filters: list | None) -> "pc.Expression | None":
    """Convert a DNF filter list into a pyarrow compute expression.

    Args:
        filters: None, a flat list of [col, op, value] (AND), or a list of such
            lists (OR of ANDs).

    Returns:
        The combined expression, or None when ``filters`` is None or empty.

    Raises:
        ValueError: on an unknown operator, a malformed entry, a non-list value
            for ``in``/``not in``, or a value given to ``is null``/``is not null``.
    """
    if not filters:
        return None
    if not isinstance(filters, list):
        raise ValueError("filters must be a list of [column, operator, value] entries")
    # FILL IN: flat → AND-reduce _predicate over entries (position f"filters[{i}]");
    #          nested → OR-reduce the AND of each group (position f"filters[{i}][{j}]");
    #          an empty group → ValueError. Bounded by: spec §2 grammar.
    raise NotImplementedError


def filter_columns(filters: list | None) -> set[str]:
    """Return every column name referenced by *filters* (empty set for None/[]).

    Used by ParquetSource to report unknown columns against the dataset schema
    before scanning. Assumes *filters* already passed build_filter_expression.
    """
    # FILL IN: walk the same flat/nested shapes and collect entry[0].
    raise NotImplementedError
```
**Why this shape**: `build_filter_expression` is the exact signature from spec §3 M2. `filter_columns`
is a small addition TASK-797 needs for the "unknown column → ValueError" rule (spec §7). Keep every pyarrow
import function-local.

### `tests/test_source_parquet_filters.py` (CREATE)
```python
"""Unit tests for Parquet DNF filter translation (FEAT-158, TASK-796)."""
import pyarrow as pa
import pyarrow.dataset as ds
import pytest

from querysource.queries.multi.sources.parquet.filters import (
    ALLOWED_OPERATORS,
    build_filter_expression,
    filter_columns,
)


@pytest.fixture
def dataset():
    """In-memory dataset used to evaluate expressions."""
    table = pa.table({
        "a": [1, 2, 3, None],
        "c": ["US", "CA", "US", "MX"],
    })
    return ds.dataset(table)


def _count(dataset, filters) -> int:
    return dataset.count_rows(filter=build_filter_expression(filters))


class TestBuildFilterExpression:
    def test_none_or_empty(self):
        assert build_filter_expression(None) is None
        assert build_filter_expression([]) is None

    def test_flat_and(self, dataset):
        assert _count(dataset, [["c", "==", "US"], ["a", ">=", 2]]) == 1

    def test_or_of_ands(self, dataset):
        # FILL IN: [[["c","==","US"]], [["c","==","CA"]]] → 3 rows
        raise NotImplementedError

    # FILL IN: test_in_requires_list, test_not_in, test_unknown_operator
    #          ("like", "eval", "__import__" → ValueError), test_is_null_forms
    #          (["a","is null"] and ["a","is null",None]→ ValueError for value given? —
    #          decide per _predicate docstring: 2-element accepted, a non-None 3rd value rejected),
    #          test_equals_aliases ("=" and "=="), test_malformed_entry, test_string_filter_rejected
    #          (filters="a > 1" → ValueError).


def test_filter_columns():
    assert filter_columns([["a", ">", 1], ["c", "==", "US"]]) == {"a", "c"}
    assert filter_columns(None) == set()


def test_allowed_operators_is_exact():
    assert ALLOWED_OPERATORS == frozenset({
        "=", "==", "!=", "<", "<=", ">", ">=", "in", "not in", "is null", "is not null",
    })
```

### FILL IN checklist
- [ ] `filters.py::_is_conjunction`: shape detection; bounded by spec §2 grammar.
- [ ] `filters.py::_predicate`: validation and mapping; bounded by the allowlist and no eval.
- [ ] `filters.py::build_filter_expression`: AND/OR reduction; empty group → ValueError.
- [ ] `filters.py::filter_columns`: column collection.
- [ ] Remaining test bodies listed in the test file.

---

## Acceptance Criteria

- [ ] Flat lists AND together, nested lists OR together, and None/[] → None.
- [ ] Every operator outside `ALLOWED_OPERATORS`, every malformed entry and a string `filters` → `ValueError`.
- [ ] `in`/`not in` require a list value, and `is null`/`is not null` accept 2-element entries.
- [ ] `import querysource.queries.multi.sources.parquet.filters` does not import pyarrow (module-level).
- [ ] `ruff check querysource/queries/multi/sources/parquet/ tests/test_source_parquet_filters.py` is clean.

---

## Validation Commands

- `pytest tests/test_source_parquet_filters.py -q`

---

## Test Specification

See the `tests/test_source_parquet_filters.py` block above.

---

## Agent Instructions

1. **Work in the feature worktree**: `python -m scripts.sdd.ensure_worktree --slug parquet-multiqs-source --feature-id FEAT-158`.
2. **Read the spec** at the path above.
3. **Check dependencies** in `sdd/tasks/index/parquet-multiqs-source.json`.
4. **Verify the Codebase Contract** before writing code.
5. **Update status** to `"in-progress"` (set `started_at`) and commit only the index file.
6. **Implement** from the Blueprint and complete every FILL IN.
7. **Verify**: run the Validation Commands.
8. **Commit the code**, staging only the files listed above.
9. **Close**: `scripts/sdd/close_task.sh TASK-796 parquet-multiqs-source verified`.
10. **Fill in the Completion Note**, then commit the staged SDD state.

---

## Completion Note

**Completed by**: sdd-worker (seat gpt-5.6-terra, codex, 1 attempt, ~188s)
**Date**: 2026-09-30
**Notes**: `filters.py` delivered as specified. Review fix `deb4e5a`: `test_import_is_lazy` could not pass
(pandas imports pyarrow via the package chain; subprocess resolved to another checkout); replaced with an AST check of
module-level imports. 19 tests pass; ruff clean. feedback_id: coder-feedback:c0cd44883e6ef7ef0653bd4c

**Deviations from spec**: none
