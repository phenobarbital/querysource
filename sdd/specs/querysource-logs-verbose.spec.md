---
type: feature
base_branch: dev
---

# Feature Specification: Reduce Verbose Error Responses (QS & MultiQuery)

**Feature ID**: FEAT-102
**Date**: 2026-06-19
**Author**: Jesus Lara
**Status**: draft
**Target version**: TBD

---

## 1. Motivation & Business Requirements

> Why does this feature exist? What problem does it solve?

### Problem Statement

QuerySource currently returns **highly verbose error payloads** to API clients.
On any query failure, the JSON body includes the full Python traceback, internal
file paths, the package install layout, internal module/class names, and the raw
database error text. A real example returned to a client:

```json
{
  "error": "Error on Query: polestar_graduates",
  "reason": "{'error': 'Query Error: Sentence Error: column \"apikey\" does not exist...",
  "trace": "\"Traceback (most recent call last):\\n  File \\\"/code/.venv/lib/python3.11/site-packages/querysource/outputs/output.py\\\", line 200, in response\\n    await writer.get_result()\\n  File \\\"/code/.venv/lib/python3.11/site-packages/querysource/outputs/writers/abstract.py\\\", line 312, in get_result\\n    raise QueryException(error)\\n..."
}
```

This is a security problem: it leaks information an attacker can use to map the
backend (database schema/column names, ORM/driver internals, dependency
versions, file system layout, internal module structure). It also produces
noisy, deeply-escaped, hard-to-consume payloads for legitimate API consumers.

The same leak occurs across the single-query path (`DataOutput` / writers /
`QueryService` handler) **and** the MultiQuery path (`handlers/multi.py`), so
the fix must cover both.

### Goals

- G1. By default (production / `DEBUG=False`), **never** include Python
  tracebacks, internal file paths, or internal module/class names in the
  HTTP error body returned to clients.
- G2. By default, **sanitize the human-readable error message** so it does not
  echo raw database/driver error text (e.g. `column "apikey" does not exist`).
  Return a categorized, generic message instead.
- G3. Always **log the full detail server-side** (message + traceback) at
  `error`/`exception` level so operators lose no diagnostic capability.
- G4. Include a short **`error_id`** in every error response and in the
  corresponding server log line, so an operator can correlate a client-visible
  error with the full server-side trace.
- G5. Preserve full verbose output (trace + raw message) **only** when
  `DEBUG=True` (the existing `navconfig.DEBUG` flag), for local development.
- G6. Apply the fix consistently across **both** the QS single-query path and
  the MultiQuery path (and all output writers) via a single shared formatter,
  so there is one place to reason about redaction.

### Non-Goals (explicitly out of scope)

- Changing HTTP status codes returned for each error class (codes stay as-is).
- Redacting / restructuring server-side **logs** — logs keep full detail.
- A configurable redaction policy / allow-list of which fields to expose.
  Gating is binary on `navconfig.DEBUG` (a dedicated `QS_*` env flag was
  considered and rejected in favor of reusing the existing flag).
- Rate limiting, auth, or other unrelated hardening of the query endpoints.
- Changing the success-path response shape — only error responses change.

---

## 2. Architectural Design

### Overview

Introduce a **single shared error-payload builder** in a new utility module,
`querysource/utils/errors.py`. Every error-producing site (the handler base
class `Error`/`Except`, `DataOutput.error`, and `AbstractWriter.error`) routes
through this builder instead of constructing the `reason`/`trace` dict inline.

The builder's contract:

1. Generate a short `error_id` (e.g. `uuid4().hex[:12]`).
2. Log the full message + traceback server-side, prefixed with `error_id`.
3. Return a payload whose verbosity depends on a `debug` flag:
   - **`debug=False` (default / production):** `{ "error": <generic categorized
     message>, "status": <code>, "error_id": <id> }` — no trace, no raw DB text,
     no internal paths.
   - **`debug=True` (dev):** additionally include `"detail"` (the original
     message) and `"trace"` (the traceback) for debugging.

The `debug` value comes from `navconfig.DEBUG`:
- Handlers already expose `self.debug` (set from `navconfig.DEBUG` at
  `handlers/abstract.py:43`) — they pass it through.
