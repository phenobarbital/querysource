# TASK-946: QS_DESCRIBE_EXECUTE_FALLBACK flag

**Feature**: FEAT-182 — PBAC Coverage for the Management API, Direct Driver Route and Describe Fallback
**Spec**: `sdd/specs/querysource-management-pbac.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §1 problem 3, §3 Module 3. Describe list, detail and columns accept `slug:execute` as a hard-coded
fallback (`handlers/describe.py:138,236`). A host that keeps `slug:execute` open to everyone
(FieldSync) therefore cannot restrict the catalogue. This task makes the fallback configurable;
the default keeps today's behaviour.

---

## Scope

- Add `QS_DESCRIBE_EXECUTE_FALLBACK = config.getboolean('QS_DESCRIBE_EXECUTE_FALLBACK', fallback=True)` to `conf.py`,
  next to the other `QS_DESCRIBE_*` settings.
- In `describe.py`, pass `"slug:execute" if QS_DESCRIBE_EXECUTE_FALLBACK else None` as the fallback at both call sites.
- Write tests.

**NOT in scope**: `slug:describe_raw` (already has no fallback); any other conf flag (TASK-947).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/conf.py` | MODIFY | new flag |
| `querysource/handlers/describe.py` | MODIFY | conditional fallback at 2 sites |
| `tests/handlers/test_describe_fallback_flag.py` | CREATE | flag tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from navconfig import config   # used throughout querysource/conf.py
```

### Existing Signatures to Use
```python
# querysource/conf.py
QS_DESCRIBE_MAX_SCAN = config.getint('QS_DESCRIBE_MAX_SCAN', fallback=10000)   # :377
QS_DESCRIBE_ADMIN_GROUPS = [ ... ]                                              # :379
QS_DESCRIBE_COLUMNS_TIMEOUT = float(config.get('QS_DESCRIBE_COLUMNS_TIMEOUT', fallback=5))  # :385

# querysource/handlers/describe.py
#   can_access(request, principal, slug, "slug:describe", "slug:execute", detached=...)  (:137-139)
#   filter_visible(request, principal, slugs, "slug:list", "slug:execute", detached=...) (:235-237)

# querysource/auth/slug_visibility.py
async def filter_visible(..., primary_action: str, fallback_action: str | None = None)  # :259
async def can_access(..., primary_action: str, fallback_action: str | None = None)      # :329
```

### Does NOT Exist
- ~~`QS_DESCRIBE_EXECUTE_FALLBACK`~~: new in this task.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/conf.py", "action": "MODIFY"},
    {"path": "querysource/handlers/describe.py", "action": "MODIFY"},
    {"path": "tests/handlers/test_describe_fallback_flag.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/auth/slug_visibility.py#filter_visible",
    "sym:querysource/auth/slug_visibility.py#can_access"
  ]
}
```

---

## Implementation Notes

- Import the flag into `describe.py` from `..conf`. Tests monkeypatch it on the `describe` module
  (`monkeypatch.setattr(describe_mod, "QS_DESCRIBE_EXECUTE_FALLBACK", False)`).
- The columns endpoint shares `_load_visible` with detail. Confirm by reading `describe.py:93-140`
  that both go through the `:138` call site.

---

## Implementation Blueprint

### Steps (in order)
1. Add the flag to `conf.py` after `QS_DESCRIBE_COLUMNS_TIMEOUT`. *Why*: groups the describe settings.
2. Import it into `describe.py` and replace the two literal fallbacks. *Why*: hosts can then remove the fallback.
3. Write the tests: flag False hides list and detail from an execute-only user; default True keeps today's behaviour.

### `querysource/conf.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c "QS_DESCRIBE_COLUMNS_TIMEOUT = " querysource/conf.py)
# AFTER — insert below the QS_DESCRIBE_COLUMNS_TIMEOUT line (verified: conf.py:385)
# FEAT-182: when False, describe list/detail/columns require slug:list / slug:describe
# and no longer accept slug:execute as a fallback.
QS_DESCRIBE_EXECUTE_FALLBACK = config.getboolean('QS_DESCRIBE_EXECUTE_FALLBACK', fallback=True)
```

### `querysource/handlers/describe.py` (MODIFY)
```python
# occurrences: 1 each (verified: grep -c '"slug:describe", "slug:execute",' and grep -c '"slug:list", "slug:execute",')
# :138  "slug:describe", "slug:execute",  →  "slug:describe", _execute_fallback(),
# :236  "slug:list", "slug:execute",      →  "slug:list", _execute_fallback(),
# FILL IN: add a module-level helper
#   def _execute_fallback() -> str | None:
#       return "slug:execute" if QS_DESCRIBE_EXECUTE_FALLBACK else None
# reading the module global at call time (so monkeypatch works); import the flag from ..conf.
```

### `tests/handlers/test_describe_fallback_flag.py` (CREATE)
```python
"""FEAT-182 TASK-946: QS_DESCRIBE_EXECUTE_FALLBACK."""
import pytest


@pytest.mark.asyncio
async def test_describe_fallback_disabled(monkeypatch):
    """Flag False: user holding only slug:execute sees no list rows and gets 404 on detail."""
    # FILL IN: reuse the fixtures/patterns of tests/handlers/test_describe_list.py and test_describe_detail.py


@pytest.mark.asyncio
async def test_describe_fallback_default(monkeypatch):
    """Default: execute-only user still sees list/detail (today's behaviour)."""
    # FILL IN
```

### FILL IN checklist
- [ ] `_execute_fallback` helper and the import.
- [ ] Test bodies; mutation-check the disabled test.

---

## Acceptance Criteria

- [ ] `QS_DESCRIBE_EXECUTE_FALLBACK=false` removes the fallback at both sites.
- [ ] Default behaviour is unchanged (existing describe tests pass).

---

## Validation Commands

- `pytest tests/handlers/test_describe_fallback_flag.py -q`
- `pytest tests/handlers/test_describe_list.py -q`
- `pytest tests/handlers/test_describe_detail.py -q`
- `pytest tests/handlers/test_describe_columns.py -q`

---

## Test Specification

See the blueprint above.

---

## Agent Instructions

Same as TASK-943, with this task's id and files.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none
