# TASK-771: IR → pushdown `conditions` + `ResidualPlan` (`translate.split`)

**Feature**: FEAT-152 — qsurl: HTSQL-style URL query dialect (chumsky 0.13 + PyO3)
**Spec**: `sdd/specs/qsurl-parser.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-765
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 6, AC8, AC10, AC11, AC15. This is the pushdown decision: given the IR and the
executing provider's capabilities, produce (a) the flat `conditions` dict the existing
dialect parsers already consume (`fields`, `filter`, `ordering`, `_limit`, `_offset`) and
(b) a `ResidualPlan` for everything else. It is a pure function — it never imports
providers (the handler passes `provider.capabilities` in), so it can be built and tested
independently of TASK-770.

---

## Scope

Implement `querysource/qsurl/translate.py` with `IDENT_RE`, `like_escape`, `split`, obeying
these rules **exactly** (spec §3 M6):

1. `needed & UNSUPPORTED_PHASE1` → `QSUrlError("unsupported", "capability `<cap>` is not supported by any provider in this release")`, first offending cap in `capabilities.ALL` order.
2. Every column, alias and sort key must match `IDENT_RE`, else `QSUrlError("lower", ...)`.
3. **Filter** — only leaves that are *direct children of a root `and`* are pushdown candidates; a root `or`, any `or`/`not` subtree and every leaf not pushable go to `plan.filter` as `{"and": [...]}` (a root `or` goes whole), preserving order. Leaf table:

   | expression | value | pushdown | needs |
   |---|---|---|---|
   | `==` | scalar | `{col: v}` | `filter` |
   | `!=` | scalar | `{f"{col}!": v}` | `filter` |
   | `< <= > >=` | scalar | `{col: {op: v}}` | `filter` |
   | `==` | list | `{col: [..]}` | `in_list` |
   | `!=` | list | `{f"{col}!": [..]}` | `in_list` |
   | `is_null` / `not_null` | — | `{col: "null"}` / `{col: "!null"}` | `null_check` |
   | `startswith` / `contains` / `endswith` | str | `{col: {"ILIKE": like_escape(v)+"%"}}` / `"%"+…+"%"` / `"%"+…` | `text_match` |
   | `not_contains` | str | `{col: {"NOT ILIKE": "%"+like_escape(v)+"%"}}` | `text_match` |
   | `regex` | — | never | — |

   A leaf is pushed only if its needs ⊆ capabilities AND its pushdown key is not already used in this conjunction (later ones go residual). `dtype` is dropped on pushdown, kept on residual leaves. Scalar = str/int/float/bool; a `bool` or `None` value with `<`… is still a scalar.
4. **Fields** — `requested` = field names (alias fields use `column`). If the residual filter or a residual sort references columns outside `requested`, `conditions["fields"] = requested + missing` and `plan.project = tuple(requested)`. Aliases: `"col AS alias"` pushed only when `alias` ∈ capabilities AND the plan is otherwise empty; else aliases go to `plan.rename` and the bare column is pushed. No fields → no `fields` key.
5. **Sort** — `conditions["ordering"] = ["col", "col DESC"]` when `sort` ∈ capabilities and every key is a real column (not an alias-only name); else `plan.sort`.
6. **Window** — `_limit`/`_offset` pushed only when the capability is declared AND `plan.filter is None` AND not `distinct` AND (no sort or sort pushed). If either of limit/offset cannot be pushed, both go to the plan.
7. `distinct` → always `plan.distinct`.
8. `not residual_scan and plan.filter is not None and not conditions.get("filter")` → `QSUrlError("cost", "residual-only filter on a provider that forbids scans (residual_scan=False); push down at least one condition")`.
9. `conditions` contains no keys other than `fields`, `filter`, `ordering`, `_limit`, `_offset`; every dict is freshly built (the dialect parsers `popitem()` inner dicts).

**NOT in scope**: executing anything; reading providers; the residual evaluator (TASK-772).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/qsurl/translate.py` | CREATE | `IDENT_RE`, `like_escape`, `split` |
| `tests/qsurl/test_translate.py` | CREATE | One test per rule/table row |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.qsurl.errors import QSUrlError             # TASK-765
from querysource.qsurl.plan import ResidualPlan             # TASK-765
from querysource.qsurl import capabilities                  # TASK-765 (ALL, UNSUPPORTED_PHASE1, constants)
```

### Existing Signatures to Use
```text
Dialect-parser conventions the pushdown must target (verified):
  conditions['fields']   → AbstractParser._query_fields_sync pops it (querysource/parsers/abstract.pyx:199-200); list joined verbatim
                           by process_fields (rust/src/sql_parser.rs:502) — NO identifier validation there
  conditions['filter']   → _query_filter_sync (abstract.pyx:293-301)
  conditions['ordering'] → _ordering_sync (abstract.pyx:259-275), list kept as-is; ORDER BY joined with ', ' (sql.pyx:302)
  conditions['_limit']   → _query_limit_sync (abstract.pyx:207-213) ; conditions['_offset'] → _offset_pagination_sync (215-218)
  filter value forms (sql.pyx:113-248, pgsql.pyx:164-300):
    {col: v} → col='v' ; {col!: v} → col != 'v' ; {col: {op: v}} op ∈ ('>=','<=','<>','!=','<','>') (sql.pyx:25)
    {col: [..]} → IN ; {col!: [..]} → NOT IN ; 'null' / '!null' → IS NULL / IS NOT NULL
    {col: {"ILIKE"|"NOT ILIKE": pattern}} → PostgreSQL only, added by TASK-769
  the inner dict is consumed with value.popitem() (sql.pyx:154, pgsql.pyx:224) → never reuse dicts