- `DataOutput` and `AbstractWriter` are **not** handlers and do not currently
  hold `self.debug`; they import `DEBUG` from `navconfig` directly (same source).

Message sanitization: the builder maps each error to a stable, generic public
message based on the error category the caller already distinguishes (e.g.
"Invalid query request", "Query execution failed", "Resource not found"),
discarding the raw exception string from the public payload while keeping it
in the server log.

### Component Diagram

```
                         ┌─────────────────────────────┐
Client ◄── HTTP error ───│  querysource/utils/errors.py│──► server log (full
                         │   build_error_payload(...)   │     trace + error_id)
                         └──────────────▲──────────────┘
                                        │ (debug flag + category + exc)
        ┌───────────────────────────────┼───────────────────────────────┐
        │                                │                                │
┌───────┴────────┐            ┌──────────┴─────────┐          ┌───────────┴────────┐
│ handlers/      │            │ outputs/output.py  │          │ outputs/writers/   │
│ abstract.py    │            │  DataOutput.error  │          │ abstract.py        │
│ Error / Except │            │  (single-query)    │          │ AbstractWriter.error│
│ (QS + Multi)   │            └────────────────────┘          └────────────────────┘
└────────────────┘
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `AbstractHandler.Error` / `.Except` (`handlers/abstract.py`) | refactor to call builder | Used by `QueryService` and `handlers/multi.py`. Pass `self.debug`. |
| `DataOutput.error` (`outputs/output.py:117`) | refactor to call builder | Single-query output path. Read `DEBUG` from navconfig. |
| `AbstractWriter.error` (`outputs/writers/abstract.py:204`) | refactor to call builder | All format writers inherit this. Read `DEBUG` from navconfig. |
| `handlers/multi.py` query error blocks (lines 266–296) | unchanged call sites | They call `self.Error`/`self.Except`; behavior changes via the builder. |
| `navconfig.DEBUG` | uses | Single source of truth for verbose toggle. |
| `querysource.exceptions.QueryException` (`code` attr) | reads | Builder can use `exception.code` for status when present. |

### Data Models

This feature is a formatter, not a data-model feature; no Pydantic model is
required. The error payload is a plain dict with a fixed shape:

```python
# Production (DEBUG=False)
{
    "error": str,      # generic, categorized — never raw DB/driver text
    "status": int,     # HTTP status code
    "error_id": str,   # correlation id, also in server log
}

# Development (DEBUG=True) — superset of the above
{
    "error": str,
    "status": int,
    "error_id": str,
    "detail": str,     # original message (may contain DB text)
    "trace": str,      # python traceback
}
```

### New Public Interfaces

```python
# querysource/utils/errors.py  (new module)

GENERIC_MESSAGES: dict[str, str]  # category -> safe public message

def build_error_payload(
    *,
    category: str,                       # e.g. "query_error", "not_found", "bad_request", "server_error"
    status: int,
    exception: BaseException | None = None,
    debug: bool = False,
    logger: "logging.Logger | None" = None,
    public_message: str | None = None,   # explicit override for the public message
) -> dict:
    """Build a client-safe error payload.

    Always logs full detail (message + traceback) server-side under a generated
    error_id. Returns a minimal payload unless debug=True, in which case the
    original detail and traceback are included.
    """
