# TASK-695: ThreadQuery Executor Integration

**Feature**: FEAT-101 — MultiQuery Remote Execution
**Spec**: `sdd/specs/multiquery-remote-execution.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-693, TASK-694
**Assigned-to**: unassigned

---

## Context

> Modifies ThreadQuery to accept an optional `remote_config` parameter and delegate
> execution to the appropriate executor (LocalExecutor or RemoteExecutor) instead of
> directly calling QueryObject. This is the integration point between the executor
> abstraction and the existing thread-based dispatch.
> Implements Spec §3 (Module 3).

---

## Scope

- Modify `ThreadQuery.__init__()` to accept an optional `remote_config: RemoteConfig | None = None` parameter
  - If `remote_config` is provided: store a `RemoteExecutor(host, port, timeout)` as `self._executor`
  - If `remote_config` is None: store a `LocalExecutor()` as `self._executor`
- Refactor `ThreadQuery.fetch()` to delegate to `self._executor.execute(name, query, queue, request)`
  instead of directly creating QueryObject
- Ensure `super().__init__()` call still works (ThreadSource expects `name, options, request, queue`)
- Write unit tests verifying both executor paths

**NOT in scope**: MultiQS changes to detect `remote: true` (TASK-696), config settings (TASK-697),
catalog schema updates (TASK-698)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/sources/query.py` | MODIFY | Add remote_config param, delegate to executor |
| `tests/test_threadquery_executor.py` | CREATE | Tests for both LocalExecutor and RemoteExecutor paths |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# Current imports in query.py (line 1-7):
import asyncio
import pandas as pd
from aiohttp import web
from ...obj import QueryObject
from .base import ThreadSource

# New imports to add:
from .executors import LocalExecutor, RemoteExecutor, RemoteConfig
```

### Existing Signatures to Use
```python
# querysource/queries/multi/sources/query.py:127 — CURRENT __init__ (will be modified):
def __init__(
    self,
    name: str,
    query: dict,
    request: web.Request,
    queue: asyncio.Queue,
):
    super().__init__(name, query, request, queue)   # line 134
    self._query = query                              # line 135
    self._request = request                          # line 136

# querysource/queries/multi/sources/query.py:150 — CURRENT fetch() (will be refactored):
async def fetch(self) -> pd.DataFrame | None:
    loop = asyncio.get_event_loop()                  # line 165
    self._query = QueryObject(                       # line 166
        self._name,
        self._query,
        queue=self._queue,
        request=self._request,
        loop=loop,
    )
    await self._query.build_provider()               # line 173
    await self._query.query()                        # line 174
    return None                                      # line 177

# querysource/queries/multi/sources/base.py:22 — ThreadSource.__init__:
def __init__(
    self,
    name: str,
    options: dict,        # <-- query dict is passed here as "options"
    request: web.Request,
    queue: asyncio.Queue,
) -> None:

# querysource/queries/multi/__init__.py:152 — current call site (will be updated in TASK-696):
t = ThreadQuery(name, query, self._request, self._queue)
```

### Does NOT Exist
- ~~`ThreadQuery.executor`~~ — does not exist yet; this task adds `self._executor`
- ~~`ThreadQuery._remote_config`~~ — does not exist yet; this task adds it
- ~~`ThreadQuery(name, query, request, queue, remote_config=...)`~~ — current signature does NOT have remote_config

---

## Implementation Notes

### Pattern to Follow
```python
# Modified ThreadQuery.__init__:
def __init__(
    self,
    name: str,
    query: dict,
    request: web.Request,
    queue: asyncio.Queue,
    remote_config: RemoteConfig | None = None,
):
    super().__init__(name, query, request, queue)
    self._query = query
    self._request = request
    if remote_config is not None:
        self._executor = RemoteExecutor(
            remote_config.host, remote_config.port, remote_config.timeout
        )
    else:
        self._executor = LocalExecutor()

# Modified fetch():
async def fetch(self) -> pd.DataFrame | None:
    await self._executor.execute(
        self._name, self._query, self._queue, self._request
    )
    return None
