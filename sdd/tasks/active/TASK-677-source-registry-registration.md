# TASK-677: Register AirtableSource in SOURCE_REGISTRY and `__all__`

**Feature**: FEAT-096 — Multi-Query ThreadSource: Airtable
**Spec**: `sdd/specs/multi-threadsource-airtable.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-676
**Assigned-to**: unassigned

---

## Context

`MultiQS` dispatches sources by name through the `SOURCE_REGISTRY` dict in `querysource/queries/multi/sources/__init__.py`. Without this entry, `AirtableSource` exists but is unreachable from YAML/JSON pipeline definitions. This task is intentionally small and isolated — mirrors `TASK-652` (FEAT-093, commit `68bdb2b`) which did the same registration for the FEAT-093 batch.

Implements §3 Module 3 of the spec.

---

## Scope

Modify `querysource/queries/multi/sources/__init__.py`:

1. Add the import: `from .airtable import AirtableSource`.
2. Add `"AirtableSource"` to the `__all__` list.
3. Add `"AirtableSource": AirtableSource,` to the `SOURCE_REGISTRY` dict.

That is the entire change — three single-line edits in one file.

**NOT in scope**:
- Re-exporting from `querysource/queries/multi/__init__.py` — not the convention; sources are imported through the dict.
- Updating documentation — that is `TASK-681`.
- Changing the `SOURCE_REGISTRY` schema or extending the dispatcher in `MultiQS` — out of scope.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/sources/__init__.py` | MODIFY | Add import + `__all__` entry + `SOURCE_REGISTRY` entry |
| `tests/multi/sources/test_registry.py` | CREATE-OR-EXTEND | Add a test asserting `SOURCE_REGISTRY["AirtableSource"]` resolves to the class |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
# Current state of querysource/queries/multi/sources/__init__.py (verified):
from .base import ThreadSource
from .file import FileSource
from .query import ThreadQuery
from .s3 import S3Source
from .sharepoint import SharepointSource
from .smartsheet import SmartSheetSource
from .table import TableSource

# To be added in this task:
from .airtable import AirtableSource    # created by TASK-676
```

### Existing Signatures to Use

```python
# querysource/queries/multi/sources/__init__.py (verified — entire file):
from .base import ThreadSource
from .file import FileSource
from .query import ThreadQuery
from .s3 import S3Source
from .sharepoint import SharepointSource
from .smartsheet import SmartSheetSource
from .table import TableSource

__all__ = [
    "ThreadSource",
    "ThreadQuery",
    "FileSource",
    "SharepointSource",
    "SmartSheetSource",
    "S3Source",
    "TableSource",
    "SOURCE_REGISTRY",
]

#: Registry mapping source type names (as used in YAML config) to their classes.
#: Used by :class:`~querysource.queries.multi.MultiQS` for dynamic dispatch.
SOURCE_REGISTRY: dict = {
    "SharepointSource": SharepointSource,
    "SmartSheetSource": SmartSheetSource,
    "S3Source": S3Source,
    "TableSource": TableSource,
}
```

### Does NOT Exist

- ~~A `register_source(name, cls)` helper~~ — `SOURCE_REGISTRY` is mutated by hand-edit.
- ~~A side-effect-based auto-discovery (e.g. `entry_points`)~~ — explicit registration only.
- ~~`FileSource` or `ThreadQuery` entries in `SOURCE_REGISTRY`~~ — note these are exported via `__all__` but are NOT in the dispatch dict (they have their own special-case handling in `MultiQS`). Do not add `AirtableSource` to anything other than the existing registry dict.

---

## Implementation Notes

### Pattern to Follow

After this task the file should look like (note the alphabetized-ish but order-preserved style of the existing dict — match it):

```python
from .airtable import AirtableSource
from .base import ThreadSource
from .file import FileSource
from .query import ThreadQuery
from .s3 import S3Source
from .sharepoint import SharepointSource
from .smartsheet import SmartSheetSource
from .table import TableSource

__all__ = [
    "ThreadSource",
    "ThreadQuery",
    "FileSource",
    "AirtableSource",
    "SharepointSource",
    "SmartSheetSource",
    "S3Source",
    "TableSource",
    "SOURCE_REGISTRY",
]

SOURCE_REGISTRY: dict = {
    "AirtableSource": AirtableSource,
    "SharepointSource": SharepointSource,
    "SmartSheetSource": SmartSheetSource,
    "S3Source": S3Source,
    "TableSource": TableSource,
}
```

### Key Constraints

- Do not reorder existing entries unnecessarily; keep the diff minimal.
- The dict key string MUST be exactly `"AirtableSource"` (case-sensitive). YAML pipeline definitions will reference it by this string.

### References in Codebase

- `git log --oneline -- querysource/queries/multi/sources/__init__.py` — commit `68bdb2b` (`TASK-652`) is the precedent; this task is its analogue for FEAT-096.

---

## Acceptance Criteria

- [ ] `from querysource.queries.multi.sources import AirtableSource` works.
- [ ] `from querysource.queries.multi.sources import SOURCE_REGISTRY; SOURCE_REGISTRY["AirtableSource"]` resolves to the `AirtableSource` class.
- [ ] `"AirtableSource" in querysource.queries.multi.sources.__all__` is `True`.
- [ ] No existing entry in `SOURCE_REGISTRY` was removed or renamed.
- [ ] `pytest tests/multi/sources/test_registry.py -v` passes.
- [ ] `ruff check querysource/queries/multi/sources/__init__.py` passes.

---

## Test Specification

```python
# tests/multi/sources/test_registry.py
import pytest

from querysource.queries.multi.sources import (
    SOURCE_REGISTRY,
    AirtableSource,
    SharepointSource,
    SmartSheetSource,
    S3Source,
    TableSource,
)


class TestSourceRegistry:
    def test_airtable_registered(self):
        assert "AirtableSource" in SOURCE_REGISTRY
        assert SOURCE_REGISTRY["AirtableSource"] is AirtableSource

    def test_existing_sources_still_registered(self):
        # Regression: previously registered sources must still be present.
        assert SOURCE_REGISTRY["SharepointSource"] is SharepointSource
        assert SOURCE_REGISTRY["SmartSheetSource"] is SmartSheetSource
        assert SOURCE_REGISTRY["S3Source"] is S3Source
        assert SOURCE_REGISTRY["TableSource"] is TableSource
```

---

## Agent Instructions

1. Confirm `TASK-676` is `completed`.
2. Re-read `querysource/queries/multi/sources/__init__.py` to confirm its current state matches the Codebase Contract above.
3. Apply the three edits per Scope.
4. Run `pytest tests/multi/sources/test_registry.py -v`.
5. Move to `sdd/tasks/completed/` and update index.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:
**Deviations from spec**:
