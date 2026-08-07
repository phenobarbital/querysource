# TASK-714: Handler — consolidate Output path + map OutputError to 422/500

**Feature**: FEAT-146 — MultiQuery Output Error Propagation
**Spec**: `sdd/specs/multiquery-output-error-propagation.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-711, TASK-712, TASK-713
**Assigned-to**: unassigned

---

## Context

Implements spec §3 "Module 3". With MultiQS now raising (TASK-713) and the
error layer capable of 422 + detail exposure (TASK-712), the handler must:
(1) stop running its own **duplicate** Output loop that swallows into
`output_errors` and returns 200, and (2) map the propagated `OutputError` to a
differentiated HTTP status (data → 422, infra → 500) via `self.Error`, with the
real detail in the body. This is the **consolidation** decision — Output runs
in exactly one authoritative place (MultiQS), not two.

---

## Scope

- In `QueryHandler.query()` (`handlers/multi.py`), remove the duplicate Output
  execution loop + `output_errors` swallow (region ~368-417). Output is now run
  solely by `MultiQS.query()`.
- Ensure the existing `except (QueryException, DriverError)` (line 267) path
  maps a raised `OutputError` to `self.Error(...)` with a status chosen from
  `OutputError.category`: `"data"` → **422**, `"infra"` (or unknown) → **500**.
- Pass the `OutputError` as the `exception=` to `self.Error` and its message so
  the detail (exposed by TASK-712) reaches the body.
- Keep populating the `X-Output-Errors` header from the raised error as
  supplementary detail (do not rely on it as the only signal).
- **Verify the double-execution concern** (spec §7 risk): confirm Output no
  longer runs twice after removing the handler loop; a successful pipeline must
  still write exactly once and return 200.
- Add unit tests for the status mapping.

**NOT in scope**: the raise itself (TASK-713), the 422/detail primitives
(TASK-712), end-to-end integration tests (TASK-715).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/multi.py` | MODIFY | Remove duplicate Output loop (~368-417); map `OutputError.category` → 422/500 via `self.Error`. |
| `tests/unit/test_handler_output_status.py` | CREATE | Unit tests: category data→422, infra/unknown→500, detail in body. |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.queries import MultiQS               # verified: handlers/multi.py:14
from querysource.exceptions import (                   # verified: querysource/exceptions.py
    QueryException, DriverError, DataNotFound, OutputError,  # :6, :58, :48, :74
)
```

### Existing Signatures to Use
```python
# querysource/handlers/multi.py
class QueryHandler(AbstractHandler):
    async def query(self, request) -> web.StreamResponse:   # line 109
        # line 232: result, options = await qs.query()
        # 267-277: except (QueryException, DriverError) as qe: raise self.Error(...)
        # 283-293: except Exception as ex: raise self.Except(...)
        # 368-384: DUPLICATE Output loop; output_errors list; swallows  <-- REMOVE
        # 415-416: response.headers['X-Output-Errors'] = ' | '.join(output_errors)

# querysource/handlers/abstract.py:128
def Error(self, reason=None, message=None, exception=None,
          stacktrace=None, code=400) -> HTTPException:
    # After TASK-712 this supports code=422 -> HTTPUnprocessableEntity
```

### Does NOT Exist
- ~~a shared Output executor already used by both handler and MultiQS~~ — the point of this task is to leave MultiQS as the single executor.
- ~~`self.Error(code=422)` before TASK-712~~ — depends on TASK-712 landing first.

---

## Implementation Notes

- Because `OutputError` is a `QueryException`, it is already caught at
  `handlers/multi.py:267`. Enhance that branch (or add a dedicated
  `except OutputError`) to pick the status from `.category`:
  ```python
  except OutputError as oe:
      code = 422 if getattr(oe, "category", None) == "data" else 500
      raise self.Error(message=str(oe), exception=oe, code=code)
  ```
  Place the `except OutputError` BEFORE the broader `except (QueryException,
  DriverError)` so it wins.
- Removing the 368-417 loop: make sure any variables it defined
  (`output_errors`) that are referenced later (line 415-416) are handled —
  either drop the header population or source it from the raised error.
- Confirm the `Step 5` block that returns `self.response(...)` for the
  `isinstance(result, str)` case (line 360-367) is preserved.

### Key Constraints
- Output must run exactly once (MultiQS). No double write.
- Successful pipeline still returns 200 with data (no regression).

---

## Acceptance Criteria

- [ ] The handler no longer runs its own Output loop; Output executes only in `MultiQS.query()` (verified — no double write).
- [ ] A raised `OutputError` with `category="data"` produces an HTTP **422** with the real detail in the body.
- [ ] A raised `OutputError` with `category="infra"` or unknown produces an HTTP **500**.
- [ ] A successful MultiQuery + Output still returns **200** with the data (no regression).
- [ ] `X-Output-Errors` header still populated on failure for supplementary detail.
- [ ] `pytest tests/unit/test_handler_output_status.py -v` passes; `ruff check querysource/handlers/multi.py` clean.

---

## Test Specification

```python
# tests/unit/test_handler_output_status.py
# Drive QueryHandler.query with a patched MultiQS.query that raises
# OutputError(category="data") and (category="infra"); assert the response
# status is 422 and 500 respectively and that the detail is present in body.
```

---

## Agent Instructions

Standard SDD flow. Verify TASK-711/712/713 are completed. Re-verify the
368-417 region and line 267 against the live file. Move to `completed/` +
update the per-spec index when done.

---

## Completion Note

**Completed by**:
**Date**:
**Notes**:
**Deviations from spec**: none