```

### Key Constraints
- **Backward compatible**: The `remote_config` parameter defaults to `None`, so existing
  call sites (`ThreadQuery(name, query, request, queue)`) work unchanged.
- **slug property**: The `slug` property currently accesses `self._query` as a dict or
  QueryObject. With the executor pattern, `self._query` remains a dict (it's no longer
  replaced with a QueryObject in fetch()). Update the slug property to always access the
  dict since the QueryObject replacement was an implementation detail of the old flow.
- **ThreadSource.run()**: The base `run()` method (base.py:90) calls `self.fetch()` and
  handles the None return. This contract is preserved — fetch() still returns None.

### References in Codebase
- `querysource/queries/multi/sources/query.py` — full current implementation
- `querysource/queries/multi/sources/base.py:90-116` — ThreadSource.run() that calls fetch()
- `querysource/queries/multi/sources/executors.py` — created by TASK-693/694

---

## Acceptance Criteria

- [ ] `ThreadQuery.__init__()` accepts optional `remote_config: RemoteConfig | None = None`
- [ ] Default (no remote_config) uses `LocalExecutor` — identical behavior to current code
- [ ] With remote_config, uses `RemoteExecutor`
- [ ] `fetch()` delegates to `self._executor.execute()` and returns None
- [ ] `slug` property works correctly (no longer depends on QueryObject replacement)
- [ ] Existing callers (`ThreadQuery(name, query, request, queue)`) still work
- [ ] Tests pass: `pytest tests/test_threadquery_executor.py -v`
- [ ] No linting errors: `ruff check querysource/queries/multi/sources/query.py`

---

## Test Specification

```python
# tests/test_threadquery_executor.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from querysource.queries.multi.sources.query import ThreadQuery
from querysource.queries.multi.sources.executors import (
    LocalExecutor,
    RemoteExecutor,
    RemoteConfig,
)


class TestThreadQueryExecutorSelection:
    def test_default_uses_local_executor(self):
        """ThreadQuery without remote_config uses LocalExecutor."""
        tq = ThreadQuery("test", {"slug": "s"}, MagicMock(), asyncio.Queue())
        assert isinstance(tq._executor, LocalExecutor)

    def test_remote_config_uses_remote_executor(self):
        """ThreadQuery with remote_config uses RemoteExecutor."""
        rc = RemoteConfig(host="localhost", port=8888)
        tq = ThreadQuery("test", {"slug": "s"}, MagicMock(), asyncio.Queue(), remote_config=rc)
        assert isinstance(tq._executor, RemoteExecutor)

    def test_slug_property_from_dict(self):
        """slug property works from the query dict."""
        tq = ThreadQuery("test", {"slug": "my-slug"}, MagicMock(), asyncio.Queue())
        assert tq.slug == "my-slug"

    def test_slug_property_fallback(self):
        """slug property falls back to name when no slug key."""
        tq = ThreadQuery("fallback", {"query": "SELECT 1"}, MagicMock(), asyncio.Queue())
        assert tq.slug == "fallback"


class TestThreadQueryFetchDelegation:
    @pytest.mark.asyncio
    async def test_fetch_delegates_to_executor(self):
        """fetch() calls executor.execute() with correct args."""
        queue = asyncio.Queue()
        request = MagicMock()
        query = {"slug": "test-slug"}
        tq = ThreadQuery("test", query, request, queue)
        tq._executor = AsyncMock()

        result = await tq.fetch()

        assert result is None
        tq._executor.execute.assert_awaited_once_with("test", query, queue, request)
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiquery-remote-execution.spec.md` for full context
2. **Check dependencies** — verify TASK-693 and TASK-694 are in `sdd/tasks/completed/`
3. **Verify the Codebase Contract** — read `query.py` and confirm current __init__/fetch() signatures
4. **Update status** in `sdd/tasks/index/multiquery-remote-execution.json` → `"in-progress"`
5. **Implement** following the scope, codebase contract, and notes above
6. **Verify** all acceptance criteria are met
7. **Move this file** to `sdd/tasks/completed/TASK-695-threadquery-executor-integration.md`
8. **Update index** → `"done"`
9. **Fill in the Completion Note** below

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
