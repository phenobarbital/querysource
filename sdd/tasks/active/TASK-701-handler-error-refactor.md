# TASK-701: Refactor handler Error/Except to use the shared formatter

**Feature**: FEAT-102 — Reduce Verbose Error Responses (QS & MultiQuery)
**Spec**: `sdd/specs/querysource-logs-verbose.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-700
**Assigned-to**: unassigned

---

## Context

Implements spec §3 "Module 2". `AbstractHandler.Error` and `.Except` in
`querysource/handlers/abstract.py` are the error path for BOTH the single-query
service handler AND the MultiQuery handler (`handlers/multi.py` calls
`self.Error`/`self.Except`). Today `Except()` unconditionally captures and
embeds a 20-line traceback, and both methods echo the raw exception into the
client body and into `X-ERROR`/`X-MESSAGE` headers. This task routes both
methods through `build_error_payload()` (TASK-700), gated on `self.debug`.

---

## Scope

- Refactor `AbstractHandler.Error()` (`handlers/abstract.py:109`) to build the
  client body via `build_error_payload(..., debug=self.debug)`. Stop adding
  `reason["trace"]`/`stacktrace` to the production body.
- Refactor `AbstractHandler.Except()` (`handlers/abstract.py:165`) likewise:
  remove the unconditional `traceback.format_exc(limit=20)` from the client
  body; full detail goes to the formatter's server-side log instead.
- Map each call site's intent to a `category`/`status` for the formatter
  (e.g. 404 → `not_found`, 400/402 → `query_error`/`bad_request`, 500 →
  `server_error`). Preserve existing HTTP status codes (incl. MultiQuery's `402`).
- Sanitize headers in production: `X-ERROR` (line 193) must NOT carry the raw
  exception string when `self.debug` is False; `X-MESSAGE` must carry the
  generic message (or the `error_id`), not raw DB text.
- Keep `self.logger.exception(...)` calls at the `handlers/multi.py` call sites
  intact (server-side logging must remain full-detail).
- Confirm the MultiQuery path (`handlers/multi.py:266-296`) still works through
  the refactored methods without changing those call sites.

**NOT in scope**: `querysource/outputs/` (TASK-702); creating the formatter
(TASK-700); changing status codes; adding tests for the formatter itself.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/abstract.py` | MODIFY | Refactor `Error()` and `Except()` to use `build_error_payload`; sanitize headers |

---

## Codebase Contract (Anti-Hallucination)

> Verified against source on 2026-06-19.

### Verified Imports
```python
from navconfig import DEBUG                 # verified: querysource/handlers/abstract.py:6
from querysource.utils.errors import build_error_payload  # created by TASK-700
import traceback                            # already imported: handlers/abstract.py:2
from aiohttp import web                     # already imported: handlers/abstract.py:4
```

### Existing Signatures to Use
```python
# querysource/handlers/abstract.py
class AbstractHandler(BaseHandler):
    self.debug: bool = DEBUG                 # line 43 (set in post_init)
    self._json                               # JSON encoder used as self._json.dumps(...)
    def Error(self, reason=None, message=None, exception=None,
              stacktrace=None, code=400) -> HTTPException:   # line 109
        # currently: reason={"error":message,"reason":...}; reason["trace"]=stacktrace (136-137)
        # args["text"]=self._json.dumps(reason) -> client body (line 140)
        # headers X-MESSAGE=str(message), X-STATUS=str(code) (142-143)
        # raises web.HTTPBadRequest/Unauthorized/Forbidden/NotFound/... by code (147-162)
    def Except(self, reason=None, message=None, exception=None,
               stacktrace=None, headers=None, code=500) -> HTTPException:  # line 165
        trace = traceback.format_exc(limit=20)   # line 178 (REMOVE from client body)
        # reason["trace"]=self._json.dumps(trace) (183); X-ERROR=str(exception) (193)
        # raises HTTPInternalServerError/NotImplemented/ServiceUnavailable (198-203)

# querysource/handlers/multi.py  (CALL SITES — do not edit, just verify behavior)
#   line 255 -> self.Error(message="Slug Not Found", exception=snf, code=404)
#   line 261 -> self.Error(message="Error parsing Query Slug", exception=pe, code=401)
#   line 276 -> self.Error(message="Query Error", exception=qe, stacktrace=trace, code=402)
#   line 292 -> self.Except(message=f"Unknown Error on Query: {ex!s}", exception=ex, stacktrace=trace)
```

### Does NOT Exist
- ~~`DataOutput.debug` / `AbstractWriter.debug`~~ — those classes are out of scope here
  (TASK-702) and are not handlers.
- ~~a conf.py DEBUG flag~~ — `self.debug` already exists on the handler (line 43).
- ~~`build_error_payload` before TASK-700~~ — depends on TASK-700; verify it exists
  before importing.

---

## Implementation Notes

### Pattern to Follow
```python
# inside Error()/Except():
payload = build_error_payload(
    category=<mapped from code>,
    status=code,
    exception=exception,
    debug=self.debug,
    logger=self.logger,
    public_message=message if self.debug else None,
)
args = {
    "reason": payload["error"],
    "text": self._json.dumps(payload),
    "headers": {
        "X-MESSAGE": payload["error"],
        "X-STATUS": str(code),
        # X-ERROR only when self.debug
    },
    "content_type": "application/json",
}
```

### Key Constraints
- Preserve the existing code→HTTP-exception mapping (the `if code == ...` ladder).
- Do not reduce server-side logging — the formatter logs full detail; keep
  `self.logger.exception` at the multi.py call sites.
- `stacktrace=` kwarg may still be passed by callers (multi.py) — it must NOT
  reach the production client body; fold it into the server-side log only.

### References in Codebase
- `querysource/handlers/multi.py:266-296` — the MultiQuery error blocks that call these methods.

---

## Acceptance Criteria

- [ ] With `self.debug=False`, neither `Error()` nor `Except()` produces a body
      containing `Traceback`, `File "`, `/site-packages/`, or a `trace` key.
- [ ] With `self.debug=False`, `X-ERROR` header is absent (or generic); `X-MESSAGE`
      carries the generic message, not raw DB text.
- [ ] Every error body includes a non-empty `error_id`.
- [ ] With `self.debug=True`, body still includes `detail`/`trace`.
- [ ] HTTP status codes are unchanged for all existing call sites (incl. 402).
- [ ] MultiQuery error path (`handlers/multi.py`) returns the redacted shape.
- [ ] No linting errors: `ruff check querysource/handlers/abstract.py`

---

## Test Specification

> Integration-style assertions live in TASK-703. Here, at minimum, exercise both
> methods directly with a fake exception and assert the redacted vs. debug shape.

```python
# (add to FEAT-102 test module per TASK-703 layout)
def test_handler_except_redacts_in_production(monkeypatch):
    # build a handler instance with self.debug=False and assert no 'trace' in body
    ...
```

---

## Agent Instructions

1. **Read the spec** for full context.
2. **Check dependencies** — verify TASK-700 is in `sdd/tasks/completed/` and
   `build_error_payload` imports cleanly.
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
