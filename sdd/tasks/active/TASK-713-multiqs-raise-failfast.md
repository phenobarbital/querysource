# TASK-713: MultiQS — raise Output errors (fail-fast) instead of swallowing

**Feature**: FEAT-146 — MultiQuery Output Error Propagation
**Spec**: `sdd/specs/multiquery-output-error-propagation.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-711
**Assigned-to**: unassigned

---

## Context

Implements spec §3 "Module 2". Today `MultiQS.query()` runs each Output
destination inside a `try/except Exception` that logs and **continues** — the
error is never re-raised (`queries/multi/__init__.py:531-546`). This task makes
it **fail-fast**: on the first destination failure, wrap the underlying error
in an enriched `OutputError(step_name=...)` and **raise**, so it propagates to
the handler.

---

## Scope

- Replace the swallow at `queries/multi/__init__.py:531-546` so that when a
  destination `.run()` raises, MultiQS:
  1. logs it (keep the existing log line), then
  2. raises `OutputError(<detail>, step_name=<step_name>, category=<data|infra>)`
     — **fail-fast**: do not continue to the remaining destinations.
- Preserve the underlying message text (`str(dest_err)`) inside the raised
  error so the detail survives to the client.
- If an `OutputError` is already raised by the destination, re-raise it
  (optionally annotating `step_name`) rather than double-wrapping.
- Category classification: a minimal best-effort mapping is acceptable here
  (default `category="data"` for known DB/data failures, else leave `None`);
  the precise classifier is an open question owned by Juan — keep it small and
  centralised so it can be refined later.
- Add unit tests using a fake destination.

**NOT in scope**: the handler's own duplicate Output loop and status mapping
(TASK-714); the 422/detail primitives (TASK-712).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/__init__.py` | MODIFY | Fail-fast raise in the Output loop (~531-546). |
| `tests/unit/test_multiqs_output_raise.py` | CREATE | Unit tests: raises on failure, fail-fast, carries step_name. |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.exceptions import OutputError, DataNotFound   # verified: exceptions.py:74, :48
from querysource.outputs.destinations import get_destination   # verified: outputs/destinations/__init__.py
```

### Existing Signatures to Use
```python
# querysource/queries/multi/__init__.py
class MultiQS(BaseQuery):
    async def query(self):
        # line 443: _output = self._options.pop('Output', None)
        # lines 531-546 (Step 5), CURRENT (to be replaced):
        #   for step in _output:
        #       for step_name, component in step.items():
        #           try:
        #               destination_cls = get_destination(step_name)
        #               obj = destination_cls(data=result, **component)
        #               result = await obj.run()
        #           except Exception as dest_err:
        #               logging.error("MultiQS: output destination '%s' failed: %s",
        #                             step_name, dest_err)
        #               # Per spec: continue to next destination on failure  <-- REMOVE

# querysource/outputs/destinations/__init__.py
def get_destination(step_name) -> type[AbstractDestination]:  # dispatch, unchanged
```

### Does NOT Exist
- ~~`MultiQS._raise_output`~~ / ~~a config flag to toggle raising~~ — decision is "always raise" (no flag).
- ~~a shared classify_output_error() helper~~ — none exists; if you add one keep it local/small.

---

## Implementation Notes

- Fail-fast means: the `raise` happens inside the loop on the first failure, so
  subsequent `step` iterations never run. Do not accumulate.
- Keep the `logging.error(...)` line (server-side visibility) and then raise.
- Suggested shape:
  ```python
  try:
      destination_cls = get_destination(step_name)
      obj = destination_cls(data=result, **component)
      result = await obj.run()
  except OutputError as oe:
      if getattr(oe, "step_name", None) is None:
          oe.step_name = step_name
      logging.error("MultiQS: output '%s' failed: %s", step_name, oe)
      raise
  except Exception as dest_err:
      logging.error("MultiQS: output '%s' failed: %s", step_name, dest_err)
      raise OutputError(str(dest_err), step_name=step_name, category="data") from dest_err
  ```
- `DataNotFound` (if a destination legitimately raises it) should keep its
  existing meaning — do not convert it into an OutputError; let it propagate.

### Key Constraints
- Async throughout; use module logging as today.
- Do not change destination dispatch or `TableOutput` internals.

---

## Acceptance Criteria

- [ ] A destination whose `.run()` raises causes `MultiQS.query()` to **raise `OutputError`** (it no longer returns a success result).
- [ ] Fail-fast: with two Output steps, a failure in the first prevents the second's `.run()` from being called.
- [ ] The raised `OutputError` carries `step_name` and preserves the underlying message text.
- [ ] A destination raising `DataNotFound` still propagates as `DataNotFound` (unchanged).
- [ ] `pytest tests/unit/test_multiqs_output_raise.py -v` passes; `ruff check querysource/queries/multi/__init__.py` clean.

---

## Test Specification

```python
# tests/unit/test_multiqs_output_raise.py
import pytest
from querysource.exceptions import OutputError

# Register a fake destination that raises, monkeypatch get_destination,
# build a minimal MultiQS with a DataFrame result + two Output steps,
# assert query() raises OutputError and the second destination never ran.
```

---

## Agent Instructions

Standard SDD flow. Verify TASK-711 completed. Re-verify lines 531-546 against
the live file (they may drift). Move to `completed/` + update the per-spec
index when done.

---

## Completion Note

**Completed by**:
**Date**:
**Notes**:
**Deviations from spec**: none
