# TASK-876: Idempotent `Entity.quoteString` / `to_string` and `unquote_literal` (Cython + Python)

**Feature**: FEAT-167 — WHERE value hardening — `is_valid()` closure and idempotent literal quoting
**Spec**: `sdd/specs/where-value-hardening.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-874
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 3 (Cython/Python half) / goal G4. `is_valid()` pre-quotes a
plain string as `'O''Brien'` (module-level `quoteString`, `validators.pyx:155`,
pinned by `tests/qsurl/test_pg_ilike.py:112`), and the Cython dialect
builders then call `Entity.quoteString` (`validators.pyx:590`), which strips
the outer pair and doubles the interior quotes **again** → `'O''''Brien'`
(verified for pgsql, cql). Typed `string` placeholders go through
`to_string` (`validators.pyx:430` = `quoteString(escape_string(v))`), and
`escape_string` deletes the apostrophe → `'OBrien'`. This task adds the
`unquote_literal` grammar from spec §3 M3 and makes `Entity.quoteString` and
`to_string` idempotent. Depends on TASK-874 only because both edit
`validators.pyx`.

**Placement note**: the spec skeleton puts `unquote_literal` in
`querysource/parsers/filter_values.py`. That module imports
`querysource.types.validators` (`filter_values.py:12`), so `validators.pyx`
cannot import it back without a cycle. The function is therefore implemented
as `cpdef` in `validators.pyx` and **re-exported** from `filter_values.py`
under the spec's public name; both import paths work.

---

## Scope

- `validators.pyx`: add `cpdef object unquote_literal(object value)`; rewrite
  `Entity.quoteString` to unwrap a well-formed pre-quoted literal before
  escaping; `to_string` → `Entity.quoteString(value)`.
- `filter_values.py`: `from ..types.validators import unquote_literal` and
  add it to the module's public surface.
- Add `tests/unit/test_literal_quoting.py`.

**NOT in scope**: the module-level `quoteString` used by `is_valid`
(unchanged — the pre-quoting invariant stays); `escape_string` (still used
by the `literal` hint); Rust (TASK-877); e2e dialect renders (TASK-878).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/types/validators.pyx` | MODIFY | `unquote_literal` (new, near `:155`), `to_string` (`:430`), `Entity.quoteString` (`:590-618`) |
| `querysource/parsers/filter_values.py` | MODIFY | re-export `unquote_literal` (`:12`, after `_quote` `:52-54`) |
| `tests/unit/test_literal_quoting.py` | CREATE | grammar + idempotency tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.types.validators import Entity, is_valid, unquote_literal   # Entity verified: validators.pyx:489; is_valid :625; unquote_literal CREATED here
from querysource.parsers.filter_values import unquote_literal, _quote        # _quote verified: filter_values.py:52; unquote_literal re-exported here
from ..types.validators import is_udf                                        # verified: filter_values.py:12 (existing import line to extend)
```

### Existing Signatures to Use
```python
# querysource/types/validators.pyx (line numbers at base 82214b25; TASK-872/874 shift them — re-grep)
cdef str quoteString(object value)                      # line 155 — module-level pre-quoter; UNCHANGED
cdef str to_string(object value):                       # line 430
    return quoteString(escape_string(value))            # line 431 — REPLACED
cpdef object escape_string(object value)                # line 433 — keep; strips ' and "
cdef dict type_validators = {... "string": [is_string, to_string], "varchar": [...], "field": [...]}  # line 463 — unchanged
cdef class Entity:                                      # line 489
    @classmethod
    def escapeString(cls, value)                        # line 583 — unchanged
    @classmethod
    def quoteString(cls, value, bool_t no_dblquoting=True):   # line 590 — body 591-618 REPLACED
        # today: 'None'→"", null passthrough, bool→str, `"`→`'` when no_dblquoting,
        #        strip leading/trailing ', double interior ', wrap — NO undo of '' (the double-escape)
    @classmethod
    def dblQuoting(cls, value)                          # line 620 — unchanged

# Callers of Entity.quoteString (fixed transitively; do NOT edit):
#   parsers/pgsql.pyx:408,416,452,465,483,516,520,564; parsers/sql.pyx (8); parsers/cql.pyx (7);
#   parsers/sqlserver.pyx (3); parsers/sosql.pyx (3); handlers/_pagination.py (2);
#   repositories/definitions.py (2); handlers/manager.py (1); utils/fn.py (1)

# querysource/parsers/filter_values.py
from ..types.validators import is_udf        # line 12
def _quote(text: str) -> str:                # line 52-54 — "'" + text.replace("'", "''") + "'"

# Reference idempotent pattern (do not modify): querysource/parsers/bigquery.pyx:40 bq_quote_string —
#   unwrap `'...'`, `.replace("''", "'")`, then re-escape from scratch.
```

### Does NOT Exist
- ~~`unquote_literal`~~ in either module today — created here.
- ~~`Entity.unquote` / `Entity.pg_literal`~~ — `pg_literal` is a function in `pgsql.pyx` (ILIKE branch only).
- ~~`validators.pyx` importing `parsers.filter_values`~~ — would be a cycle; never add it.
- ~~changing the module-level `quoteString`~~ — out of scope; `test_pg_ilike.py:128` pins it.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/types/validators.pyx", "action": "MODIFY"},
    {"path": "querysource/parsers/filter_values.py", "action": "MODIFY"},
    {"path": "tests/unit/test_literal_quoting.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/types/validators.pyx#Entity",
    "sym:querysource/types/validators.pyx#to_string",
    "sym:querysource/types/validators.pyx#quoteString",
    "sym:querysource/parsers/filter_values.py#_quote"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- **Grammar (spec §3 M3, decided)**: `unquote_literal(v)` returns the inner
  text iff `len(v) >= 2`, `v[0] == v[-1] == "'"`, and `v[1:-1]` has no
  single `'` outside a `''` pair; inner `''` → `'`. Otherwise `None`.
- Quoting is then `inner = unquote_literal(v) or v; "'" + inner.replace("'", "''") + "'"`.
  Property: `q(q(x)) == q(x)` and `q(x) == "'" + x.replace("'", "''") + "'"` for raw `x`.
- Behaviour change to accept: a value with a *lone* leading or trailing `'`
  (e.g. `'abc`) used to be stripped; it is now escaped as text (`'''abc'`).
  Only well-formed literals unwrap.
