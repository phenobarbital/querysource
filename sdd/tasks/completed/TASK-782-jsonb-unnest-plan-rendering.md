# TASK-782: JSONB-unnest plan rendering — select / group / order / having / lateral / wrap (Cython)

**Feature**: FEAT-153 — Group & Aggregate by JSONB Array Elements (PostgreSQL)
**Spec**: `sdd/specs/group-aggregation-jsonb-columns.spec.md`
**Status**: done
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-780
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 1 (second third), §2 rendering rules, AC3/AC5/AC6/AC8. Adds SQL rendering
and the public `unnest_plan` / `unnest_wrap` to `querysource/parsers/jsonb_unnest.pyx`.
Element filters are TASK-783: here every filter entry passes through to `row_filter`
unchanged, and `element_where` is always `[]`.

---

## Scope

- Add to `jsonb_unnest.pyx`: `render_ref`, `render_expr`, `default_alias`, a `_Planner`
  helper class, `unnest_plan`, `unnest_wrap`, `SAFE_CAST_PATTERNS`.
- Enforce: single array column, `columns` allowlist, `strict`, alias expansion,
  duplicate-alias rejection, `having` validation, `empty` policy, `safe_cast`.
- Tests `tests/test_jsonb_unnest_plan.py` with golden SQL strings.

**NOT in scope**: path-keyed filters + prefilter (TASK-783); parser wiring (TASK-784); Rust.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/parsers/jsonb_unnest.pyx` | MODIFY | Rendering + `unnest_plan` + `unnest_wrap` |
| `tests/test_jsonb_unnest_plan.py` | CREATE | Golden SQL rendering tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.parsers import jsonb_unnest as ju   # created by TASK-780 (verify: grep -c "def parse_expr" querysource/parsers/jsonb_unnest.pyx == 1)
```

### Existing Signatures to Use
```python
# querysource/parsers/jsonb_unnest.pyx (TASK-780)
ALLOWED_CASTS, AGGREGATES, BUCKETS, ARRAY_ALIAS='_qs_e0', SOURCE_ALIAS='_qs_src', IDENT_RE
cdef str _pg_literal(str value)
class Ref: column: str; keys: tuple; cast: str | None; is_path: bool
class Expr: kind in ('ref','count_star','agg','bucket'); func; distinct; arg; is_aggregate
class Item: text; expr; name; alias; direction; nulls
def parse_ref(text: str) -> Ref
def parse_expr(text: str) -> Expr
def parse_select_item(text: str) -> Item
def parse_order_item(text: str) -> Item
def validate_config(config) -> dict   # {'columns': dict|None, 'aliases': dict, 'strict': bool, 'safe_cast': bool}
def is_plan_candidate(fields, grouping, ordering, filter, having, config) -> bool
```

