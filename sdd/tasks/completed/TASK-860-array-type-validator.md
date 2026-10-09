# TASK-860: `array`/`json` type validator rejects plain strings

**Feature**: FEAT-165 — JSON Dialect Filter Pre-processing Fixes
**Spec**: `sdd/specs/json-dialect-filter-fixes.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §2 C / §3 Module 3 (bug 3). `is_valid(key, 'vip', 'array')` raises
`ValueError: invalid literal for int()` because the `array` row of
`type_validators` uses `is_array`, which is `True` for any `str` (it checks
`collections.abc.Sequence`), and then calls `to_unquoted('vip')`. A request
with a flat `array`-typed placeholder holding a string therefore fails.

---

## Scope

- Add `cpdef bool_t is_collection(object value)` next to `is_array`.
- Use it in the `"array"` and `"json"` rows of `type_validators` only.
- Write tests in `tests/test_validators_array_type.py`.

**NOT in scope**: changing `is_array` (it has other callers); routing changes
in `abstract.pyx` (TASK-862).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/types/validators.pyx` | MODIFY | add `is_collection`, swap it into two `type_validators` rows |
| `tests/test_validators_array_type.py` | CREATE | tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.types.validators import is_valid, is_array  # verified: validators.pyx:620, :259
```

### Existing Signatures to Use
```python
# querysource/types/validators.pyx
cpdef bool_t is_array(object value):                 # line 259
    return isinstance(value,(list, dict, Sequence, ndarray))   # Sequence → True for str
cdef dict type_validators = {                        # line 458
    "array": [ is_array, to_unquoted ],              # line 460
    "json": [ is_array, to_unquoted ],               # line 461
cpdef object is_valid(object key, object value, str T = None, bint noquote = False):  # line 620
    # if T: validator, conv = type_validators[T]; if validator(value): return conv(value)
# `ndarray` is already imported at module top: `from numpy import int64, ndarray`
```

### Does NOT Exist
- ~~`querysource.types.validators.is_collection`~~ — this task creates it.
- ~~`querysource/types/validators.pxd`~~ — does not exist (verified); no declaration file to update.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/types/validators.pyx", "action": "MODIFY"},
    {"path": "tests/test_validators_array_type.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/types/validators.pyx#is_array",
    "sym:querysource/types/validators.pyx#is_valid"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Rebuild the extension before running tests:
  `source .venv/bin/activate && python setup.py build_ext --inplace --build-temp /tmp/qs-build`
  (never `make build` — `make clean` deletes every `.so` in `.venv`).
- Exclusive task: the rebuild touches shared compiled artifacts.

---

## Implementation Blueprint

### Steps (in order)
1. Add `is_collection` below `is_array` — *why*: a narrow check that excludes `str`/`bytes` without changing `is_array`'s other callers.
2. Swap the two `type_validators` rows — *why*: `is_valid(T='array')` must fall through (not crash) for a string.
3. Rebuild in place and run the tests — *why*: `.pyx` changes are invisible until compiled.

### `querysource/types/validators.pyx` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'cpdef bool_t is_array(object value):' querysource/types/validators.pyx)
# AFTER — insert below the 2-line body of `cpdef bool_t is_array(object value):` (verified: validators.pyx:259)
cpdef bool_t is_collection(object value):
    """True for list, tuple, dict or numpy ndarray; False for str/bytes."""
    return isinstance(value, (list, tuple, dict, ndarray))
```
```python
# occurrences: 1 each (verified: grep -c '"array": \[ is_array, to_unquoted \],' and '"json": \[ is_array, to_unquoted \],')
# REPLACE (verified: validators.pyx:460-461)
    "array": [ is_collection, to_unquoted ],
    "json": [ is_collection, to_unquoted ],
```
**Why**: `is_valid` only converts when the validator accepts the value; with
`is_collection` a string skips conversion and continues through `is_valid`'s
generic branches (quoted literal), so the request no longer fails.

### `tests/test_validators_array_type.py` (CREATE)
```python
"""FEAT-165: array/json typed values never crash on a plain string."""
from querysource.types.validators import is_array, is_collection, is_valid


def test_is_collection():
    assert is_collection(['a']) and is_collection({'a': 1}) and is_collection(('a',))
    assert not is_collection('vip') and not is_collection(b'vip')


def test_is_valid_array_rejects_str():
    # must not raise ValueError (was: int('vip'))
    assert is_valid('tags', 'vip', 'array') is not None


# FILL IN: list value still accepted for 'array'/'json'; is_array('vip') unchanged (still True)
#          — bounded by "is_array itself is unchanged" (spec §2 C)
```

### FILL IN checklist
- [ ] remaining test cases — bounded by spec §2 C

---

## Acceptance Criteria

- [ ] `is_valid('tags', 'vip', 'array')` returns without raising
- [ ] `is_array` behaviour unchanged
- [ ] `pytest tests/test_validators_array_type.py -v` passes after an in-place rebuild

## Validation Commands

- `pytest tests/test_validators_array_type.py -q`

## Completion Note

Seat: gpt-5.6-terra · Backend: codex · Model: gpt-5.6-terra · Attempts: 1 · Duration: 225s · Tokens: n/a.
Added `is_collection`, swapped into `array`/`json` rows; `is_array` unchanged. 4 tests passed after in-place rebuild. Review: no fixes (coder-review:663930f4193446797f7f07ea).