- Existing special cases of `Entity.quoteString` stay in front: `'None'` →
  `''`, `null`/`NULL` passthrough, bool → `str`, leading `"` → `'` when
  `no_dblquoting`.
- Rebuild Cython before pytest.

### References in Codebase
- `querysource/parsers/bigquery.pyx:40-52` — `bq_quote_string`, the reference unwrap.
- `querysource/parsers/pgsql.pyx:418-441` — ILIKE strip-then-requote comments.

---

## Implementation Blueprint

### Steps (in order)
1. Add `unquote_literal` right after the module-level `quoteString` (after line ~176) — *why*: it is the grammar every quoter shares.
2. Replace `Entity.quoteString`'s body — *why*: G4; the builders call it on `is_valid`'s pre-quoted output.
3. Replace `to_string`'s body — *why*: typed `string` placeholders must not lose apostrophes.
4. Re-export from `filter_values.py` — *why*: the spec's public name; later BETWEEN work can use it.
5. Rebuild, `ruff check querysource/parsers/filter_values.py tests/unit/test_literal_quoting.py`, Validation Commands.

### `querysource/types/validators.pyx` (MODIFY — `unquote_literal`)
```python
# occurrences: 1 (verified: grep -c 'cdef str quoteString(object value):' querysource/types/validators.pyx)
# AFTER — insert below the END of the module-level quoteString function (its last `return "'{}'".format(inner)`), before `cpdef bool_t is_udf`
cpdef object unquote_literal(object value):
    """Inner text of a well-formed pre-quoted SQL literal, or None.

    Well-formed: ``len >= 2``, starts and ends with ``'``, and every interior
    ``'`` belongs to a ``''`` pair. The doubling is undone in the result
    (``"'O''Brien'"`` -> ``"O'Brien"``). Anything else (raw text, a lone
    quote, ``"'a'b'"``) returns None so the caller escapes it from scratch.
    """
    cdef str inner
    if not isinstance(value, str) or len(value) < 2:
        return None
    if value[0] != "'" or value[-1] != "'":
        return None
    inner = value[1:-1]
    if "'" in inner.replace("''", ""):
        return None
    return inner.replace("''", "'")
```
**Why**: this is the only place the "pre-quoted" shape is recognised; `bq_quote_string` and the ILIKE branches inline the same idea.

