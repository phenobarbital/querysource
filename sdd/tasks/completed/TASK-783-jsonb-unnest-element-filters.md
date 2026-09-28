# TASK-783: Element-level filters and opt-in containment pre-filter (Cython)

**Feature**: FEAT-153 — Group & Aggregate by JSONB Array Elements (PostgreSQL)
**Spec**: `sdd/specs/group-aggregation-jsonb-columns.spec.md`
**Status**: done
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-782
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 1 (last third), §2 "Element filter entries", AC4/AC6. Path-keyed `filter`
entries (`"graduation_details[].course": "Pilates Studio"`) must filter **per element** in
the outer `WHERE`, while every other filter entry stays a row filter rendered unchanged by
`filter_conditions` (including `@>` / `@>|`). The containment pre-filter is **opt-in** per
array column (`columns.<col>.prefilter: true`) — resolved in spec §8 Q2.

---

## Scope

- Add `_unquote`, `_filter_literal`, `split_filters` to `jsonb_unnest.pyx`.
- Make `unnest_plan` call `split_filters` so `element_where` / `row_filter` are populated,
  and path filter keys participate in the single-array / allowlist / strict checks.
- Tests `tests/test_jsonb_unnest_filters.py`.

**NOT in scope**: parser wiring (TASK-784); Rust (TASK-786).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/parsers/jsonb_unnest.pyx` | MODIFY | Filter split, literals, pre-filter; hook into `unnest_plan` |
| `tests/test_jsonb_unnest_filters.py` | CREATE | Element-filter tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.parsers import jsonb_unnest as ju   # TASK-780/782 (verify: grep -c "^def unnest_plan" querysource/parsers/jsonb_unnest.pyx == 1)
```

### Existing Signatures to Use
```python
# querysource/parsers/jsonb_unnest.pyx (TASK-780/782)
cdef str _pg_literal(str value)
def parse_ref(text: str) -> Ref
def render_ref(ref: Ref, safe_cast: bool = False, implicit_cast: str | None = None) -> str
class _Planner: cfg; array_column; expand(text, kind); track(expr, text, from_alias); safe_cast_for(column); lateral()
def unnest_plan(fields, grouping, ordering, filter, having, config) -> dict | None
# querysource/parsers/pgsql.pyx:282-285 — pre-quoted value pattern to mirror:
#   _v = v[1:-1].replace("''", "'") if len(v) >= 2 and v[0] == "'" and v[-1] == "'" else v
# querysource/parsers/pgsql.pyx:32 — JSONB_KEY_SUFFIXES = '|!~#@:' ; jsonb_condition strips them
#   from the column (pgsql.pyx:173; Rust pgsql_parser.rs:320) and the FEAT-103 key check strips them too (pgsql.pyx:237-243),
#   so extra pre-filter entries can use keys `<col>|`, `<col>||`, ... without colliding.
# querysource/types/validators.pyx:620 is_valid — upstream set_where quotes strings, keeps lists,
#   and `_where_element` (abstract.pyx:547) popitem()s comparison dicts to ONE operator.
```

