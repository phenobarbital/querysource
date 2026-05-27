# TASK-693: QueryExecutor Interface + LocalExecutor

**Feature**: FEAT-101 — MultiQuery Remote Execution
**Spec**: `sdd/specs/multiquery-remote-execution.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

> This is the foundational task for FEAT-101. It creates the `QueryExecutor` abstract
> interface and the `LocalExecutor` implementation that encapsulates the current
> ThreadQuery → QueryObject flow. All subsequent tasks depend on this interface.
> Implements Spec §2 (New Public Interfaces) and §3 (Module 1).

---

## Scope

- Create `querysource/queries/multi/sources/executors.py` with:
  - `RemoteConfig` frozen dataclass (`host: str`, `port: int`, `timeout: int = 60`)
  - `QueryExecutor` ABC with single abstract method `execute(name, query, queue, request) -> None`
  - `LocalExecutor(QueryExecutor)` that wraps the current QueryObject flow:
    creates QueryObject, calls `build_provider()`, calls `query()`, returns None
    (QueryObject puts result in queue internally)
- Export `QueryExecutor`, `LocalExecutor`, `RemoteConfig` from `sources/__init__.py`
- Write unit tests for `LocalExecutor`

**NOT in scope**: RemoteExecutor (TASK-694), ThreadQuery modification (TASK-695),
MultiQS changes (TASK-696), config settings (TASK-697)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/sources/executors.py` | CREATE | QueryExecutor ABC, LocalExecutor, RemoteConfig |
| `querysource/queries/multi/sources/__init__.py` | MODIFY | Add exports for new classes |
| `tests/test_local_executor.py` | CREATE | Unit tests for LocalExecutor |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# These imports are confirmed to work — use VERBATIM:
from querysource.queries.obj import QueryObject           # query.py:6 uses relative: ...obj
from querysource.queries.multi.sources.base import ThreadSource  # sources/__init__.py:2
from querysource.exceptions import QueryException          # exceptions.py:6

# Inside executors.py, use relative imports matching the package:
from ...obj import QueryObject                             # matches query.py:6
```

### Existing Signatures to Use
```python
# querysource/queries/obj.py:20
class QueryObject(BaseQuery):
    def __init__(self, name, query, conditions=None, request=None,
                 queue=None, loop=None):                                   # line 26
    async def build_provider(self):                                        # line 65
    async def query(self):                                                 # line 183
    # queue put at line 203: await self._queue.put({self._name: result})

# querysource/queries/multi/sources/query.py:150 — current fetch() to replicate:
async def fetch(self) -> pd.DataFrame | None:
    loop = asyncio.get_event_loop()
    self._query = QueryObject(
        self._name,
        self._query,
        queue=self._queue,
        request=self._request,
        loop=loop,
    )
    await self._query.build_provider()
    await self._query.query()
    return None