### `querysource/types/validators.pyx` (MODIFY — `Entity.quoteString`)
```python
# occurrences: 1 (verified: grep -c '    def quoteString(cls, value, bool_t no_dblquoting=True):' querysource/types/validators.pyx)
# REPLACE the method (line 590 through its `return v`, line 618) with:
    @classmethod
    def quoteString(cls, value, bool_t no_dblquoting=True):
        """Idempotent SQL literal quoting (FEAT-167 G4).

        quoteString(quoteString(x)) == quoteString(x): a well-formed pre-quoted
        literal (see unquote_literal) is unwrapped before escaping, so the
        is_valid() pre-quoting pass and this builder pass compose without
        double-escaping. Raw text round-trips with every ' doubled.
        """
        v = value if value != 'None' else ""
        if value == 'null' or value == 'NULL':
            return v
        if isinstance(v, bool):
            return str(v)
        if isinstance(v, str):
            if v.startswith('"') and no_dblquoting:
                v = v.replace('"', "'")
            inner = unquote_literal(v)
            if inner is None:
                inner = v
            v = inner.replace("'", "''")
        v = "'{}'".format(v) if type(v) == str else v
        return v
```
**Why**: same prologue as today (`'None'`, null, bool, `"`→`'`); only the strip step becomes the grammar-checked unwrap.

### `querysource/types/validators.pyx` (MODIFY — `to_string`)
```python
# occurrences: 1 (verified: grep -c 'cdef str to_string(object value):' querysource/types/validators.pyx)
# REPLACE lines 430-431 with:
cdef str to_string(object value):
    # Typed string/varchar/field hints: idempotent quoting, no quote-stripping escape_string (FEAT-167 G4).
    return Entity.quoteString(value)
```
**Why**: `is_valid('n', "O'Brien", 'string')` must return `'O''Brien'` (AC4). `Entity` is defined later in the module; the name resolves at call time.

### `querysource/parsers/filter_values.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'from ..types.validators import is_udf' querysource/parsers/filter_values.py)
# REPLACE line 12 with:
from ..types.validators import is_udf, unquote_literal

# occurrences: 1 (verified: grep -c 'def _quote(text: str) -> str:' querysource/parsers/filter_values.py)
# AFTER — insert below the `_quote` function body (verified: filter_values.py:54):
__all__ = [
    "BetweenClause", "COMPARISON_OPERATORS", "KEY_SUFFIX_CHARS", "TYPED_FILTER_FORMATS",
    "base_key", "is_comparison_dict", "parse_between", "unquote_literal",
]
```
**Why**: keeps the spec's public name importable from `filter_values` without a cycle; `__all__` makes the re-export explicit for ruff (F401).

### `tests/unit/test_literal_quoting.py` (CREATE)
```python
"""FEAT-167 TASK-876 — unquote_literal grammar and idempotent Entity.quoteString (AC4)."""
from __future__ import annotations

import pytest

from querysource.parsers.filter_values import unquote_literal as fv_unquote
from querysource.types.validators import Entity, is_valid, unquote_literal

RAW = ["O'Brien", "plain", "it''s", "'pre'", "50%", "a\\b", "", "x) OR (1=1", "Acme (US)"]


@pytest.mark.parametrize("value,expected", [
    ("'O''Brien'", "O'Brien"), ("'abc'", "abc"), ("''", ""), ("'it''''s'", "it''s"),
    ("O'Brien", None), ("'a'b'", None), ("'", None), ("'abc", None), ("abc'", None), (None, None), (5, None),
])
def test_unquote_literal_grammar(value, expected) -> None:
    assert unquote_literal(value) == expected
    assert fv_unquote is unquote_literal