### Does NOT Exist
- ~~`jsonb_unnest.render_filter`~~ / ~~`_split_filters`~~ — TASK-783.
- ~~`date_trunc` passthrough syntax~~ — buckets only (resolved decision).
- ~~`pg_input_is_valid`~~ — PG 16+ only; not used (spec Q1: documented limitation).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/parsers/jsonb_unnest.pyx", "action": "MODIFY"},
    {"path": "tests/test_jsonb_unnest_plan.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/parsers/jsonb_unnest.pyx#parse_expr",
    "sym:querysource/parsers/jsonb_unnest.pyx#parse_select_item",
    "sym:querysource/parsers/jsonb_unnest.pyx#parse_order_item",
    "sym:querysource/parsers/jsonb_unnest.pyx#validate_config",
    "sym:querysource/parsers/jsonb_unnest.pyx#is_plan_candidate"
  ]
}
```

---

## Implementation Notes

### Rendering rules (fixed — TASK-785 reproduces them byte-for-byte)
| Input | Output |
|---|---|
| row ident `x` | `_qs_src.x` |
| row ident with cast `x::date` | `(_qs_src.x::date)` |
| path `a[].k` | `(_qs_e0.elem ->> 'k')` (key via `_pg_literal`) |
| nested `a[].k1.k2` | `(_qs_e0.elem -> 'k1' ->> 'k2')` |
| path + cast `a[].k::date` | `((_qs_e0.elem ->> 'k')::date)` |
| path + cast + safe_cast | `(CASE WHEN (_qs_e0.elem ->> 'k') ~ '<re>' THEN (_qs_e0.elem ->> 'k')::date END)` |
| bucket `month(r)` | `(date_trunc('month', <r as date>)::date)` — path w/o cast gets implicit `date`; row ident w/o cast is used as-is |
| `sum`/`avg` over path w/o cast | implicit `numeric` cast |
| `count(*)` | `count(*)` |
| `count(distinct r)` | `count(DISTINCT <r>)` |
| `min(r)` etc. | `min(<r>)` |
| select entry | `<expr> AS "<alias>"` (alias ALWAYS double-quoted) |
| group entry | rendered expression (never the alias) |
| order entry naming a select alias | `"<alias>"[ ASC|DESC][ NULLS FIRST|LAST]` |
| order entry otherwise | `<expr>[ ASC|DESC][ NULLS FIRST|LAST]` |

`text` casts are never guarded. `SAFE_CAST_PATTERNS` (brace- and backslash-free on purpose,
equivalent to spec §2's table — `[.]` replaces `\.` so `_pg_literal` emits a plain `'...'`):
```python
SAFE_CAST_PATTERNS = {
    'date': '^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]',
    'timestamp': '^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]',
    'timestamptz': '^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]',
    'int': '^-?[0-9]+$', 'integer': '^-?[0-9]+$', 'bigint': '^-?[0-9]+$',
    'numeric': '^-?[0-9]+([.][0-9]+)?$', 'float': '^-?[0-9]+([.][0-9]+)?$',
    'boolean': '^(true|false)$',
}
```
safe_cast applies to **path** refs only; effective value = `columns[col]['safe_cast']` when not
`None`, else top-level `safe_cast`.

### Default aliases
path → last key; row ident → ident; bucket → `<unit>_<name>`; `count(*)` → `count`;
`agg(ref)` → `<func>_<name>` (count distinct included); `agg(bucket)` → `<func>_<unit>_<name>`;
where `<name>` = last key for paths, column for row idents. A duplicate alias → 
`jsonb_unnest: duplicate output alias '<alias>'`.

### Alias expansion & strict
- A bare string in fields/grouping/order (ident only) that is a key of `config['aliases']`
  expands to `parse_expr(alias_value)` with alias = that name.
- In grouping/order, a bare name equal to a select alias refers to that select item.
- Precedence: select alias > config alias > parse as expression.
- `strict: true` → any path ref that did NOT come from alias expansion raises
  `jsonb_unnest: raw path '<text>' not allowed in strict mode`.

### Array column
All path refs across select/group/order/having must share one column, else
`jsonb_unnest: more than one array column ('<first>', '<second>')` (first = first seen).
With `config['columns']` not `None`, the column must be declared, else
`jsonb_unnest: array column '<col>' is not declared in columns`.
Lateral (empty from `columns[col]['empty']`, default `exclude`):
```
CROSS JOIN LATERAL jsonb_array_elements(CASE jsonb_typeof(_qs_src.<col>) WHEN 'array' THEN _qs_src.<col> ELSE '[]'::jsonb END) AS _qs_e0(elem)
LEFT JOIN LATERAL jsonb_array_elements(CASE jsonb_typeof(_qs_src.<col>) WHEN 'array' THEN _qs_src.<col> ELSE '[]'::jsonb END) AS _qs_e0(elem) ON true
```
No path anywhere → `lateral = ""`.

### having
- `having` not a dict → `jsonb_unnest: having must be a mapping`.
- Key resolves to: a select alias whose expr `is_aggregate`, else `parse_expr(key)` that
  `is_aggregate`; otherwise `jsonb_unnest: unknown having key '<key>'`.
- Value: `int`/`float` (not bool) → `<expr> = <v>`; `str` → `<expr> = <_pg_literal(v)>`;
  dict → one condition per `(op, v)` in insertion order, op ∈ `('=', '>=', '<=', '<>', '!=', '<', '>')`
  else `jsonb_unnest: invalid having operator '<op>'`; any other value type →
  `jsonb_unnest: invalid having value for '<key>'`.
- Non-empty having while no select item is an aggregate → `jsonb_unnest: having requires an aggregate`.

### Empty fields
select = each group item as `<expr> AS "<alias>"` then `count(*) AS "count"`.

### Plan dict (exact keys, all present)
`{"select": [...], "group_by": [...], "order_by": [...], "having": [...], "element_where": [], "lateral": str, "row_filter": dict(filter)}`

### unnest_wrap
```
SELECT <', '.join(select)> FROM (<inner_sql.strip()>) AS _qs_src[ <lateral>][ WHERE <' AND '.join(element_where)>]
```

---

## Implementation Blueprint

### Steps (in order)
1. Append the rendering helpers — *why*: pure functions are testable in isolation.
2. Append `_Planner`, `unnest_plan`, `unnest_wrap` — *why*: the public contract TASK-784 calls.
3. Write golden tests; `make build-inplace`; run validation.

### `querysource/parsers/jsonb_unnest.pyx` (MODIFY) — append at end of file
```python
# occurrences: 1 (verified: grep -c '^def is_plan_candidate' querysource/parsers/jsonb_unnest.pyx)
# AFTER — append below the end of `is_plan_candidate` (last function of the file after TASK-780)