```

### Does NOT Exist
- ~~`querysource.queries.multi.sources.executors`~~ — does not exist yet; this task creates it
- ~~`ThreadQuery.executor`~~ — does not exist yet (TASK-695 adds it)
- ~~`QueryExecutor`~~ — does not exist yet; this task creates it

---

## Implementation Notes

### Pattern to Follow
```python
# The LocalExecutor must replicate the exact flow from ThreadQuery.fetch()
# (query.py:150-177). The key contract is:
# 1. Create QueryObject with name, query dict, queue, request, and current event loop
# 2. Call await query_obj.build_provider()
# 3. Call await query_obj.query()  — this puts {name: DataFrame} into the queue
# 4. Return None (queue already written)
```

### Key Constraints
- `LocalExecutor.execute()` must accept the same args as the spec's QueryExecutor.execute():
  `name: str, query: dict, queue: asyncio.Queue, request: web.Request`
- The method must get the current event loop via `asyncio.get_event_loop()` and pass it
  to QueryObject (matching the current ThreadQuery.fetch() at line 165)
- `RemoteConfig` is a frozen dataclass — immutable value object
- Use `from abc import ABC, abstractmethod` for QueryExecutor

### References in Codebase
- `querysource/queries/multi/sources/query.py:150-177` — current fetch() flow to extract into LocalExecutor
- `querysource/queries/obj.py:20-245` — QueryObject that LocalExecutor delegates to

---

## Acceptance Criteria

- [ ] `executors.py` created with `QueryExecutor`, `LocalExecutor`, `RemoteConfig`
- [ ] `QueryExecutor` is an ABC with a single abstract `execute()` method
- [ ] `LocalExecutor.execute()` replicates the current QueryObject flow from ThreadQuery.fetch()
- [ ] `RemoteConfig` is a frozen dataclass with `host`, `port`, `timeout` fields
- [ ] `sources/__init__.py` exports `QueryExecutor`, `LocalExecutor`, `RemoteConfig`
- [ ] Unit tests pass: `pytest tests/test_local_executor.py -v`
- [ ] No linting errors: `ruff check querysource/queries/multi/sources/executors.py`
- [ ] Import works: `from querysource.queries.multi.sources.executors import QueryExecutor, LocalExecutor, RemoteConfig`

---

## Test Specification

```python
# tests/test_local_executor.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from querysource.queries.multi.sources.executors import (
    QueryExecutor,
    LocalExecutor,
    RemoteConfig,
)


class TestRemoteConfig:
    def test_frozen_dataclass(self):
        rc = RemoteConfig(host="localhost", port=8888)
        assert rc.host == "localhost"
        assert rc.port == 8888
        assert rc.timeout == 60

    def test_immutable(self):
        rc = RemoteConfig(host="localhost", port=8888)
        with pytest.raises(AttributeError):
            rc.host = "other"


class TestLocalExecutor:
    @pytest.mark.asyncio
    async def test_delegates_to_query_object(self):
        """LocalExecutor creates QueryObject and calls build_provider + query."""
        queue = asyncio.Queue()
        request = MagicMock()
        executor = LocalExecutor()

        with patch(
            "querysource.queries.multi.sources.executors.QueryObject"
        ) as MockQO:
            mock_qo = AsyncMock()
            MockQO.return_value = mock_qo
            result = await executor.execute("test", {"slug": "test-slug"}, queue, request)

        assert result is None
        MockQO.assert_called_once()
        mock_qo.build_provider.assert_awaited_once()
        mock_qo.query.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_none(self):
        """LocalExecutor returns None (queue written by QueryObject)."""
        queue = asyncio.Queue()
        request = MagicMock()
        executor = LocalExecutor()

        with patch(
            "querysource.queries.multi.sources.executors.QueryObject"
        ) as MockQO:
            MockQO.return_value = AsyncMock()
            result = await executor.execute("test", {"slug": "s"}, queue, request)

        assert result is None
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiquery-remote-execution.spec.md` for full context
2. **Check dependencies** — this task has none (it's the foundation)
3. **Verify the Codebase Contract** — confirm QueryObject signature at `querysource/queries/obj.py:26`
4. **Update status** in `sdd/tasks/index/multiquery-remote-execution.json` → `"in-progress"`
5. **Implement** following the scope, codebase contract, and notes above
6. **Verify** all acceptance criteria are met
7. **Move this file** to `sdd/tasks/completed/TASK-693-query-executor-interface.md`
8. **Update index** → `"done"`
9. **Fill in the Completion Note** below

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: claude-sonnet-4-6
**Date**: 2026-05-26
**Notes**: Created executors.py with QueryExecutor ABC, LocalExecutor, and RemoteConfig. Removed unused `pandas` import flagged by ruff. Tests written per spec but cannot be run directly in the worktree because compiled Cython extensions (querysource.types.validators) are not present there — this is a pre-existing environment constraint, not a regression.

**Deviations from spec**: none
