# TASK-694: RemoteExecutor Implementation

**Feature**: FEAT-101 — MultiQuery Remote Execution
**Spec**: `sdd/specs/multiquery-remote-execution.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-693
**Assigned-to**: unassigned

---

## Context

> Implements the `RemoteExecutor` class that dispatches queries to a remote qworker
> server via `QClient.run()`. This is the core remote execution capability of FEAT-101.
> Implements Spec §2 (New Public Interfaces — RemoteExecutor) and §3 (Module 2).

---

## Scope

- Add `RemoteExecutor(QueryExecutor)` to `querysource/queries/multi/sources/executors.py`:
  - `__init__(host, port, timeout)` stores connection params
  - `execute(name, query, queue, request)`:
    1. Import QClient lazily (qworker is an optional dependency)
    2. Create QClient with `worker_list=[(host, port)]` and `timeout`
    3. Extract slug and conditions from the query dict
    4. Call `await client.run(query_handler_fn, slug, conditions=conditions)` where
       `query_handler_fn` is a reference to the remote handler (to be defined on qworker side)
    5. Put `{name: result}` into the queue
    6. Wrap `ConnectionError`, `TimeoutError`, `OSError` in `QueryException` with worker address
- Export `RemoteExecutor` from `sources/__init__.py`
- Write unit tests with mocked QClient

**NOT in scope**: ThreadQuery integration (TASK-695), MultiQS changes (TASK-696),
actual qworker handler implementation (separate repo spec)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/sources/executors.py` | MODIFY | Add RemoteExecutor class |
| `querysource/queries/multi/sources/__init__.py` | MODIFY | Add RemoteExecutor export |
| `tests/test_remote_executor.py` | CREATE | Unit tests with mocked QClient |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# From TASK-693 (will exist after that task completes):
from querysource.queries.multi.sources.executors import QueryExecutor, RemoteConfig

# QClient — import LAZILY inside execute() to keep qworker optional:
from qw.client import QClient  # qw/client.py:58

# Exceptions to wrap remote errors:
from querysource.exceptions import QueryException  # exceptions.py:6
```

### Existing Signatures to Use
```python
# qw/client.py:58
class QClient:
    timeout: int = 5                                                       # line 69
    def __init__(self, worker_list: list = None, timeout: int = 5):        # line 72
    async def run(self, fn: Any, *args, use_wrapper: bool = False, **kwargs):  # line 326
    # run() serializes fn via cloudpickle, sends to worker, waits for result
    # Returns: the deserialized result (DataFrame in our case)
    # Raises: ConnectionError, TimeoutError, ParserError, QWException

# querysource/exceptions.py:6
class QueryException(Exception): ...

# From TASK-693 (executors.py — will exist):
class QueryExecutor(ABC):
    @abstractmethod
    async def execute(self, name: str, query: dict,
                      queue: asyncio.Queue, request: web.Request) -> None: ...
```

### Does NOT Exist
- ~~`QClient.run_query()`~~ — no dedicated query method; only generic `run()`
- ~~`QClient.run_stream()`~~ — no streaming method
- ~~`qw.handlers.query`~~ — no query handler module exists in qworker
- ~~`RemoteExecutor`~~ — does not exist yet; this task creates it

---

## Implementation Notes

### Pattern to Follow
```python
class RemoteExecutor(QueryExecutor):
    def __init__(self, host: str, port: int, timeout: int = 60) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout

    async def execute(self, name, query, queue, request) -> None:
        from qw.client import QClient  # lazy import — qworker is optional

        slug = query.get("slug")
        conditions = {k: v for k, v in query.items()
                      if k not in ("slug", "query", "driver", "datasource")}
        try:
            client = QClient(
                worker_list=[(self._host, self._port)],
                timeout=self._timeout,
            )
            result = await client.run(
                "querysource.remote.query_handler",  # handler reference
                slug,
                conditions=conditions,
            )
            await queue.put({name: result})
        except (ConnectionError, TimeoutError, OSError) as exc:
            raise QueryException(
                f"Remote query {name!r} failed on {self._host}:{self._port}: {exc}"
            ) from exc