```

---

## 3. Module Breakdown

> These directly map to Task Artifacts in Phase 2.

### Module 1: Shared error formatter
- **Path**: `querysource/utils/errors.py` (new)
- **Responsibility**: `build_error_payload()` + `GENERIC_MESSAGES` map +
  `error_id` generation + server-side logging of full detail. Single place
  that decides what is and isn't exposed.
- **Depends on**: stdlib `traceback`, `uuid`, `logging`; `navconfig.DEBUG`
  (read by callers, not hard-coded here).

### Module 2: Handler error methods refactor
- **Path**: `querysource/handlers/abstract.py`
- **Responsibility**: Rewrite `Error()` (lines 109–163) and `Except()`
  (lines 165–204) to delegate to `build_error_payload()`, passing
  `debug=self.debug`. Remove unconditional `traceback.format_exc()` and the
  `trace`/`reason`/`X-ERROR` leakage from the client-facing body/headers.
- **Depends on**: Module 1. Affects both `QueryService` and `handlers/multi.py`
  (they call these methods).

### Module 3: Output + writer error methods refactor
- **Path**: `querysource/outputs/output.py`, `querysource/outputs/writers/abstract.py`
- **Responsibility**: Rewrite `DataOutput.error()` (output.py:117) and
  `AbstractWriter.error()` (writers/abstract.py:204) to delegate to
  `build_error_payload()`, reading `DEBUG` from `navconfig`. Remove inline
  `trace` construction.
- **Depends on**: Module 1.

### Module 4: Tests
- **Path**: `tests/` (location per existing test layout)
- **Responsibility**: Unit tests for the formatter (debug on/off, categories,
  error_id present, no trace/paths in production) + regression tests asserting
  no traceback / no `/site-packages/` / no raw DB text appears in
  production-mode error bodies for QS and MultiQuery paths.
- **Depends on**: Modules 1–3.

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_build_payload_production_minimal` | Module 1 | `debug=False` returns only `error`, `status`, `error_id`; no `trace`/`detail`. |
| `test_build_payload_debug_verbose` | Module 1 | `debug=True` includes `detail` and `trace`. |
| `test_build_payload_generates_error_id` | Module 1 | `error_id` present, non-empty, stable within one call. |
| `test_build_payload_logs_full_detail` | Module 1 | With a captured logger, full message + traceback are logged and contain `error_id`. |
| `test_build_payload_sanitizes_db_message` | Module 1 | Raw DB text (e.g. `column "apikey" does not exist`) does NOT appear in production payload. |
| `test_build_payload_uses_category_message` | Module 1 | Each category maps to its generic public message. |

### Integration Tests
| Test | Description |
|---|---|
| `test_qs_error_no_traceback_in_prod` | Single-query failure with `DEBUG=False` → body has no `trace`, no `/site-packages/`, no module paths; has `error_id`. |
| `test_multiquery_error_no_traceback_in_prod` | MultiQuery failure with `DEBUG=False` → same assertions via `handlers/multi.py`. |
| `test_error_verbose_when_debug` | Same failures with `DEBUG=True` → `trace`/`detail` present (dev parity preserved). |
| `test_error_id_matches_log` | `error_id` in the HTTP body matches the `error_id` in the captured server log. |

### Test Data / Fixtures
```python
@pytest.fixture
def db_column_error():
    # Simulates the leaked example: a driver error echoing schema detail.
    from querysource.exceptions import QueryException
    return QueryException('Sentence Error: column "apikey" does not exist', code=400)
```

---

## 5. Acceptance Criteria

> This feature is complete when ALL of the following are true:

- [ ] With `DEBUG=False`, no QS or MultiQuery error response body contains the
      substring `Traceback`, `/site-packages/`, `File "`, or a `trace` key. (G1)
- [ ] With `DEBUG=False`, no error response echoes raw database/driver text such
      as `column "apikey" does not exist`; the public `error` is a generic
      categorized message. (G2)
- [ ] Every error response includes a non-empty `error_id`. (G4)
- [ ] The full original message + traceback are written to the server log at
      `error`/`exception` level, tagged with the same `error_id`. (G3, G4)
- [ ] With `DEBUG=True`, error responses still include `detail` + `trace`
      (development parity preserved). (G5)
- [ ] Both `querysource/outputs/output.py`, `querysource/outputs/writers/abstract.py`,
      and `querysource/handlers/abstract.py` (`Error`/`Except`) route through the
      single `build_error_payload()` formatter. (G6)
- [ ] The `X-ERROR` header (handlers/abstract.py:193) no longer carries the raw
      exception string in production.
- [ ] All unit tests pass (`pytest tests/ -v`).
- [ ] No change to HTTP status codes for existing error classes.

---

## 6. Codebase Contract

> **CRITICAL — Anti-Hallucination Anchor**
> Verified against source on 2026-06-19.

### Verified Imports
```python
from navconfig import DEBUG                      # verified: querysource/handlers/abstract.py:6
from navconfig.logging import logging            # verified: querysource/conf.py:6, output.py:6
from querysource.exceptions import (             # verified: querysource/exceptions.py
    QueryException, DataNotFound, DriverError, QueryError, ParserError,
    SlugNotFound, OutputError, CacheException,
)
from datamodel.parsers.encoders import DefaultEncoder  # verified: outputs/output.py:7 (self._json)
```

