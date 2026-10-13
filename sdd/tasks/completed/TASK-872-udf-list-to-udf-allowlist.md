# TASK-872: Single `UDF_LIST` source and allowlist-only `to_udf()`

**Feature**: FEAT-167 — WHERE value hardening — `is_valid()` closure and idempotent literal quoting
**Spec**: `sdd/specs/where-value-hardening.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 2 / goals G3, Q1 (resolved: `FDOW`, `LDOW`, `LDOPM` join the
default list). `to_udf()` (`querysource/utils/functions.pyx:821-844`) resolves
*any* name with `globals()[name]` and falls back to `builtins`; `is_valid()`
calls it for every untyped string value, so a filter value such as
`"midnight"` executes `functions.pyx::midnight()`. The membership test it
uses (`from .validators import is_udf`, `functions.pyx:26`) is the pure-Python
dispatcher in `querysource/utils/validators.py:79` — itself a `globals()`
executor that returns a *result*, so `is_udf(value) is True` is almost always
`False` and the else branch runs. This task gives the keyword list ONE
env-aware home (`querysource/utils/udf_list.py`), makes `to_udf` resolve only
that list, and deletes the dead dispatcher. TASK-874 then removes the
`to_udf` fallback from `is_valid()`.

---

## Scope

- Create `querysource/utils/udf_list.py` with `DEFAULT_UDF_LIST` (10 names),
  `udf_list()` and `is_udf_keyword()` — same env semantics as
  `validators.pyx:30 _env_list` (comma-split, strip, drop blanks, upper-case).
- `querysource/utils/functions.pyx`: replace the `from .validators import is_udf`
  import with `from .udf_list import udf_list`, cache `_UDF_LIST = udf_list()`
  at import, and rewrite `to_udf` as allowlist-only (raises `KeyError`).
- `querysource/types/validators.pyx:27-40`: `UDF_LIST` is built from
  `udf_list()` (the local 7-name `udf` literal goes away); `PG_CONSTANTS` /
  `PG_UDF` keep using `_env_list`.
- `querysource/utils/validators.py`: delete `UDF`, `is_an_udf`, `is_udf` and
  the imports they alone used.
- Add `tests/unit/test_udf_list.py`.

**NOT in scope**: `is_valid()` itself (TASK-874); the vocabulary registry
(TASK-873); the Rust static list (TASK-875); `Entity.quoteString` (TASK-876).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/utils/udf_list.py` | CREATE | env-aware keyword list, single source |
| `querysource/utils/functions.pyx` | MODIFY | import swap (`:26`), allowlist-only `to_udf` (`:821-844`) |
| `querysource/types/validators.pyx` | MODIFY | `UDF_LIST = udf_list()` (`:27`, `:40`) |
| `querysource/utils/validators.py` | MODIFY | delete `UDF`/`is_an_udf`/`is_udf` block (`:73-100`) |
| `tests/unit/test_udf_list.py` | CREATE | unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
import os                                                     # stdlib
from querysource.utils.udf_list import DEFAULT_UDF_LIST, udf_list, is_udf_keyword  # CREATED by this task
from querysource.utils.functions import to_udf, today, fdom, ldopm, current_year  # verified: functions.pyx:821, :133, :206 (compiled module)
from querysource.types.validators import udf_keywords, is_udf, resolve_udf_conditions  # verified: validators.pyx:219, :206, :233
```

### Existing Signatures to Use
```python
# querysource/types/validators.pyx
cdef list udf = ["CURRENT_YEAR", "CURRENT_MONTH", "TODAY", "YESTERDAY", "LAST_YEAR", "FDOM", "LDOM"]  # line 27 — REPLACED
cdef list _env_list(str name, list default, bint upper = True)   # line 30 — keep (PG_CONSTANTS/PG_UDF still use it)
cdef list UDF_LIST = _env_list('UDF_LIST', udf)                  # line 40 — becomes udf_list()
cdef list PG_CONSTANTS = _env_list('PG_CONSTANTS', [...])        # line 41 — unchanged
cdef list PG_UDF = _env_list('PG_UDF', ["now()"], False)         # line 42 — unchanged
from ..utils.functions import *                                  # line 22 — star import: names starting with `_` are NOT imported (hence `_UDF_LIST` below)
cpdef bool_t is_udf(object value)                                # line 206 — `value in UDF_LIST`, callers pass .upper()
cpdef list udf_keywords()                                        # line 219 — returns list(UDF_LIST)
cpdef dict resolve_udf_conditions(dict conditions, dict cond_definition = None)  # line 233 — guards with UDF_LIST, then str(to_udf(value.strip()))

