# TASK-673: End-to-End Acceptance Validation

**Feature**: FEAT-097 — New Destination Folder for MultiQuery
**Spec**: `sdd/specs/new-destination-multiquery.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-672
**Assigned-to**: unassigned

---

## Context

Final validation pass for FEAT-097. The structural refactor is complete; this task wires together the proof points that the spec's §5 Acceptance Criteria are satisfied — particularly the integration with the FEAT-095 documentation endpoint, which must now expose populated JSON schemas for every destination.

---

## Scope

- Add an integration test that calls the FEAT-095 documentation endpoint and asserts that every destination in the `"Destinations"` category has a non-empty `json_schema.properties` (except `tableOutput`/`TableOutput` — the `TableOutputAdapter` is explicitly allowed to have empty schema per spec).
- Add (or update) a `MultiQS` pipeline test that runs an end-to-end query ending in a destination dispatched through `get_destination()`, confirming the migrated classes are wired correctly via the layered registry.
- Run the full project test suite. Fix any regression introduced by FEAT-097 (no new behaviour — only structural).
- Run `ruff check .` and `mypy querysource/` over the touched files. Fix any new lint/type errors introduced by FEAT-097.
- Verify backward-compat invariants for downstream consumers (Flowtask-style imports) by running a small script that imports every public symbol from `outputs/destinations/`.

**NOT in scope**:
- Documentation rewrites — the spec is the documentation source of truth for this change.
- Performance benchmarks.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/test_destinations_documentation_endpoint.py` | CREATE | Integration test against FEAT-095 docs endpoint asserting populated schemas |
| `tests/test_multiqs_destination_dispatch.py` | CREATE (or extend if a similar file exists) | End-to-end MultiQS test exercising `get_destination` with a migrated destination class (mocked external IO) |
| `tests/test_multi_destinations_subpackage.py` | MODIFY | Append a final invariant test consolidating identity/category guarantees |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# FEAT-095 documentation endpoint — verify route via the handlers package
# verified pattern in spec §6: API path is /api/v3/qs/components

# verified: querysource/queries/multi/__init__.py:18
from querysource.outputs.destinations import get_destination
# verified: querysource/queries/multi/registry.py:64
from querysource.queries.multi.registry import ComponentRegistry
```

### Existing FEAT-095 Endpoint Location

```bash
# Confirm where the components endpoint is mounted — handlers/multi.py is a likely host.
grep -rn "api/v3/qs/components\|components_handler" querysource/handlers/ querysource/api/
```

The integration test should:
- Either call the endpoint via `aiohttp.test_utils` (preferred), or
- Call the handler function directly with a mock request, or
- Call `ComponentRegistry.get_catalog()` and serialize manually — this last option is acceptable as a fallback if the HTTP wiring is fiddly to bootstrap in test (it covers the same data).

### Existing test fixtures to reference
- `tests/test_component_registry.py` — pattern for clearing `discover_all.cache_clear()`.
- `tests/test_destination_integration.py` — pattern for mocking external IO (SharePoint/S3) so the destination tests don't actually hit the network.

### Does NOT Exist
- ~~A dedicated `tests/integration/test_feat097.py`~~ — tests for FEAT-097 are co-located with their domain (`test_destinations_*`, `test_component_registry.py`). Don't invent an `integration/` subdirectory.
- ~~`ComponentRegistry.get_destinations_only()`~~ — no such helper. Filter `get_catalog()` by `category == "Destinations"`.

---

## Implementation Notes

### Documentation-endpoint test pattern

```python
# tests/test_destinations_documentation_endpoint.py
"""FEAT-097 acceptance — destinations must expose populated JSON schemas
through the FEAT-095 documentation endpoint."""
import pytest


def test_every_real_destination_has_populated_schema():
    from querysource.queries.multi.registry import ComponentRegistry
    ComponentRegistry.discover_all.cache_clear()

    catalog = ComponentRegistry.get_catalog()
    destinations = {ci.name: ci for ci in catalog if ci.category == "Destinations"}

    # Adapter wrapping the legacy TableOutput is exempt — wraps a non-introspectable class
    adapter_exempt_names = {"tableOutput", "TableOutput"}

    failures = []
    for name, ci in destinations.items():
        if name in adapter_exempt_names:
            continue
        props = ci.json_schema.get("properties") or {}
        if not props:
            failures.append(name)
    assert not failures, (
        f"Destinations with empty json_schema.properties: {failures}. "
        "Every destination except TableOutputAdapter must produce a populated schema."
    )


def test_destinations_category_in_catalog():
    from querysource.queries.multi.registry import ComponentRegistry
    ComponentRegistry.discover_all.cache_clear()
    categories = {ci.category for ci in ComponentRegistry.get_catalog()}
    assert "Destinations" in categories