@pytest.mark.parametrize("raw", RAW)
def test_entity_quote_string_idempotent(raw: str) -> None:
    once = Entity.quoteString(raw)
    assert once == "'" + raw.replace("'", "''") + "'"
    assert Entity.quoteString(once) == once
    assert Entity.quoteString(Entity.quoteString(once)) == once


def test_entity_quote_string_special_cases_unchanged() -> None:
    assert Entity.quoteString("null") == "null"
    assert Entity.quoteString("None") == "''"
    # FILL IN: bool and leading-double-quote cases (`True` -> "True"; '"x"' -> "'x'") — bounded by current behaviour (validators.pyx:590-618 at base)


def test_typed_string_keeps_apostrophe() -> None:
    assert is_valid("n", "O'Brien", "string") == "'O''Brien'"
    assert is_valid("n", "O'Brien", "varchar") == "'O''Brien'"


def test_prequoted_then_builder_pass() -> None:
    """The is_valid pre-quote composed with the builder quote yields a single escape."""
    pre = is_valid("n", "O'Brien")
    assert pre == "'O''Brien'"
    assert Entity.quoteString(pre) == "'O''Brien'"
```
**Why**: the idempotency property is the whole contract; the composed test is the exact failure mode seen at spec time.

### FILL IN checklist
- [ ] `test_literal_quoting.py::test_entity_quote_string_special_cases_unchanged` — bool / `"` cases; bounded by current behaviour

---

## Acceptance Criteria

- [ ] `Entity.quoteString` idempotent per the property test; `Entity.quoteString("'O''Brien'") == "'O''Brien'"` (AC4).
- [ ] `is_valid('n', "O'Brien", 'string')` → `"'O''Brien'"` (AC4).
- [ ] `unquote_literal` importable from both `querysource.types.validators` and `querysource.parsers.filter_values`.
- [ ] Existing suites green: `tests/qsurl/test_pg_ilike.py`, `tests/unit/test_cython_filter_fallback.py`, `tests/test_dialect_filter_preprocessing.py`.

---

## Validation Commands

- `pytest tests/unit/test_literal_quoting.py -q`
- `pytest tests/qsurl/test_pg_ilike.py -q`
- `pytest tests/unit/test_cython_filter_fallback.py -q`
- `pytest tests/test_dialect_filter_preprocessing.py -q`

---

## Test Specification

See the `tests/unit/test_literal_quoting.py` blueprint block above.

---

## Agent Instructions

When you pick up this task:

1. **Work in the feature worktree** — never on `base_branch`
   (`python -m scripts.sdd.ensure_worktree --slug where-value-hardening --feature-id FEAT-167`)
2. **Read the spec** at the path listed above for full context
3. **Check dependencies** — TASK-874 must be `"done"` in `sdd/tasks/index/where-value-hardening.json`
4. **Verify the Codebase Contract** — line numbers shift after TASK-872/874; re-grep every anchor
5. **Update status** in the per-spec index → `"in-progress"` and commit only that index file
6. **Implement** from the Implementation Blueprint; rebuild Cython before testing
7. **Verify** — run the Validation Commands
8. **Commit the code** — stage only the files this task lists
9. **Close the task** with `scripts/sdd/close_task.sh TASK-876 where-value-hardening verified`
10. **Fill in the Completion Note** below, then commit the staged SDD state

---

## Completion Note

Implemented by seat gpt-5.6-luna (codex), 1 attempt, 239s, merged.
- `types/validators.pyx`: new `unquote_literal`; `Entity.quoteString` idempotent (well-formed pre-quoted literals unwrapped once, anything else escaped from scratch); `to_string` delegates to it.
- `parsers/filter_values.py`: re-exports `unquote_literal` (`__all__`).
- Added `tests/unit/test_literal_quoting.py`.
- Validation: merge tier 23 passed (extensions rebuilt in-place first).
- Review: orchestrator diff review, no fixes.
- Closed via close_task.sh (no durable review EvidenceRef for finalize_task).
