# TASK-658: Destination Integration Tests

**Feature**: FEAT-094 — MultiQuery New Destinations
**Spec**: `sdd/specs/multiquery-destinations.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-653, TASK-654, TASK-655, TASK-656, TASK-657
**Assigned-to**: unassigned

---

## Context

This task creates integration tests that verify the full MultiQuery pipeline with destination components. While each destination has unit tests in its own task, this task tests the end-to-end flow: MultiQuery executes queries → applies operators/transforms → dispatches to destinations via the registry.

It also tests backward compatibility: existing YAML configs that use `tableOutput`/`TableOutput` must continue to work unchanged after the registry refactoring (TASK-659).

Implements spec §4 Integration Tests.

---

## Scope

- Create integration test file `tests/test_destination_integration.py`
- Test full MultiQuery → destination pipeline with mocked backends:
  - `test_multiqs_to_sharepoint_e2e` — pipeline → ToSharepoint (mocked Graph API)
  - `test_multiqs_to_s3_e2e` — pipeline → ToS3 (mocked S3)
  - `test_multiqs_table_pg_e2e` — pipeline → Table with PostgreSQL
  - `test_multiqs_dwh_bigquery_e2e` — pipeline → DWH BigQuery
  - `test_multiqs_multiple_destinations` — pipeline → Table + ToS3 chained
  - `test_backward_compat_table_output` — existing `tableOutput` YAML still works
- Test destination chaining: verify each destination returns original data so the next destination receives it

**NOT in scope**: Implementing any destination logic — only testing the integration. Actual backend connectivity (real SharePoint, real S3, real databases).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/test_destination_integration.py` | CREATE | Integration tests for full pipeline |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# All destination classes (from TASK-653 through TASK-657)
from querysource.outputs.destinations import DESTINATION_REGISTRY, get_destination
from querysource.outputs.destinations.abstract import AbstractDestination
from querysource.outputs.destinations.sharepoint import ToSharepoint
from querysource.outputs.destinations.s3 import ToS3
from querysource.outputs.destinations.table import TableDestination
from querysource.outputs.destinations.dwh import DWHDestination

# Existing MultiQuery
from querysource.queries.multi import MultiQS  # verified: querysource/queries/multi/__init__.py:53

# Existing TableOutput
from querysource.outputs.tables import TableOutput  # verified: querysource/outputs/tables/__init__.py:1
```

### Existing Signatures to Use
```python
# querysource/queries/multi/__init__.py:53
class MultiQS(BaseQuery):
    def __init__(self, slug=None, queries=None, files=None, query=None, conditions=None, request=None, loop=None, user_session=None, **kwargs):  # line 59
    async def query(self):  # line 105
        # Returns (result, self._options)
```

### Does NOT Exist
- ~~`MultiQS.add_destination()`~~ — no such method; destinations are configured via `Output` key in query dict
- ~~`AbstractDestination.pipeline()`~~ — no pipeline method; chaining is done in the dispatch loop

---

## Implementation Notes

### Key Constraints
- Use `unittest.mock` to mock all external backends (Graph API, S3, database connections)
- Focus on verifying the dispatch mechanism and data flow, not actual write operations
- Each test should verify that the destination's `run()` was called with the correct data
- Backward compatibility test must use the exact same YAML structure as existing configs

### References in Codebase
- `querysource/queries/multi/__init__.py:378-385` — output dispatch loop
- `querysource/handlers/multi.py:342-350` — handler output dispatch

---

## Acceptance Criteria

- [ ] Integration test file exists at `tests/test_destination_integration.py`
- [ ] All 6 integration test scenarios implemented
- [ ] Backward compatibility test confirms `tableOutput` YAML still works
- [ ] Multiple-destination chaining test verifies pass-through behavior
- [ ] All tests pass: `pytest tests/test_destination_integration.py -v`

---

## Test Specification

```python
# tests/test_destination_integration.py
import pytest
import pandas as pd
from unittest.mock import AsyncMock, patch, MagicMock

from querysource.outputs.destinations import DESTINATION_REGISTRY, get_destination


@pytest.fixture
def pipeline_result():
    return pd.DataFrame({
        "store_id": [1, 2, 3],
        "name": ["Store A", "Store B", "Store C"],
        "revenue": [100.0, 200.0, 300.0],
    })


class TestDestinationDispatch:
    def test_all_destinations_registered(self):
        expected = {"tableOutput", "TableOutput", "ToSharepoint", "ToS3", "Table", "DWH"}
        assert expected.issubset(set(DESTINATION_REGISTRY.keys()))

    @pytest.mark.asyncio
    async def test_backward_compat_table_output(self, pipeline_result):
        """Existing tableOutput YAML configs must still work."""
        cls = get_destination("tableOutput")
        dest = cls(data=pipeline_result, flavor="postgresql", tablename="test", schema="public")
        # Verify it wraps the original TableOutput
        assert dest is not None

    @pytest.mark.asyncio
    async def test_multiple_destinations_chain(self, pipeline_result):
        """Each destination returns original data for chaining."""
        output_steps = [
            {"Table": {"driver": "pg", "schema": "public", "table": "t", "method": "append"}},
            {"ToS3": {"credentials": {"bucket": "b", "aws_key": "k", "aws_secret": "s"}, "destination": {"file": "f.csv", "directory": "d/"}}},
        ]
        result = pipeline_result
        for step in output_steps:
            for step_name, component in step.items():
                cls = get_destination(step_name)
                dest = cls(data=result, **component)
                with patch.object(dest, "run", new_callable=AsyncMock, return_value=result):
                    result = await dest.run()
        # After chaining, result should still be the original DataFrame
        assert result is pipeline_result
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiquery-destinations.spec.md` for full context
2. **Check dependencies** — verify TASK-653 through TASK-657 are all in `sdd/tasks/completed/`
3. **Verify the Codebase Contract** — confirm all destination classes are importable
4. **Update status** in `sdd/tasks/index/multiquery-destinations.json` → `"in-progress"`
5. **Implement** following the scope and notes above
6. **Verify** all acceptance criteria are met
7. **Move this file** to `sdd/tasks/completed/TASK-658-destination-tests.md`
8. **Update index** → `"done"`
9. **Fill in the Completion Note** below

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
