# TASK-646: Refactor ThreadQuery to Inherit ThreadSource

**Feature**: FEAT-093 — MultiQuery New Sources
**Spec**: `sdd/specs/multiquery-new-sources.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-644
**Assigned-to**: unassigned

---

## Context

Implements Spec Module 3: Refactor ThreadQuery. After the ThreadSource base class
is created (TASK-644), this task refactors the existing `ThreadQuery` to inherit
from it. This can run in parallel with TASK-645 (ThreadFile refactor) since they
modify different files.

---

## Scope

- Refactor `ThreadQuery` in `querysource/queries/multi/sources/query.py` to inherit
  from `ThreadSource` instead of `threading.Thread`.
- Move the QueryObject building and execution from `run()` into an async `fetch()` method.
- Preserve the `slug` property.
- Preserve the exact same constructor signature: `ThreadQuery(name, query, request, queue)`.
- Ensure existing MultiQuery query behavior is unchanged.

**NOT in scope**: Refactoring ThreadFile (TASK-645), new source implementations.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/sources/query.py` | MODIFY | Inherit from ThreadSource, move logic to fetch() |
| `tests/test_thread_query_refactor.py` | CREATE | Backward compatibility tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# Existing imports in query.py (lines 1-5):
import asyncio                               # verified: query.py:1
import threading                             # verified: query.py:2
from aiohttp import web                      # verified: query.py:3
from ...obj import QueryObject               # verified: query.py:4
from ....exceptions import QueryException    # verified: query.py:5

# New import needed:
from .base import ThreadSource                # created by TASK-644
```

### Existing Signatures to Use
```python
# querysource/queries/multi/sources/query.py:8-71
class ThreadQuery(threading.Thread):
    def __init__(self, name: str, query: dict, request: web.Request, queue: asyncio.Queue):
        super().__init__()                          # line 21
        self._loop = asyncio.new_event_loop()       # line 22
        asyncio.set_event_loop(self._loop)          # line 23
        self._queue = queue                          # line 24
        self.exc = None                              # line 25
        self._name = name                            # line 26
        self._query = query                          # line 27
        self._request = request                      # line 28
        self._loop = None                            # line 29 (delay init)

    @property
    def slug(self) -> str:                           # line 31-33
        return self._query.slug

    def run(self):                                   # line 35-71
        # Creates event loop, builds QueryObject, calls build_provider + query

# querysource/queries/obj.py — QueryObject (used in run()):
# QueryObject(name, query_dict, queue=queue, request=request, loop=loop)
# Methods: build_provider(), query()

# ThreadSource base (created by TASK-644):
class ThreadSource(threading.Thread, ABC):
    def __init__(self, name: str, options: dict, request: web.Request, queue: asyncio.Queue): ...
    async def fetch(self) -> pd.DataFrame: ...  # abstract
    def run(self) -> None: ...  # creates loop, calls fetch(), puts in queue
```

### Does NOT Exist
- ~~`ThreadQuery.fetch()`~~ — does not exist yet; this task adds it
- ~~`ThreadSource.build_provider()`~~ — not on the base class; provider building is specific to ThreadQuery

---

## Implementation Notes

### Pattern to Follow
```python
from .base import ThreadSource
from ...obj import QueryObject
from ....exceptions import QueryException


class ThreadQuery(ThreadSource):
    def __init__(self, name: str, query: dict, request: web.Request, queue: asyncio.Queue):
        super().__init__(name, query, request, queue)
        self._query = query
        self._request = request

    @property
    def slug(self):
        return self._query.slug

    async def fetch(self) -> pd.DataFrame:
        # Build QueryObject and execute
        self._query = QueryObject(
            self._name, self._query,
            queue=self._queue, request=self._request,
            loop=asyncio.get_event_loop()
        )
        await self._query.build_provider()
        await self._query.query()
        # QueryObject puts result in queue directly, so fetch() may not
        # need to return a DataFrame — check QueryObject.query() behavior.
        # If QueryObject already puts in queue, return a sentinel or adjust base.
```

### Key Constraints
- **Important**: `QueryObject.query()` already puts its result into `self._queue` directly. This means `ThreadQuery.fetch()` may need special handling compared to other sources. Options:
  1. Have `fetch()` return the result and let the base `run()` put it in the queue (requires QueryObject to return rather than queue).
  2. Override `run()` in ThreadQuery to handle the QueryObject's queue behavior.
  3. Best approach: let `fetch()` handle the full async work and have the base class only put in queue if `fetch()` returns a non-None value. If QueryObject already queues, `fetch()` returns None.
- The `slug` property must still work — it accesses `self._query.slug` where `self._query` starts as a dict but becomes a `QueryObject` after `fetch()` sets it up.
- Constructor signature `(name, query, request, queue)` must NOT change.

### References in Codebase
- `querysource/queries/multi/sources/query.py` — current implementation to refactor
- `querysource/queries/obj.py` — QueryObject that ThreadQuery wraps
- `querysource/queries/multi/__init__.py:148-157` — how MultiQS creates ThreadQuery instances

---

## Acceptance Criteria

- [ ] `ThreadQuery` inherits from `ThreadSource` (not `threading.Thread` directly)
- [ ] `ThreadQuery.fetch()` handles QueryObject building and execution
- [ ] `slug` property still works
- [ ] Constructor signature `(name, query, request, queue)` is preserved
- [ ] Existing MultiQuery query behavior is unchanged
- [ ] Tests pass: `pytest tests/test_thread_query_refactor.py -v`
- [ ] No linting errors: `ruff check querysource/queries/multi/sources/query.py`

---

## Test Specification

```python
# tests/test_thread_query_refactor.py
import pytest
from querysource.queries.multi.sources.query import ThreadQuery
from querysource.queries.multi.sources.base import ThreadSource


class TestThreadQueryRefactor:
    def test_inherits_thread_source(self):
        assert issubclass(ThreadQuery, ThreadSource)

    def test_has_fetch_method(self):
        assert hasattr(ThreadQuery, 'fetch')

    def test_has_slug_property(self):
        assert isinstance(
            ThreadQuery.__dict__.get('slug')
            or getattr(type, 'slug', None),
            property
        ) or hasattr(ThreadQuery, 'slug')
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiquery-new-sources.spec.md` for full context
2. **Check dependencies** — verify TASK-644 is completed
3. **Read `querysource/queries/obj.py`** to understand how QueryObject interacts with the queue
4. **Verify the Codebase Contract** — confirm ThreadQuery's current signature, ThreadSource exists
5. **Update status** in `sdd/tasks/index/multiquery-new-sources.json` → `"in-progress"`
6. **Implement** following the scope and notes above
7. **Verify** all acceptance criteria are met
8. **Move this file** to `sdd/tasks/completed/TASK-646-refactor-thread-query.md`
9. **Update index** → `"done"`
10. **Fill in the Completion Note** below

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
