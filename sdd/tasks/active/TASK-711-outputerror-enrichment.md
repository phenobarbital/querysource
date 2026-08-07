# TASK-711: Enrich OutputError with step_name and category

**Feature**: FEAT-146 — MultiQuery Output Error Propagation
**Spec**: `sdd/specs/multiquery-output-error-propagation.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Root task of FEAT-146 (Module 1). Every other task imports the enriched
`OutputError`. Today `OutputError` is a bare `QueryException` subclass with no
structured context. We need it to optionally carry the failing destination
**step name** and an error **category** (`"data"` | `"infra"`) so downstream
the handler can pick an HTTP status (422 vs 500) and surface a useful message.

Implements spec §2 "Data Models" and §3 "Module 1".

---

## Scope

- Extend `OutputError.__init__` to accept optional keyword args `step_name`
  and `category`, storing them as attributes (defaulting to `None`).
- Keep it **100% backwards compatible**: existing call sites that do
  `OutputError("some message")` or `OutputError(f"...")` must keep working
  unchanged (there are several in `outputs/tables/TableOutput/*.py`).
- Add unit tests.

**NOT in scope**: changing how/where `OutputError` is raised (TASK-713),
HTTP status mapping (TASK-712/714), any handler code.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/exceptions.py` | MODIFY | Add `step_name` / `category` kwargs + attributes to `OutputError`. |
| `tests/unit/test_output_error.py` | CREATE | Unit tests for the enriched exception + backwards compat. |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.exceptions import OutputError, QueryException  # verified: querysource/exceptions.py:74, :6
```

### Existing Signatures to Use
```python
# querysource/exceptions.py:6
class QueryException(Exception):
    # base class; inspect its __init__ before extending OutputError so the
    # new __init__ forwards *args/**kwargs to super() correctly.

# querysource/exceptions.py:74
class OutputError(QueryException):
    ...  # currently no custom __init__
```

### Existing call sites that MUST keep working (backwards compat)
```python
# querysource/outputs/tables/TableOutput/postgres.py  (via _execute)
raise OutputError(f"SQL Operational Error: {err}") from err
# querysource/outputs/tables/TableOutput/mysql.py:62,92,94,96
# querysource/outputs/tables/TableOutput/sa.py:67,92
```

### Does NOT Exist
- ~~`OutputError.step_name`~~ / ~~`OutputError.category`~~ — added by THIS task.
- ~~a base `QueryException` that already stores `step_name`~~ — it does not; verify its `__init__` first.

---

## Implementation Notes

- Read `QueryException.__init__` first (exceptions.py:6) — the new
  `OutputError.__init__` must call `super().__init__(message, *args, **kwargs)`
  so the message still reaches the base class and `str(err)` is unchanged.
- Signature suggestion (keyword-only for the new fields):
  ```python
  class OutputError(QueryException):
      def __init__(self, message="", *args, step_name=None, category=None, **kwargs):
          super().__init__(message, *args, **kwargs)
          self.step_name = step_name
          self.category = category
  ```
- `category` values are the string literals `"data"` and `"infra"`; do not
  introduce an Enum unless the module already uses one.

### Key Constraints
- No new external dependencies.
- `str(OutputError("x"))` must still equal what it does today.

---

## Acceptance Criteria

- [ ] `OutputError("msg")` still works and `str()` is unchanged (backwards compat).
- [ ] `OutputError("msg", step_name="TableOutput", category="data")` stores both attributes.
- [ ] `.step_name` and `.category` default to `None` when not provided.
- [ ] `isinstance(OutputError(...), QueryException)` remains `True`.
- [ ] `pytest tests/unit/test_output_error.py -v` passes.
- [ ] `ruff check querysource/exceptions.py` clean.

---

## Test Specification

```python
# tests/unit/test_output_error.py
import pytest
from querysource.exceptions import OutputError, QueryException


def test_backwards_compatible_message():
    err = OutputError("boom")
    assert str(err) == "boom"
    assert isinstance(err, QueryException)
    assert err.step_name is None
    assert err.category is None


def test_carries_step_name_and_category():
    err = OutputError("boom", step_name="TableOutput", category="data")
    assert err.step_name == "TableOutput"
    assert err.category == "data"
```

---

## Agent Instructions

Follow the standard SDD agent flow: verify the Codebase Contract before coding,
implement per scope, make the tests pass, move this file to
`sdd/tasks/completed/` and update the per-spec index
`sdd/tasks/index/multiquery-output-error-propagation.json`.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:
**Deviations from spec**: none