```

```python
# IR shapes (TASK-764 / spec §2): root filter is None | {"and": [...]} | {"or": [...]}
# leaf: {"column": str | {"fn","args"}, "expression": str, "value"?: any, "dtype"?: "date"|"datetime"}
# node: {"and": [...]} | {"or": [...]} | {"not": node}
# fields: [str | {"alias": str, "column": str}] ; sort: [{"column": str, "order": "asc"|"desc"}]
# limit/offset: int | None ; distinct: bool ; requires: list[str]
```

### Does NOT Exist
- ~~`querysource/qsurl/translate.py`~~ — created here.
- ~~an `ILIKE` key suffix~~ — use the dict-operator form only.
- ~~`distinct` pushdown~~ — `_distinct` is popped by `set_options` (abstract.pyx:399) but no SQL dialect renders it.
- ~~`where_cond`~~ as the output key — emit `filter`; `where_cond` wins over it in `_query_filter_sync` and is reserved for callers.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/qsurl/translate.py", "action": "CREATE"},
    {"path": "tests/qsurl/test_translate.py", "action": "CREATE"}
  ],
  "contract_symbols": []
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Rule 1 and rule 2 first, as guards at the top of `split` — *why*: fail before any partial work.
2. Split the filter (rule 3) with a helper returning `(pushed: dict, residual: list)` — *why*: fields/sort/window decisions depend on whether a residual filter exists.
3. Decide sort (5), window (6), distinct (7), then fields/aliases (4) last — *why*: alias pushdown needs to know whether the plan is otherwise empty.
4. Apply the cost check (8) and return.
5. Tests: one per table row, per rule, both on `BASE` and on a PostgreSQL-like set (use literal frozensets; do not import providers).

### `querysource/qsurl/translate.py` (CREATE)
```python
"""Split a qsurl IR into pushdown ``conditions`` and an in-memory ``ResidualPlan``."""
from __future__ import annotations

import re

from . import capabilities as caps
from .errors import QSUrlError
from .plan import ResidualPlan

IDENT_RE: re.Pattern[str] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CMP = ("<", "<=", ">", ">=")
_TEXT_PATTERNS: dict[str, tuple[str, str, str]] = {
    # expression: (operator, prefix, suffix)
    "startswith": ("ILIKE", "", "%"),
    "contains": ("ILIKE", "%", "%"),
    "endswith": ("ILIKE", "%", ""),
    "not_contains": ("NOT ILIKE", "%", "%"),
}


