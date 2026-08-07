# TASK-712: Error layer — add 422 support + expose OutputError detail to client

**Feature**: FEAT-146 — MultiQuery Output Error Propagation
**Spec**: `sdd/specs/multiquery-output-error-propagation.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-711
**Assigned-to**: unassigned

---

## Context

Implements spec §3 "Module 4". Even once MultiQS raises (TASK-713), the HTTP
layer today would still (a) not be able to return **422** and (b) **redact**
the real error text in production. This task fixes the error layer so an
`OutputError` reaches the client as a 422/500 with the **actual underlying
message** in the body — the same text seen in the execution log
(e.g. `'date' is both an index level and a column label`, `duplicate key
value...`, `Unconsumed column names`).

This is the piece that makes Jesús's requirement ("...que la API pueda no solo
consumirlo sino enviarlo al cliente") actually true end-to-end.

---

## Scope

- In `AbstractHandler.Error()` (`handlers/abstract.py`): add support for
  HTTP **422** → `web.HTTPUnprocessableEntity`, and include `422` in the
  code→category map so it is treated as a `query_error` (client) category,
  not `server_error`.
- In `build_error_payload()` (`utils/errors.py`): allow the **real detail** of
  an `OutputError` to reach the client body even when `debug=False`. Scope the
  override to `OutputError` **only** — every other exception keeps the current
  redacted client-safe payload. Implementation options (choose one, document
  it): pass the destination message as `public_message`, or add a dedicated
  `detail`/`output_error` field for this error class.
- Add unit tests for both the 422 mapping and the OutputError detail exposure.

**NOT in scope**: the MultiQS raise (TASK-713), the handler's Output loop
consolidation and the actual `self.Error(code=...)` call for OutputError
(TASK-714). This task only makes the primitives *capable* of 422 + detail.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/abstract.py` | MODIFY | Add 422 → `HTTPUnprocessableEntity`; add 422 to query_error category map. |
| `querysource/utils/errors.py` | MODIFY | Expose real detail for `OutputError` even in prod (scoped override). |
| `tests/unit/test_error_payload.py` | CREATE | Unit tests for 422 + OutputError detail exposure + non-Output redaction unchanged. |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from aiohttp import web                                  # HTTPUnprocessableEntity lives here
from querysource.exceptions import OutputError           # verified: querysource/exceptions.py:74
from querysource.utils.errors import build_error_payload # verified: querysource/utils/errors.py:41
```

### Existing Signatures to Use
```python
# querysource/handlers/abstract.py:128
def Error(self, reason=None, message=None, exception=None,
          stacktrace=None, code=400) -> HTTPException:
    # line 155-160: code -> category map
    #   404 -> "not_found"
    #   400,401,402,403,406,412,428 -> "query_error"      # <-- 422 is MISSING
    #   else -> "server_error"
    # line 162-169: payload = build_error_payload(category, code, exception,
    #                          debug=self.debug, logger=self.logger,
    #                          public_message=message if self.debug else None)
    # line 179-194: if/elif code -> web.HTTP* ; NO 422 branch -> else HTTPBadRequest(400)

# querysource/utils/errors.py:41
def build_error_payload(category, status, exception=None, debug=False,
                        logger=None, public_message=None) -> dict:
    # ALWAYS logs full detail server-side under a generated error_id
    # returns {"error", "status", "error_id"}                     (prod)
    # adds  {"detail": str(exception), "trace": ...}  ONLY if debug (121-122)
    # public_message overrides the generic "error" field when provided (68, 110-114)
```

### Does NOT Exist
- ~~a 422 branch in `Error()`~~ — added by THIS task (today 422 falls through to 400).
- ~~`build_error_payload(..., expose_detail=True)`~~ — no such flag today; you add the mechanism.
- ~~`web.HTTPUnprocessableEntity` already imported in abstract.py~~ — verify the `web` import and use `web.HTTPUnprocessableEntity`.

---

## Implementation Notes

- The detail exposure MUST be **scoped to `OutputError`** — do NOT globally
  disable redaction. Simplest safe approach: in `build_error_payload`, when
  `isinstance(exception, OutputError)`, treat its message as a public message
  (set it into the `"error"` field or a `"detail"` field) even if `debug` is
  False; otherwise keep current behavior verbatim.
- Keep the server-side full log with `error_id` intact for ALL errors.
- Preserve the existing `content_type="application/json"` response shape.

### Key Constraints
- No regression for non-Output errors (they stay redacted in prod).
- 422 must produce a real `web.HTTPUnprocessableEntity` (status 422), not 400.

---

## Acceptance Criteria

- [ ] `Error(code=422, ...)` returns a response whose status is **422** (not 400).
- [ ] 422 is classified as a client (`query_error`) category, not `server_error`.
- [ ] For an `OutputError`, `build_error_payload(debug=False)` includes the real message/detail in the client payload.
- [ ] For a non-`OutputError` exception, `build_error_payload(debug=False)` still redacts (only generic message + `error_id`) — no regression.
- [ ] Full detail is still logged server-side with an `error_id` for all errors.
- [ ] `pytest tests/unit/test_error_payload.py -v` passes; `ruff check` clean on both files.

---

## Test Specification

```python
# tests/unit/test_error_payload.py
from querysource.exceptions import OutputError, QueryException
from querysource.utils.errors import build_error_payload


def test_output_error_detail_exposed_in_prod():
    p = build_error_payload("query_error", 422,
                            exception=OutputError("duplicate key value ..."),
                            debug=False)
    assert "duplicate key value" in (p.get("detail") or p.get("error", ""))


def test_non_output_error_still_redacted_in_prod():
    p = build_error_payload("server_error", 500,
                            exception=QueryException("internal boom"),
                            debug=False)
    assert "internal boom" not in p.get("error", "")
    assert "error_id" in p
```

---

## Agent Instructions

Standard SDD flow. Verify TASK-711 is completed (enriched `OutputError`
available) before starting. Verify the contract against the live files first
(line numbers may drift). Move to `completed/` and update the per-spec index
when done.

---

## Completion Note

**Completed by**:
**Date**:
**Notes**:
**Deviations from spec**: none
