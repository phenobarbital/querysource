# TASK-734: UDF keyword resolution on the raw-query path

**Feature**: FEAT-148 — Describe Query-Slug REST Endpoints
**Spec**: `sdd/specs/describe-queryslug.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

This task implements spec §3 **Module 1**, the first task of the feature (a brainstorm decision). It ships in 4.6.0.

**Why it is needed.** Relative-date keywords (`TODAY`, `FDOM`, …) are accepted as condition values:
- The Cython parser path (`types/validators.pyx::is_valid`) resolves them through `to_udf`.
- The Rust validating substitution used by raw-query providers (`_rs.safe_format_map_validated`) never resolves them, typed or untyped.

The describe API (later tasks) advertises `accepts_keywords`, so both paths must agree first.

**Reproduction (2026-09-15):**
- Cython `is_valid('firstdate', 'FDOM', 'date')` → `'2026-09-01'`.
- Rust `safe_format_map_validated("{firstdate}", {'firstdate': 'FDOM'}, {'firstdate': 'date'})` → `'FDOM'`.

**Chosen mechanism (spec §8 default).** Pre-resolve keyword values in Cython *before* the Rust call. Rust is **not** modified.

The same Cython module also gets Python-visible accessors for the effective `UDF_LIST`, `PG_CONSTANTS` and `PG_UDF`, which TASK-739 (the vocabulary) needs. It also gets a fix for their environment override, which currently assigns a raw `str` to a `cdef list`.

---

## Scope

- Add `_env_list()`, `udf_keywords()`, `pg_constants()`, `pg_udfs()` and `resolve_udf_conditions()` to `querysource/types/validators.pyx`.
- Make the `UDF_LIST`, `PG_CONSTANTS` and `PG_UDF` initialisation use `_env_list` (comma-separated override).
- Add `BaseProvider._udf_resolved_conditions()` in `querysource/providers/abstract.py`.
- Replace `self._conditions` with `self._udf_resolved_conditions()` as the **second argument** of every `_rs.safe_format_map_validated(...)` call: 7 sites in 6 files (contract correction: the original count of "6 sites in 5 files" missed `querysource/providers/documentdb.py:102`, which the AC's repo-wide grep also requires fixed — see Completion Note).
- Rebuild the Cython extensions (`make build-inplace`).
- Write `tests/unit/test_udf_keyword_resolution.py`, covering resolution rules, parity, no mutation and the env override.

**NOT in scope**:
- Any change to `rust/src/*.rs`.
- Any change to keyword date masks (`TODAY` stays `%m/%d/%Y`).
- `describe_columns` (TASK-738) and the vocabulary registry (TASK-739).
- Parser-path code (`parsers/*.pyx`), which already resolves keywords.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/types/validators.pyx` | MODIFY | env parsing, accessors, `resolve_udf_conditions` |
| `querysource/providers/abstract.py` | MODIFY | `BaseProvider._udf_resolved_conditions()` |
| `querysource/providers/sql.py` | MODIFY | 2 call sites (`raw_query`, `get_raw_query`) |
| `querysource/providers/mysql.py` | MODIFY | 1 call site |
| `querysource/providers/sqlserver.py` | MODIFY | 1 call site |
| `querysource/providers/cassandra.py` | MODIFY | 1 call site |
| `querysource/providers/default.py` | MODIFY | 1 call site |
| `querysource/providers/documentdb.py` | MODIFY | 1 call site (contract correction — see Scope) |
| `tests/unit/test_udf_keyword_resolution.py` | CREATE | unit + parity tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.types.validators import is_valid, is_udf, to_udf   # verified: runtime import 2026-09-15
from querysource.utils.functions import to_udf                       # verified: querysource/utils/functions.pyx:821
from querysource.qs_parsers import HAS_RUST                           # verified: querysource/providers/sql.py:25
from querysource.qs_parsers import _qs_parsers as _rs                 # verified: querysource/providers/sql.py:27
from querysource.providers.sql import sqlProvider                     # verified: querysource/providers/sql.py:32
# validators.pyx already has: import os (:4); from ..utils.functions import * (:22)
```

### Existing Signatures to Use
```python
# querysource/types/validators.pyx
cdef list udf = ["CURRENT_YEAR", "CURRENT_MONTH", "TODAY", "YESTERDAY", "LAST_YEAR", "FDOM", "LDOM"]  # :27
cdef list UDF_LIST = os.environ.get('UDF_LIST', udf)            # :28  (occurrences: 1)
cdef list PG_CONSTANTS = os.environ.get(                        # :29-32
    'PG_CONSTANTS',
     ["CURRENT_DATE", "CURRENT_TIMESTAMP"]
)
cdef list PG_UDF = os.environ.get('PG_UDF', ["now()"])          # :33  (occurrences: 1)
cpdef bool_t is_udf(object value): return value in UDF_LIST     # :149-150 (case-sensitive; callers uppercase)
cpdef object is_valid(object key, object value, str T = None, bint noquote = False)  # :523
    # typed validators first (:536-545), generic: elif is_udf(str(value).upper()): return quoteString(to_udf(value))  # :552-553

# querysource/utils/functions.pyx:821
def to_udf(str value, *args, **kwargs)   # globals()[value.lower()](*args, **kwargs)

# querysource/providers/abstract.py
class BaseProvider(ABC):                                        # :23
    def _get_cond_definition(self) -> dict:                     # :133 (occurrences: 1)
        ...
        return cond_definition or {}                            # :146 (occurrences: 1)

# Call sites — each line is exactly:
#   sql = _rs.safe_format_map_validated(sql, self._conditions, self._get_cond_definition())
# querysource/providers/sql.py:177 (inside raw_query) and :202 (inside get_raw_query) — occurrences: 2
# querysource/providers/mysql.py:112      — occurrences: 1
# querysource/providers/sqlserver.py:91   — occurrences: 1
# querysource/providers/cassandra.py:78   — occurrences: 1
# querysource/providers/default.py:77     — occurrences: 1
```

**Measured parity after pre-resolution** (2026-09-15, `tests` must encode this):
- For every keyword × hint in {None, date, datetime, timestamp}, `is_valid(k, kw, T)` == `_rs.safe_format_map_validated("{x}", {"x": str(to_udf(kw))}, {"x": T} or {})`.
- **Exception:** untyped `CURRENT_YEAR`/`CURRENT_MONTH`: Cython `"'2026'"` vs Rust `'2026'`. The test compares after stripping one level of single quotes (spec AC1).

### Does NOT Exist
- ~~Python access to `UDF_LIST` / `PG_CONSTANTS` / `PG_UDF`~~: they are `cdef` globals. `hasattr(validators, 'UDF_LIST')` is `False`.
- ~~`querysource/types/validators.pxd`~~: does not exist; new functions are reached via Python `import`.
- ~~UDF resolution inside Rust~~: `rust/src/validators.rs::is_valid` never calls a UDF. Do **not** edit Rust.
- ~~`BaseProvider._udf_resolved_conditions`~~: added by this task.
- ~~A working `UDF_LIST` env override~~: currently a `str` is assigned to a `cdef list`.

---

## Implementation Notes

### Key Constraints
- **Resolution predicate.** Resolve only when all three hold:
  - `isinstance(value, str)`;
  - `value.strip().upper()` is in the effective `UDF_LIST`;
  - the hint `cond_definition.get(key)` is `None`, or its `.lower()` is one of `date`, `datetime`, `timestamp`.

  Every other hint (`string`, `literal`, `integer`, …) is untouched, because in Cython `is_valid` those typed validators run first and never resolve.
- **Resolved value.** Use `str(to_udf(value.strip()))`. `to_udf` lowercases internally, so `fdom` works too.
- **No mutation.** Return a **new** dict and never mutate `conditions`: providers keep `self._conditions` for checksums and cache keys.
- **Errors propagate.** If `to_udf` raises, re-raise. A keyword that is in `UDF_LIST` but has no function is a configuration error, and swallowing it would silently send the literal to the database.
- **Env override.** `_env_list(name, default)`: unset or empty (after strip) → `list(default)`. Otherwise `[s.strip().upper() for s in raw.split(',') if s.strip()]`.
  - `PG_UDF` values are function names like `now()`, so do **not** uppercase them.
  - Use a `bint upper` flag on `_env_list`.
- **FEAT-103 is untouched.** Only the dict passed as the second argument changes.

### References in Codebase
- `querysource/providers/sql.py:165-194`: the two-phase substitution this task feeds.
- `tests/unit/test_provider_raw_query_validated.py`: existing FEAT-103 tests; must stay green.

---

## Implementation Blueprint

### Steps (in order)
1. Edit `validators.pyx` lines 27-33 to use `_env_list` — *why*: the override must be parseable before `is_udf` reads `UDF_LIST`.
2. Add the three accessors and `resolve_udf_conditions` right after `is_pgconstant` — *why*: they depend on `is_udf`, and on `to_udf` from the module star-import.
3. Run `make build-inplace` — *why*: Python only sees `.pyx` changes after rebuild.
4. Add `_udf_resolved_conditions` to `BaseProvider` under `_get_cond_definition` — *why*: one shared helper keeps every provider identical.
5. Swap the 6 call-site arguments — *why*: the Rust substitution must receive pre-resolved values.
6. Write the tests and run `pytest tests/unit/test_udf_keyword_resolution.py tests/unit/test_provider_raw_query_validated.py tests/unit/test_qs_parsers_validated.py -q` — *why*: AC1 and AC2.

### `querysource/types/validators.pyx` (MODIFY — env parsing)
```python
# occurrences: 1 (verified: grep -c "cdef list UDF_LIST = os.environ.get('UDF_LIST', udf)" querysource/types/validators.pyx)
# REPLACE lines :28-33 (from `cdef list UDF_LIST = ...` through `cdef list PG_UDF = ...`) with:
cdef list _env_list(str name, list default, bint upper = True):
    """Parse a comma-separated env override; `default` copy when unset/blank."""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return list(default)
    return [
        (s.strip().upper() if upper else s.strip())
        for s in str(raw).split(',') if s.strip()
    ]

cdef list UDF_LIST = _env_list('UDF_LIST', udf)
cdef list PG_CONSTANTS = _env_list('PG_CONSTANTS', ["CURRENT_DATE", "CURRENT_TIMESTAMP"])
cdef list PG_UDF = _env_list('PG_UDF', ["now()"], False)
```
**Why**: `os.environ.get` returns `str`, and assigning that to a `cdef list` raises at import. The override has never worked, and the vocabulary endpoint (TASK-739) must list the *effective* values.

### `querysource/types/validators.pyx` (MODIFY — accessors + resolver)
```python
# occurrences: 1 (verified: grep -c "cpdef bool_t is_pgconstant(object value):" querysource/types/validators.pyx)
# AFTER — insert below the `is_pgconstant` function body (`    return value in PG_CONSTANTS`)
cpdef list udf_keywords():
    """Return a copy of the effective relative-date keyword list (UDF_LIST)."""
    return list(UDF_LIST)

cpdef list pg_constants():
    """Return a copy of the effective PostgreSQL constant list."""
    return list(PG_CONSTANTS)

cpdef list pg_udfs():
    """Return a copy of the effective PostgreSQL function allowlist (PG_UDF)."""
    return list(PG_UDF)

cdef tuple _KEYWORD_HINTS = ('date', 'datetime', 'timestamp')

cpdef dict resolve_udf_conditions(dict conditions, dict cond_definition = None):
    """Return a new dict with relative-date keyword values resolved via to_udf.

    Resolves only str values whose stripped uppercase form is in UDF_LIST and whose
    cond_definition hint is absent or date/datetime/timestamp (case-insensitive),
    mirroring is_valid(). Never mutates ``conditions``; to_udf errors propagate.
    """
    cdef dict result = dict(conditions) if conditions else {}
    cdef dict hints = cond_definition or {}
    # FILL IN: loop over result.items(); apply the predicate from Implementation Notes;
    #          assign result[key] = str(to_udf(value.strip())) — bounded by AC1 (no other hint resolves)
    return result
```
**Why**: this is a single Cython implementation, so the parser path and the provider path share the same `UDF_LIST`, including overrides. Returning a copy protects provider checksums.

### `querysource/providers/abstract.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c "        return cond_definition or {}" querysource/providers/abstract.py)
# AFTER — insert below `        return cond_definition or {}` (verified: querysource/providers/abstract.py:146)

    def _udf_resolved_conditions(self) -> dict:
        """Conditions with relative-date keywords resolved for the Rust substitution.

        Returns:
            A new dict from ``resolve_udf_conditions(self._conditions, cond_definition)``;
            ``self._conditions`` is never mutated.
        """
        from ..types.validators import resolve_udf_conditions
        return resolve_udf_conditions(
            dict(self._conditions or {}), self._get_cond_definition()
        )
```
**Why**: a local import avoids widening `providers/abstract.py` module-level imports into the Cython types module. `dict(...)` guards against `QueryObject`/`ClassDict` subclasses that Cython's `dict` typing would reject.

### Provider call sites (MODIFY ×6)
```python
# querysource/providers/sql.py — occurrences: 2 (verified: grep -c) → FILL IN: disambiguate
#   site 1 context (inside `def raw_query`, :174-177):
#       # Phase 2: untrusted user-supplied conditions (if any)
#       if self._conditions:
#           try:
#               sql = _rs.safe_format_map_validated(sql, self._conditions, self._get_cond_definition())
#   site 2 context (inside `def get_raw_query`, :199-202):
#       sql = _rs.safe_format_map(query, self.replacement)
#       if self._conditions:
#           try:
#               sql = _rs.safe_format_map_validated(sql, self._conditions, self._get_cond_definition())
# querysource/providers/mysql.py:112, sqlserver.py:91, cassandra.py:78, default.py:77 — occurrences: 1 each
# REPLACE the call line at each site with:
                    sql = _rs.safe_format_map_validated(
                        sql, self._udf_resolved_conditions(), self._get_cond_definition()
                    )
```
**Why**: this is the minimal diff. Keep the surrounding `if self._conditions:` guard and the `ValueError → ParserError` handling byte-identical, because FEAT-103 relies on them.

### `tests/unit/test_udf_keyword_resolution.py` (CREATE)
```python
"""FEAT-148 TASK-734 — relative-date keyword resolution parity (Rust raw path vs Cython)."""
import os
import subprocess
import sys

import pytest

from querysource.qs_parsers import _qs_parsers as rs
from querysource.types.validators import (
    is_valid, resolve_udf_conditions, udf_keywords, pg_constants, pg_udfs,
)
from querysource.utils.functions import to_udf

HINTS = [None, "date", "DATE", "datetime", "timestamp"]
INTEGER_KEYWORDS = {"CURRENT_YEAR", "CURRENT_MONTH"}


def _rust_render(value, hint):
    cd = {"x": hint} if hint else {}
    resolved = resolve_udf_conditions({"x": value}, cd)
    return rs.safe_format_map_validated("{x}", resolved, cd)


@pytest.mark.parametrize("hint", HINTS)
@pytest.mark.parametrize("keyword", udf_keywords() + ["fdom"])
def test_rust_cython_parity_all_keywords(keyword, hint):
    # FILL IN: cy = is_valid("x", keyword, hint); r = _rust_render(keyword, hint);
    #          untyped INTEGER_KEYWORDS compare with one level of single quotes stripped — bounded by AC1
    ...


def test_resolve_udf_leaves_string_literal_integer_hints():
    # FILL IN: hints "string", "literal", "integer" keep "TODAY" verbatim
    ...


def test_resolve_udf_does_not_mutate_input():
    # FILL IN: original dict equal and unchanged after call; non-str values pass through
    ...


def test_accessors_return_copies():
    # FILL IN: mutating udf_keywords() result does not change a second call; pg_constants/pg_udfs defaults
    ...


def test_udf_env_override_comma_separated():
    # FILL IN: subprocess `python -c` with env UDF_LIST="today, fdom" prints ['TODAY', 'FDOM'];
    #          PG_UDF="now()" keeps lowercase — bounded by AC3
    ...
```
**Why**: the parity test is the acceptance evidence for AC1. The environment override must be tested in a subprocess, because the `cdef` globals are fixed at import time.

### FILL IN checklist
- [ ] `validators.pyx::resolve_udf_conditions`: loop plus predicate; bounded by AC1 and the Implementation Notes predicate.
- [ ] `sql.py`: disambiguate the 2 identical call lines by context (raw_query / get_raw_query).
- [ ] All 5 test bodies; bounded by AC1–AC3.

---

## Acceptance Criteria

- [ ] `make build-inplace` succeeds.
- [ ] Spec AC1: parity test passes for every effective keyword × hint (with the documented integer exception).
- [ ] Spec AC2: `pytest tests/unit/test_provider_raw_query_validated.py tests/unit/test_qs_parsers_validated.py -q` passes unchanged.
- [ ] Spec AC3: `from querysource.types.validators import udf_keywords, pg_constants, pg_udfs, resolve_udf_conditions` works, and the comma-separated override is honoured.
- [ ] `grep -rn "safe_format_map_validated(sql, self._conditions" querysource/providers` returns nothing.
- [ ] `ruff check querysource/providers/abstract.py querysource/providers/sql.py querysource/providers/mysql.py querysource/providers/sqlserver.py querysource/providers/cassandra.py querysource/providers/default.py tests/unit/test_udf_keyword_resolution.py` is clean.

---

## Test Specification

See the `tests/unit/test_udf_keyword_resolution.py` blueprint above. Minimum cases:
- `test_rust_cython_parity_all_keywords`
- `test_resolve_udf_leaves_string_literal_integer_hints`
- `test_resolve_udf_does_not_mutate_input`
- `test_accessors_return_copies`
- `test_udf_env_override_comma_separated`

---

## Agent Instructions

When you pick up this task:

1. **Read the spec**, especially §3 Module 1, §5 AC1–AC3 and §7 Known Risks.
2. **Check dependencies**: none.
3. **Verify the Codebase Contract**: re-run the `grep -c` anchor counts before editing.
4. **Update status** in `sdd/tasks/index/describe-queryslug.json` → `"in-progress"`.
5. **Implement** from the blueprint, completing every `# FILL IN:`, then run `make build-inplace`.
6. **Verify** all acceptance criteria.
7. **Move this file** to `sdd/tasks/completed/`.
8. **Update the index** → `"done"`.
9. **Fill in the Completion Note**.

---

## Completion Note

**Completed by**: sdd-worker (Claude Sonnet 5)
**Date**: 2026-09-15
**Notes**:
- Implemented exactly per blueprint: `_env_list`, `udf_keywords`, `pg_constants`, `pg_udfs`,
  `resolve_udf_conditions` in `validators.pyx`; `BaseProvider._udf_resolved_conditions()` in
  `abstract.py`; swapped the second argument of every `_rs.safe_format_map_validated(...)`
  call site to `self._udf_resolved_conditions()`.
- **Contract correction**: the Codebase Contract undercounted the call sites as "6 sites in
  5 files". `querysource/providers/documentdb.py:102` has the identical
  `safe_format_map_validated(sql, self._conditions, self._get_cond_definition())` pattern on
  `dev` and is caught by the task's own repo-wide AC
  (`grep -rn "safe_format_map_validated(sql, self._conditions" querysource/providers`
  must return nothing). Fixed it too, following the identical minimal-diff pattern used at
  the other 6 sites. Updated Scope/Files table above accordingly.
- `make build-inplace` succeeded; extension rebuilt in-place in the worktree.
- Wrote `tests/unit/test_udf_keyword_resolution.py` completing all 5 `# FILL IN` test bodies
  from the blueprint (parity, hint predicate, no-mutation, accessor copies, env override).
- All ACs verified:
  - `make build-inplace` — succeeded.
  - AC1 parity — `test_rust_cython_parity_all_keywords` covers every effective keyword ×
    hint combination (including the untyped `CURRENT_YEAR`/`CURRENT_MONTH` quote-stripping
    exception); all pass.
  - AC2 — `pytest tests/unit/test_udf_keyword_resolution.py
    tests/unit/test_provider_raw_query_validated.py tests/unit/test_qs_parsers_validated.py -q`
    → 69 passed.
  - AC3 — accessors + env override (comma-separated `UDF_LIST`/`PG_UDF`, verified in a
    subprocess since the `cdef` globals are fixed at import time) all pass.
  - `grep -rn "safe_format_map_validated(sql, self._conditions" querysource/providers` →
    empty (clean).
  - `ruff check` on all 7 modified provider files + the new test file: introduces **zero**
    new lint violations (verified by diffing violation counts with/without this task's
    changes via a scoped `git stash`); the new test file itself is fully clean after
    `ruff check --fix`. Pre-existing lint debt in the provider files (49 violations on
    `dev`, e.g. `UP007`/`UP008`/`RUF012`/`RUF013`/`BLE001`/`S110`/`TRY401`) is untouched —
    fixing it is out of this task's scope.
- No other files touched; no scope creep.

**Deviations from spec**: none | describe if any
