# TASK-793: Lazy `querysource.outputs` package — `DataOutput` on first access

**Feature**: FEAT-154 — Lazy-import output writers
**Spec**: `sdd/specs/lazy-import-writers.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

`querysource/outputs/__init__.py:3` runs `from .output import DataOutput`. Because Python runs a
package's `__init__` before any submodule, the library path
`querysource/queries/base.py:14` → `from ..outputs.dt import OutputFactory` also executes
`output.py`, which loads the whole writer registry.

This task implements spec §3 **Module 4** (AC2, library mode). `DataOutput` is resolved via
PEP 562, so `import querysource.outputs.dt` no longer loads `querysource.outputs.output`.

---

## Scope

- Replace the eager `from .output import DataOutput` with a `__getattr__` that imports it on demand.
- Keep `__all__ = ('DataOutput', )`.
- Add `__dir__`.

**NOT in scope**: `output.py` (TASK-792), `writers/__init__.py` (TASK-791), tests (TASK-794),
and the eager handler imports in `querysource/handlers/__init__.py`. HTTP mode still loads `output.py`
through those imports, which spec AC2 accepts.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/outputs/__init__.py` | MODIFY | Lazy `DataOutput` via `__getattr__` / `__dir__` |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from .output import DataOutput   # verified: querysource/outputs/__init__.py:3 (moves inside __getattr__)
```

### Existing Signatures to Use
```python
# querysource/outputs/__init__.py (verified at HEAD 3c19da4) — whole file:
"""Supported Outputs for QuerySource.
"""
from .output import DataOutput     # line 3


__all__ = ('DataOutput', )         # line 6

# Pattern to copy — querysource/queries/multi/destinations/__init__.py:76-86 (__getattr__ + __dir__)
```

Callers that must keep working (`from ..outputs import DataOutput`):
`querysource/handlers/qsurl.py:12`, `querysource/handlers/service.py:24`, `querysource/handlers/multi.py:19`.

### Does NOT Exist
- ~~Other public names in `querysource.outputs`~~: `DataOutput` is the only export.
- ~~`querysource.outputs.WRITERS`~~: the registry is `querysource.outputs.output.WRITERS`, and it is not re-exported.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/outputs/__init__.py", "action": "MODIFY"}
  ],
  "contract_symbols": [
    "sym:querysource/outputs/output.py#DataOutput"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Cache the resolved class in `globals()` so later lookups skip `__getattr__`. This matches TASK-791.
- An unknown name raises `AttributeError(f"module {__name__!r} has no attribute {name!r}")`, the
  same message as the precedent.
- The module body must import nothing from `.output`.

---

## Implementation Blueprint

### Steps (in order)
1. Confirm the anchor: `grep -c 'from .output import DataOutput' querysource/outputs/__init__.py` → 1.
   *Why*: a 0 means the file has drifted. Stop and report.
2. Replace the whole file with the block below.
3. Run the smoke checks, the Validation Commands and `ruff check querysource/outputs/__init__.py`.
   The current file already fails `I001`, and the rewrite fixes that.

### `querysource/outputs/__init__.py` (MODIFY — full rewrite)
```python
"""Supported Outputs for QuerySource.

``DataOutput`` is resolved lazily (PEP 562) so importing a subpackage such as
``querysource.outputs.dt`` does not load ``output.py`` and its writer
registry (FEAT-154).
"""

__all__ = ('DataOutput', )


def __getattr__(name: str) -> type:
    """Import ``DataOutput`` from ``.output`` on first access.

    Args:
        name: attribute requested on the package.

    Returns:
        type: the ``DataOutput`` class.

    Raises:
        AttributeError: for any name other than ``DataOutput``.
    """
    if name == "DataOutput":
        from .output import DataOutput

        globals()["DataOutput"] = DataOutput
        return DataOutput
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Expose ``DataOutput`` to ``dir()``."""
    return sorted(__all__)
```
**Why this shape**: the function-local import is what breaks the chain from package to `output.py`.
Caching in `globals()` makes later lookups plain attribute reads.

### FILL IN checklist
- [ ] None. The block is complete. Verify it with the smoke checks.

---

## Acceptance Criteria

- [ ] `python -c "import sys, querysource.outputs.dt; assert 'querysource.outputs.output' not in sys.modules"` succeeds (AC2)
- [ ] `python -c "from querysource.outputs import DataOutput; print(DataOutput)"` succeeds
- [ ] `querysource.outputs.Nope` raises `AttributeError`
- [ ] `ruff check querysource/outputs/__init__.py` is clean
- [ ] Validation Commands pass

---

## Validation Commands

- `pytest tests/unit/test_handler_output_status.py -q`
- `pytest tests/qsurl/test_error_envelope.py -q`

---

## Test Specification

Covered by TASK-794 (`test_outputs_package_lazy_dataoutput`).

---

## Agent Instructions

1. Work in the feature worktree:
   `python -m scripts.sdd.ensure_worktree --slug lazy-import-writers --feature-id FEAT-154`
2. Read spec §3 M4.
3. No dependencies.
4. Verify the Codebase Contract.
5. Set the status to `"in-progress"` in `sdd/tasks/index/lazy-import-writers.json`.
6. Implement from the blueprint.
7. Run the Validation Commands, the smoke checks and ruff.
8. Commit only `querysource/outputs/__init__.py`.
9. Close with `scripts/sdd/close_task.sh TASK-793 lazy-import-writers verified`.
10. Fill in the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none
