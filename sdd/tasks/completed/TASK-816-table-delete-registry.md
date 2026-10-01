# TASK-816: Register the `TableDelete` step in DESTINATION_REGISTRY

**Feature**: FEAT-155 — MultiQuery TableDelete Destination
**Spec**: `sdd/specs/multi-tabledelete.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-815
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 2. The MultiQS Output loop resolves each step with
`get_destination(step_name)` (`querysource/queries/multi/__init__.py:846`). That
lookup reads the legacy `DESTINATION_REGISTRY` in
`querysource/outputs/destinations/__init__.py`, which is keyed by step name. The
folder-scanned registry in `querysource/queries/multi/destinations/__init__.py` is
keyed by class name. So `"TableDelete"` must be registered explicitly, in the same
`try/except ImportError` style the `"Table"` entry uses.

---

## Scope

- Add one `try/except ImportError` block that registers
  `DESTINATION_REGISTRY["TableDelete"] = TableDeleteDestination`, directly after the `"Table"` block.
- Add `tests/test_destination_table_delete_registry.py::test_registry_has_tabledelete`.

**NOT in scope**: the destination class itself (TASK-815) and the PBAC gate (TASK-817).
Do not touch `__all__` or `get_destination`.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/outputs/destinations/__init__.py` | MODIFY | register `"TableDelete"` after the `"Table"` block |
| `tests/test_destination_table_delete_registry.py` | CREATE | `test_registry_has_tabledelete` |

---

## Codebase Contract (Anti-Hallucination)

Re-verified against HEAD `f8a32ae`.

### Verified Imports
```python
from querysource.queries.multi.destinations.table_delete import TableDeleteDestination  # created by TASK-815
from querysource.outputs.destinations import DESTINATION_REGISTRY, get_destination       # verified: querysource/outputs/destinations/__init__.py:196, :236
```

### Existing Signatures to Use
```python
# querysource/outputs/destinations/__init__.py
_pkg_logger = _logging.getLogger(__name__)                                   # line 21
DESTINATION_REGISTRY: dict[str, type[AbstractDestination]] = {...}           # line 196
# "Table" block, lines 219-225:
try:
    from querysource.queries.multi.destinations.table import TableDestination
    DESTINATION_REGISTRY["Table"] = TableDestination
except ImportError:
    _pkg_logger.debug(
        "Table destination not available"
    )
# "DWH" block follows at lines 227-233
def get_destination(step_name: str) -> type[AbstractDestination]:            # line 236 — raises OutputError on unknown step
```

### Does NOT Exist
- ~~`DESTINATION_REGISTRY["TableDelete"]`~~ — added by this task.
- ~~`flowtask.components.TableDelete`~~ — never import it.
- ~~Automatic step-name registration from the folder scan~~ — `querysource/queries/multi/destinations/__init__.py` keys by class name (`TableDeleteDestination`), not by step name.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/outputs/destinations/__init__.py", "action": "MODIFY"},
    {"path": "tests/test_destination_table_delete_registry.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/outputs/destinations/__init__.py#DESTINATION_REGISTRY",
    "sym:querysource/outputs/destinations/__init__.py#get_destination"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Keep the `except ImportError` guard, because every other entry uses it and a missing optional dependency must not break the package import.
- Insert after the `"Table"` block and before the `"DWH"` block. FEAT-156 (`multi-executesql`) edits this same region later, so keep the diff to exactly one block.

---

## Implementation Blueprint

### Steps (in order)
1. Insert the registration block after the `"Table"` block. *Why*: `get_destination("TableDelete")` must resolve before the Output loop can dispatch the step.
2. Create the registry test. *Why*: it pins the step-name-to-class mapping (spec §4 M2).
3. Run the Validation Commands.

### `querysource/outputs/destinations/__init__.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '    DESTINATION_REGISTRY\["Table"\] = TableDestination' querysource/outputs/destinations/__init__.py)
# occurrences: 1 (verified: grep -c '        "Table destination not available"' querysource/outputs/destinations/__init__.py)
# AFTER — insert below the closing `    )` of the block containing
#   `    DESTINATION_REGISTRY["Table"] = TableDestination` (verified: querysource/outputs/destinations/__init__.py:221)
#   `        "Table destination not available"`         (verified: querysource/outputs/destinations/__init__.py:224; block ends :225)
# keep one blank line before and after, like the neighbouring blocks

try:
    from querysource.queries.multi.destinations.table_delete import TableDeleteDestination
    DESTINATION_REGISTRY["TableDelete"] = TableDeleteDestination
except ImportError:
    _pkg_logger.debug(
        "TableDelete destination not available"
    )
```
**Why**: this matches the spec §3 M2 skeleton verbatim. The anchor is unique (count 1). The spec's §6 table cites `:221` for the `DESTINATION_REGISTRY["Table"]` line, which is still correct. The insertion point is after line 225.

### `tests/test_destination_table_delete_registry.py` (CREATE)
```python
"""FEAT-155 / TASK-816: the ``TableDelete`` step name resolves to TableDeleteDestination."""
from querysource.outputs.destinations import DESTINATION_REGISTRY, get_destination
from querysource.queries.multi.destinations.table_delete import TableDeleteDestination


def test_registry_has_tabledelete():
    # FILL IN: assert get_destination("TableDelete") is TableDeleteDestination and DESTINATION_REGISTRY["TableDelete"] is TableDeleteDestination
    ...
```

### FILL IN checklist
- [ ] `tests/test_destination_table_delete_registry.py::test_registry_has_tabledelete`: both identity asserts.

---

## Acceptance Criteria

- [ ] `get_destination("TableDelete") is TableDeleteDestination`.
- [ ] Existing registry and dispatch tests still pass; `"Table"`, `"DWH"`, `"ToS3"`, `"ToSharepoint"` and `"TableOutput"` are unchanged.
- [ ] `ruff check querysource/outputs/destinations/__init__.py` is clean.

## Validation Commands

- `pytest tests/test_destination_table_delete_registry.py -q`
- `pytest tests/test_multi_destinations_subpackage.py -q`
- `pytest tests/test_multiqs_destination_dispatch.py -q`
- `pytest tests/test_destinations_documentation_endpoint.py -q`

---

## Test Specification

```python
# tests/test_destination_table_delete_registry.py
def test_registry_has_tabledelete():
    assert get_destination("TableDelete") is TableDeleteDestination
```

---

## Agent Instructions

When you pick up this task:

1. **Work in the feature worktree**, never on `base_branch`
   (`python -m scripts.sdd.ensure_worktree --slug multi-tabledelete --feature-id FEAT-155`).
2. **Read the spec** at the path listed above for full context.
3. **Check dependencies**: TASK-815 must be `"done"` in `sdd/tasks/index/multi-tabledelete.json`.
4. **Verify the Codebase Contract**: re-run the two `grep -c` anchors. If a count is not 1, re-locate the anchor before editing.
5. **Update status** in `sdd/tasks/index/multi-tabledelete.json` to `"in-progress"` (set `started_at`) and commit only that index file.
6. **Implement** from the blueprint and complete every `# FILL IN:`.
7. **Verify** by running the Validation Commands.
8. **Commit the code**, staging only the files this task lists (never `git add .` / `-A`).
9. **Close the task** with `scripts/sdd/close_task.sh TASK-816 multi-tabledelete verified`.
10. **Fill in the Completion Note** below, then commit the staged SDD state.

---

## Completion Note

Registered TableDelete in DESTINATION_REGISTRY; registry test passes.

**Completed by**: sdd-worker (fallback sequential)
**Date**: 2026-09-30

**Deviations from spec**: none
