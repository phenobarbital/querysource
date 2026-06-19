# TASK-702: Refactor DataOutput.error and AbstractWriter.error to use the shared formatter

**Feature**: FEAT-102 — Reduce Verbose Error Responses (QS & MultiQuery)
**Spec**: `sdd/specs/querysource-logs-verbose.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-700
**Assigned-to**: unassigned

---

## Context

Implements spec §3 "Module 3". The single-query output path has two more leak
sites that are NOT handlers: `DataOutput.error` (`outputs/output.py:117`) and
`AbstractWriter.error` (`outputs/writers/abstract.py:204`, inherited by all
format writers — json, csv, excel, etc.). Both build an inline
`{"error":..., "trace": traceback.format_exc(...)}` body. This task routes both
through `build_error_payload()` (TASK-700). Since these classes are not handlers
and have no `self.debug`, they read `DEBUG` from `navconfig` directly.

---

## Scope

- Refactor `DataOutput.error()` (`outputs/output.py:117`) to build the body via
  `build_error_payload(..., debug=DEBUG)` (import `DEBUG` from `navconfig`).
  Remove the inline `traceback.format_exc(limit=10)` / `reason["trace"]`.
- Refactor `AbstractWriter.error()` (`outputs/writers/abstract.py:204`) the same
  way. All inheriting writers automatically pick up the redacted behavior.
- Map the existing call sites in `DataOutput.response()` (output.py:209-266) to
  appropriate `category`/`status` values; preserve current status codes
  (404 for StatementError, 400 for DriverError/QueryException, 500 generic,
  400/500 for writer errors).
- Sanitize the `X-MESSAGE`/`X-STATUS` headers set in these methods so they do not
  echo raw DB text in production.
- Ensure full detail is logged server-side (the formatter logs it; pass
  `self.logger` where available — `DataOutput.logger` exists; confirm the writer's
  logger attribute).

**NOT in scope**: `querysource/handlers/` (TASK-701); creating the formatter
(TASK-700); changing status codes; changing the success-path response.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/outputs/output.py` | MODIFY | Refactor `DataOutput.error()` to use `build_error_payload`; read `DEBUG` |
| `querysource/outputs/writers/abstract.py` | MODIFY | Refactor `AbstractWriter.error()` likewise |

---

## Codebase Contract (Anti-Hallucination)

> Verified against source on 2026-06-19.

### Verified Imports
```python
from navconfig import DEBUG                 # value source (verified usable: handlers/abstract.py:6)
from navconfig.logging import logging       # verified: outputs/output.py:6
from querysource.utils.errors import build_error_payload  # created by TASK-700
import traceback                            # already imported: output.py:1, writers/abstract.py
from aiohttp import web                     # already imported: output.py:3
```

### Existing Signatures to Use
```python
# querysource/outputs/output.py
class DataOutput:
    self.logger = logging.getLogger('QS.Output')   # line 74
    self._json = DefaultEncoder()                  # line 102 (self._json.dumps)
    def error(self, message, status=400, exception=None,
              headers=None, content_type='application/json') -> BaseException:  # line 117
        trace = traceback.format_exc(limit=10)     # line 128 (REMOVE from body)
        reason = {"error": message, "trace": self._json.dumps(trace)}  # 129-132 (REPLACE)
        # args={"reason":reason,"headers":{content_type,X-MESSAGE,X-STATUS}} (133-140)
        # raises web.HTTPBadRequest/.../HTTPInternalServerError by status (141-158)
        # NOTE: this method RAISES the exception (ends with `raise obj`, line 162)
    async def response(self):                      # line 174
        # dispatch -> self.error(...) for StatementError(404), DriverError/QueryException(400),
        #   Exception(500), writer TypeError/RuntimeError/ValueError(400), Exception(500)

# querysource/outputs/writers/abstract.py
class AbstractWriter:
    def error(self, message, status=400, exception=None,
              headers=None, content_type='application/json'):  # line 204
        trace = traceback.format_exc(limit=10)     # line 215 (REMOVE from body)
        reason = {"error": str(message), "trace": trace}  # 216-219 (REPLACE)
        # args={"reason":self._json.dumps(reason),"headers":{...}} (220-227)
        # RETURNS obj (not raise) — preserve return vs raise semantics per method
```

### Does NOT Exist
- ~~`DataOutput.debug` / `AbstractWriter.debug`~~ — these classes have no `debug`
  attribute; read `DEBUG` from `navconfig` directly.
- ~~`self.debug` on these classes~~ — only handlers have it (handlers/abstract.py:43).
- ~~`build_error_payload` before TASK-700~~ — verify it exists before importing.

> ⚠️ Preserve the existing control-flow contract of each method: `DataOutput.error`
> **raises** (`raise obj`, output.py:162); `AbstractWriter.error` **returns** the
> response object. Do not change raise/return semantics — only change how the body
> dict is built.

---

## Implementation Notes

### Pattern to Follow
```python
# DataOutput.error / AbstractWriter.error:
payload = build_error_payload(
    category=<mapped from status>,
    status=status,
    exception=exception,
    debug=DEBUG,
    logger=self.logger,
    public_message=message if DEBUG else None,
)
args = {
    "reason": self._json.dumps(payload),  # writer uses dumps; DataOutput passes dict to reason=
    "headers": {
        "content_type": content_type,
        "X-MESSAGE": payload["error"],
        "X-STATUS": str(status),
    },
}
# then the same `if status == ...` HTTP-exception ladder as today
```

### Key Constraints
- Keep each method's raise-vs-return behavior identical to today.
- Do not import `from querysource.conf` for DEBUG — use `navconfig.DEBUG`
  (spec Non-Goal: no new conf.py key).
- Full traceback only to logs / `debug=True` body.

### References in Codebase
- `querysource/outputs/output.py:209-266` — the `response()` dispatch that calls `self.error`.
- `querysource/outputs/writers/abstract.py:276-322` — `get_result()` that raises into `response()`.

---

## Acceptance Criteria

- [ ] With `DEBUG=False`, neither `DataOutput.error` nor `AbstractWriter.error`
      produces a body containing `Traceback`, `File "`, `/site-packages/`, or a
      `trace` key.
- [ ] With `DEBUG=False`, headers do not echo raw DB text; body carries a generic
      message + non-empty `error_id`.
- [ ] With `DEBUG=True`, body still includes `detail`/`trace`.
- [ ] `DataOutput.error` still raises; `AbstractWriter.error` still returns.
- [ ] HTTP status codes unchanged across all `response()` dispatch branches.
- [ ] No linting errors: `ruff check querysource/outputs/output.py querysource/outputs/writers/abstract.py`

---

## Test Specification

> Integration assertions in TASK-703. Minimal direct checks here:

```python
def test_dataoutput_error_redacts(monkeypatch):
    # force DEBUG False, call DataOutput.error with an exception,
    # assert the raised HTTP exception body has no 'trace' and has 'error_id'
    ...

def test_writer_error_redacts(monkeypatch):
    # AbstractWriter.error returns an HTTP exception with redacted body
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