```

### End-to-end MultiQS dispatch test pattern

If the agent finds an existing `tests/test_multiqs_*.py` for end-to-end dispatch tests, extend it. Otherwise, create a small new file:

```python
# tests/test_multiqs_destination_dispatch.py
"""FEAT-097 — verify get_destination dispatches to migrated classes."""
import pytest
from unittest.mock import patch


def test_get_destination_returns_migrated_class():
    from querysource.outputs.destinations import get_destination
    from querysource.queries.multi.destinations.sharepoint import ToSharepoint
    from querysource.queries.multi.destinations.s3 import ToS3
    from querysource.queries.multi.destinations.table import TableDestination
    from querysource.queries.multi.destinations.dwh import DWHDestination

    assert get_destination("ToSharepoint") is ToSharepoint
    assert get_destination("ToS3") is ToS3
    assert get_destination("Table") is TableDestination
    assert get_destination("DWH") is DWHDestination


def test_get_destination_table_output_still_routes_to_adapter():
    from querysource.outputs.destinations import get_destination, TableOutputAdapter
    assert get_destination("tableOutput") is TableOutputAdapter
    assert get_destination("TableOutput") is TableOutputAdapter
```

### Backward-compat smoke script

```python
# Run interactively or as part of the test session
import importlib
for path in (
    "querysource.outputs.destinations",
    "querysource.outputs.destinations.abstract",
    "querysource.outputs.destinations.sharepoint",
    "querysource.outputs.destinations.s3",
    "querysource.outputs.destinations.table",
    "querysource.outputs.destinations.dwh",
    "querysource.queries.multi.destinations",
    "querysource.queries.multi.destinations.sharepoint",
    "querysource.queries.multi.destinations.s3",
    "querysource.queries.multi.destinations.table",
    "querysource.queries.multi.destinations.dwh",
):
    importlib.import_module(path)
    print("OK:", path)
```

### Key Constraints

- Don't introduce real network calls in tests. Use mocks consistent with the existing FEAT-094 tests.
- Run lint+mypy AFTER all earlier tasks merged into the worktree's branch so the diff is final.
- If `mypy` reports pre-existing errors that this feature doesn't touch, document them in the Completion Note rather than fixing them (out of scope).

### References in Codebase
- `tests/test_destination_integration.py` — mock patterns for IO-heavy destinations.
- `tests/test_component_registry.py` — discovery-cache patterns.

---

## Acceptance Criteria

- [ ] Every spec §5 acceptance criterion is satisfied. Manually walk the list before closing.
- [ ] `pytest` — full suite passes (`pytest -v` from repo root). Document any pre-existing failures in the Completion Note (must be unchanged from baseline).
- [ ] `ruff check querysource/queries/multi/ querysource/outputs/destinations/ tests/test_destination_*.py tests/test_multi_destinations_subpackage.py tests/test_component_registry.py` — no errors introduced.
- [ ] `mypy querysource/queries/multi/ querysource/outputs/destinations/` — no NEW errors compared to the baseline on `dev` at the time of TASK-672 completion.
- [ ] New tests are present and passing:
  - `tests/test_destinations_documentation_endpoint.py`
  - `tests/test_multiqs_destination_dispatch.py`
- [ ] The backward-compat smoke script (above) prints `OK:` for every path.
- [ ] No code change in `querysource/queries/multi/__init__.py` (`MultiQS.query()` is out of scope per spec §1 Non-Goals) — verify with `git diff dev -- querysource/queries/multi/__init__.py`.
- [ ] Update `sdd/specs/new-destination-multiquery.spec.md` status from `approved` to `done` if the project convention is to do so (check by looking at how previous closed specs handle this — e.g. `multiquery-destinations.spec.md`).

---

## Test Specification

(See Implementation Notes above — the two new test files contain the tests.)

---

## Agent Instructions

1. **Read the spec** at `sdd/specs/new-destination-multiquery.spec.md` (§5 Acceptance Criteria — the canonical checklist).
2. **Check dependencies** — TASK-672 in `sdd/tasks/completed/`.
3. **Verify the Codebase Contract** — confirm the catalog entries match expectations: `python -c "from querysource.queries.multi.registry import ComponentRegistry; ComponentRegistry.discover_all.cache_clear(); print(sorted({ci.name for ci in ComponentRegistry.get_catalog() if ci.category == 'Destinations'}))"`.
4. **Update status** in `sdd/tasks/index/new-destination-multiquery.json` → `"in-progress"`.
5. **Implement** — write the two new test files; run them; debug any issues with the prior tasks (regressions found here flow back into TASK-667-672, not into more new code).
6. **Verify** — full `pytest` + `ruff` + `mypy` gate.
7. **Move** this file to `sdd/tasks/completed/TASK-673-acceptance-validation.md`.
8. **Update index** → `"done"`. Set the feature's `completed_at` timestamp in `sdd/tasks/index/new-destination-multiquery.json`.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none | describe if any