```

### Key Constraints
- **Lazy import of QClient**: `from qw.client import QClient` MUST be inside `execute()`,
  not at module level. qworker is an optional dependency — importing at module level would
  break querysource installations without qworker installed.
- **QClient instantiation inside execute()**: QClient grabs `asyncio.get_event_loop()` at
  init time (client.py:74). Since execute() runs inside a thread's event loop (via
  ThreadSource.run()), QClient must be created there, not in `__init__()`.
- **Error wrapping**: Catch transport-level errors and wrap in `QueryException` with the
  worker address for diagnostics. Let qworker-side errors (SlugNotFound, etc.) propagate
  as-is since QClient already deserializes them.

### References in Codebase
- `qw/client.py:326-418` — QClient.run() full implementation
- `qw/client.py:72-98` — QClient.__init__() with worker_list
- `querysource/exceptions.py:6` — QueryException base class

---

## Acceptance Criteria

- [ ] `RemoteExecutor` added to `executors.py` extending `QueryExecutor`
- [ ] QClient imported lazily inside `execute()`, not at module level
- [ ] Transport errors wrapped in `QueryException` with worker address
- [ ] qworker-side exceptions (SlugNotFound, etc.) propagate as-is
- [ ] Result put into queue as `{name: DataFrame}`
- [ ] `sources/__init__.py` exports `RemoteExecutor`
- [ ] Unit tests pass with mocked QClient: `pytest tests/test_remote_executor.py -v`
- [ ] No linting errors: `ruff check querysource/queries/multi/sources/executors.py`

---

## Test Specification

```python
# tests/test_remote_executor.py
import asyncio
import pytest
import pandas as pd
from unittest.mock import AsyncMock, MagicMock, patch
from querysource.queries.multi.sources.executors import RemoteExecutor
from querysource.exceptions import QueryException


class TestRemoteExecutor:
    @pytest.mark.asyncio
    async def test_calls_qclient_run(self):
        """RemoteExecutor dispatches to QClient.run() with slug and conditions."""
        queue = asyncio.Queue()
        request = MagicMock()
        executor = RemoteExecutor(host="localhost", port=8888, timeout=30)
        expected_df = pd.DataFrame({"id": [1], "val": [10]})

        with patch("querysource.queries.multi.sources.executors.QClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.run.return_value = expected_df
            MockClient.return_value = mock_instance

            await executor.execute(
                "revenue", {"slug": "monthly-revenue", "store_id": 42}, queue, request
            )

        result = await queue.get()
        assert "revenue" in result
        assert result["revenue"].equals(expected_df)

    @pytest.mark.asyncio
    async def test_wraps_connection_error(self):
        """ConnectionError from QClient is wrapped in QueryException."""
        queue = asyncio.Queue()
        executor = RemoteExecutor(host="bad-host", port=9999)

        with patch("querysource.queries.multi.sources.executors.QClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.run.side_effect = ConnectionError("refused")
            MockClient.return_value = mock_instance

            with pytest.raises(QueryException, match="bad-host:9999"):
                await executor.execute("q", {"slug": "s"}, queue, MagicMock())

    @pytest.mark.asyncio
    async def test_propagates_slug_not_found(self):
        """SlugNotFound from qworker side propagates as-is."""
        from querysource.exceptions import SlugNotFound
        queue = asyncio.Queue()
        executor = RemoteExecutor(host="localhost", port=8888)

        with patch("querysource.queries.multi.sources.executors.QClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.run.side_effect = SlugNotFound("no-such-slug")
            MockClient.return_value = mock_instance

            with pytest.raises(SlugNotFound):
                await executor.execute("q", {"slug": "no-such-slug"}, queue, MagicMock())
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiquery-remote-execution.spec.md` for full context
2. **Check dependencies** — verify TASK-693 is in `sdd/tasks/completed/`
3. **Verify the Codebase Contract** — confirm QClient signature at `qw/client.py:326`
4. **Update status** in `sdd/tasks/index/multiquery-remote-execution.json` → `"in-progress"`
5. **Implement** following the scope, codebase contract, and notes above
6. **Verify** all acceptance criteria are met
7. **Move this file** to `sdd/tasks/completed/TASK-694-remote-executor.md`
8. **Update index** → `"done"`
9. **Fill in the Completion Note** below

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