def like_escape(value: str) -> str:
    """Escape ``\\``, ``%`` and ``_`` for use inside an ILIKE pattern (quoting is the builder's job)."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _leaf_pushdown(leaf: dict, capabilities: frozenset[str]) -> tuple[str, object] | None:
    """Return ``(key, value)`` for a pushable leaf, or None when it must stay residual."""
    # FILL IN: the leaf table (rule 3): column must be a plain str; value type checks; needs ⊆ capabilities
    #   — bounded by the Scope table; return None for regex, fn/dotted columns, or undeclared needs


def _split_filter(node: dict | None, capabilities: frozenset[str]) -> tuple[dict, dict | None]:
    """Return ``(pushed_filter, residual_filter)`` per rule 3 (duplicate keys stay residual)."""
    # FILL IN: None → ({}, None); root "or" → ({}, node); root "and" → iterate children in order


def _referenced_columns(node: dict | None) -> list[str]:
    """Plain-string columns referenced anywhere in ``node``, in first-seen order."""
    # FILL IN: recursive walk over and/or/not/leaf


def split(ir: dict, capabilities: frozenset[str], *, residual_scan: bool = True) -> tuple[dict, ResidualPlan]:
    """Split the IR into the parser ``conditions`` dict and the in-memory ResidualPlan.

    Args:
        ir: qsurl IR dict (``querysource.qsurl.parse`` output).
        capabilities: the executing provider's ``capabilities``.
        residual_scan: the executing provider's ``residual_scan`` flag.

    Returns:
        ``(conditions, plan)``; ``conditions`` only uses ``fields``, ``filter``,
        ``ordering``, ``_limit``, ``_offset``.

    Raises:
        QSUrlError: kind "unsupported" (functions/navigation), "lower" (bad identifier),
            or "cost" (residual-only filter with ``residual_scan=False``).
    """
    needed = set(ir.get("requires", []))
    for cap in caps.ALL:
        if cap in needed and cap in caps.UNSUPPORTED_PHASE1:
            raise QSUrlError("unsupported", f"capability `{cap}` is not supported by any provider in this release")
    # FILL IN: rule 2 identifier guard over fields (column + alias), sort keys and filter columns
    pushed_filter, residual_filter = _split_filter(ir.get("filter"), capabilities)
    conditions: dict = {}
    if pushed_filter:
        conditions["filter"] = pushed_filter
    # FILL IN: rules 5, 6, 7, then 4 (fields/aliases/projection) — build ResidualPlan(...)
    plan = ResidualPlan(filter=residual_filter)
    if not residual_scan and plan.filter is not None and not conditions.get("filter"):
        raise QSUrlError(
            "cost",
            "residual-only filter on a provider that forbids scans (residual_scan=False); "
            "push down at least one condition",
        )
    return conditions, plan
```
**Why this shape**: the pure-function boundary keeps the pushdown decision testable without a database and without providers; the rule order in `split` follows the data dependencies between rules.

### `tests/qsurl/test_translate.py` (CREATE)
```python
"""translate.split: every pushdown rule and table row (spec §3 M6, AC8/AC10/AC11/AC15)."""
from __future__ import annotations

import pytest

from querysource.qsurl import QSUrlError
from querysource.qsurl.translate import like_escape, split

BASE = frozenset({"select", "filter", "in_list", "null_check"})
SQL = BASE | {"alias", "sort", "limit", "offset"}
PG = SQL | {"text_match"}


def _ir(**over) -> dict:
    ir = {"slug": "s", "fields": [], "filter": None, "sort": [], "limit": None,
          "offset": None, "distinct": False, "requires": []}
    ir.update(over)
    return ir


def _leaf(col, expr, value=None, **extra) -> dict:
    d = {"column": col, "expression": expr, **extra}
    if value is not None:
        d["value"] = value
    return d


@pytest.mark.parametrize("leaf,key,value", [
    (_leaf("a", "==", "x"), "a", "x"),
    (_leaf("a", "!=", "x"), "a!", "x"),
    (_leaf("a", ">=", 5), "a", {">=": 5}),
    (_leaf("a", "==", ["x", "y"]), "a", ["x", "y"]),
    (_leaf("a", "!=", ["x"]), "a!", ["x"]),
    (_leaf("a", "is_null"), "a", "null"),
    (_leaf("a", "not_null"), "a", "!null"),
    (_leaf("a", "contains", "5%_off"), "a", {"ILIKE": "%5\\%\\_off%"}),
    (_leaf("a", "not_contains", "x"), "a", {"NOT ILIKE": "%x%"}),
])
def test_leaf_table_on_pg(leaf, key, value):
    conditions, plan = split(_ir(filter={"and": [leaf]}), PG)
    assert conditions["filter"] == {key: value} and plan.filter is None

def test_text_ops_residual_without_text_match(): ...     # FILL IN: BASE → filter residual, no "filter" key
def test_regex_always_residual(): ...                    # FILL IN
def test_root_or_is_full_residual(): ...                 # FILL IN
def test_duplicate_key_goes_residual(): ...              # FILL IN: price>10 & price<20
def test_dtype_dropped_on_pushdown_kept_on_residual(): ...  # FILL IN
def test_window_rules(): ...                             # FILL IN: residual filter / distinct / residual sort block _limit/_offset
def test_alias_pushdown_only_when_plan_empty(): ...      # FILL IN: "name AS n" vs plan.rename
def test_projection_adds_missing_columns(): ...          # FILL IN: plan.project + extra field
def test_unsupported_functions_navigation(): ...         # FILL IN: kind "unsupported", message names the cap
def test_bad_identifier_is_lower_error(): ...            # FILL IN
def test_cost_residual_only_without_scan(): ...          # FILL IN: residual_scan=False
def test_conditions_are_fresh_dicts(): ...               # FILL IN: mutating the output does not change the IR
def test_like_escape(): assert like_escape("a\\b%c_d") == "a\\\\b\\%c\\_d"
```

### FILL IN checklist
- [ ] `_leaf_pushdown`, `_split_filter`, `_referenced_columns`.
- [ ] `split`: rule 2, rules 4–7, `ResidualPlan` construction.
- [ ] Every stub test.

---

## Acceptance Criteria

- [ ] Every Scope rule and table row has a passing test (spec AC10).
- [ ] `functions`/`navigation` → `unsupported` (AC8); `residual_scan=False` residual-only → `cost` (AC12 pre-check).
- [ ] No window pushdown above a residual filter, distinct or residual sort (AC15).
- [ ] `%`, `_`, `\` in user text are escaped before reaching `ILIKE` (AC11).
- [ ] `ruff check querysource/qsurl/translate.py tests/qsurl/test_translate.py` clean.

---

## Validation Commands

- `pytest tests/qsurl/test_translate.py -q`

---

## Test Specification

See the `tests/qsurl/test_translate.py` block above.

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at the path listed above for full context.
2. **Check dependencies** — verify every `Depends-on` task is `done` in `sdd/tasks/index/qsurl-parser.json`.
3. **Verify the Codebase Contract** before writing any code: re-run the `grep -c` for every MODIFY anchor; if a count changed, re-locate the anchor; if it is `0`, stop and report the drift.
4. **Update status** in `sdd/tasks/index/qsurl-parser.json` → `"in-progress"`.
5. **Implement** from the Implementation Blueprint; complete every `# FILL IN:`; never change a signature or path the blueprint fixes.
6. **Verify** every Acceptance Criterion and run the Validation Commands (`source .venv/bin/activate` first).
7. **Move this file** to `sdd/tasks/completed/TASK-771-qsurl-translate-split.md` and set the index entry to `"done"`.
8. **Fill in the Completion Note** below.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
