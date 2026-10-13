# TASK-874: Close `is_valid()` — no config lookup, no `to_udf` fallback, no raw `(`

**Feature**: FEAT-167 — WHERE value hardening — `is_valid()` closure and idempotent literal quoting
**Spec**: `sdd/specs/where-value-hardening.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-872
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 1 (Cython half) / goals G1, G2, G5; §8 Q2 (remove config
substitution unconditionally) and Q3 (`(` values are quoted literals). The
generic branch of `is_valid()` (`querysource/types/validators.pyx:625-682`)
currently: returns any value containing `(` verbatim (`is_pg_function`,
line 209/658), substitutes a `navconfig` value when the filter value names a
config key (`get_config_var`, lines 427/664 — a secret oracle), and executes
`to_udf(value)` for any remaining string (line 673). Errors are `print`ed
(line 680). This task turns that branch into the closed decision list of
spec §2 A. Depends on TASK-872 because both edit `validators.pyx` and because
`to_udf` must already be allowlist-only.

---

## Scope

- `is_pg_function()`: exact membership in `PG_UDF`.
- Delete `get_config_var()` and `from navconfig import config`.
- Rewrite the generic branch of `is_valid()`: keyword → `quoteString(to_udf(...))`
  (errors logged at warning, value falls through to a quoted literal); PG
  constant → upper; exact `PG_UDF` → raw; list/dict → as is; else quoted
  literal (or raw when `noquote=True`). No config lookup, no `to_udf`
  fallback, no `print`.
- Add `import logging` + module logger.
- Add `tests/unit/test_is_valid_closure.py`.

**NOT in scope**: `Entity.quoteString`/`to_string` (TASK-876); the Rust
mirror (TASK-875); dialect builders; typed-hint dispatch (`type_validators`,
line 463) stays as is.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/types/validators.pyx` | MODIFY | logger (`:4-24`), `is_pg_function` (`:209`), delete `get_config_var` (`:427-428`), `is_valid` generic branch (`:625-682`) |
| `tests/unit/test_is_valid_closure.py` | CREATE | unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
import logging                                                      # stdlib; NOT imported by validators.pyx today
from querysource.types.validators import is_valid, udf_keywords, pg_udfs, is_udf  # verified: validators.pyx:625, :219, :227, :206
from querysource.utils.functions import to_udf                      # verified: functions.pyx:821 (allowlist-only after TASK-872); also reachable inside validators.pyx via `from ..utils.functions import *` (line 22)
```

### Existing Signatures to Use
```python
# querysource/types/validators.pyx (line numbers at base 82214b25; TASK-872 shifts lines 27/40 by a few — re-grep)
from navconfig import config                      # line 18 — DELETE (only user: get_config_var)
from ..utils.functions import *                   # line 22 — brings to_udf, is_* helpers into scope
cdef list PG_UDF = _env_list('PG_UDF', ["now()"], False)   # line 42
cdef str quoteString(object value)                # line 155 — module-level pre-quoter used by is_valid (''-escapes)
cpdef bool_t is_udf(object value)                 # line 206
cdef bool_t is_pg_function(object value):         # line 209
    if '(' in value: return True                  # line 210-211 — REMOVE
    else: return value in PG_UDF                  # line 212-213
cpdef bool_t is_pgconstant(object value)          # line 215
cdef object get_config_var(object value):         # line 427 — DELETE
    return config.get(value)                      # line 428
cpdef object escape_string(object value)          # line 433 — keep (used by the 'literal' hint, line 641)
cdef dict type_validators = {...}                 # line 463 — unchanged
cpdef object is_valid(object key, object value, str T = None, bint noquote = False):  # line 625
    # 638-646 typed dispatch (keep verbatim); 647-682 generic branch (REPLACED):
    #   647 null-like → 'null'; 650 is_boolean; 652 is_integer; 654 is_udf(upper) → quoteString(to_udf(value));
    #   656 is_pgconstant → upper(); 658 is_pg_function → value; 660 list/dict → value;
    #   662-669 get_config_var; 671-680 to_udf fallback + print; 681-682 noquote/quoteString
cpdef bool_t is_boolean(object value)             # used at line 650
cpdef bool_t is_integer(object value)             # used at line 652

# querysource/parsers/abstract.pyx — callers (unchanged): is_valid(key, value, _type) at 495, 503, 527;
#   is_valid(key, v, noquote=self.string_literal) at 611, 634, 643
```

### Does NOT Exist
- ~~`validators.pyx` logger / `self.logger`~~ — module functions; add `logger = logging.getLogger(__name__)` at module level.
- ~~`navconfig.logging` in validators.pyx~~ — use stdlib `logging` (no new dependency).
- ~~`is_pg_function` as a pyfunction / `cpdef`~~ — it is `cdef` (module-private); tests observe it through `is_valid`.
- ~~`QS_FILTER_CONFIG_LOOKUP` or any flag~~ — removal is unconditional (§8 Q2).
- ~~`ParserError` on `(`~~ — values containing `(` are quoted, not rejected (§8 Q3).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/types/validators.pyx", "action": "MODIFY"},
    {"path": "tests/unit/test_is_valid_closure.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/types/validators.pyx#is_valid",
    "sym:querysource/types/validators.pyx#is_pg_function",
    "sym:querysource/types/validators.pyx#get_config_var",
    "sym:querysource/types/validators.pyx#quoteString",
    "sym:querysource/types/validators.pyx#is_udf"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Signature of `is_valid` unchanged (AC10). The typed-hint block (lines
  638-646) is copied verbatim.
- `is_valid('k', 'now()')` must still return `now()` raw (default `PG_UDF`);
  `tests/test_rust_parsers.py:177` pins the same on the Rust side.
- `tests/qsurl/test_pg_ilike.py:128` pins `is_valid("name", "%o'brien%") == "'%o''brien%'"` — the module-level `quoteString` is untouched, so this holds.
- Rebuild (`python setup.py build_ext --inplace`) before pytest.

### References in Codebase
- `querysource/types/validators.pyx:233-256` — `resolve_udf_conditions`: the keyword-only shape to mirror.

---

## Implementation Blueprint

### Steps (in order)
1. Add `import logging` after `import os` (line 4) and `logger = logging.getLogger(__name__)` after the import block (after line 24) — *why*: G5 replaces `print`.
2. Delete line 18 (`from navconfig import config`) and lines 427-428 (`get_config_var`) — *why*: G1; `grep -n "config" validators.pyx` must then return nothing.
3. Replace `is_pg_function` body (lines 209-213) — *why*: G2, a `(` in user text is not a function.
4. Replace the generic branch of `is_valid` (lines 647-682) with the block below — *why*: the decision list in spec §2 A.
5. Create the test file; rebuild; run `ruff` on the test and the Validation Commands.

### `querysource/types/validators.pyx` (MODIFY — `is_pg_function`)
```python
# occurrences: 1 (verified: grep -c 'cdef bool_t is_pg_function(object value):' querysource/types/validators.pyx)
# REPLACE lines 209-213 with:
cdef bool_t is_pg_function(object value):
    """True only for an exact member of PG_UDF (default ['now()']) — never for a value that merely contains '('."""
    return isinstance(value, str) and value in PG_UDF
```
**Why**: FEAT-167 G2; the Rust mirror (TASK-875) applies the same rule so `safe_dict.rs` agrees.

### `querysource/types/validators.pyx` (MODIFY — `is_valid` generic branch)
```python
# occurrences: 1 (verified: grep -c 'cpdef object is_valid(object key, object value, str T = None, bint noquote = False):' querysource/types/validators.pyx)
# Keep lines 625-646 (signature, docstring, `if T:` typed dispatch) verbatim.
# REPLACE everything from `    if value == 'null' or value == 'NULL' or value == None or value == 'None':` (line 647)
# to the final `        return quoteString(value)` (line 682) with:
    if value == 'null' or value == 'NULL' or value is None or value == 'None':
        return 'null'
    elif is_boolean(value):
        return value
    elif is_integer(value):
        return value
    elif is_udf(str(value).upper()):
        # Relative-date keyword (UDF_LIST only — to_udf is allowlist-only since TASK-872).
        try:
            return quoteString(to_udf(value))
        except Exception as ex:  # noqa: BLE001 — a keyword whose impl fails degrades to a literal
            logger.warning("is_valid: keyword %r for %r could not be resolved: %s", value, key, ex)
    elif is_pgconstant(str(value).upper()):
        return value.upper()
    elif is_pg_function(value):
        # Exact PG_UDF member (default: now()) — the only raw pass-through.
        return value
    elif isinstance(value, (list, dict)):
        return value
    # Everything else is a literal: never a configuration value, never a function name,
    # never raw because it contains '(' (FEAT-167 G1/G2, §8 Q2/Q3).
    if noquote:
        return value
    return quoteString(value)
```
**Why this shape**: the three deleted branches (`get_config_var`, `to_udf` fallback, `print`) are exactly the request-controlled gaps; everything kept behaves as before. The keyword `try` only wraps `to_udf`, so a failing keyword logs and falls through to the quoted literal instead of crashing the request.

### `tests/unit/test_is_valid_closure.py` (CREATE)
```python
"""FEAT-167 TASK-874 — is_valid() generic branch is a closed decision list (AC1, AC3, AC5)."""
from __future__ import annotations

from pathlib import Path

import pytest

from querysource.types.validators import is_valid

_SRC = Path(__file__).resolve().parents[2] / "querysource" / "types" / "validators.pyx"


@pytest.mark.parametrize("name", ["DBHOST", "DBPWD", "DBUSER", "REDIS_HOST", "APP_NAME"])
def test_config_key_is_a_plain_literal(name: str) -> None:
    """A value equal to a navconfig key is quoted as text, never substituted (AC1)."""
    assert is_valid("u", name) == f"'{name}'"
    assert is_valid("d", name, "date") == f"'{name}'"


@pytest.mark.parametrize("name", ["midnight", "now", "days_ago", "uri_exists", "print", "fdate"])
def test_function_name_is_a_plain_literal(name: str) -> None:
    """Non-keyword function names are literals (AC2)."""
    assert is_valid("c", name) == f"'{name}'"


def test_keywords_still_resolve() -> None:
    # FILL IN: is_valid('d', 'today') / 'TODAY' / 'fdom' / 'ldopm' return a quoted date, not the keyword text — bounded by AC2


@pytest.mark.parametrize("value", ["pg_sleep(5)", "(select 1)", "lower('x')", "Acme (US)", "x) OR (1=1"])
def test_parenthesis_value_is_quoted(value: str) -> None:
    """A '(' no longer bypasses quoting (AC3, §8 Q3)."""
    # FILL IN: assert is_valid('k', value) == "'" + value.replace("'", "''") + "'" — bounded by AC3
    assert is_valid("k", value, noquote=True) == value


def test_pg_udf_member_stays_raw() -> None:
    assert is_valid("k", "now()") == "now()"


def test_source_has_no_config_or_print() -> None:
    """AC1/AC5 static guards."""
    src = _SRC.read_text(encoding="utf-8")
    assert "from navconfig import config" not in src
    assert "get_config_var" not in src
    assert "print(" not in src
```
**Why**: the config-key cases use keys that have values in every deployment (`DBHOST`, …) so the test is meaningful even where `navconfig` is loaded.

### FILL IN checklist
- [ ] `test_is_valid_closure.py::test_keywords_still_resolve` — assert quoted date shape per keyword; bounded by AC2
- [ ] `test_is_valid_closure.py::test_parenthesis_value_is_quoted` — quoted-literal assertion; bounded by AC3

---

## Acceptance Criteria

- [ ] `is_valid('u', 'DBHOST')` → `"'DBHOST'"`; `is_valid('d', 'DBPWD', 'date')` → `"'DBPWD'"` (AC1).
- [ ] `is_valid('c', 'midnight')` → `"'midnight'"`; `is_valid('d', 'today')` → a quoted date (AC2).
- [ ] `is_valid('k', 'pg_sleep(5)')` → `"'pg_sleep(5)'"`; `is_valid('k', 'now()')` → `now()` (AC3).
- [ ] `grep -c "print(" querysource/types/validators.pyx` → `0`; no `navconfig` import (AC1, AC5).
- [ ] Existing suites green: `tests/qsurl/test_pg_ilike.py`, `tests/unit/test_udf_keyword_resolution.py`, `tests/test_validators_array_type.py`, `tests/test_dialect_filter_preprocessing.py`.

---

## Validation Commands

- `pytest tests/unit/test_is_valid_closure.py -q`
- `pytest tests/qsurl/test_pg_ilike.py -q`
- `pytest tests/unit/test_udf_keyword_resolution.py -q`
- `pytest tests/test_validators_array_type.py -q`
- `pytest tests/test_dialect_filter_preprocessing.py -q`

---

## Test Specification

See the `tests/unit/test_is_valid_closure.py` blueprint block above.

---

## Agent Instructions

When you pick up this task:

1. **Work in the feature worktree** — never on `base_branch`
   (`python -m scripts.sdd.ensure_worktree --slug where-value-hardening --feature-id FEAT-167`)
2. **Read the spec** at the path listed above for full context
3. **Check dependencies** — TASK-872 must be `"done"` in `sdd/tasks/index/where-value-hardening.json`
4. **Verify the Codebase Contract** — line numbers shift after TASK-872; re-grep every anchor
5. **Update status** in the per-spec index → `"in-progress"` and commit only that index file
6. **Implement** from the Implementation Blueprint; rebuild Cython before testing
7. **Verify** — run the Validation Commands
8. **Commit the code** — stage only the files this task lists
9. **Close the task** with `scripts/sdd/close_task.sh TASK-874 where-value-hardening verified`
10. **Fill in the Completion Note** below, then commit the staged SDD state

---

## Completion Note

Implemented by seat gpt-5.6-terra (codex), 1 attempt, 331s, merged.
- `types/validators.pyx`: module logger; `is_pg_function` exact-match only; `get_config_var` and the `to_udf` fallback removed from `is_valid`; generic values are always literals.
- Added `tests/unit/test_is_valid_closure.py`.
- Validation: merge tier 19 passed (extensions rebuilt in-place first).
- Review: orchestrator diff review, no fixes; coder-review:fd6146a6f6689d8b2958a378.
- Closed via close_task.sh (no durable review EvidenceRef available for finalize_task).
