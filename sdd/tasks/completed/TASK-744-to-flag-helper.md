# TASK-744: Add `to_flag` flag-coercion helper to `querysource.types`

**Feature**: FEAT-149 — Falsy Refresh — flag-condition coercion for `refresh` and `paged`
**Spec**: `sdd/specs/falsy-refresh.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §2 / §3 Module 1. `BaseProvider` reads `refresh` with plain `bool()`, so the
string `"false"` is truthy; the parser reads it differently. This task adds the
single shared truth table both of them will use (TASK-745, TASK-746). The helper
is pure: it never logs — callers log and fall back.

---

## Scope

- Implement `cpdef object to_flag(object value)` in `querysource/types/validators.pyx`,
  directly after `strtobool`, with exactly the spec §2 truth table.
- Export it from `querysource/types/__init__.py` (import line + `__all__`).
- Rebuild the Cython extensions (`make build-inplace`).

**NOT in scope**: changing any caller (TASK-745 / TASK-746); writing the new test
file (TASK-747); touching `strtobool`, `is_boolean` or `converters.to_boolean`.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/types/validators.pyx` | MODIFY | add `cpdef object to_flag(object value)` after `strtobool` |
| `querysource/types/__init__.py` | MODIFY | import and export `to_flag` |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.types import strtobool, is_boolean, is_empty   # verified: querysource/types/__init__.py:2
```

### Existing Signatures to Use
```python
# querysource/types/validators.pyx:47-62
cpdef object strtobool(str val):
    # val = val.lower()
    # True:  'y', 'yes', 't', 'true', 'on', '1'
    # False: 'n', 'no', 'f', 'false', 'off', '0', 'null'
    # else: raise ValueError(f"invalid truth value for {val}")
    # NOTE: typed `str val` → passing a non-str raises TypeError, not ValueError.