### Existing Class Signatures
```python
# querysource/exceptions.py
class QueryException(Exception):
    code: int = 0                                          # line 9
    def __init__(self, message: str, code: int = 0, **kwargs):  # line 11
        self.stacktrace = kwargs.get('stacktrace', None)  # line 13
        self.message = message                            # line 14
        self.args = kwargs                                # line 15
        self.code = int(code)                             # line 16
# Subclasses (lines 28-75): ConfigError, SlugNotFound(404), EmptySentence,
#   QueryError, DataNotFound, QueryNotFound(404), DriverError,
#   DriverException(DriverError), CacheException, ParserError, OutputError

# querysource/handlers/abstract.py
class AbstractHandler(BaseHandler):
    self.debug: bool = DEBUG                               # line 43 (set in post_init)
    def Error(self, reason=None, message=None, exception=None,
              stacktrace=None, code=400) -> HTTPException:  # line 109
        # builds reason={"error":message,"reason":...}; adds reason["trace"]=stacktrace if given (line 136-137)
        # text=self._json.dumps(reason) -> client body (line 140)
    def Except(self, reason=None, message=None, exception=None,
               stacktrace=None, headers=None, code=500) -> HTTPException:  # line 165
        trace = traceback.format_exc(limit=20)             # line 178  (UNCONDITIONAL leak)
        # reason["trace"]=self._json.dumps(trace) (line 183); X-ERROR header = str(exception) (line 193)

# querysource/outputs/output.py
class DataOutput:
    self._json = DefaultEncoder()                          # line 102
    self.logger = logging.getLogger('QS.Output')           # line 74
    def error(self, message, status=400, exception=None,
              headers=None, content_type='application/json') -> BaseException:  # line 117
        trace = traceback.format_exc(limit=10)             # line 128 (leak)
        reason = {"error": message, "trace": self._json.dumps(trace)}  # line 129-132
        # raises web.HTTPBadRequest / ... with args["reason"]=reason  (lines 141-162)
    async def response(self):                              # line 174
        # error dispatch: StatementError->error(404), (DriverError,QueryException)->error(400),
        #   Exception->error(500); writer errors -> error(400/500)  (lines 209-266)

# querysource/outputs/writers/abstract.py
class AbstractWriter (base of jsonWriter, CSVWriter, ExcelWriter, ...):
    def error(self, message, status=400, exception=None,
              headers=None, content_type='application/json'):  # line 204
        trace = traceback.format_exc(limit=10)             # line 215 (leak)
        reason = {"error": str(message), "trace": trace}   # line 216-219
    async def get_result(self):                            # line 276
        # raises DataNotFound / re-raises StatementError, DriverError, QueryException (lines 306-322)

# querysource/handlers/multi.py  (MultiQuery handler)
#   query() error blocks: lines 266-281 -> self.Error(message="Query Error", exception, stacktrace=trace, code=402)
#                         lines 282-296 -> self.Except(message=..., exception, stacktrace=trace)
#   trace captured via traceback.format_exc() at lines 267, 283
```

### Config Reference
```python
# querysource/conf.py
from navconfig import BASE_DIR, config                    # line 5
ENVIRONMENT = config.get('ENVIRONMENT', fallback='development')  # line 206
# Pattern for boolean env flags already in use, e.g.:
#   POSTGRES_SSL = config.getboolean('POSTGRES_SSL', fallback=False)  # line 63
# NOTE: gating reuses navconfig.DEBUG — no new conf.py key required (per resolved decision).
```

### Integration Points
| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `build_error_payload()` | `AbstractHandler.Error/.Except` | function call, `debug=self.debug` | `handlers/abstract.py:109,165` |
| `build_error_payload()` | `DataOutput.error` | function call, `debug=DEBUG` | `outputs/output.py:117` |
| `build_error_payload()` | `AbstractWriter.error` | function call, `debug=DEBUG` | `outputs/writers/abstract.py:204` |
| `build_error_payload()` | `navconfig.DEBUG` | import | `handlers/abstract.py:6` |