### Does NOT Exist
- ~~Automatic pre-filter~~ — opt-in only (spec §8 Q2 resolved: opt-in).
- ~~SQL functions as element-filter values~~ — every value is a literal (spec Non-Goal).
- ~~Suffixes other than `!` on path keys~~ — `|~#@:` on a path key is a reference error.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/parsers/jsonb_unnest.pyx", "action": "MODIFY"},
    {"path": "tests/test_jsonb_unnest_filters.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/parsers/jsonb_unnest.pyx#unnest_plan",
    "sym:querysource/parsers/jsonb_unnest.pyx#render_ref",
    "sym:querysource/parsers/jsonb_unnest.pyx#parse_ref"
  ]
}
```

---

## Implementation Notes

### Which entries are element filters
Key `k`; `neg = k.endswith('!')`; `base = k[:-1] if neg else k`. Element filter when
`'[].' in base`, or `base` is a config alias whose expression is a **path ref**. Everything
else → `row_filter[k] = value` unchanged (same insertion order).
The key is parsed with `parse_ref` (aggregates/buckets are not allowed as filter keys →
reference error), then goes through `_Planner.track` (single array, allowlist, strict).

### Rendering (`expr` = `render_ref(ref, safe_cast_for(col))`)
| Value (after upstream processing) | Not negated | Negated (`!`) |
|---|---|---|
| `None`, `'null'`, `'NULL'` | `<expr> IS NULL` | `<expr> IS NOT NULL` |
| scalar (str/int/float/bool) | `<expr> = <lit>` | `<expr> <> <lit>` |
| non-empty list of scalars | `<expr> IN (<lit>, <lit>)` | `<expr> NOT IN (...)` |
| dict `{op: v}` (each pair AND-ed, insertion order), op ∈ `= >= <= <> != < >` | `<expr> <op> <lit>` | same (negation ignored for dicts) |

`<lit>` = `_filter_literal(v, key)`:
- `str` → `_pg_literal(_unquote(v))`
- `bool` → `_pg_literal('true' | 'false')`
- `int` / `float` → `_pg_literal(str(v))` (a text literal: PostgreSQL coerces it for casted
  paths, and an uncasted `->>` path compares as text — a bare number would raise
  `text = integer`).
- anything else (dict inside list, empty list, nested list) →
  `jsonb_unnest: invalid filter value for '<key>'`
Unknown dict operator → `jsonb_unnest: invalid filter operator '<op>' for '<key>'`.
`_unquote(v)`: if `len(v) >= 2 and v[0] == v[-1] == "'"` → `v[1:-1].replace("''", "'")`, else `v`.

### Opt-in pre-filter (row level)
Only when `cfg['columns']` declares the column with `prefilter: True`, the ref has **no cast**,
the entry is **not negated**, and the value is a str scalar (→ `=`) or a list of str (→ IN):
- document: nested object from the keys, e.g. keys `('course',)` + `'X'` → `{'course': 'X'}`;
  `('meta','level')` → `{'meta': {'level': 'X'}}` (values unquoted).
- `=` → `row_filter[<col><suffix>] = {'@>': [doc]}`
- IN → `row_filter[<col><suffix>] = {'@>|': [[doc_1], [doc_2], ...]}`
- `<suffix>` = the shortest run of `'|'` (`'|'`, `'||'`, …) making the key unused in `row_filter`
  — both jsonb_condition and the FEAT-103 check strip trailing `|`.
Pre-filters are appended AFTER all original row filters, in element-filter order.

---

## Implementation Blueprint

### Steps (in order)
1. Append the helpers and `split_filters` — *why*: isolates value handling from planning.
2. Replace the TASK-782 pass-through in `unnest_plan` with a `split_filters` call — *why*: filters must count toward the single-array rule.
3. Tests; `make build-inplace`; validation.

### `querysource/parsers/jsonb_unnest.pyx` (MODIFY) — append at end of file
```python
# occurrences: 1 (verified: grep -c '^def unnest_wrap' querysource/parsers/jsonb_unnest.pyx)
# AFTER — append below the end of `unnest_wrap`

FILTER_OPERATORS = ('=', '>=', '<=', '<>', '!=', '<', '>')


def _unquote(value: str) -> str:
    """Undo ``is_valid``/``quoteString`` quoting (mirrors pgsql.pyx:282-285)."""
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def _filter_literal(value, key: str) -> str:
    """Render one element-filter value as a text literal (see Implementation Notes)."""
    # FILL IN: str / bool / int / float rules; else ValueError invalid filter value —
    #   bounded by the "<lit>" rules (bool checked BEFORE int).
    raise NotImplementedError


def split_filters(planner, filter) -> tuple:
    """Split ``filter`` into ``(element_where: list[str], row_filter: dict)``.

    Raises:
        ValueError: reference/strict/array errors via ``planner.track``; invalid value/operator.
    """
    # FILL IN: classify each key; render per the table; collect opt-in pre-filters and append
    #   them after the row filters using the '|' suffix rule — bounded by Implementation Notes.
    raise NotImplementedError
```

```python
# In unnest_plan (added by TASK-782): replace the lines that set
#   element_where = []  and  row_filter = dict(filter or {})
# with:
    element_where, row_filter = split_filters(planner, filter or {})
