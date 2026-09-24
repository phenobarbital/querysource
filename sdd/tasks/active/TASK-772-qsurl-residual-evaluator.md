# TASK-772: Residual evaluator (`residual.apply`) — eval-free pandas stage

**Feature**: FEAT-152 — qsurl: HTSQL-style URL query dialect (chumsky 0.13 + PyO3)
**Spec**: `sdd/specs/qsurl-parser.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-765
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 7, AC11, AC13, AC14. Whatever the provider did not push down is applied in
memory by this module. qsurl literals come from the URL, so leaves are evaluated with
**vectorised pandas calls, never string `eval`**: `types/dt/filters.py::build_condition`
interpolates raw values into eval strings (`filters.py:82-91`) and is therefore not reused
as a mechanism — only its semantics are mirrored. Text operators are case-insensitive
everywhere (brainstorm resolution), which `filters.py`'s `startswith`/`endswith` are not.

---

## Scope

- Implement `evaluate(df, node) -> pd.Series` and `apply(rows, plan)` per the tables below.
- Apply order is fixed: filter → sort → project → distinct → offset → limit → rename.
- Test every leaf branch, the order, list round-trip, error cases, and AC13 (no `eval` in the module source).

| expression | pandas evaluation |
|---|---|
| `== != < <= > >=` scalar | `df[c] <op> v`; if `dtype` ∈ {date, datetime}: `pd.to_datetime(df[c], utc=True, errors="coerce") <op> pd.Timestamp(v).tz_localize("UTC") if naive else .tz_convert("UTC")` |
| `==` / `!=` list | `df[c].isin(v)` / `~df[c].isin(v)` |
| `is_null` | `df[c].isnull() \| (df[c] == "")` |
| `not_null` | negation of `is_null` |
| `contains` / `not_contains` | `df[c].astype("string").str.contains(re.escape(v), case=False, na=False, regex=True)` (negated) |
| `startswith` / `endswith` | `df[c].astype("string").str.lower().str.startswith(v.lower(), na=False)` / `.str.endswith(...)` |
| `regex` | `df[c].astype("string").str.contains(v, case=True, na=False, regex=True)`; `re.error` → `QSUrlError("lower", ...)` |
| `and` / `or` / `not` | `&` / `\|` / `~` over boolean Series (`and` of an empty list → all True) |

Unknown column (leaf, sort key, projection, rename) → `QSUrlError("lower", "column `<c>` not in result")`.

**NOT in scope**: the cost guard and `QS` wiring (TASK-773); modifying `types/dt/filters.py`.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/qsurl/residual.py` | CREATE | `evaluate`, `apply` |
| `tests/qsurl/test_residual.py` | CREATE | Leaf, order, error, no-eval tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
import pandas as pd                                  # already a dependency (querysource/types/dt/filters.py:6)
from querysource.qsurl.errors import QSUrlError      # TASK-765
from querysource.qsurl.plan import ResidualPlan      # TASK-765
```

### Existing Signatures to Use
```python
# querysource/types/dt/filters.py — SEMANTICS reference only, do NOT import or call
def build_condition(expression, column, value, condition, df=None) -> str   # line 22 — returns an eval STRING
    # is_null  → "df[c].isnull() | (df[c] == '')"                (55-56)
    # not_null → "~(df[c].isnull() | (df[c] == ''))"             (57-58)
    # contains → df[c].str.contains(r'{value}', na=False, case=False)   (82) — raw value as regex (unsafe)
    # startswith/endswith → case-SENSITIVE (86, 90)

# tests/qsurl/conftest.py (TASK-765): stores_df fixture — 8 rows, mixed case, None and '' cities, tz datetimes
```

### Does NOT Exist
- ~~`querysource/qsurl/residual.py`~~ — created here.
- ~~reusing `create_filter` / `build_condition`~~ — forbidden here (eval strings; spec §7 Known Risks).
- ~~`DataFrame.query` / `DataFrame.eval` / `eval` / `exec`~~ — must not appear in `residual.py` (AC13 test greps for them).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/qsurl/residual.py", "action": "CREATE"},
    {"path": "tests/qsurl/test_residual.py", "action": "CREATE"}
  ],
  "contract_symbols": []
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Implement `_leaf_mask` per the table — *why*: one function per semantic keeps the AC13 grep trivially true.
2. Implement recursive `evaluate` — *why*: IR nodes nest arbitrarily.
3. Implement `apply` with list↔DataFrame conversion and the fixed order — *why*: `QS` passes provider rows (list of records) or cached rows.
4. Write the tests using `stores_df`.

### `querysource/qsurl/residual.py` (CREATE)
```python
"""In-memory qsurl residual stage: apply a ResidualPlan without string eval."""
from __future__ import annotations

import logging
import operator
import re

import pandas as pd

from .errors import QSUrlError
from .plan import ResidualPlan