SAFE_CAST_PATTERNS = {
    # FILL IN: copy the dict from Implementation Notes verbatim — bounded by spec §2 safe_cast.
}


def render_ref(ref: Ref, safe_cast: bool = False, implicit_cast: str | None = None) -> str:
    """Render a reference per the rendering table.

    Args:
        ref: parsed reference.
        safe_cast: guard the cast with SAFE_CAST_PATTERNS (path refs only).
        implicit_cast: cast applied to a path ref that has none (buckets → 'date', sum/avg → 'numeric').
    """
    # FILL IN: exactly the table rows; keys via _pg_literal; bounded by Implementation Notes.
    raise NotImplementedError


def render_expr(expr: Expr, safe_cast_for) -> str:
    """Render an expression; ``safe_cast_for(column) -> bool`` resolves the effective safe_cast."""
    # FILL IN: count_star / agg (DISTINCT uppercase) / bucket (date_trunc(...)::date) / ref,
    #   with implicit casts as specified — bounded by the rendering table.
    raise NotImplementedError


def default_alias(expr: Expr) -> str:
    """Default output alias per Implementation Notes."""
    # FILL IN: rules from "Default aliases".
    raise NotImplementedError


class _Planner:
    """Accumulates array-column, alias and strict-mode state while building one plan."""

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.array_column = None  # first path column seen
        self.select_aliases = {}  # alias -> Expr

    def expand(self, text: str, kind: str) -> Item:
        """Parse a fields ('select'), grouping ('group') or ordering ('order') entry with alias expansion."""
        # FILL IN: precedence select alias > config alias > parse; strict check on raw paths;
        #   record path columns via self.track(); bounded by "Alias expansion & strict".
        raise NotImplementedError

    def track(self, expr: Expr, text: str, from_alias: bool) -> None:
        """Register every path column in ``expr``; enforce single column, columns allowlist, strict."""
        # FILL IN: messages from "Array column" and "strict"; bounded by those sections.
        raise NotImplementedError

    def safe_cast_for(self, column: str) -> bool:
        """Effective safe_cast for ``column`` (column value, else top-level)."""
        # FILL IN: bounded by Implementation Notes safe_cast rule.
        raise NotImplementedError

    def lateral(self) -> str:
        """Render the lateral clause (or '' without an array column)."""
        # FILL IN: exact strings from "Array column"; bounded by the empty policy.
        raise NotImplementedError


def unnest_plan(fields, grouping, ordering, filter, having, config):
    """Build the UnnestPlan dict, or return None when ``is_plan_candidate`` is False.

    Raises:
        ValueError: any rule in Implementation Notes (exact messages).
    """
    if not is_plan_candidate(fields, grouping, ordering, filter, having, config):
        return None
    planner = _Planner(validate_config(config))
    # FILL IN: select items (default aliases, duplicate check, empty-fields rule), group items,
    #   order items, having; element_where [] and row_filter dict(filter or {}) in this task;
    #   return the dict with EXACTLY the keys listed under "Plan dict" — bounded by AC3/AC5/AC8.
    raise NotImplementedError


def unnest_wrap(inner_sql: str, plan: dict) -> str:
    """Wrap ``inner_sql`` (placeholders already blanked by the caller) per Implementation Notes."""
    sql = f"SELECT {', '.join(plan['select'])} FROM ({inner_sql.strip()}) AS {SOURCE_ALIAS}"
    if plan['lateral']:
        sql = f"{sql} {plan['lateral']}"
    if plan['element_where']:
        sql = f"{sql} WHERE {' AND '.join(plan['element_where'])}"
    return sql
```
**Why this shape**: `_Planner` keeps cross-entry state (single array column, aliases) out of
the pure renderers so the Rust port can mirror it as one struct. `unnest_wrap` is complete
because it is fully specified.

### `tests/test_jsonb_unnest_plan.py` (CREATE)
```python
"""FEAT-153 plan rendering (Cython): golden SQL for select/group/order/having/lateral/wrap."""
from __future__ import annotations

