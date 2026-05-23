# TASK-659: Output Dispatch Integration

**Feature**: FEAT-094 — MultiQuery New Destinations
**Spec**: `sdd/specs/multiquery-destinations.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-653
**Assigned-to**: unassigned

---

## Context

This task replaces the hardcoded `TableOutput` dispatch in both `MultiQS.query()` and `QueryHandler` with the new `DESTINATION_REGISTRY` lookup. Currently, lines 378-385 in `querysource/queries/multi/__init__.py` and lines 342-350 in `querysource/handlers/multi.py` only check for `('tableOutput', 'TableOutput')`. After this change, any registered destination name will be recognized and dispatched.

This is the integration glue that connects the destination registry (TASK-653) to the existing MultiQuery pipeline.

Implements spec §3 Module 7.

---

## Scope

- Modify `querysource/queries/multi/__init__.py` lines 378-385:
  - Replace `if step_name in ('tableOutput', 'TableOutput')` with `get_destination(step_name)` lookup
  - Import `get_destination` from `querysource.outputs.destinations`
  - Remove direct `TableOutput` import (it's now accessed through the registry)
- Modify `querysource/handlers/multi.py` lines 342-350:
  - Same registry-based dispatch replacement
  - Remove direct `TableOutput` import from line 15
- Ensure backward compatibility: `tableOutput` and `TableOutput` YAML keys still work (they're registered in TASK-653)

**NOT in scope**: Implementing any destination classes — those are separate tasks. Modifying the operator or transformation dispatch logic.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/__init__.py` | MODIFY | Replace hardcoded TableOutput dispatch with registry lookup (lines 378-385) |
| `querysource/handlers/multi.py` | MODIFY | Replace hardcoded TableOutput dispatch with registry lookup (lines 342-350) |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# New import to add
from querysource.outputs.destinations import get_destination  # created by TASK-653

# Existing import to REMOVE from multi/__init__.py
from querysource.outputs.tables import TableOutput  # line 17 in querysource/queries/multi/__init__.py — REMOVE THIS

# Existing import to REMOVE from handlers/multi.py
from ..outputs.tables import TableOutput  # line 15 in querysource/handlers/multi.py — REMOVE THIS

# Exceptions (keep)
from querysource.exceptions import OutputError  # verified: querysource/outputs/tables/TableOutput/table.py:9
```

### Existing Code to Replace

```python
# querysource/queries/multi/__init__.py — CURRENT (lines 378-385):
if _output:
    for step in _output:
        obj = None
        for step_name, component in step.items():
            if step_name in ('tableOutput', 'TableOutput'):
                obj = TableOutput(data=result, **component)
                result = await obj.run()

# querysource/handlers/multi.py — CURRENT (lines 342-350):
if isinstance(data, dict):
    if 'Output' in data:
        for step in options['Output']:
            obj = None
            for step_name, component in step.items():
                if step_name in ('tableOutput', 'TableOutput'):
                    obj = TableOutput(data=result, **component)
                    result = await obj.run()
```

### Target Code (Replace With)

```python
# querysource/queries/multi/__init__.py — NEW:
if _output:
    for step in _output:
        for step_name, component in step.items():
            destination_cls = get_destination(step_name)
            obj = destination_cls(data=result, **component)
            result = await obj.run()

# querysource/handlers/multi.py — NEW:
if isinstance(data, dict):
    if 'Output' in data:
        for step in options['Output']:
            for step_name, component in step.items():
                destination_cls = get_destination(step_name)
                obj = destination_cls(data=result, **component)
                result = await obj.run()
```

### Does NOT Exist
- ~~`get_output_module()`~~ — does not exist; use `get_destination()` from TASK-653
- ~~`DESTINATION_REGISTRY` in multi/__init__.py~~ — the registry lives in `querysource.outputs.destinations`, not in multi/

---

## Implementation Notes

### Key Constraints
- This is a minimal, surgical change — only the dispatch logic changes
- The `obj = None` assignment before the inner loop can be removed (it was only needed for the conditional pattern)
- If `get_destination()` raises `OutputError` for an unknown step name, it will propagate naturally — this is the desired behavior (fail fast on unknown destinations)
- Both files must be changed consistently — they have duplicated dispatch logic
- Verify the `TableOutput` import removal doesn't break other usages in the same file

### Pre-Change Verification
Before modifying, check if `TableOutput` is used anywhere else in the same file:
```bash
grep -n "TableOutput" querysource/queries/multi/__init__.py
grep -n "TableOutput" querysource/handlers/multi.py
```

If `TableOutput` is used elsewhere in the file (beyond the dispatch), keep the import but still add the `get_destination` import.

---

## Acceptance Criteria

- [ ] `querysource/queries/multi/__init__.py` uses `get_destination()` instead of hardcoded `TableOutput` check
- [ ] `querysource/handlers/multi.py` uses `get_destination()` instead of hardcoded `TableOutput` check
- [ ] Existing `tableOutput`/`TableOutput` YAML configs still work (backward compat via registry)
- [ ] New destination names (`ToSharepoint`, `ToS3`, `Table`, `DWH`) are dispatched correctly when registered
- [ ] Unknown destination names raise `OutputError`
- [ ] No linting errors: `ruff check querysource/queries/multi/__init__.py querysource/handlers/multi.py`

---

## Test Specification

No separate test file needed — this is verified by the integration tests in TASK-658 (`test_backward_compat_table_output`, `test_dispatch_registry_integration`). However, run existing MultiQuery tests to confirm no regressions:

```bash
pytest tests/ -v -k "multi" --no-header
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiquery-destinations.spec.md` for full context
2. **Check dependencies** — verify TASK-653 is in `sdd/tasks/completed/` (the registry must exist)
3. **Verify the Codebase Contract**:
   - `grep -n "TableOutput" querysource/queries/multi/__init__.py` — confirm current dispatch location
   - `grep -n "TableOutput" querysource/handlers/multi.py` — confirm current dispatch location
   - Confirm `get_destination` is importable from `querysource.outputs.destinations`
4. **Update status** in `sdd/tasks/index/multiquery-destinations.json` → `"in-progress"`
5. **Make the surgical edits** per the target code above
6. **Run existing tests** to verify no regressions
7. **Verify** all acceptance criteria are met
8. **Move this file** to `sdd/tasks/completed/TASK-659-output-dispatch-integration.md`
9. **Update index** → `"done"`
10. **Fill in the Completion Note** below

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any

---
**Completed by**: SDD Worker (Claude)
**Date**: 2026-05-20
**Notes**: Replaced hardcoded TableOutput dispatch in multi/__init__.py and handlers/multi.py with registry-based get_destination() lookup. Both files now use DESTINATION_REGISTRY for all destinations.
**Deviations from spec**: 3 pre-existing ruff warnings in the files (F841 unused variables) not introduced by this task — not fixed per no-scope-creep rule.