### Does NOT Exist (Anti-Hallucination)
- ~~`querysource/conf.py` `DEBUG` / `QS_DEBUG` / `PRODUCTION` flag~~ — not defined
  in conf.py; `DEBUG` lives in `navconfig` and is imported in handlers/abstract.py:6.
- ~~A central error formatter / redactor~~ — does not exist today; each site builds
  its own `reason`/`trace` dict inline. This feature creates it.
- ~~`error_id` / correlation id in any current error payload~~ — does not exist.
- ~~`DataOutput.debug` / `AbstractWriter.debug` attribute~~ — does not exist; these
  classes are not handlers and must read `navconfig.DEBUG` directly.
- ~~`QueryException.public_message` / `.safe_message`~~ — not a real attribute;
  only `message`, `stacktrace`, `args`, `code` exist (exceptions.py:13-16).

---

## 7. Implementation Notes & Constraints

### Patterns to Follow
- Single source of truth: all client-facing error bodies go through
  `querysource/utils/errors.py:build_error_payload`. Do not reintroduce inline
  `traceback.format_exc()` into a client body.
- Keep full diagnostics in logs via `self.logger` / `logging` — never reduce the
  server-side log detail.
- Gate verbosity exclusively on `navconfig.DEBUG`; do not add a new env flag.
- Follow the existing aiohttp `web.HTTP*` raise/return pattern already in each
  `error`/`Error`/`Except` method; only the body construction changes.

### Known Risks / Gotchas
- **Client compatibility:** consumers that currently parse `trace` / `reason`
  keys will no longer find them in production. This is intended (those keys leak
  data); document the new error shape. Mitigation: `error_id` + `error` remain.
- **Message sanitization vs. usefulness:** generic messages reduce client-side
  debuggability. Mitigation: `error_id` lets operators retrieve the full detail
  from logs; `DEBUG=True` restores full verbosity locally.
- **Two debug sources:** handlers use `self.debug`; output/writers read
  `navconfig.DEBUG`. Both resolve to the same value — verify they agree in tests
  to avoid a path that is verbose while another is not.
- **Headers leak too:** `X-ERROR` (handlers/abstract.py:193) and `X-MESSAGE`
  currently carry raw exception text. Sanitize these in production as well.
- **MultiQuery code 402:** `handlers/multi.py` uses status `402` for query
  errors — preserve it (Non-Goal: don't change codes).

### External Dependencies
| Package | Version | Reason |
|---|---|---|
| `navconfig` | (existing) | source of `DEBUG` flag and `config` |
| stdlib `uuid`, `traceback`, `logging` | — | error_id, trace capture (log only), logging |

---

## 8. Open Questions

> Resolved items captured the design decisions made during /sdd-spec intake.

- [x] How to gate verbose tracebacks off in production? — *Resolved during
  intake*: Reuse the existing `navconfig.DEBUG` flag (handlers already expose
  `self.debug`); no new `QS_*` env var.
- [x] Should the raw error message be sanitized, or only the traceback? —
  *Resolved during intake*: Sanitize both — drop the trace AND replace
  DB/internal error text with a generic categorized message in production; log
  full detail server-side.
- [x] What does the production error body contain? — *Resolved during intake*:
  `{ "error": <generic>, "status": <code>, "error_id": <id> }`; add `error_id`
  for log correlation. `detail` + `trace` only when `DEBUG=True`.
- [ ] Exact category → public-message map (wording of each generic message) —
  *Owner: Jesus Lara* (can be finalized during implementation; default set:
  bad_request → "Invalid query request", query_error → "Query execution failed",
  not_found → "Resource not found", server_error → "Internal query error").
- [ ] Confirm the test directory/layout for new tests (`tests/unit` vs `tests/`)
  before writing Module 4 — *Owner: implementer*.

---

## Worktree Strategy

- **Default isolation unit**: `per-spec` (single worktree, sequential tasks).
- Rationale: Modules 2 and 3 both depend on Module 1's new public function;
  they touch overlapping concerns (error formatting) and must be reasoned about
  together. Module 4 (tests) depends on all of 1–3. There is no benefit to
  parallel worktrees and a real risk of conflicting edits to the shared
  formatter contract.
- **Cross-feature dependencies**: none. No other spec must merge first.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-06-19 | Jesus Lara | Initial draft |
