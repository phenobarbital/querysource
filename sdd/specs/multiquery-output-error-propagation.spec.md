---
type: feature
base_branch: dev
---

# Feature Specification: MultiQuery Output Error Propagation

**Feature ID**: FEAT-146
**Date**: 2026-08-06
**Author**: Juan (jfrruffato@trocglobal.com)
**Status**: draft
**Target version**: 4.6.0

---

## 1. Motivation & Business Requirements

> Why does this feature exist? What problem does it solve?

### Problem Statement

In a MultiQuery pipeline, when an **Output** component (e.g. `TableOutput`)
fails, the error is **silently swallowed**. The failure is only written to the
server log; the HTTP API returns a **success** response. As a result, the
consuming frontend (`navigator-front-next`) has no way to surface the real
error to the end user.

There are **two** independent places where this happens today:

1. **`MultiQS.query()`** (`querysource/queries/multi/__init__.py:531-546`) runs
   each destination inside a `try/except Exception` that calls
   `logging.error(...)` and continues to the next destination — the exception
   is never re-raised (comment: *"Per spec: continue to next destination on
   failure"*).
2. **`QueryHandler.query()`** (`querysource/handlers/multi.py:368-417`) *also*
   iterates `options['Output']`, runs each destination, catches
   `Exception as dest_err`, appends to a local `output_errors` list, and only
   exposes it as an `X-Output-Errors` **response header on an otherwise-200
   success** (lines 415-416). The frontend reads status/body, not this header,
   so the error stays invisible.

This is the root cause of every "the pipeline ran but nothing loaded / no
error" symptom observed in production, including: `ON CONFLICT ... ambiguous`,
duplicate-key / PK collisions, `Unconsumed column names`, numeric overflow,
missing-column and type-cast errors from `TableOutput`.

Crucially, the handler **already** maps `QueryException` / `DriverError` to a
real HTTP error response (`querysource/handlers/multi.py:267-293`), and
`OutputError` is a subclass of `QueryException`
(`querysource/exceptions.py:74`). So the missing piece is simply to **stop
swallowing** and let the typed error propagate.

### Goals
- When an Output/destination fails, **raise** a typed error instead of
  swallowing it, so `MultiQS.query()` propagates it to the handler.
- Have the HTTP API return a **real error response** (non-2xx) whose body
  carries the destination step name and the underlying error detail, so
  `navigator-front-next` can display it to the user.
- **Fail-fast**: stop at the first failing destination and raise immediately.
- **Differentiated HTTP status**: data/validation errors (PK collision, missing
  column, numeric overflow, cast errors) → `4xx` (422/400); infrastructure
  errors (connection, timeout) → `500`.
- **Consolidate** Output execution into a single authoritative code path to
  eliminate the current double-execution / double-error-handling between
  `MultiQS` and the handler.

### Non-Goals (explicitly out of scope)
- Changing the behavior of non-Output pipeline steps (sources, Transform,
  Filter, GroupBy) — their error handling already raises.
- Adding retry / fallback-on-failure logic for destinations.
- Redesigning the destination registry or the `TableOutput` write internals.
- Aggregate-and-report semantics across multiple destinations (rejected in
  favour of fail-fast for this iteration; may revisit later).

---

## 2. Architectural Design

### Overview

Remove the error-swallowing `try/except` around destination execution in
**both** locations and consolidate Output execution into **one** authoritative
path. When a destination raises, wrap it (if not already) in an `OutputError`
that carries `step_name` + underlying detail, and let it propagate:

- `MultiQS.query()` → raises → handler's existing
  `except (QueryException, DriverError)` (multi.py:267) catches it →
  `self.Error(...)` returns a non-2xx HTTP response with the message in the
  body.
- The HTTP status is chosen by classifying the underlying exception:
  data/validation → 422/400, infrastructure → 500.
- The `X-Output-Errors` header is **retained** as supplementary detail, but is
  no longer the *only* signal — the status code and body now reflect the
  failure.

Fail-fast: the destination loop stops and raises on the first failure; no
subsequent destinations run.

### Component Diagram
```
navigator-front-next (HTTP client)
        │  (sees non-2xx + error body)
        ▼
QueryHandler.query()  (handlers/multi.py)
   maps QueryException/OutputError ──► self.Error(code=4xx|5xx)
        ▲  raises (no longer swallowed)
        │
MultiQS.query()  (queries/multi/__init__.py)   ← single authoritative Output loop
        │  runs destination, on failure: raise OutputError(step_name, detail)
        ▼
get_destination(step) ──► TableOutputAdapter ──► TableOutput.run()
                                                     │ raises OutputError
                                                     ▼
                                              PgOutput._execute (postgres.py)
```

### Integration Points

> How does this feature integrate with existing QuerySource components?

| Existing Component | Integration Type | Notes |
|---|---|---|
| `MultiQS.query()` (`queries/multi/__init__.py:443,531-546`) | modifies | Remove swallow at 531-546; raise `OutputError` with step name. This becomes the single authoritative Output executor. |
| `QueryHandler.query()` (`handlers/multi.py:368-417`) | modifies | Remove the duplicate Output loop + `output_errors` swallow; rely on `MultiQS` raising. Keep `X-Output-Errors` header populated from the raised error's detail. |
| `QueryHandler.Error` / `Except` (`handlers/abstract.py:128,197`) | uses | Existing error responders; map OutputError → differentiated status. |
| `get_destination` / `DESTINATION_REGISTRY` (`outputs/destinations/__init__.py:196-236`) | uses (unchanged) | Dispatch is unchanged; only error handling around `.run()` changes. |
| `OutputError` (`exceptions.py:74`) | uses / extends | Carrier for destination failures; already a `QueryException` subclass so the handler maps it. May add `step_name` / `category` attributes. |
| `TableOutput` / `PgOutput` (`outputs/tables/TableOutput/{table,postgres}.py`) | uses (unchanged) | Already raise `OutputError` internally (`postgres.py:_execute`). |

### Data Models
```python
# Proposed enrichment of the existing OutputError (exceptions.py:74)
# to carry structured context for the API layer. Backwards compatible:
# existing raises with a plain message keep working.
class OutputError(QueryException):
    def __init__(
        self,
        message: str,
        *,
        step_name: str | None = None,      # e.g. "TableOutput"
        category: str | None = None,       # "data" | "infra" (drives HTTP status)
        **kwargs,
    ):
        ...
```

### New Public Interfaces
```python
# No new public classes. Behavioral change to an existing method:
# MultiQS.query() now RAISES OutputError (QueryException subclass) on the
# first Output destination failure instead of returning a success result.
```

---

## 3. Module Breakdown

> Define the discrete modules that will be implemented.

### Module 1: OutputError enrichment
- **Path**: `querysource/exceptions.py`
- **Responsibility**: Extend `OutputError` to optionally carry `step_name` and
  `category` ("data" | "infra") so the handler can pick an HTTP status. Keep
  the plain `OutputError(message)` call sites working (backwards compatible).
- **Depends on**: existing `QueryException`.

### Module 2: MultiQS authoritative Output executor
- **Path**: `querysource/queries/multi/__init__.py`
- **Responsibility**: Replace the swallow at lines 531-546 with fail-fast:
  on the first destination failure, wrap in `OutputError(step_name=...)` and
  raise. This becomes the single place Output runs.
- **Depends on**: Module 1.

### Module 3: Handler consolidation + status mapping
- **Path**: `querysource/handlers/multi.py`
- **Responsibility**: Remove the duplicate Output loop / `output_errors`
  swallow (lines 368-417 region). Rely on `MultiQS` raising. Map
  `OutputError.category` → HTTP status (data → 422/400, infra → 500) via
  `self.Error`. Keep populating the `X-Output-Errors` header from the raised
  error for supplementary detail.
- **Depends on**: Module 1, Module 2.

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_multiqs_output_failure_raises` | Module 2 | A destination that raises causes `MultiQS.query()` to raise `OutputError` (not return success). |
| `test_multiqs_output_failure_is_fail_fast` | Module 2 | With two Output destinations, a failure in the first prevents the second from running. |
| `test_output_error_carries_step_name` | Module 1/2 | The raised `OutputError` includes the failing step name (e.g. `TableOutput`). |
| `test_output_error_category_data_vs_infra` | Module 1 | `category` classification drives the expected HTTP status. |

### Integration Tests
| Test | Description |
|---|---|
| `test_tableoutput_pk_collision_returns_4xx` | A MultiQuery whose `TableOutput` hits a duplicate-key/PK collision returns a non-2xx response with the error in the body (not a 200 + header). |
| `test_tableoutput_missing_column_returns_4xx` | `Unconsumed column names` surfaces as a 4xx with detail. |
| `test_infra_error_returns_500` | A connection/timeout error from the destination returns 500. |
| `test_successful_output_still_200` | A healthy MultiQuery + Output still returns 200 (no regression). |

### Test Data / Fixtures
```python
@pytest.fixture
def failing_destination(monkeypatch):
    # A fake destination whose .run() raises OutputError, registered in
    # DESTINATION_REGISTRY under a test step name.
    ...
```

---

## 5. Acceptance Criteria

> This feature is complete when ALL of the following are true:

- [ ] When any Output destination fails, `MultiQS.query()` **raises** (verified: it no longer returns a success result).
- [ ] The HTTP API returns a **non-2xx** response for a failed Output, with the failing step name and underlying error detail in the response **body**.
- [ ] Fail-fast: given multiple Output destinations, a failure in destination N prevents destinations N+1… from running.
- [ ] Data/validation errors (PK collision, missing column, numeric overflow, cast) map to `4xx` (422/400); infrastructure errors map to `500`.
- [ ] Output is executed in exactly **one** authoritative path (no double write, verified against `handlers/multi.py` and `queries/multi/__init__.py`).
- [ ] A successful MultiQuery + Output still returns `200` (no regression) — verified with an existing passing pipeline.
- [ ] The `X-Output-Errors` header is still populated for supplementary detail.
- [ ] All unit tests pass (`pytest tests/unit/ -v`).
- [ ] All integration tests pass (`pytest tests/integration/ -v`).

---

## 6. Codebase Contract

> **CRITICAL — Anti-Hallucination Anchor**

### Verified Imports
```python
from querysource.queries import MultiQS                      # verified: querysource/handlers/multi.py:14
from querysource.exceptions import (                          # verified: querysource/exceptions.py
    QueryException,   # line 6
    DriverError,      # line 58
    DataNotFound,     # line 48
    OutputError,      # line 74  (subclass of QueryException)
)
from querysource.outputs.destinations import get_destination  # verified: querysource/outputs/destinations/__init__.py
```

### Existing Class Signatures
```python
# querysource/queries/multi/__init__.py
class MultiQS(BaseQuery):
    async def query(self):                       # Output loop at 531-546; _output popped at 443
        # Step 5 (531-546): for step in _output: for step_name, component in step.items():
        #   try: destination_cls = get_destination(step_name)
        #        obj = destination_cls(data=result, **component); result = await obj.run()
        #   except Exception as dest_err: logging.error(...); # <-- SWALLOWED, continues

# querysource/handlers/multi.py
class QueryHandler(AbstractHandler):
    async def query(self, request) -> web.StreamResponse:     # line 109
        # calls: result, options = await qs.query()           # line 232
        # except (QueryException, DriverError) as qe: raise self.Error(...)   # 267-277
        # except Exception as ex: raise self.Except(...)      # 283-293
        # Step 5 duplicate Output loop:                       # 368-384
        #   output_errors: list[str] = []                     # 368
        #   except Exception as dest_err: output_errors.append(...)   # 378-384  <-- SWALLOWED
        #   response.headers['X-Output-Errors'] = ...         # 415-416 (only signal today)

# querysource/handlers/abstract.py
class AbstractHandler:
    def NoData(self, ...):   # line 83
    def Error(self, message, *, exception=None, code=..., ...):   # line 128
    def Except(self, message, *, exception=None, ...):            # line 197

# querysource/exceptions.py
class OutputError(QueryException):   # line 74

# querysource/outputs/tables/TableOutput/postgres.py
class PgOutput:
    def _execute(self, stmt, conn, tablename):   # raises OutputError on ProgrammingError/OperationalError/StatementError
```

### Integration Points
| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| MultiQS Output loop (raise) | `QueryHandler.query` error mapping | raised `OutputError`→`except (QueryException, DriverError)` | `handlers/multi.py:267` |
| `OutputError.category` | `AbstractHandler.Error(code=...)` | HTTP status selection | `handlers/abstract.py:128` |
| destination `.run()` | `get_destination` | registry dispatch (unchanged) | `outputs/destinations/__init__.py:196` |

### Does NOT Exist (Anti-Hallucination)
- ~~`MultiQS.raise_output_errors`~~ — no such flag/attribute today.
- ~~`OutputError.step_name` / `OutputError.category`~~ — do NOT exist yet; Module 1 adds them.
- ~~a shared Output executor utility~~ — Output is currently duplicated inline in `MultiQS.query()` and `QueryHandler.query()`; no shared function exists.

---

## 7. Implementation Notes & Constraints

### Patterns to Follow
- Raise typed `OutputError` (a `QueryException`) so the existing handler mapping
  at `handlers/multi.py:267` catches it — do not invent new exception types.
- Preserve the `raise ... from err` chaining already used in
  `PgOutput._execute` (`postgres.py`).
- Use `self.logger` for logging; keep the log line but ADD the raise.

### Known Risks / Gotchas
- **Double execution**: Output currently appears to run in BOTH `MultiQS.query()`
  and `QueryHandler.query()`. Before removing either, confirm which path
  actually writes in production (the handler reads `options['Output']`; MultiQS
  pops `Output` from its own `self._options`). Consolidating must ensure Output
  runs **exactly once** — a naive change could double-write or skip writing.
- **Status classification**: mapping specific DB errors to data-vs-infra needs a
  small, explicit classifier (e.g. inspect the underlying `sqlalchemy` /
  `asyncpg` error). Default to 500 when unsure, to avoid masking infra issues.
- **Backwards compatibility**: any consumer relying on the old "200 + swallow"
  behavior will now receive a non-2xx. This is intentional, but must be called
  out in release notes for `navigator-front-next`.

### External Dependencies
| Package | Version | Reason |
|---|---|---|
| (none new) | — | Uses existing pandas / sqlalchemy / aiohttp stack. |

---

## 8. Open Questions

- [x] Multi-destination failure semantics — *Resolved*: **fail-fast** (stop at first failing destination and raise immediately).
- [x] HTTP status for destination failures — *Resolved*: **differentiate** — data/validation → 4xx (422/400), infrastructure → 500.
- [x] Keep "continue on failure" behavior? — *Resolved*: **no** — always raise; retain `X-Output-Errors` header only as supplementary detail.
- [x] Two Output execution paths — *Resolved*: **consolidate to a single authoritative path**; verify at implementation which path is the live one.
- [ ] Exact 4xx code for data errors: `400` vs `422`? — *Owner: Juan* (decide during implementation; lean `422` for validation-type failures).
- [ ] Precise data-vs-infra classifier: which underlying exception types map to which category? — *Owner: Juan*.

---

## Worktree Strategy

- **Default isolation unit**: `per-spec` (sequential tasks).
- Modules 1→2→3 have a strict dependency chain (Module 3 depends on 2 depends on
  1), so they run **sequentially in one worktree**; no parallelizable tasks.
- **Cross-feature dependencies**: none. Independent of other in-flight specs.
- Base branch: `dev` (feature). Suggested branch:
  `feat-FEAT-146-multiquery-output-error-propagation`.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-08-06 | Juan | Initial draft |
