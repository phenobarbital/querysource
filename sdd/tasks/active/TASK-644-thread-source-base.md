# TASK-644: ThreadSource Base Class

**Feature**: FEAT-093 — MultiQuery New Sources
**Spec**: `sdd/specs/multiquery-new-sources.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

This is the foundation task for FEAT-093. All other tasks depend on this base class.
Implements Spec Module 1: ThreadSource Base Class.

The existing `ThreadFile` and `ThreadQuery` both duplicate the same boilerplate:
create an asyncio event loop, manage exceptions, put results into a shared queue.
This task extracts that into an abstract base class that all MultiQuery source
threads will inherit from.

---

## Scope

- Create `ThreadSource` abstract base class at `querysource/queries/multi/sources/base.py`.
- Encapsulate common thread boilerplate: event loop creation, exception capture on `self.exc`,
  queue interaction (`self._queue.put({self._name: result})`), loop cleanup.
- Add `resolve_credential(key, value)` method that checks if a value matches a navconfig
  variable name (uppercase convention) and resolves it, otherwise returns the literal.
- Define abstract `async def fetch(self) -> pd.DataFrame` method.
- Implement `run()` that creates the event loop, calls `fetch()`, puts the DataFrame in the queue,
  and handles exceptions.
- Write unit tests.

**NOT in scope**: Refactoring ThreadFile/ThreadQuery (TASK-645/646), new sources (TASK-647–650).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/sources/base.py` | CREATE | ThreadSource base class |
| `tests/test_thread_source_base.py` | CREATE | Unit tests for base class |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
import asyncio                    # verified: querysource/queries/multi/sources/file.py:1
import threading                  # verified: querysource/queries/multi/sources/file.py:2
from aiohttp import web           # verified: querysource/queries/multi/sources/file.py:3
from abc import ABC, abstractmethod
import pandas as pd               # verified: querysource/queries/multi/sources/file.py:8
```

### Existing Signatures to Use
```python
# querysource/queries/multi/sources/file.py:19-31
# Pattern to extract — ThreadFile.__init__:
class ThreadFile(threading.Thread):
    def __init__(self, name: str, file_options: dict, request: web.Request, queue: asyncio.Queue):
        super().__init__()
        self._loop = asyncio.new_event_loop()  # line 23
        self._queue = queue                     # line 24
        self.exc = None                         # line 25
        self._name = name                       # line 26

# querysource/queries/multi/sources/query.py:14-29
# Pattern to extract — ThreadQuery.__init__:
class ThreadQuery(threading.Thread):
    def __init__(self, name: str, query: dict, request: web.Request, queue: asyncio.Queue):
        super().__init__()
        self._loop = asyncio.new_event_loop()  # line 22
        self._queue = queue                     # line 24
        self.exc = None                         # line 25
        self._name = name                       # line 26

# Common run() pattern — file.py:54-100, query.py:35-71:
# Both create event loop, run async work, capture exceptions, close loop.
```

### Does NOT Exist
- ~~`querysource.queries.multi.sources.base`~~ — does not exist yet; this task creates it
- ~~`ThreadSource`~~ — does not exist yet; this task creates it
- ~~`querysource.utils.credentials`~~ — no such module; credential resolution is new

---

## Implementation Notes

### Pattern to Follow
```python
class ThreadSource(threading.Thread, ABC):
    def __init__(self, name: str, options: dict, request: web.Request, queue: asyncio.Queue) -> None:
        super().__init__()
        self._queue = queue
        self.exc = None
        self._name = name
        self._options = options
        self._request = request

    def resolve_credential(self, key: str, value: str) -> str:
        """Resolve navconfig variable name or return literal."""
        if isinstance(value, str) and value.isupper() and '_' in value:
            try:
                from navconfig import config
                resolved = config.get(value)
                if resolved is not None:
                    return resolved
            except ImportError:
                pass
        return value

    @abstractmethod
    async def fetch(self) -> pd.DataFrame:
        ...

    def run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            df = loop.run_until_complete(self.fetch())
            loop.run_until_complete(self._queue.put({self._name: df}))
        except Exception as ex:
            self.exc = ex
        finally:
            try:
                loop.stop()
                loop.close()
            except Exception:
                pass
```

### Key Constraints
- Constructor signature must accept `(name, options, request, queue)` — the `options` dict is generic; each subclass extracts what it needs.
- `resolve_credential` should use navconfig's `config` object. The heuristic for "looks like an env var name" is: all uppercase + contains underscore.
- `self.exc` must be set on failure so `MultiQS` can check after `join()`.

### References in Codebase
- `querysource/queries/multi/sources/file.py` — existing thread pattern to extract from
- `querysource/queries/multi/sources/query.py` — existing thread pattern to extract from

---

## Acceptance Criteria

- [ ] `ThreadSource` class exists at `querysource/queries/multi/sources/base.py`
- [ ] Has abstract `fetch()` method returning `pd.DataFrame`
- [ ] Has `resolve_credential()` method that resolves navconfig vars
- [ ] `run()` creates event loop, calls `fetch()`, puts result in queue, captures exceptions
- [ ] Unit tests pass: `pytest tests/test_thread_source_base.py -v`
- [ ] No linting errors: `ruff check querysource/queries/multi/sources/base.py`

---

## Test Specification

```python
# tests/test_thread_source_base.py
import asyncio
import pytest
import pandas as pd
from querysource.queries.multi.sources.base import ThreadSource


class ConcreteSource(ThreadSource):
    """Test-only concrete implementation."""
    async def fetch(self) -> pd.DataFrame:
        return pd.DataFrame({"col1": [1, 2], "col2": ["a", "b"]})


class FailingSource(ThreadSource):
    async def fetch(self) -> pd.DataFrame:
        raise ValueError("test error")


class TestThreadSource:
    def test_run_puts_dataframe_in_queue(self):
        queue = asyncio.Queue()
        source = ConcreteSource("test", {}, None, queue)
        source.start()
        source.join()
        assert source.exc is None
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(queue.get())
        loop.close()
        assert "test" in result
        assert isinstance(result["test"], pd.DataFrame)

    def test_exception_captured(self):
        queue = asyncio.Queue()
        source = FailingSource("test", {}, None, queue)
        source.start()
        source.join()
        assert source.exc is not None
        assert "test error" in str(source.exc)

    def test_resolve_credential_literal(self):
        source = ConcreteSource("test", {}, None, asyncio.Queue())
        assert source.resolve_credential("key", "literal_value") == "literal_value"

    def test_resolve_credential_env_var(self):
        source = ConcreteSource("test", {}, None, asyncio.Queue())
        # Should attempt navconfig lookup for uppercase+underscore values
        result = source.resolve_credential("key", "SOME_VAR_NAME")
        # If navconfig doesn't have it, falls back to literal
        assert isinstance(result, str)
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiquery-new-sources.spec.md` for full context
2. **Check dependencies** — this task has none
3. **Verify the Codebase Contract** — before writing ANY code:
   - Confirm every import in "Verified Imports" still exists
   - Confirm ThreadFile and ThreadQuery still have the listed signatures
   - **NEVER** reference an import not in the contract without verifying it exists
4. **Update status** in `sdd/tasks/index/multiquery-new-sources.json` → `"in-progress"`
5. **Implement** following the scope, codebase contract, and notes above
6. **Verify** all acceptance criteria are met
7. **Move this file** to `sdd/tasks/completed/TASK-644-thread-source-base.md`
8. **Update index** → `"done"`
9. **Fill in the Completion Note** below

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
