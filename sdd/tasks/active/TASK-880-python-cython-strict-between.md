# TASK-880: Strict canonical BETWEEN + numeric BigQuery operands (Python/Cython)

**Feature**: FEAT-168 — Builder parity: numeric comparison operands and strict canonical BETWEEN
**Spec**: `sdd/specs/src-bigquery-parser-tech-debt.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned
**discovered_from**: issue:fc81723e95ac, issue:7a248a32aa07

---

## Context

Spec §1 A/B, §3 Module 1. The Cython builders (the fallback used when the Rust
call fails) decide "splice this BETWEEN string verbatim" from a prefix plus a
marker blacklist (`--`, `/*`, `;`, `UNION`, `SELECT`), which does not stop
`BETWEEN 1 AND 2 OR 1=1`. Separately, the Cython BigQuery builder quotes
numeric comparison-dict operands as strings (`x > "1"`), a type error against an
INT64 column.

---

## Scope

- Add `has_between_prefix(value)` and `is_canonical_between(value)` to
  `querysource/parsers/filter_values.py` and export both in `__all__`.
- Make `_normalise_bound` reject a bound containing a backslash (`ParserError`),
  so `BetweenClause.render()` never emits a value the builders would refuse.
- In `sql.pyx`, `sqlserver.pyx`, `bigquery.pyx`, `pgsql.pyx`: replace the
  `startswith(("BETWEEN ", "NOT BETWEEN "))` + blacklist block with
  `has_between_prefix` → `is_canonical_between` → emit, else `continue` (drop).
- In `bigquery.pyx`: comparison-dict operands that are `int`/`float` and not
  `bool` render bare (`str(v)`), both in the all-comparisons comprehension and
  in the single-token `COMPARISON_TOKENS` branch.
- Unit tests for the grammar helpers in `tests/unit/test_between_grammar.py`.

**NOT in scope**: Rust (TASK-881); `tests/test_dialect_filter_parity.py` and
builder-level cross-path tests (TASK-882); `pgsql.pyx`/`sql.pyx` comparison
rendering (already bare for numbers).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/parsers/filter_values.py` | MODIFY | grammar helpers, backslash rejection |
| `querysource/parsers/sql.pyx` | MODIFY | strict BETWEEN |
| `querysource/parsers/sqlserver.pyx` | MODIFY | strict BETWEEN |
| `querysource/parsers/bigquery.pyx` | MODIFY | strict BETWEEN, numeric operands bare |
| `querysource/parsers/pgsql.pyx` | MODIFY | strict BETWEEN |
| `tests/unit/test_between_grammar.py` | CREATE | helper unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.parsers.filter_values import base_key, is_comparison_dict, parse_between, BetweenClause  # filter_values.py:44,49,95,31
from querysource.exceptions import ParserError  # filter_values.py:11
# Cython builders import from filter_values on exactly this line (1 occurrence each):
from .filter_values import base_key, is_comparison_dict  # sql.pyx:15, sqlserver.pyx:12, bigquery.pyx:13, pgsql.pyx:17
```

### Existing Signatures to Use
```python
# querysource/parsers/filter_values.py (verified at dda6d6b6)
_BOUND = r"(?:'(?:[^']|'')*'|[A-Za-z0-9_:./+-]+)"            # :21
_BETWEEN_PREFIX = re.compile(r"^\s*(?:NOT\s+)?BETWEEN\s", re.IGNORECASE)  # :22
_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")                    # :27
class BetweenClause:                                          # :31
    def render(self) -> str:  # :38 → f"{'NOT BETWEEN' if negated else 'BETWEEN'} {low} AND {high}"
def _quote(text: str) -> str                                  # :54  "'" + text.replace("'", "''") + "'"
__all__ = [...]                                               # :59
def _normalise_bound(key: str, bound: str) -> str             # :76
def parse_between(key: str, value: str) -> BetweenClause | None  # :95
# querysource/parsers/bigquery.pyx
cdef str bq_quote_string(object value)                        # :40
```

### Does NOT Exist
- ~~`filter_values.is_canonical_between`~~ / ~~`filter_values.has_between_prefix`~~ — this task creates them
- ~~`querysource.parsers.filter_values.CANONICAL_BETWEEN`~~ — name the compiled regex `_CANONICAL_BETWEEN` (private)
- ~~a Cython `cimport` of filter_values~~ — it is a pure-Python module; use the normal `from .filter_values import ...`

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/parsers/filter_values.py", "action": "MODIFY"},
    {"path": "querysource/parsers/sql.pyx", "action": "MODIFY"},
    {"path": "querysource/parsers/sqlserver.pyx", "action": "MODIFY"},
    {"path": "querysource/parsers/bigquery.pyx", "action": "MODIFY"},
    {"path": "querysource/parsers/pgsql.pyx", "action": "MODIFY"},
    {"path": "tests/unit/test_between_grammar.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/parsers/filter_values.py#BetweenClause",
    "sym:querysource/parsers/filter_values.py#_normalise_bound",
    "sym:querysource/parsers/filter_values.py#parse_between"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- The canonical grammar is exactly `BetweenClause.render()` output (spec §2):
  `^(?:NOT )?BETWEEN (B) AND (B)$`, `B = -?\d+(?:\.\d+)?|'(?:[^'\\]|'')*'`,
  case-sensitive, single spaces, `fullmatch`.
- `bool` is a subclass of `int` — test `isinstance(v, bool)` first.
- After editing `.pyx` files rebuild in place (`python setup.py build_ext --inplace`
  in the worktree). Never `make clean`.

---

## Implementation Blueprint

### Steps (in order)
1. Add the helpers to `filter_values.py` — *why*: one Python definition of the grammar shared by all four Cython builders.
2. Add the backslash rejection in `_normalise_bound` — *why*: pre-processing must never emit a clause the builders drop (spec §2).
3. Edit the four `.pyx` BETWEEN blocks and the two BigQuery comparison sites — *why*: G1–G3.
4. Rebuild Cython in place, write and run `tests/unit/test_between_grammar.py` — *why*: AC3 groundwork; builder cross-path tests come in TASK-882.

### `querysource/parsers/filter_values.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '_NUMBER = re.compile' querysource/parsers/filter_values.py)
# AFTER — insert below `_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")` (verified: filter_values.py:27)
# Exactly what BetweenClause.render() can emit; builders splice nothing else (FEAT-168).
_CANONICAL_BOUND = r"(?:-?\d+(?:\.\d+)?|'(?:[^'\\]|'')*')"
_CANONICAL_BETWEEN = re.compile(rf"(?:NOT )?BETWEEN {_CANONICAL_BOUND} AND {_CANONICAL_BOUND}")


def has_between_prefix(value: object) -> bool:
    """Return whether ``value`` is a str that starts like a builder BETWEEN clause."""
    return isinstance(value, str) and value.startswith(("BETWEEN ", "NOT BETWEEN "))


def is_canonical_between(value: object) -> bool:
    """Return whether ``value`` exactly matches ``BetweenClause.render()`` output."""
    return isinstance(value, str) and _CANONICAL_BETWEEN.fullmatch(value) is not None
```
```python
# occurrences: 1 (verified: grep -c 'if any(marker in bound for marker in (";", "--", "/\*")):' ...)
# REPLACE the condition at filter_values.py:82 with one that also rejects "\\":
    if "\\" in bound or any(marker in bound for marker in (";", "--", "/*")):
```
```python
# __all__ (filter_values.py:59): add "has_between_prefix" and "is_canonical_between" (keep alphabetical order).
```
**Why**: `_normalise_bound` quotes identifier-like bounds via `_quote`, so a
backslash could only arrive inside a quoted bound or an unquoted token; rejecting
it in both keeps `render()` ⊆ grammar. Do not change `_BOUND` or `_BETWEEN_CLAUSE`.

### Cython builders (MODIFY ×4) — same shape in each
```python
# occurrences: 1 per file (verified: grep -c 'str_value.startswith(("BETWEEN ", "NOT BETWEEN "))')
# sql.pyx:235 / sqlserver.pyx:171 / bigquery.pyx:290 use `if`; pgsql.pyx:498 uses `elif`. Keep the keyword.
                    if has_between_prefix(str_value):
                        # SECURITY (FEAT-168): splice only the canonical form parse_between emits.
                        if not is_canonical_between(str_value):
                            continue
                        where_cond.append(f"({key} {str_value})")   # bigquery.pyx: f"({field_expr} {str_value})"
```
And extend each file's import line:
`from .filter_values import base_key, has_between_prefix, is_canonical_between, is_comparison_dict`
**Why**: drop-on-mismatch preserves today's outcome for rejected values; the
blacklist is replaced, not supplemented.

### `querysource/parsers/bigquery.pyx` — numeric operands (MODIFY)
```python
# occurrences: 1 (verified: bigquery.pyx:247 and :252, one each)
# Add a module-level helper next to bq_quote_string (bigquery.pyx:40):
cdef str bq_comparison_operand(object value):
    """Render a comparison-dict operand: numbers bare, everything else bq-quoted."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return bq_quote_string(str(value))
# :247 → parts = [f"{field_expr} {op} {bq_comparison_operand(v)}" for op, v in value.items()]
# :252 → where_cond.append(f"{field_expr} {op} {bq_comparison_operand(v)}")
```
**Why**: BigQuery is strictly typed (spec §1 A); `str(v)` before quoting is what loses the type today.

### `tests/unit/test_between_grammar.py` (CREATE)
```python
"""FEAT-168: canonical BETWEEN grammar shared by the builders."""
from __future__ import annotations

import pytest

from querysource.exceptions import ParserError
from querysource.parsers.filter_values import has_between_prefix, is_canonical_between, parse_between

ACCEPT = ["BETWEEN 1 AND 2", "NOT BETWEEN -1.5 AND 3", "BETWEEN '2025-01-01' AND 'O''Brien'"]
REJECT = [
    "BETWEEN 1 AND 2 OR 1=1", "BETWEEN 1 AND 2;", "between 1 and 2", "BETWEEN  1 AND 2",
    "BETWEEN 'a\\' AND 'b'", "BETWEEN x AND y", " BETWEEN 1 AND 2", None, 5,
]


@pytest.mark.parametrize("value", ACCEPT)
def test_is_canonical_between_accepts(value: str) -> None:
    assert is_canonical_between(value)


@pytest.mark.parametrize("value", REJECT)
def test_is_canonical_between_rejects(value: object) -> None:
    assert not is_canonical_between(value)

# FILL IN: test_render_is_canonical — for a corpus of parse_between inputs
#   (numbers, quoted dates, bare 2025-12-31, NOT/`!` key negation, udf keyword if any),
#   assert is_canonical_between(parse_between(key, v).render()); bounded by spec §4.
# FILL IN: test_backslash_bound_rejected — parse_between("d", "BETWEEN 'a\\' AND 'b'") raises ParserError.
# FILL IN: test_has_between_prefix — True for "BETWEEN ..."/"NOT BETWEEN ...", False for "IN BETWEEN", non-str.
```

### FILL IN checklist
- [ ] `test_render_is_canonical` corpus — bounded by spec §4 / gotcha "grammar drift"
- [ ] `test_backslash_bound_rejected`, `test_has_between_prefix`

---

## Acceptance Criteria

- [ ] Helpers exist, exported, and the grammar tests pass
- [ ] No `.pyx` builder contains the `'UNION' in upper_val` blacklist any more
- [ ] BigQuery Cython renders `{">": 1}` as `x > 1`, `{">": "b"}` as `x > "b"`
- [ ] `ruff check querysource/parsers/filter_values.py tests/unit/test_between_grammar.py` clean

---

## Validation Commands

- `pytest tests/unit/test_between_grammar.py -q`
- `pytest tests/test_dialect_filter_preprocessing.py -q`

---

## Agent Instructions

1. Work in the feature worktree (`python -m scripts.sdd.ensure_worktree --slug src-bigquery-parser-tech-debt --feature-id FEAT-168`).
2. Verify the Codebase Contract anchors, implement from the blueprint, rebuild Cython in place, run the Validation Commands.
3. Commit only the listed files; close with `scripts/sdd/close_task.sh TASK-880 src-bigquery-parser-tech-debt verified`.

---

## Completion Note

*(Agent fills this in when done)*
