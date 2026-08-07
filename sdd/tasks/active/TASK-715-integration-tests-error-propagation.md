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

**Completed by**:
**Date**:
**Notes**:
**Deviations from spec**: none