# querysource/utils/functions.pyx
from .validators import is_udf          # line 26 — the ONLY use besides line 826; REPLACED
cpdef str fdom(object value = None, str mask = "%Y-%m-%d", str zone = None)   # line 133
cpdef str fdow(object value=None, str mask="%Y-%m-%d", str zone=None)         # line 147
cpdef str ldow(object value=None, str mask="%Y-%m-%d", str zone=None)         # line 150
cpdef str ldom(object value = None, str mask = "%Y-%m-%d", str zone = None)   # line 183
cpdef str ldopm(object value = None, str mask = "%Y-%m-%d", str zone = None)  # line 206
def to_udf(str value, *args, **kwargs)  # line 821; body 822-844: globals()[f] at 827/829, getattr(builtins, f) at 832
# current_year(), current_month(), today(), yesterday(), last_year() exist as zero-arg callables (enumerated at spec time)

# querysource/utils/validators.py
from typing import Callable             # line 2 — used only by is_udf's annotation
import builtins                         # line 5 — used only by is_udf (line 89)
UDF = [...]                             # line 74
def is_an_udf(value)                    # line 75
def is_udf(value: str, *args, **kwargs) -> Callable  # line 79 — DELETE (through `return fn`, ~line 100)

# Callers of to_udf that must keep working (all already guard with the keyword list):
# validators.pyx:255 resolve_udf_conditions; validators.pyx:655 is_valid (UDF branch);
# querysource/parsers/filter_values.py:78 _normalise_bound; querysource/utils/vocabulary.py:161 keyword_example (try/except)
```

### Does NOT Exist
- ~~`querysource.utils.udf_list`~~ — created here; nothing imports it yet.
- ~~`functions.pyx::UDF_LIST`~~ — no such name today; the cache is `_UDF_LIST` (underscore: excluded from `validators.pyx`'s star import, so it cannot collide with the `cdef list UDF_LIST` there).
- ~~`to_udf(..., strict=...)`~~ — no new parameters; signature stays `(str value, *args, **kwargs)`.
- ~~`querysource.types.validators.is_udf_keyword`~~ — lives in `udf_list.py`, not the Cython module.
- ~~any other importer of `utils/validators.py::is_udf`~~ — `grep -rn "validators import is_udf" querysource/` → only `functions.pyx:26`.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/utils/udf_list.py", "action": "CREATE"},
    {"path": "querysource/utils/functions.pyx", "action": "MODIFY"},
    {"path": "querysource/types/validators.pyx", "action": "MODIFY"},
    {"path": "querysource/utils/validators.py", "action": "MODIFY"},
    {"path": "tests/unit/test_udf_list.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/utils/functions.pyx#to_udf",
    "sym:querysource/types/validators.pyx#udf_keywords",
    "sym:querysource/types/validators.pyx#is_udf",
    "sym:querysource/types/validators.pyx#resolve_udf_conditions",
    "sym:querysource/utils/validators.py#is_udf"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- `to_udf` raises `KeyError(value)` for a non-keyword — `is_valid()` (until
  TASK-874 lands) catches `KeyError` at `validators.pyx:675`, and
  `vocabulary.keyword_example` catches `Exception`; no caller needs changing.
- Keep `_env_list` in `validators.pyx` — `PG_CONSTANTS`/`PG_UDF` still use it.
- The list is read **at import** on both sides (`UDF_LIST = udf_list()` in
  `validators.pyx`, `_UDF_LIST = udf_list()` in `functions.pyx`) — matches
  `tests/unit/test_udf_keyword_resolution.py:90` ("parsed at import").
- `.pyx` changes are invisible until `python setup.py build_ext --inplace`
  (memory `sdd-fix-worktree-tests`): rebuild before running pytest.

### References in Codebase
- `querysource/types/validators.pyx:30-38` — `_env_list` semantics to mirror.
- `tests/unit/test_udf_keyword_resolution.py:90-110` — subprocess env-override test shape.

---

## Implementation Blueprint

### Steps (in order)
1. Create `querysource/utils/udf_list.py` from the block below — *why*: both compiled modules must read the same list without importing each other.
2. In `functions.pyx` replace line 26 and add the `_UDF_LIST` cache; replace `to_udf` (lines 821-844) — *why*: the allowlist check must happen before any `globals()` lookup, and `builtins` must be unreachable (G3).
3. In `validators.pyx` replace line 27 with the import and line 40 with `udf_list()` — *why*: one source of truth; `udf_keywords()` keeps returning a copy.
4. In `utils/validators.py` delete lines 73-100 (the `# TODO` comment through `return fn`) and drop the now-unused `Callable`/`builtins` imports (`ruff check` reports F401) — *why*: the dispatcher is dead after step 2 and is the same `globals()` pattern FEAT-166 flagged.
5. `python setup.py build_ext --inplace`, then `ruff check querysource/utils/udf_list.py querysource/utils/validators.py tests/unit/test_udf_list.py` and the Validation Commands.