# FILL IN: call split_filters BEFORE computing planner.lateral() so a path used only in a
#   filter still yields the lateral join — bounded by AC4.
```
**Why**: `lateral()` depends on `planner.array_column`; a filter-only path must still unnest.

### `tests/test_jsonb_unnest_filters.py` (CREATE)
```python
"""FEAT-153 element-level filters and opt-in containment pre-filter (Cython)."""
from __future__ import annotations

import pytest

from querysource.parsers import jsonb_unnest as ju

CFG_PREFILTER = {"columns": {"graduation_details": {"prefilter": True}}}


def _plan(filter, config=None, fields=("graduation_details[].course",)):
    return ju.unnest_plan(list(fields), [], [], filter, {}, config or {})


def test_scalar_element_filter_prequoted():
    plan = _plan({"graduation_details[].course": "'Pilates Studio'", "licensee": "'Asia'"})
    assert plan["element_where"] == ["(_qs_e0.elem ->> 'course') = 'Pilates Studio'"]
    assert plan["row_filter"] == {"licensee": "'Asia'"}


def test_cast_comparison():
    plan = _plan({"graduation_details[].course_date::date": {">=": "'2025-01-01'"}})
    assert plan["element_where"] == ["((_qs_e0.elem ->> 'course_date')::date) >= '2025-01-01'"]


def test_prefilter_opt_in():
    plan = _plan({"graduation_details[].course": "'Pilates Studio'"}, CFG_PREFILTER)
    assert plan["row_filter"] == {"graduation_details|": {"@>": [{"course": "Pilates Studio"}]}}


def test_no_prefilter_by_default():
    plan = _plan({"graduation_details[].course": "'Pilates Studio'"})
    assert plan["row_filter"] == {}

# FILL IN: negation (`!`) scalar/list/null; IN list; IS NULL; numbers rendered as text literals;
#   bool; SQL-function-looking value 'CURRENT_DATE' stays a literal; quote injection
#   ("'x'' OR 1=1 --'") stays escaped; invalid value (dict in list, empty list); invalid operator;
#   '|' suffix on path key -> reference error; filter-only path yields lateral; second array
#   column in a filter -> "more than one array column"; prefilter IN -> '@>|'; prefilter
#   skipped for cast / negated / comparison dict; suffix collision with an existing
#   'graduation_details' row filter -> key 'graduation_details|'; nested-key doc — bounded by
#   Implementation Notes tables (exact strings/messages).
```

### FILL IN checklist
- [ ] `_filter_literal` — literal rules
- [ ] `split_filters` — classification, rendering, pre-filter, suffix rule
- [ ] `unnest_plan` hook — before `lateral()`
- [ ] tests — listed cases

---

## Acceptance Criteria

- [ ] Row filters (incl. `@>`, `@>|`, `->>`) are returned in `row_filter` byte-for-byte unchanged.
- [ ] Pre-filter appears only with `prefilter: true` and only for `=`/IN on uncast, non-negated paths.
- [ ] `pytest tests/test_jsonb_unnest_plan.py tests/test_jsonb_unnest_grammar.py -q` still passes.
- [ ] `ruff check tests/test_jsonb_unnest_filters.py` is clean.

---

## Validation Commands

- `pytest tests/test_jsonb_unnest_filters.py -q`
- `pytest tests/test_jsonb_unnest_plan.py -q`

---

## Test Specification

See the test block above.

---

## Agent Instructions

1. Read spec §2 "Element filter entries" and §7; confirm TASK-782 is completed.
2. Implement; `make build-inplace` (exclusive resource).
3. Move this file to `sdd/tasks/completed/`, set index status `"done"`, fill the Completion Note.

---

## Completion Note

**Completed by**: sdd-worker (sequential fallback, Claude)
**Date**: 2026-09-28T22:34:37+00:00
**Notes**: split_filters runs after select/group/order/having tracking (so 'first array column' order = select,group,order,having,filter). Empty dict value -> invalid filter value; alias-path filter keys supported. 131 unnest tests pass.

**Deviations from spec**: none
