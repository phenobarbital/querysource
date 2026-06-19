# TASK-700: Shared error-payload formatter

**Feature**: FEAT-102 — Reduce Verbose Error Responses (QS & MultiQuery)
**Spec**: `sdd/specs/querysource-logs-verbose.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

This is the foundation of FEAT-102 (spec §2 "Overview", §3 "Module 1"). Today
every error site builds its own `reason`/`trace` dict inline, leaking the full
Python traceback, internal file paths, and raw DB error text to clients. This
task creates the single shared formatter that all error sites (TASK-701,
TASK-702) will route through, so redaction is reasoned about in exactly one
place.

---

## Scope

- Create `querysource/utils/errors.py` with:
  - `GENERIC_MESSAGES: dict[str, str]` — maps an error category to a safe,
    generic public message (no DB/driver/internal text).
  - `build_error_payload(*, category, status, exception=None, debug=False,
    logger=None, public_message=None) -> dict` per the spec's "New Public
    Interfaces" contract.
- Behavior of `build_error_payload`:
  1. Generate a short `error_id` (`uuid4().hex[:12]`).
  2. Capture the traceback (`traceback.format_exc(limit=20)`) and log the full
     detail (category, original message/exception, traceback) at `error` level
     via the passed `logger`, prefixed with the `error_id`. If `logger` is None,
     fall back to a module-level logger.
  3. Return the production-minimal payload by default:
     `{"error": <public_message or GENERIC_MESSAGES[category]>, "status": status,
     "error_id": error_id}`.
  4. When `debug=True`, additionally include `"detail"` (the original
     message/exception string) and `"trace"` (the traceback).
- Default category→message set (finalizes spec §8 open question):
  - `bad_request` → "Invalid query request"
  - `query_error` → "Query execution failed"
  - `not_found` → "Resource not found"
  - `server_error` → "Internal query error"
  - `output_error` → "Output generation failed"
  - Unknown/missing category → falls back to `server_error` message.
- Write unit tests for the formatter (this module's behavior only).

**NOT in scope**: editing any handler, output, or writer call site (TASK-701,
TASK-702); changing HTTP status codes; changing server-side log verbosity
elsewhere.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/utils/errors.py` | CREATE | `build_error_payload()` + `GENERIC_MESSAGES` |
| `tests/test_error_formatter.py` | CREATE | Unit tests for the formatter |

> NOTE: confirm the existing test directory/layout (`tests/` vs `tests/unit/`)
> before creating the test file — spec §8 leaves this to the implementer.

---

## Codebase Contract (Anti-Hallucination)

> Verified against source on 2026-06-19.

### Verified Imports
```python
# stdlib only for the core logic
import traceback
import uuid
import logging

# value source for the debug flag (used by CALLERS, not hard-coded here)
from navconfig import DEBUG               # verified: querysource/handlers/abstract.py:6
```

### Existing Signatures to Use
```python
# querysource/exceptions.py
class QueryException(Exception):
    code: int = 0                         # line 9
    def __init__(self, message: str, code: int = 0, **kwargs):  # line 11
        self.stacktrace = kwargs.get('stacktrace', None)        # line 13
        self.message = message            # line 14
        self.args = kwargs                # line 15
        self.code = int(code)             # line 16
    def __str__(self):                    # line 21 -> returns self.message
# Subclasses exist: DataNotFound, DriverError, QueryError, ParserError,
#   SlugNotFound(code=404), QueryNotFound(code=404), OutputError, CacheException
```

### Does NOT Exist
- ~~`querysource/utils/errors.py`~~ — does not exist yet; this task creates it.
- ~~`querysource/conf.py` `DEBUG` / `QS_DEBUG`~~ — not in conf.py; `DEBUG` lives in
  `navconfig`. Do NOT add a key to conf.py (spec Non-Goal).
- ~~`QueryException.public_message` / `.safe_message`~~ — not real attributes; only
  `message`, `stacktrace`, `args`, `code` exist.

---

## Implementation Notes

### Pattern to Follow
- Keep the module dependency-light (stdlib + optionally a module logger). Do NOT
  import aiohttp or navconfig.DEBUG inside the builder — `debug` is a parameter
  passed by callers so the function stays pure/testable.
- The builder returns a plain `dict`; callers are responsible for serializing it
  into the aiohttp response body (they already use `self._json.dumps(...)`).

### Key Constraints
- Never put the traceback or raw exception text into the production (`debug=False`)
  return value — only into the log and into the `debug=True` payload.
- `error_id` must be present in BOTH the returned payload and the log line so an
  operator can correlate them (spec G4).
- Use `logger.error(...)` (or `logger.exception`) for the full server-side detail.

### References in Codebase
- `querysource/handlers/abstract.py:178` — current `traceback.format_exc(limit=20)`
  pattern being replaced.
- `querysource/outputs/output.py:128` — current inline trace construction.

---

## Acceptance Criteria

- [ ] `from querysource.utils.errors import build_error_payload, GENERIC_MESSAGES` works.
- [ ] `build_error_payload(category="query_error", status=400)` returns exactly
      keys `{"error","status","error_id"}` with a non-empty `error_id` and a
      generic `error` (no DB/trace text).
- [ ] With `debug=True`, the return value additionally contains `"detail"` and
      `"trace"`.
- [ ] Full detail (message + traceback) is logged via the passed logger, tagged
      with the same `error_id` returned to the caller.
- [ ] Raw DB text (e.g. `column "apikey" does not exist`) never appears in the
      `debug=False` payload.
- [ ] All tests pass: `pytest tests/test_error_formatter.py -v`
- [ ] No linting errors: `ruff check querysource/utils/errors.py`

---

## Test Specification

```python
# tests/test_error_formatter.py
import logging
import pytest
from querysource.utils.errors import build_error_payload, GENERIC_MESSAGES


def test_production_minimal():
    payload = build_error_payload(category="query_error", status=400)
    assert set(payload) == {"error", "status", "error_id"}
    assert payload["status"] == 400
    assert payload["error_id"]
    assert "Traceback" not in payload["error"]


def test_debug_verbose():
    try:
        raise ValueError('column "apikey" does not exist')
    except ValueError as exc:
        payload = build_error_payload(
            category="query_error", status=400, exception=exc, debug=True
        )
    assert "detail" in payload and "trace" in payload
    assert "apikey" in payload["detail"]


def test_production_hides_db_text():
    try:
        raise ValueError('column "apikey" does not exist')
    except ValueError as exc:
        payload = build_error_payload(
            category="query_error", status=400, exception=exc, debug=False
        )
    assert "apikey" not in str(payload)
    assert "trace" not in payload


def test_logs_full_detail_with_error_id(caplog):
    logger = logging.getLogger("test.qs.errors")
    with caplog.at_level(logging.ERROR):
        payload = build_error_payload(
            category="server_error", status=500,
            exception=RuntimeError("boom"), logger=logger,
        )
    assert payload["error_id"] in caplog.text
    assert "boom" in caplog.text
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at the path listed above for full context.
2. **Check dependencies** — none.
3. **Verify the Codebase Contract** before writing code.
4. **Update status** in `sdd/tasks/index/querysource-logs-verbose.json` → `"in-progress"`.
5. **Implement** per scope.
6. **Verify** all acceptance criteria.
7. **Move this file** to `sdd/tasks/completed/`.
8. **Update index** → `"done"`.
9. **Fill in the Completion Note** below.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**:

**Deviations from spec**: none | describe if any
