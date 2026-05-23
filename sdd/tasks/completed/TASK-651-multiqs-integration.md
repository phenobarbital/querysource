# TASK-651: MultiQS Integration — Sources Dispatch

**Feature**: FEAT-093 — MultiQuery New Sources
**Spec**: `sdd/specs/multiquery-new-sources.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-644, TASK-645, TASK-646, TASK-652
**Assigned-to**: unassigned

---

## Context

Implements Spec Module 8: MultiQS Integration. Extends the `MultiQS.query()` method
to parse a new `sources` key from the options dict, instantiate the appropriate
`ThreadSource` subclass by type name using `SOURCE_REGISTRY`, and join them alongside
existing query/file threads.

---

## Scope

- Modify `MultiQS.__init__()` to extract `sources` from the query dict (alongside `queries` and `files`).
- Modify `MultiQS.query()` to create and start threads for each source entry using the
  `SOURCE_REGISTRY` dict from `querysource/queries/multi/sources/__init__.py`.
- Each source entry in the YAML has a key (the source type name, e.g., `SourceSharepoint`)
  and a value (the config dict).
- Source threads are joined and checked for exceptions in the same loop as query/file threads.
- Update the empty-check: `MultiQS` should not raise if at least one of `slug`, `queries`, `files`, or `sources` is non-empty.
- Write integration tests.

**NOT in scope**: Creating the individual source classes (TASK-644–650) or the registry (TASK-652).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/__init__.py` | MODIFY | Add sources parsing and dispatch |
| `tests/test_multiqs_sources_integration.py` | CREATE | Integration tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# Existing imports in __init__.py:
import asyncio                               # line 1
from typing import Optional                  # line 2
from aiohttp import web                      # line 3
from ...exceptions import (
    SlugNotFound, QueryException, DriverError,
    DataNotFound, ParserError
)                                             # lines 4-10
from importlib import import_module          # line 11
from ..base import BaseQuery                  # line 12
from .transformations import GoogleMaps       # lines 13-15
from .operators.filter import Filter          # line 16
from ...outputs.tables import TableOutput     # line 17
from .sources import ThreadQuery, ThreadFile  # line 18

# New import needed:
from .sources import SOURCE_REGISTRY          # created by TASK-652
```

### Existing Signatures to Use
```python
# querysource/queries/multi/__init__.py:53-104
class MultiQS(BaseQuery):
    def __init__(self, slug, queries, files, query, conditions, request, loop, user_session, **kwargs):
        # ...
        self._queries = queries              # line 84
        self._files = files                  # line 85
        self._options: dict = query or {}    # line 87
        if query:
            self._queries = query.pop('queries', {})   # line 92
            self._files = query.pop('files', {})        # line 93
        if not (self.slug or self._queries or self._files):  # line 94
            raise DriverError(...)

    async def query(self) -> tuple:          # line 105
        # lines 142-157: creates ThreadQuery objects
        # lines 158-164: creates ThreadFile objects
        # lines 166-194: joins all threads, checks exceptions
        # lines 203-204: collects results from queue
```

### Does NOT Exist
- ~~`MultiQS._sources`~~ — does not exist yet; this task adds it
- ~~`SOURCE_REGISTRY`~~ — created by TASK-652; must be available before this task
- ~~`MultiQS.dispatch_source()`~~ — no such method

---

## Implementation Notes

### Pattern to Follow

In `__init__`:
```python
# After existing query/files extraction:
self._sources = query.pop('sources', {}) if query else {}

# Update empty check:
if not (self.slug or self._queries or self._files or self._sources):
    raise DriverError(...)
```

In `query()`, after the files dispatch block (line ~164):
```python
if self._sources:
    from .sources import SOURCE_REGISTRY
    for entry in self._sources:
        for source_type, config in entry.items():
            cls = SOURCE_REGISTRY.get(source_type)
            if cls is None:
                raise DriverError(f"Unknown source type: {source_type}")
            t = cls(source_type, config, self._request, self._queue)
            t.start()
            tasks[source_type] = t
```

### Key Constraints
- The `sources` key in the YAML config is a **list of dicts** (like Transform), where each dict has a single key (the source type name) and value (the config). This matches the existing MultiQuery YAML pattern for other components.
- The thread join + exception check loop already handles all threads in `tasks` dict — no changes needed there.
- Import `SOURCE_REGISTRY` lazily (inside `query()`) to avoid circular imports.

### References in Codebase
- `querysource/queries/multi/__init__.py` — lines 142-164 for existing dispatch pattern
- `querysource/queries/multi/__init__.py` — lines 290-331 for Transform dispatch pattern (list-of-dicts)

---

## Acceptance Criteria

- [ ] `MultiQS.__init__()` extracts `sources` from query dict
- [ ] `MultiQS.query()` dispatches source threads via `SOURCE_REGISTRY`
- [ ] Unknown source types raise `DriverError`
- [ ] Empty check includes `sources` (no false "all empty" error)
- [ ] Existing query and file behavior unchanged
- [ ] Integration tests pass: `pytest tests/test_multiqs_sources_integration.py -v`
- [ ] No linting errors: `ruff check querysource/queries/multi/__init__.py`

---

## Test Specification

```python
# tests/test_multiqs_sources_integration.py
import asyncio
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
from querysource.queries.multi import MultiQS
from querysource.exceptions import DriverError


class TestMultiQSSources:
    def test_sources_key_extracted(self):
        """Sources key should be parsed from query dict."""
        query = {
            "sources": [{"SourceTable": {"driver": "pg", "table": "test"}}]
        }
        mqs = MultiQS(query=query, request=MagicMock())
        assert mqs._sources is not None

    def test_empty_check_includes_sources(self):
        """Should not raise if only sources is provided."""
        query = {
            "sources": [{"SourceTable": {"driver": "pg", "table": "test"}}]
        }
        # Should not raise DriverError
        mqs = MultiQS(query=query, request=MagicMock())
        assert mqs._sources

    def test_unknown_source_type_raises(self):
        """Unknown source type should raise DriverError."""
        ...

    def test_existing_queries_still_work(self):
        """Existing queries key should work as before."""
        ...
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiquery-new-sources.spec.md` for full context
2. **Check dependencies** — verify TASK-644, 645, 646, 652 are completed
3. **Read `querysource/queries/multi/__init__.py`** carefully — this is the file you're modifying
4. **Verify `SOURCE_REGISTRY`** exists in `querysource/queries/multi/sources/__init__.py`
5. **Implement** following the scope and notes above
6. **Run existing MultiQuery tests** to ensure no regression
7. **Verify** all acceptance criteria are met
8. **Move this file** to `sdd/tasks/completed/TASK-651-multiqs-integration.md`
9. **Update index** → `"done"`

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