_logger = logging.getLogger(__name__)
_OPS = {"==": operator.eq, "!=": operator.ne, "<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}


def _column(df: pd.DataFrame, name: str) -> pd.Series:
    """Return ``df[name]`` or raise ``QSUrlError("lower")`` when it is missing."""
    if name not in df.columns:
        raise QSUrlError("lower", f"column `{name}` not in result")
    return df[name]


def _utc(value: str) -> pd.Timestamp:
    """Parse an ISO date/datetime literal as a UTC timestamp."""
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _leaf_mask(df: pd.DataFrame, leaf: dict) -> pd.Series:
    """Boolean mask for one IR leaf (see the table in TASK-772)."""
    col = _column(df, leaf["column"])
    expr = leaf["expression"]
    value = leaf.get("value")
    # FILL IN: every row of the leaf table; unknown expression → QSUrlError("lower", f"unsupported expression `{expr}`")


def evaluate(df: pd.DataFrame, node: dict) -> pd.Series:
    """Boolean mask for an IR filter node or leaf over ``df`` (recursive; no eval)."""
    if "and" in node:
        # FILL IN: start from pd.Series(True, index=df.index) and & each child
        ...
    if "or" in node:
        # FILL IN: start from pd.Series(False, index=df.index) and | each child
        ...
    if "not" in node:
        return ~evaluate(df, node["not"])
    return _leaf_mask(df, node)


def apply(rows: pd.DataFrame | list, plan: ResidualPlan) -> pd.DataFrame | list:
    """Apply ``plan`` in the order filter → sort → project → distinct → offset → limit → rename.

    A list input (records or asyncdb Records) is materialised with
    ``pd.DataFrame([dict(r) for r in rows])`` and returned as ``list[dict]``; a DataFrame
    is returned as a DataFrame. An empty plan returns ``rows`` untouched.

    Raises:
        QSUrlError: kind "lower" on unknown columns or an invalid regex.
    """
    if plan.is_empty():
        return rows
    as_list = not isinstance(rows, pd.DataFrame)
    df = pd.DataFrame([dict(r) for r in rows]) if as_list else rows
    # FILL IN: filter (df[evaluate(df, plan.filter)]), sort (sort_values by columns/ascending),
    #   project (df[list(plan.project)]), distinct (drop_duplicates), offset/limit (iloc),
    #   rename (df.rename(columns=dict(plan.rename))); reset_index(drop=True) at the end
    return df.to_dict("records") if as_list else df
```
**Why**: `operator` functions replace the eval-string comparison of `build_condition` while keeping identical semantics for scalars.

### `tests/qsurl/test_residual.py` (CREATE)
```python
"""Residual evaluator semantics (spec §3 M7, AC11/AC13/AC14)."""
from __future__ import annotations

from pathlib import Path

import pytest

import querysource.qsurl.residual as residual
from querysource.qsurl import QSUrlError, ResidualPlan


def test_module_never_uses_eval():
    src = Path(residual.__file__).read_text(encoding="utf-8")
    for banned in ("eval(", "exec(", ".query(", ".eval("):
        assert banned not in src

def test_empty_plan_returns_rows_untouched(stores_df):
    assert residual.apply(stores_df, ResidualPlan()) is stores_df

@pytest.mark.parametrize("leaf,expected_ids", [
    ({"column": "city", "expression": "startswith", "value": "SAN"}, None),   # FILL IN: case-insensitive
    ({"column": "city", "expression": "is_null"}, None),                     # FILL IN: None AND '' rows
    ({"column": "city", "expression": "contains", "value": "a.b"}, None),    # FILL IN: '.' is literal, not regex
    ({"column": "opened", "expression": ">=", "value": "2024-01-01T00:00:00+02:00", "dtype": "datetime"}, None),  # FILL IN: utc compare
])
def test_leaf_semantics(stores_df, leaf, expected_ids): ...   # FILL IN

def test_apply_order(stores_df): ...            # FILL IN: filter+sort+project+distinct+offset+limit+rename in one plan
def test_list_roundtrip(stores_df): ...         # FILL IN: list[dict] in → list[dict] out
def test_unknown_column_is_lower_error(stores_df): ...   # FILL IN
def test_bad_regex_is_lower_error(stores_df): ...        # FILL IN: "(" → QSUrlError kind "lower"
```

### FILL IN checklist
- [ ] `_leaf_mask` — every table row.
- [ ] `evaluate` — `and`/`or` folds.
- [ ] `apply` — the seven steps in order.
- [ ] Test bodies and expected ids for the parametrised cases.

---

## Acceptance Criteria

- [ ] Every leaf row behaves as tabled; text operators are case-insensitive (AC11).
- [ ] Datetimes compare in UTC when `dtype` is set (AC14).
- [ ] `residual.py` contains no `eval(`, `exec(`, `.query(`, `.eval(` (AC13).
- [ ] `ruff check querysource/qsurl/residual.py tests/qsurl/test_residual.py` clean.

---

## Validation Commands

- `pytest tests/qsurl/test_residual.py -q`

---

## Test Specification

See the `tests/qsurl/test_residual.py` block above.

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at the path listed above for full context.
2. **Check dependencies** — verify every `Depends-on` task is `done` in `sdd/tasks/index/qsurl-parser.json`.
3. **Verify the Codebase Contract** before writing any code: re-run the `grep -c` for every MODIFY anchor; if a count changed, re-locate the anchor; if it is `0`, stop and report the drift.
4. **Update status** in `sdd/tasks/index/qsurl-parser.json` → `"in-progress"`.
5. **Implement** from the Implementation Blueprint; complete every `# FILL IN:`; never change a signature or path the blueprint fixes.
6. **Verify** every Acceptance Criterion and run the Validation Commands (`source .venv/bin/activate` first).
7. **Move this file** to `sdd/tasks/completed/TASK-772-qsurl-residual-evaluator.md` and set the index entry to `"done"`.
8. **Fill in the Completion Note** below.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
