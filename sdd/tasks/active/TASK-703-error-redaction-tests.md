# TASK-703: End-to-end error-redaction tests (QS & MultiQuery)

**Feature**: FEAT-102 — Reduce Verbose Error Responses (QS & MultiQuery)
**Spec**: `sdd/specs/querysource-logs-verbose.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-700, TASK-701, TASK-702
**Assigned-to**: unassigned

---

## Context

Implements spec §3 "Module 4" and §4 "Test Specification". Verifies the
acceptance criteria across BOTH error paths end-to-end: that production
responses (`DEBUG=False`) leak no traceback, no internal paths, and no raw DB
text, while carrying an `error_id`; and that `DEBUG=True` preserves verbose
output for local development.

---

## Scope

- Add integration/regression tests covering the QS single-query path
  (`DataOutput` / `AbstractWriter` / service handler) and the MultiQuery path
  (`handlers/multi.py`).
- For each path, assert under `DEBUG=False`:
  - response body contains none of: `Traceback`, `File "`, `/site-packages/`,
    a `trace` key.
  - response body contains a non-empty `error_id`.
  - raw DB text (e.g. `column "apikey" does not exist`) does not appear in body
    or headers.
- Assert under `DEBUG=True`: `trace`/`detail` are present (dev parity).
- Assert the `error_id` in the HTTP body matches the `error_id` in the captured
  server log (correlation).
- Use the leaked-example fixture from the spec (`db_column_error`).

**NOT in scope**: implementing the formatter or refactors (TASK-700/701/702);
changing production code (tests only — unless a trivial test hook is required and
agreed).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/test_feat102_error_redaction.py` | CREATE | QS + MultiQuery redaction regression tests |

> Confirm the project's test directory/layout and how aiohttp handlers are
> exercised in existing tests before writing (spec §8 open question).

---

## Codebase Contract (Anti-Hallucination)

> Verified against source on 2026-06-19.

### Verified Imports
```python
from querysource.utils.errors import build_error_payload   # created by TASK-700
from querysource.exceptions import QueryException, DataNotFound, DriverError  # exceptions.py
from navconfig import DEBUG                                  # toggled in tests via monkeypatch
```

### Existing Signatures to Use
```python
# querysource/exceptions.py
class QueryException(Exception):
    def __init__(self, message: str, code: int = 0, **kwargs): ...  # line 11

# querysource/outputs/output.py
class DataOutput:
    def error(self, message, status=400, exception=None, headers=None,
              content_type='application/json') -> BaseException:  # line 117 (RAISES)
    async def response(self): ...                                 # line 174

# querysource/handlers/abstract.py
class AbstractHandler(BaseHandler):
    self.debug: bool = DEBUG                                      # line 43
    def Error(self, ...): ...                                     # line 109 (returns HTTPException)
    def Except(self, ...): ...                                    # line 165 (returns HTTPException)

# querysource/handlers/multi.py  — MultiQuery error blocks at lines 266-296
```

### Does NOT Exist
- ~~`tests/test_feat102_error_redaction.py`~~ — this task creates it.
- ~~a global `DEBUG` in querysource.conf~~ — DEBUG comes from `navconfig`; monkeypatch
  it at the import site used by the code under test (`querysource.handlers.abstract.DEBUG`
  / wherever output/writers import it).

---

## Implementation Notes

### Pattern to Follow
- Toggle DEBUG by monkeypatching the symbol where it is *used* (e.g.
  `monkeypatch.setattr("querysource.handlers.abstract.DEBUG", False)`), not just
  the `navconfig` source, because it is imported by value.
- Use `caplog` to capture server-side logs and assert `error_id` correlation.
- Inspect aiohttp `HTTP*` exception `.text`/`.body` for the serialized payload.

### Key Constraints
- Tests must pass with `DEBUG` both True and False (parametrize).
- Keep assertions about absence of leakage explicit (substring checks for
  `Traceback`, `/site-packages/`, the DB column text).

### References in Codebase
- Existing test layout under `tests/` — match its fixtures/conftest conventions.
- Spec §4 "Test Specification" for the full intended test matrix.

---

## Acceptance Criteria

- [ ] `pytest tests/test_feat102_error_redaction.py -v` passes.
- [ ] Tests cover BOTH QS single-query and MultiQuery paths.
- [ ] Tests assert no `Traceback`/`/site-packages/`/`trace`/raw-DB-text in
      production bodies, and presence of `error_id`.
- [ ] Tests assert verbose output under `DEBUG=True`.
- [ ] Test asserts body `error_id` == log `error_id`.
- [ ] No linting errors: `ruff check tests/test_feat102_error_redaction.py`

---

## Test Specification

```python
# tests/test_feat102_error_redaction.py
import pytest
from querysource.exceptions import QueryException


@pytest.fixture
def db_column_error():
    return QueryException('Sentence Error: column "apikey" does not exist', code=400)


@pytest.mark.parametrize("debug", [True, False])
def test_redaction_matrix(db_column_error, debug, monkeypatch, caplog):
    # toggle DEBUG at use sites, drive each error path, assert per `debug`.
    ...
```

---

## Agent Instructions

1. **Read the spec** (esp. §4, §5) for full context.
2. **Check dependencies** — verify TASK-700/701/702 are in `sdd/tasks/completed/`.
3. **Verify the Codebase Contract** before writing code.
4. **Update status** in `sdd/tasks/index/querysource-logs-verbose.json`.
5. **Implement** per scope.
6. **Verify** acceptance criteria.
7. **Move this file** to `sdd/tasks/completed/`.
8. **Update index** → `"done"`.
9. **Fill in the Completion Note**.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**:

**Deviations from spec**: none | describe if any