import pytest
import sqlglot

from querysource.parsers import jsonb_unnest as ju

LATERAL = (
    "CROSS JOIN LATERAL jsonb_array_elements(CASE jsonb_typeof(_qs_src.graduation_details) "
    "WHEN 'array' THEN _qs_src.graduation_details ELSE '[]'::jsonb END) AS _qs_e0(elem)"
)


def _plan(fields=(), grouping=(), ordering=(), filter=None, having=None, config=None):
    return ju.unnest_plan(list(fields), list(grouping), list(ordering), filter or {}, having or {}, config or {})


def test_spec_example_without_element_filter():
    plan = _plan(
        fields=["graduation_details[].course", "graduation_details[].category",
                "count(distinct student_uid) as graduates"],
        grouping=["graduation_details[].course", "graduation_details[].category"],
        ordering=["graduates DESC"],
        having={"graduates": {">": 5}},
        filter={"licensee": "'Asia'"},
    )
    assert plan["select"] == [
        "(_qs_e0.elem ->> 'course') AS \"course\"",
        "(_qs_e0.elem ->> 'category') AS \"category\"",
        "count(DISTINCT _qs_src.student_uid) AS \"graduates\"",
    ]
    assert plan["group_by"] == ["(_qs_e0.elem ->> 'course')", "(_qs_e0.elem ->> 'category')"]
    assert plan["order_by"] == ['"graduates" DESC']
    assert plan["having"] == ["count(DISTINCT _qs_src.student_uid) > 5"]
    assert plan["lateral"] == LATERAL
    assert plan["element_where"] == []
    assert plan["row_filter"] == {"licensee": "'Asia'"}
    sql = ju.unnest_wrap("SELECT * FROM students WHERE licensee='Asia'", plan)
    sqlglot.parse_one(sql, read="postgres")


def test_not_a_candidate_returns_none():
    assert _plan(fields=["a", "b"], grouping=["a"]) is None

# FILL IN: nested key; cast; safe_cast (column + top-level inheritance); buckets (all five,
#   implicit date); sum/avg implicit numeric; default aliases + duplicate alias error; empty
#   fields -> count(*) AS "count"; order NULLS LAST; config aliases (course / diploma_year);
#   strict rejects raw path / accepts alias; columns allowlist; two arrays error; empty include
#   -> LEFT JOIN ... ON true; having errors (non-mapping, unknown key, bad op, bad value,
#   no aggregate); having multi-op AND; having-only plan (no array -> lateral ""); every
#   rendered wrap parses with sqlglot — bounded by Implementation Notes (exact strings/messages).
```

### FILL IN checklist
- [ ] `SAFE_CAST_PATTERNS` — verbatim dict
- [ ] `render_ref` / `render_expr` / `default_alias` — rendering table
- [ ] `_Planner.expand/track/safe_cast_for/lateral` — alias, strict, array rules
- [ ] `unnest_plan` — assemble plan dict with exact keys
- [ ] tests — the listed cases

---

## Acceptance Criteria

- [ ] Golden strings match the rendering table exactly; every wrapped SQL parses with `sqlglot` (`read="postgres"`).
- [ ] Every error message listed in Implementation Notes is asserted exactly.
- [ ] `pytest tests/test_jsonb_unnest_grammar.py -q` still passes.
- [ ] `ruff check tests/test_jsonb_unnest_plan.py` is clean.

---

## Validation Commands

- `pytest tests/test_jsonb_unnest_plan.py -q`
- `pytest tests/test_jsonb_unnest_grammar.py -q`

---

## Test Specification

See the test block above.

---

## Agent Instructions

1. Read spec §2 Data Models; confirm TASK-780 is in `sdd/tasks/completed/`.
2. Implement; `make build-inplace` (exclusive resource).
3. Move this file to `sdd/tasks/completed/`, set index status `"done"`, fill the Completion Note.

---

## Completion Note

**Completed by**: sdd-worker (sequential fallback, Claude)
**Date**: 2026-09-28T22:33:34+00:00
**Notes**: Rendering, planner, having, wrap; 106 tests with 780. Extra: having keys also resolve config aliases that are aggregates (fallback before parse_expr); TASK-785 must mirror. Float having values render via Python repr.

**Deviations from spec**: safe_cast numeric regex uses `[.]` instead of `\.` (equivalent; avoids an `E''` literal).