### `querysource/utils/udf_list.py` (CREATE)
```python
"""Single source of the relative-date keyword allowlist (FEAT-167, spec §3 M2).

``querysource/types/validators.pyx`` and ``querysource/utils/functions.pyx``
both read the list from here, so ``is_udf()`` and ``to_udf()`` can never
disagree. The environment variable ``UDF_LIST`` (comma-separated) replaces the
default per deployment — same semantics as ``validators.pyx::_env_list``.
"""
from __future__ import annotations

import os

ENV_VAR: str = "UDF_LIST"

DEFAULT_UDF_LIST: tuple[str, ...] = (
    "CURRENT_YEAR",
    "CURRENT_MONTH",
    "TODAY",
    "YESTERDAY",
    "LAST_YEAR",
    "FDOM",
    "LDOM",
    "FDOW",
    "LDOW",
    "LDOPM",
)


def udf_list() -> list[str]:
    """Return the effective keyword list as a fresh, upper-cased list.

    ``UDF_LIST`` env (comma-separated; items stripped, blanks dropped,
    upper-cased) wins over ``DEFAULT_UDF_LIST`` when set and non-blank.
    """
    raw = os.environ.get(ENV_VAR)
    if raw is None or not str(raw).strip():
        return list(DEFAULT_UDF_LIST)
    return [item.strip().upper() for item in str(raw).split(",") if item.strip()]


def is_udf_keyword(value: object) -> bool:
    """Return True when ``value`` is a str whose stripped, upper-cased form is a keyword."""
    return isinstance(value, str) and value.strip().upper() in udf_list()
```
**Why this shape**: pure Python with no querysource imports, so it can be imported by both Cython modules without a cycle (`validators.pyx:22` already star-imports `functions.pyx`). `udf_list()` returns a copy each call; callers cache at import.

### `querysource/utils/functions.pyx` (MODIFY — import)
```python
# occurrences: 1 (verified: grep -c 'from .validators import is_udf' querysource/utils/functions.pyx)
# REPLACE line 26 `from .validators import is_udf` with:
from .udf_list import udf_list

# Keyword allowlist, read once at import (same moment validators.pyx reads it).
# Leading underscore: validators.pyx star-imports this module and declares its
# own `cdef list UDF_LIST` — a public name here would collide.
_UDF_LIST = udf_list()
```
**Why**: the broken pure-Python `is_udf` returned a function *result*, so the membership test never worked; membership is now a plain `in` on the shared list.

