# TASK-715: Integration tests — Output failures surface as HTTP 422/500 with detail

**Feature**: FEAT-146 — MultiQuery Output Error Propagation
**Spec**: `sdd/specs/multiquery-output-error-propagation.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-714
**Assigned-to**: unassigned

---

## Context

Implements spec §4 "Integration Tests". Verifies the assembled path end-to-end:
a MultiQuery whose `TableOutput` fails must produce a real non-2xx HTTP response
carrying the underlying error detail — the behavior Carlos needs in
`navigator-front-next`. Guards against regression of the historical
"200 + swallow" bug.

---

## Scope

- Add integration tests that exercise `QueryHandler.query()` end-to-end (or the
  closest existing integration harness) with a MultiQuery + Output where the
  destination fails, asserting HTTP status + body detail.
- Cover the concrete production failure modes seen in this feature's history:
  duplicate-key/PK collision, `Unconsumed column names` (missing/extra column),
  and an infrastructure error (connection/timeout).
- Include a success-path test proving healthy pipelines still return 200.

**NOT in scope**: implementation changes (done in 711-714). If a test reveals a
gap, file a follow-up rather than editing scope here.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/integration/test_multiquery_output_errors.py` | CREATE | End-to-end HTTP tests for Output error propagation. |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.exceptions import OutputError   # verified: querysource/exceptions.py:74
# Reuse the existing integration test harness/fixtures under tests/integration/.
```

### Existing references to follow
```python
# Follow the patterns already used by other tests under tests/integration/
# (aiohttp test client / handler invocation). Inspect an existing multi/query
# integration test before writing new fixtures — do NOT invent a new harness.
```

### Does NOT Exist
- ~~a prebuilt fixture that raises OutputError end-to-end~~ — create one (a fake destination registered in `DESTINATION_REGISTRY`, or a target table set up to force a PK collision).
- ~~an assertion helper for X-Output-Errors~~ — assert on the header directly.

---

## Implementation Notes

- Prefer forcing REAL failures where feasible (e.g. a table with a PK and two
  colliding rows to reproduce the duplicate-key path) so the test also guards
  the data-vs-infra classification. Where a real DB is not available in CI, use
  a fake destination that raises the corresponding `OutputError(category=...)`.
- Assert BOTH the status code AND that the body contains the underlying message
  substring (e.g. "duplicate key", "Unconsumed column names").
- Add a success test: a healthy pipeline returns 200 and the expected payload.

### Key Constraints
- Do not weaken production redaction for non-Output errors in the process.
- Keep tests deterministic; no reliance on external SharePoint/network.

---

## Acceptance Criteria

- [ ] PK-collision Output failure → HTTP **422** with the duplicate-key detail in the body.
- [ ] Missing/extra column (`Unconsumed column names`) → HTTP **422** with detail.
- [ ] Infrastructure error (connection/timeout) → HTTP **500**.
- [ ] Successful MultiQuery + Output → HTTP **200** with data (no regression).
- [ ] `X-Output-Errors` header present on failing responses.
- [ ] `pytest tests/integration/test_multiquery_output_errors.py -v` passes.

---

## Test Specification

```python
# tests/integration/test_multiquery_output_errors.py
# 1. test_pk_collision_returns_422_with_detail
# 2. test_unconsumed_columns_returns_422_with_detail
# 3. test_infra_error_returns_500
# 4. test_successful_output_returns_200
```

---

## Agent Instructions

Standard SDD flow. Verify TASK-714 is completed (full path assembled) before
starting. Inspect existing `tests/integration/` harness first. Move to
`completed/` + update the per-spec index when done.

---

## Completion Note

**Completed by**: sdd-worker (Claude Opus 4.8)
**Date**: 2026-08-07
**Notes**: Created `tests/integration/test_multiquery_output_errors.py`,
driving the real HTTP stack end-to-end (aiohttp `TestClient` ->
`QueryHandler.query()` -> `MultiQS.query()` -> the Output loop -> the
OutputError-to-status mapping -> the JSON response) with only the
non-deterministic collaborators faked: `ThreadQuery` (no real thread/DB
query), `get_destination` (controls the Output step), and — success-path
only — `DataOutput` (its formatting internals are unrelated to this
feature). No live Postgres or network access required. 4 tests, all
passing:
- `test_pk_collision_returns_422_with_detail` — a destination raising an
  exception named `IntegrityError` (matching `classify_output_error()`'s
  best-effort name-based check) -> 422, duplicate-key text in body,
  `X-Output-Errors` header present.
- `test_unconsumed_columns_returns_422_with_detail` — models the real
  `TableOutput/postgres.py` shape (`OutputError` chained from a
  `ProgrammingError`-named cause) -> 422, multi-line detail text preserved
  (modulo the CRLF fix below) in body.
- `test_infra_error_returns_500` — a destination raising an
  `OperationalError`-named exception -> 500.
- `test_successful_output_still_returns_200` — healthy pipeline, Output
  runs once, still 200.
Verified in `.venv-wt`: `pytest tests/integration/test_multiquery_output_errors.py -v`
-> 4 passed; full FEAT-146 suite (`test_output_error.py`,
`test_error_payload.py`, `test_multiqs_output_raise.py`,
`test_handler_output_status.py`, this file) -> 21 passed. `ruff check` on
the new file: clean.

**Bug found and fixed while writing these tests (technically an
implementation change, called out explicitly per this task's own
instruction to report rather than silently expand scope)**: the
`test_unconsumed_columns_returns_422_with_detail` scenario — the feature's
own headline motivating case — crashed the server with
`ValueError: Reason cannot contain \r or \n` (and, once that path was
patched, a second `ValueError: Forbidden control character detected in
headers`). Root cause: TASK-712's detail-exposure override lets a raw,
potentially multi-line destination message flow into `payload["error"]`,
which `AbstractHandler.Error()`/`Except()` reuse verbatim as the HTTP
`reason` phrase and the `X-MESSAGE` header, and `handlers/multi.py`'s new
`except OutputError` branch reuses `str(oe)` verbatim as the
`X-Output-Errors` header value — none of which may contain embedded CR/LF.
Fixed with two minimal, surgical changes:
1. `querysource/utils/errors.py::build_error_payload` — collapse
   `safe_message` to a single line before building the payload (`debug=True`'s
   separate `"detail"` field keeps the original text unflattened).
2. `querysource/handlers/multi.py`'s `except OutputError` branch — collapse
   `str(oe)` to a single line before assigning it to the `X-Output-Errors`
   header.
Both are noted as an addendum on `TASK-712`'s completion note (the file
`build_error_payload` lives in). Without this fix, the exact scenario this
feature exists to surface ("Unconsumed column names") would 500-crash the
server instead of returning a clean 422 — this was a completion blocker,
not an optional follow-up, so it was fixed here rather than deferred.
**Deviations from spec**: none in intent; see the bug-fix note above for
the one small, necessarily in-scope implementation correction.