# querysource/types/__init__.py
from .validators import is_boolean, is_empty, strtobool          # line 2
__all__ = (                                                       # line 95
    'SafeDict',
    'Singleton',
    'strtobool',                                                  # line 98
    ...
```

### Does NOT Exist
- ~~`querysource/types/validators.pxd`~~ — no `.pxd`; do not create one, do not `cimport`.
- ~~`to_flag` / `parse_flag` / `coerce_flag`~~ anywhere in `querysource/` — this task creates `to_flag`.
- ~~`validators.to_boolean` as a Python-callable~~ — it is `cdef` (line 350) and returns `'TRUE'`/`'FALSE'`; do not reuse it.
- ~~`legacy_implicit_noexcept`~~ — not set; build is Cython 3 defaults.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "querysource/types/validators.pyx",
      "action": "MODIFY"
    },
    {
      "path": "querysource/types/__init__.py",
      "action": "MODIFY"
    }
  ],
  "contract_symbols": [
    "sym:querysource/types/validators.pyx#strtobool"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Order of checks matters: `bool` **before** `int` (`bool` is a subclass of `int`).
- `bytes` / `bytearray` are NOT decoded → `ValueError`.
- Only call `strtobool` with a `str` (it is typed `str val`).
- Strings are `.strip()`ped first; an empty result returns `True` (flag style: bare `?refresh`).
- No logging inside the helper.
- `.claude/rules/cython-development.md`: use typed locals (`cdef str`) where it helps.

### References in Codebase
- `querysource/types/validators.pyx:47` — `strtobool`, reuse its word lists by calling it.

---

## Implementation Blueprint

### Steps (in order)
1. Insert `to_flag` after `strtobool` in `validators.pyx` — *why*: it reuses `strtobool`'s word lists, and keeping them adjacent documents the relationship.
2. Add `to_flag` to the `from .validators import ...` line and to `__all__` in `types/__init__.py` — *why*: callers import it as `from ..types import to_flag` (same as `strtobool` today).
3. Run `make build-inplace` — *why*: the `.so` must be rebuilt or `to_flag` is not importable.
4. Run the validation commands — *why*: confirms the rebuilt extension still serves the existing parser suite.

### `querysource/types/validators.pyx` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '^cpdef object strtobool(str val):' querysource/types/validators.pyx)
# AFTER — insert below the end of `strtobool` (the `raise ValueError(f"invalid truth value for {val}")` block ending at line 62),
# i.e. just before `cpdef list field_components(str field):` (verified: querysource/types/validators.pyx:64)

cpdef object to_flag(object value):
    """Coerce a flag-style condition value (e.g. ``refresh``, ``paged``) to bool.

    Truth table (FEAT-149): ``bool`` passes through; ``None`` is False; int ``1``/``0``
    are True/False; strings are stripped, an empty string is True (flag style), and
    otherwise follow :func:`strtobool`.

    Args:
        value: raw condition value (str from HTTP, bool/int/None from JSON or Python).

    Returns:
        bool: the coerced flag.

    Raises:
        ValueError: for unrecognized strings, bytes/bytearray, numbers other than
            int 0/1, and any other type.
    """
    cdef str text
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    # FILL IN: int branch — `type(value) is int` (or isinstance after the bool check): 1 → True, 0 → False, anything else → raise ValueError; bounded by spec §2 truth table
    # FILL IN: str branch — text = value.strip(); '' → True; else return strtobool(text) (lets its ValueError propagate); bounded by spec §2 truth table
    raise ValueError(f"invalid flag value: {value!r:.64}")
```
**Why this shape**: the helper raises instead of logging so each caller can log with its own logger and pick its own fallback (spec §7 "Patterns to Follow"). The final `raise` covers bytes, floats, Decimal, list, dict and everything else in one place — do not add a bytes-decoding branch (brainstorm decision: bytes are unrecognized).

### `querysource/types/__init__.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'from .validators import is_boolean, is_empty, strtobool' querysource/types/__init__.py)
# REPLACE line 2:
from .validators import is_boolean, is_empty, strtobool, to_flag

# occurrences: 1 (verified: grep -c "    'strtobool'," querysource/types/__init__.py)
# AFTER — insert below `    'strtobool',` (verified: querysource/types/__init__.py:98)
    'to_flag',
```
**Why**: exporting through the package keeps the import path identical to `strtobool`, which `parsers/abstract.pyx:15` already uses.

### FILL IN checklist
- [ ] `validators.pyx::to_flag` int branch — 1/0 only, others raise; bounded by spec §2
- [ ] `validators.pyx::to_flag` str branch — strip, empty → True, else `strtobool`; bounded by spec §2

---

## Acceptance Criteria

- [ ] `from querysource.types import to_flag` works and `'to_flag' in querysource.types.__all__`.
- [ ] `to_flag` matches spec §2 exactly: `True`/`1`/`''`/`'  '`/`' YES '` → True; `False`/`None`/`0`/`'false'`/`'null'` → False; `'maybe'`, `2`, `1.0`, `b'true'`, `[]` → `ValueError`.
- [ ] `make build-inplace` succeeds.
- [ ] Existing parser suites still pass (validation commands).

---

## Validation Commands

- `pytest tests/test_sql_parser_combinations.py -q`
- `pytest tests/test_grouping_sync.py -q`

---

## Test Specification

The dedicated truth-table tests are written in TASK-747 (`tests/test_flag_conditions.py`).
Quick manual check after the rebuild:

```python
from querysource.types import to_flag
assert to_flag("false") is False and to_flag("") is True and to_flag(1) is True
```

---

## Agent Instructions

1. Read the spec at the path above (§2 truth table, §6 contract).
2. Verify the Codebase Contract anchors (`grep -c`) before editing.
3. Update status in `sdd/tasks/index/falsy-refresh.json` → `"in-progress"`.
4. Implement from the blueprint; complete every `# FILL IN:`; never change the signature.
5. `make build-inplace`, then run the validation commands.
6. Move this file to `sdd/tasks/completed/`, set the index to `"done"`, fill the Completion Note.

---

## Completion Note

**Completed by**: sdd-worker (Claude Sonnet 5, sequential fallback loop)
**Date**: 2026-09-24
**Notes**: Implemented `to_flag` exactly per blueprint (bool → None → int 1/0 →
str strip/empty/strtobool → catch-all ValueError). Exported via
`querysource/types/__init__.py`. `make build-inplace` succeeded (rebuilt all
Cython extensions incl. `parsers/abstract.pyx`, which also covers TASK-746's
rebuild). Manual truth-table checks + both validation suites
(`test_sql_parser_combinations.py`, `test_grouping_sync.py`) pass.

**Deviations from spec**: none
