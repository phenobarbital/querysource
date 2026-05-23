# TASK-652: Source Registry & __init__ Exports

**Feature**: FEAT-093 — MultiQuery New Sources
**Spec**: `sdd/specs/multiquery-new-sources.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-644, TASK-645, TASK-646, TASK-647, TASK-648, TASK-649, TASK-650
**Assigned-to**: unassigned

---

## Context

Implements Spec Module 9: Source Registry & __init__ Exports. Updates the
`querysource/queries/multi/sources/__init__.py` to export all source classes and
provide a `SOURCE_REGISTRY` dict that maps type names to their classes for dynamic
dispatch from `MultiQS`.

---

## Scope

- Update `querysource/queries/multi/sources/__init__.py` to:
  - Import all new source classes (SourceSharepoint, SourceSmartSheet, SourceS3, SourceTable).
  - Import the base class (ThreadSource).
  - Export all classes in `__all__`.
  - Create `SOURCE_REGISTRY` dict mapping string type names to classes.
- Handle optional dependency imports gracefully — if `msgraph-sdk` or `aioboto3` are not
  installed, the corresponding source class should still be importable (it fails at runtime
  when `fetch()` is called, not at import time).
- Write a simple test verifying the registry.

**NOT in scope**: Creating the source classes (earlier tasks), modifying MultiQS (TASK-651).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/sources/__init__.py` | MODIFY | Add imports, exports, SOURCE_REGISTRY |
| `tests/test_source_registry.py` | CREATE | Registry tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# Current __init__.py content (lines 1-7):
from .query import ThreadQuery
from .file import ThreadFile

__all__ = (
    "ThreadQuery",
    "ThreadFile"
)
```

### Files That Must Exist (created by earlier tasks)
```
querysource/queries/multi/sources/base.py        # TASK-644 — ThreadSource
querysource/queries/multi/sources/file.py         # TASK-645 — ThreadFile (refactored)
querysource/queries/multi/sources/query.py        # TASK-646 — ThreadQuery (refactored)
querysource/queries/multi/sources/sharepoint.py   # TASK-647 — SourceSharepoint
querysource/queries/multi/sources/smartsheet.py   # TASK-648 — SourceSmartSheet
querysource/queries/multi/sources/s3.py           # TASK-649 — SourceS3
querysource/queries/multi/sources/table.py        # TASK-650 — SourceTable
```

### Does NOT Exist
- ~~`SOURCE_REGISTRY`~~ — does not exist yet; this task creates it
- ~~`querysource.queries.multi.sources.registry`~~ — no separate registry module; the dict goes in __init__.py

---

## Implementation Notes

### Pattern to Follow

```python
# querysource/queries/multi/sources/__init__.py
from .base import ThreadSource
from .query import ThreadQuery
from .file import ThreadFile
from .sharepoint import SourceSharepoint
from .smartsheet import SourceSmartSheet
from .s3 import SourceS3
from .table import SourceTable

__all__ = (
    "ThreadSource",
    "ThreadQuery",
    "ThreadFile",
    "SourceSharepoint",
    "SourceSmartSheet",
    "SourceS3",
    "SourceTable",
    "SOURCE_REGISTRY",
)

SOURCE_REGISTRY = {
    "SourceSharepoint": SourceSharepoint,
    "SourceSmartSheet": SourceSmartSheet,
    "SourceS3": SourceS3,
    "SourceTable": SourceTable,
}
```

### Key Constraints
- All new source classes must be importable without their optional deps installed.
  The optional deps (msgraph-sdk, aioboto3) are imported lazily inside `fetch()`, not at module level.
- The registry keys must match exactly what users put in their YAML config.
- `ThreadSource`, `ThreadQuery`, and `ThreadFile` are exported but NOT in the registry — the registry is only for sources dispatched via the `sources` config key.

### References in Codebase
- `querysource/queries/multi/sources/__init__.py` — current exports to extend

---

## Acceptance Criteria

- [ ] All 7 classes are imported and exported in `__init__.py`
- [ ] `SOURCE_REGISTRY` maps 4 source type names to their classes
- [ ] `from querysource.queries.multi.sources import SOURCE_REGISTRY` works
- [ ] `from querysource.queries.multi.sources import SourceSharepoint` works (even without msgraph-sdk)
- [ ] Tests pass: `pytest tests/test_source_registry.py -v`
- [ ] No linting errors

---

## Test Specification

```python
# tests/test_source_registry.py
import pytest
from querysource.queries.multi.sources import (
    ThreadSource, ThreadQuery, ThreadFile,
    SourceSharepoint, SourceSmartSheet, SourceS3, SourceTable,
    SOURCE_REGISTRY
)


class TestSourceRegistry:
    def test_registry_contains_all_sources(self):
        assert "SourceSharepoint" in SOURCE_REGISTRY
        assert "SourceSmartSheet" in SOURCE_REGISTRY
        assert "SourceS3" in SOURCE_REGISTRY
        assert "SourceTable" in SOURCE_REGISTRY

    def test_registry_values_are_thread_source_subclasses(self):
        for name, cls in SOURCE_REGISTRY.items():
            assert issubclass(cls, ThreadSource), f"{name} is not a ThreadSource subclass"

    def test_registry_does_not_contain_thread_query_or_file(self):
        assert "ThreadQuery" not in SOURCE_REGISTRY
        assert "ThreadFile" not in SOURCE_REGISTRY

    def test_all_exports(self):
        from querysource.queries.multi.sources import __all__
        assert "ThreadSource" in __all__
        assert "ThreadQuery" in __all__
        assert "ThreadFile" in __all__
        assert "SOURCE_REGISTRY" in __all__
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiquery-new-sources.spec.md` for full context
2. **Check dependencies** — verify ALL prior tasks (TASK-644 through TASK-650) are completed
3. **Verify all source files exist** in `querysource/queries/multi/sources/`
4. **Update `__init__.py`** following the pattern above
5. **Verify** all acceptance criteria are met
6. **Move this file** to `sdd/tasks/completed/TASK-652-source-registry-exports.md`
7. **Update index** → `"done"`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