### `querysource/utils/functions.pyx` (MODIFY — `to_udf`)
```python
# occurrences: 1 (verified: grep -c 'def to_udf(str value, \*args, \*\*kwargs):' querysource/utils/functions.pyx)
# REPLACE lines 821-844 (from `def to_udf(` through `    return fn`) with:
def to_udf(str value, *args, **kwargs):
    """Execute the relative-date keyword ``value`` and return its result.

    Only names in the effective ``UDF_LIST`` (``querysource.utils.udf_list``)
    resolve; the lookup is case-insensitive and ignores surrounding spaces.
    Raises ``KeyError(value)`` for any other name: ``to_udf`` is NOT a
    general function dispatcher (FEAT-167, G3) and has no ``builtins``
    fallback. ``*args``/``**kwargs`` are passed to the keyword's function.
    """
    cdef str name = value.strip()
    if name.upper() not in _UDF_LIST:
        raise KeyError(value)
    func = globals()[name.lower()]
    return func(*args, **kwargs)
```
**Why**: the allowlist check precedes `globals()`; a keyword whose implementation is missing (an env override naming a non-function) surfaces as `KeyError` from `globals()`, which every caller already handles.

### `querysource/types/validators.pyx` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'cdef list udf = \["CURRENT_YEAR"' querysource/types/validators.pyx)
# REPLACE line 27 `cdef list udf = [...]` with:
from ..utils.udf_list import udf_list

# occurrences: 1 (verified: grep -c "cdef list UDF_LIST = _env_list('UDF_LIST', udf)" querysource/types/validators.pyx)
# REPLACE line 40 with:
cdef list UDF_LIST = udf_list()
```
**Why**: `udf_keywords()`, `is_udf()` and `resolve_udf_conditions()` keep reading `UDF_LIST`; only its origin changes. `_env_list` (line 30) stays for `PG_CONSTANTS`/`PG_UDF`.

### `querysource/utils/validators.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'def is_udf(value: str, \*args, \*\*kwargs) -> Callable:' querysource/utils/validators.py)
# DELETE the block from the line `# TODO: get the current list of supported UDF dynamically.` (line 73)
# through the final `        return fn` of is_udf (~line 100): UDF, is_an_udf, is_udf.
# Then remove `from typing import Callable` (line 2) and `import builtins` (line 5)
# if `ruff check querysource/utils/validators.py` reports them unused (F401).
```
**Why**: nothing imports these names after the `functions.pyx` swap (verified at task time); they are the `globals()`+`builtins` pattern FEAT-166 removed elsewhere.

### `tests/unit/test_udf_list.py` (CREATE)
```python
"""FEAT-167 TASK-872 — single UDF_LIST source and allowlist-only to_udf()."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from querysource.types.validators import udf_keywords
from querysource.utils.functions import to_udf
from querysource.utils.udf_list import DEFAULT_UDF_LIST, is_udf_keyword, udf_list

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPECTED = ["CURRENT_YEAR", "CURRENT_MONTH", "TODAY", "YESTERDAY", "LAST_YEAR",
            "FDOM", "LDOM", "FDOW", "LDOW", "LDOPM"]


def test_default_list_has_ten_keywords() -> None:
    """DEFAULT_UDF_LIST, udf_list() and the Cython udf_keywords() agree (AC2)."""
    assert list(DEFAULT_UDF_LIST) == EXPECTED
    assert udf_list() == EXPECTED
    assert udf_keywords() == EXPECTED


@pytest.mark.parametrize("name", ["TODAY", "today", " Today ", "fdom", "LDOW", "ldopm"])
def test_to_udf_resolves_keywords(name: str) -> None:
    """Keywords resolve case-insensitively and return a non-empty string/int."""
    result = to_udf(name)
    # FILL IN: assert the result type per keyword (str date for TODAY/FDOM/LDOW/LDOPM) — bounded by AC2


@pytest.mark.parametrize("name", ["midnight", "now", "uri_exists", "print", "days_ago", "", "to_udf"])
def test_to_udf_rejects_non_keywords(name: str) -> None:
    """Any name outside UDF_LIST raises KeyError — never executes (AC2)."""
    with pytest.raises(KeyError):
        to_udf(name)


def test_is_udf_keyword() -> None:
    assert is_udf_keyword(" ldopm ")
    assert not is_udf_keyword("midnight")
    assert not is_udf_keyword(None)


def test_env_override_applies_to_both_sides() -> None:
    """UDF_LIST env is honoured by udf_keywords() AND to_udf() in a fresh interpreter."""
    env = os.environ.copy()
    env["UDF_LIST"] = "today, fdom"
    code = (
        "from querysource.types.validators import udf_keywords; "
        "from querysource.utils.functions import to_udf; "
        "print(udf_keywords()); "
        "try:\n    to_udf('LDOM'); print('resolved')\nexcept KeyError:\n    print('KeyError')"
    )
    out = subprocess.run([sys.executable, "-c", code], env=env, cwd=_PROJECT_ROOT,
                         capture_output=True, text=True, check=True).stdout.strip().splitlines()
    # FILL IN: assert out[-2] == "['TODAY', 'FDOM']" and out[-1] == "KeyError" (navconfig may print DEBUG lines first) — bounded by AC2
```
**Why**: the subprocess shape mirrors `tests/unit/test_udf_keyword_resolution.py:90` because both modules read the env at import.

### FILL IN checklist
- [ ] `test_udf_list.py::test_to_udf_resolves_keywords` — per-keyword result type assertions; bounded by AC2
- [ ] `test_udf_list.py::test_env_override_applies_to_both_sides` — tolerate navconfig log lines on stdout; bounded by AC2
- [ ] `utils/validators.py` — remove `Callable`/`builtins` imports only if ruff F401 confirms they are unused

---

## Acceptance Criteria

- [ ] `udf_keywords()` returns the 10-name default; `to_udf('midnight')`, `to_udf('print')`, `to_udf('uri_exists')` raise `KeyError`; `to_udf('TODAY')`, `to_udf('today')`, `to_udf('fdom')`, `to_udf('ldopm')` return values (spec AC2 part).
- [ ] `grep -c "globals()\[" querysource/utils/validators.py` is `0`; `grep -c "builtins" querysource/utils/functions.pyx` counts only the unrelated `import builtins` (if still used elsewhere) and never inside `to_udf`.
- [ ] `ruff check querysource/utils/udf_list.py querysource/utils/validators.py tests/unit/test_udf_list.py` clean.
- [ ] Existing suites green: `tests/unit/test_udf_keyword_resolution.py`, `tests/unit/test_vocabulary.py`.

---

## Validation Commands

- `pytest tests/unit/test_udf_list.py -q`
- `pytest tests/unit/test_udf_keyword_resolution.py -q`
- `pytest tests/unit/test_vocabulary.py -q`

---

## Test Specification

See the `tests/unit/test_udf_list.py` blueprint block above.

---

## Agent Instructions

When you pick up this task:

1. **Work in the feature worktree** — never on `base_branch`
   (`python -m scripts.sdd.ensure_worktree --slug where-value-hardening --feature-id FEAT-167`)
2. **Read the spec** at the path listed above for full context
3. **Check dependencies** — none
4. **Verify the Codebase Contract** — before writing ANY code (`grep`/`read` every anchor)
5. **Update status** in `sdd/tasks/index/where-value-hardening.json` → `"in-progress"` and commit only that index file
6. **Implement** from the Implementation Blueprint; rebuild Cython (`python setup.py build_ext --inplace`) before testing
7. **Verify** — run the Validation Commands
8. **Commit the code** — stage only the files this task lists
9. **Close the task** with `scripts/sdd/close_task.sh TASK-872 where-value-hardening verified`
10. **Fill in the Completion Note** below, then commit the staged SDD state

---

## Completion Note

Implemented by seat gpt-5.6-terra (codex), 1 attempt, 473s, merged at f3199e63.
- Created `querysource/utils/udf_list.py` (10-name default, env-aware `udf_list()`, `is_udf_keyword()`).
- `functions.pyx::to_udf` is allowlist-only (KeyError otherwise); `types/validators.pyx` uses `udf_list()`; dead `is_udf` dispatcher removed from `utils/validators.py`.
- Validation: merge tier `tests/unit/test_udf_list.py` 16 passed (after in-place `build_ext` in the worktree; first run failed only on missing compiled extension).
- Review: orchestrator diff review, no fixes; recorded as coder-review:7879fe7b078adc4f3cd3bbeb.
- Closed via close_task.sh, not finalize_task: engine produces no durable EvidenceRef for review, so finalize_task could not be satisfied honestly.
