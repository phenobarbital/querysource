---
type: feature
base_branch: dev
---

# Feature Specification: MultiQuery Remote Execution

**Feature ID**: FEAT-101
**Date**: 2026-05-26
**Author**: Jesus Lara
**Status**: draft
**Target version**: TBD

---

## 1. Motivation & Business Requirements

### Problem Statement

MultiQS orchestrates concurrent query execution via `ThreadQuery`, which spawns OS threads
with dedicated asyncio event loops to run `QueryObject` in-process. When QuerySource runs
behind an HTTP server, every ThreadQuery competes for the same CPU/memory/connection-pool
resources as the request-handling loop. Under heavy multi-query workloads, this starves the
HTTP server and degrades latency for all users.

**Who is affected**: Operators running QuerySource in production HTTP deployments with
resource-intensive multi-query slugs; end users experiencing degraded response times.

**Why now**: As MultiQS adoption grows and query complexity increases (joins, transforms,
large datasets), in-process execution becomes the bottleneck.

### Goals

- Allow individual MultiQS queries to be offloaded to a remote qworker server, freeing
  HTTP server resources.
- Provide an opt-in mechanism (`remote: true`) so existing configurations work unchanged.
- Design a pluggable executor interface inside ThreadQuery so future execution strategies
  (streaming, batched) can be added without modifying ThreadQuery or MultiQS.
- Document the qworker-side contract so a separate spec can cover the handler implementation.

### Non-Goals (explicitly out of scope)

- **v2 streaming implementation**: Chunked-row streaming via Redis is a future extension.
  The interface is designed to accommodate it, but this spec does not implement it.
- **Automatic fallback to local execution**: Rejected in brainstorm — remote failures
  propagate as errors. See `sdd/proposals/multiquery-remote-execution.brainstorm.md`.
- **AsyncIO-native remote dispatch (no threads)**: Rejected in brainstorm Option C —
  too high risk for v1 due to dual execution model in MultiQS.
- **New RemoteQuery subclass**: Rejected in brainstorm Option B — code duplication and
  maintenance burden outweigh isolation benefits.
- **Credential serialization across the wire**: The remote worker has its own QuerySource
  install with its own credential configuration.

---

## 2. Architectural Design

### Overview

ThreadQuery gains a pluggable `QueryExecutor` strategy via composition. The executor
interface defines a single method (`execute`) matching the current `fetch()` contract.
Two concrete implementations ship with this feature:

- **`LocalExecutor`**: Wraps the current QueryObject path (no behavior change).
- **`RemoteExecutor`**: Uses `QClient.run()` to dispatch the query slug + conditions to
  a remote qworker, receives the raw DataFrame result, and puts it into the shared queue.

MultiQS detects `remote: true` in the query config dict, resolves the worker address
(per-query `worker:` key → central `QWORKER_HOST`/`QWORKER_PORT` config → error), and
passes a `remote_config` dict to ThreadQuery. ThreadQuery instantiates the appropriate
executor based on whether `remote_config` is present.

The qworker side receives a dedicated `QueryTask` handler (not a generic callable) that
understands slugs and providers. The handler runs locally on the worker using QuerySource's
own QueryObject. Results are returned as raw DataFrames via the existing cloudpickle
TCP protocol.

### Component Diagram

```
                        MultiQS.query()
                            │
            ┌───────────────┼───────────────┐
            │               │               │
     (remote: true)   (remote: false)    FileSource
            │               │               │
      ThreadQuery      ThreadQuery      ThreadSource
            │               │               │
     RemoteExecutor   LocalExecutor     fetch()
            │               │               │
      QClient.run()   QueryObject       read file
            │          .build_provider()    │
            │          .query()             │
     ┌──────┴──────┐       │               │
     │  TCP/wire   │       │               │
     │  (cloudpickle)      │               │
     └──────┬──────┘       │               │
            │              │               │
     QWorker Server        │               │
     QueryTask handler     │               │
     (local QueryObject)   │               │
            │              │               │
            ▼              ▼               ▼
        DataFrame      DataFrame       DataFrame
            │              │               │
            └──────────────┼───────────────┘
                           │
                    asyncio.Queue
                           │
                  MultiQS result dict
                           │
                  Operators pipeline
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `ThreadQuery` (`sources/query.py`) | modifies | Accepts optional `remote_config`, delegates to executor |
| `ThreadSource` (`sources/base.py`) | unchanged | Base threading model preserved |
| `MultiQS` (`multi/__init__.py`) | modifies | Reads `remote`/`worker` keys, resolves config, passes to ThreadQuery |
| `QueryObject` (`queries/obj.py`) | unchanged | Used by LocalExecutor (and by qworker handler remotely) |
| `querysource/conf.py` | extends | New `QWORKER_HOST`, `QWORKER_PORT`, `QWORKER_TIMEOUT` settings |
| `sources/__init__.py` | extends | Exports new executor classes |
| `QueryHandler` (`handlers/multi.py`) | modifies (minor) | Adds `X-Remote-Queries` response header |
| `QClient` (`qw/client.py`) | uses (unchanged) | `QClient.run()` used by RemoteExecutor |

### Data Models

```python
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RemoteConfig:
    """Configuration for remote query execution."""
    host: str
    port: int
    timeout: int = 60
```

No new Pydantic models are required. The `RemoteConfig` dataclass is an internal
value object passed from MultiQS to ThreadQuery — it is never serialized or exposed
to users.

### New Public Interfaces

```python
from abc import ABC, abstractmethod
import asyncio
import pandas as pd
from aiohttp import web


class QueryExecutor(ABC):
    """Strategy interface for query execution inside ThreadQuery."""

    @abstractmethod
    async def execute(
        self,
        name: str,
        query: dict,
        queue: asyncio.Queue,
        request: web.Request,
    ) -> None:
        """Execute a query and put the result into the queue.

        The method MUST put a dict {name: DataFrame} into the queue
        before returning. Returning None signals that the queue was
        already written (matching the current ThreadQuery/QueryObject
        contract).
        """


class LocalExecutor(QueryExecutor):
    """Executes queries locally via QueryObject (current behavior)."""

    async def execute(
        self,
        name: str,
        query: dict,
        queue: asyncio.Queue,
        request: web.Request,
    ) -> None: ...


class RemoteExecutor(QueryExecutor):
    """Dispatches queries to a remote qworker via QClient."""

    def __init__(self, host: str, port: int, timeout: int = 60) -> None: ...

    async def execute(
        self,
        name: str,
        query: dict,
        queue: asyncio.Queue,
        request: web.Request,
    ) -> None: ...
```

---

## 3. Module Breakdown

### Module 1: QueryExecutor Interface + LocalExecutor

- **Path**: `querysource/queries/multi/sources/executors.py`
- **Responsibility**: Define the `QueryExecutor` ABC and implement `LocalExecutor` that
  encapsulates the current QueryObject instantiation + build_provider + query flow.
  Also defines the `RemoteConfig` dataclass.
- **Depends on**: `QueryObject` (existing)

### Module 2: RemoteExecutor

- **Path**: `querysource/queries/multi/sources/executors.py` (same file)
- **Responsibility**: Implement `RemoteExecutor` that creates a `QClient`, calls
  `QClient.run()` with the query slug and conditions, and puts the returned DataFrame
  into the shared queue. Wraps connection/timeout errors in `QueryException`.
- **Depends on**: Module 1 (QueryExecutor interface), `qw.client.QClient`

### Module 3: ThreadQuery Executor Integration

- **Path**: `querysource/queries/multi/sources/query.py`
- **Responsibility**: Modify ThreadQuery to accept an optional `remote_config` parameter.
  If present, instantiate `RemoteExecutor`; otherwise use `LocalExecutor`. Refactor
  `fetch()` to delegate to `self._executor.execute()`.
- **Depends on**: Modules 1–2

### Module 4: MultiQS Remote Config Resolution

- **Path**: `querysource/queries/multi/__init__.py`
- **Responsibility**: In the query dispatch loop, detect `remote: true`, resolve worker
  address from per-query `worker` key or central config, build `RemoteConfig`, and pass
  it to ThreadQuery. Track which queries ran remotely for the response header.
- **Depends on**: Module 3, Module 5

### Module 5: Configuration Settings

- **Path**: `querysource/conf.py`
- **Responsibility**: Add `QWORKER_HOST`, `QWORKER_PORT`, `QWORKER_TIMEOUT` settings
  via `navconfig.config.get()` with sensible defaults (None/None/60).
- **Depends on**: None (leaf module)

### Module 6: Catalog & Schema Update

- **Path**: `querysource/queries/multi/sources/query.py`
- **Responsibility**: Update ThreadQuery's `_catalog` dict and `json_schema` to document
  the `remote` (boolean) and `worker` (string, optional) keys.
- **Depends on**: Module 3

### Module 7: Qworker Interface Contract Documentation

- **Path**: `sdd/contracts/qworker-query-handler.md`
- **Responsibility**: Document the required qworker-side `QueryTask` handler: input
  format, execution flow, return type, error contract, and v2 streaming extension.
  This is documentation only — no implementation code.
- **Depends on**: None

---

## 4. Test Specification

### Unit Tests

| Test | Module | Description |
|---|---|---|
| `test_local_executor_delegates_to_query_object` | Module 1 | LocalExecutor creates QueryObject, calls build_provider + query, verifies queue receives result |
| `test_local_executor_returns_none` | Module 1 | Verify execute() returns None (queue written internally) |
| `test_remote_executor_calls_qclient_run` | Module 2 | Mock QClient.run(), verify RemoteExecutor sends slug+conditions and puts result into queue |
| `test_remote_executor_wraps_connection_error` | Module 2 | QClient raises ConnectionError → RemoteExecutor wraps in QueryException with worker address |
| `test_remote_executor_wraps_timeout` | Module 2 | QClient times out → QueryException with timeout details |
| `test_remote_executor_propagates_slug_not_found` | Module 2 | QClient returns SlugNotFound → RemoteExecutor re-raises |
| `test_thread_query_uses_local_executor_by_default` | Module 3 | ThreadQuery without remote_config uses LocalExecutor |
| `test_thread_query_uses_remote_executor_with_config` | Module 3 | ThreadQuery with remote_config uses RemoteExecutor |
| `test_thread_query_fetch_delegates_to_executor` | Module 3 | Verify fetch() calls executor.execute() with correct args |
| `test_multiqs_detects_remote_flag` | Module 4 | Query dict with `remote: true` triggers RemoteConfig creation |
| `test_multiqs_uses_central_config_fallback` | Module 4 | No per-query `worker` → falls back to QWORKER_HOST/PORT |
| `test_multiqs_raises_on_no_worker_config` | Module 4 | `remote: true` but no worker and no central config → DriverError |
| `test_multiqs_strips_remote_keys` | Module 4 | `remote` and `worker` keys are NOT passed into the query dict |
| `test_config_defaults_to_none` | Module 5 | QWORKER_HOST/PORT default to None when not configured |

### Integration Tests

| Test | Description |
|---|---|
| `test_mixed_local_remote_queries` | MultiQS with one local and one remote query; mock qworker returns DataFrame; verify both results in final dict |
| `test_remote_failure_does_not_affect_local` | Remote query fails, local succeeds; verify local result present and remote error reported |
| `test_backward_compatibility_no_remote_key` | Existing config without `remote` key works identically to current behavior |

### Test Data / Fixtures

```python
@pytest.fixture
def remote_config():
    return RemoteConfig(host="localhost", port=8888, timeout=30)

@pytest.fixture
def sample_query_dict():
    return {"slug": "test-query", "store_id": 42}

@pytest.fixture
def sample_remote_query_dict():
    return {"slug": "test-query", "remote": True, "worker": "localhost:8888"}

@pytest.fixture
def mock_qclient(mocker):
    client = mocker.AsyncMock(spec=QClient)
    client.run.return_value = pd.DataFrame({"id": [1, 2], "value": [10, 20]})
    return client
```

---

## 5. Acceptance Criteria

- [ ] All unit tests pass (`pytest tests/ -k "executor or remote_exec" -v`)
- [ ] All integration tests pass
- [ ] Existing MultiQS tests pass unchanged (backward compatibility)
- [ ] A query with `remote: true` and valid worker config dispatches via QClient.run()
- [ ] A query with `remote: true` but no worker config and no central QWORKER_HOST raises DriverError
- [ ] Remote query errors propagate with worker address in the error message (no silent fallback)
- [ ] The `remote` and `worker` keys are stripped from the query dict before execution
- [ ] ThreadQuery without `remote_config` behaves identically to current code (LocalExecutor path)
- [ ] `X-Remote-Queries` response header lists names of remotely-executed queries
- [ ] ThreadQuery's JSON schema includes `remote` (boolean) and `worker` (string, optional)
- [ ] `QWORKER_HOST`, `QWORKER_PORT`, `QWORKER_TIMEOUT` configurable via navconfig
- [ ] Qworker interface contract documented in `sdd/contracts/`
- [ ] No breaking changes to existing public API
- [ ] `ruff check` and `mypy` pass on all modified files

---

## 6. Codebase Contract

### Verified Imports

```python
# These imports have been confirmed to work:
from querysource.queries.multi.sources.query import ThreadQuery  # sources/__init__.py:4
from querysource.queries.multi.sources.base import ThreadSource  # sources/__init__.py:2
from querysource.queries.multi.sources import SOURCE_REGISTRY     # sources/__init__.py:24
from querysource.queries.obj import QueryObject                   # query.py:6 (relative: ...obj)
from querysource.exceptions import (
    QueryException,   # exceptions.py:6
    SlugNotFound,     # exceptions.py:34
    DriverError,      # exceptions.py:58
    DataNotFound,     # exceptions.py:48
    ParserError,      # exceptions.py:70
)
from querysource.conf import *  # conf.py uses navconfig.config (navconfig import at line 5)

# External — qworker (separate package):
from qw.client import QClient          # qw/client.py:58
from qw.wrappers import FuncWrapper     # qw/wrappers/__init__.py
from qw.conf import (
    WORKER_DEFAULT_HOST,  # qw/conf.py:15 (default '0.0.0.0')
    WORKER_DEFAULT_PORT,  # qw/conf.py:16 (default 8888)
)
```

### Existing Class Signatures

```python
# querysource/queries/multi/sources/query.py
class ThreadQuery(ThreadSource):                                          # line 10
    _catalog = {...}                                                       # line 23
    def __init__(self, name: str, query: dict,
                 request: web.Request, queue: asyncio.Queue):              # line 127
    @property
    def slug(self):                                                        # line 138
    async def fetch(self) -> pd.DataFrame | None:                          # line 150

# querysource/queries/multi/sources/base.py
class ThreadSource(threading.Thread, ABC):                                 # line 11
    def __init__(self, name: str, options: dict,
                 request: web.Request, queue: asyncio.Queue) -> None:      # line 22
    def resolve_credential(self, key: str, value: str) -> str:             # line 37
    @property
    def slug(self) -> str:                                                 # line 64
    @abstractmethod
    async def fetch(self) -> pd.DataFrame:                                 # line 74
    def run(self) -> None:                                                 # line 90

# querysource/queries/multi/__init__.py
class MultiQS(BaseQuery):                                                  # line 53
    def __init__(self, slug=None, queries=None, files=None,
                 query=None, conditions=None, request=None,
                 loop=None, user_session=None, **kwargs):                  # line 59
    self._queue = asyncio.Queue()                                          # line 79
    self._queries = queries                                                # line 84
    async def query(self):                                                 # line 108
    # ThreadQuery instantiation:
    #   t = ThreadQuery(name, query, self._request, self._queue)           # line 152

# querysource/queries/obj.py
class QueryObject(BaseQuery):                                              # line 20
    def __init__(self, name, query, conditions=None, request=None,
                 queue=None, loop=None):                                   # line 26
    self._queue = queue                                                    # line 46
    async def build_provider(self):                                        # line 65
    async def query(self):                                                 # line 183
    # queue put at line 203: await self._queue.put({self._name: result})

# qw/client.py
class QClient:                                                             # line 58
    timeout: int = 5                                                       # line 69
    def __init__(self, worker_list: list = None, timeout: int = 5):        # line 72
    async def run(self, fn: Any, *args,
                  use_wrapper: bool = False, **kwargs):                    # line 326
    async def queue(self, fn: Any, *args,
                    use_wrapper: bool = True, **kwargs):                   # line 420
    async def get_worker_connection(self):                                  # line 243
    async def sendto_worker(self, func, writer):                           # line 260
    async def get_result(self, reader, writer):                            # line 280

# qw/wrappers/func.py
class FuncWrapper(QueueWrapper):                                           # line 7
    def __init__(self, host, func, *args, **kwargs):                       # line 9
    async def __call__(self):                                              # line 15
```

### Key Attributes & Constants

- `ThreadSource._queue` → `asyncio.Queue` (base.py:30)
- `ThreadSource.exc` → `Optional[Exception]` (base.py:31)
- `ThreadSource._name` → `str` (base.py:32)
- `ThreadSource._options` → `dict` (base.py:33)
- `ThreadSource._request` → `web.Request` (base.py:34)
- `MultiQS._queue` → `asyncio.Queue` (multi/__init__.py:79)
- `MultiQS._queries` → `Optional[list]` (multi/__init__.py:84)
- `MultiQS._request` → inherited from BaseQuery
- `QClient.timeout` → `int` (client.py:69, default 5)
- `WORKER_DEFAULT_HOST` → `str` (qw/conf.py:15, default '0.0.0.0')
- `WORKER_DEFAULT_PORT` → `int` (qw/conf.py:16, default 8888)
- `navconfig.config` → config resolver used in querysource/conf.py:5

### Configuration References

```python
# querysource/conf.py — pattern for adding new settings (line 5):
from navconfig import BASE_DIR, config

# Existing pattern (conf.py:24):
DBHOST = config.get('DBHOST', fallback='localhost')

# New settings to add (same pattern):
QWORKER_HOST = config.get('QWORKER_HOST', fallback=None)
QWORKER_PORT = config.getint('QWORKER_PORT', fallback=8888)
QWORKER_TIMEOUT = config.getint('QWORKER_TIMEOUT', fallback=60)
```

### Integration Points (Exact Locations)

| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `RemoteExecutor` | `QClient.run()` | method call | `qw/client.py:326` |
| `LocalExecutor` | `QueryObject.__init__()` | constructor | `querysource/queries/obj.py:26` |
| `LocalExecutor` | `QueryObject.build_provider()` | method call | `querysource/queries/obj.py:65` |
| `LocalExecutor` | `QueryObject.query()` | method call | `querysource/queries/obj.py:183` |
| `ThreadQuery.__init__` | `RemoteExecutor.__init__` | constructor | new code |
| `MultiQS.query()` | `ThreadQuery.__init__` (with remote_config) | constructor arg | `multi/__init__.py:152` |
| `QWORKER_HOST/PORT` | `navconfig.config.get()` | config lookup | `querysource/conf.py:5` |

### Does NOT Exist (Anti-Hallucination)

- ~~`querysource.queries.multi.sources.remote`~~ — no remote module exists
- ~~`querysource.queries.multi.sources.executors`~~ — does not exist yet; this spec creates it
- ~~`ThreadQuery.executor`~~ — no executor attribute; this is what we're adding
- ~~`ThreadQuery.remote`~~ — no remote flag exists yet
- ~~`ThreadQuery._remote_config`~~ — does not exist yet
- ~~`QClient.run_stream()`~~ — QClient has no streaming method
- ~~`QClient.run_query()`~~ — no dedicated query method; only generic `run()`
- ~~`qw.handlers.query`~~ — no query handler module exists in qworker
- ~~`querysource.conf.QWORKER_HOST`~~ — no qworker config exists in querysource yet
- ~~`MultiQS.remote_queries`~~ — no remote query tracking exists
- ~~`MultiQS._remote_config`~~ — does not exist yet
- ~~`X-Remote-Queries` header~~ — not yet emitted by QueryHandler

---

## 7. Implementation Notes & Constraints

### Patterns to Follow

- **Config pattern**: Use `navconfig.config.get()` / `.getint()` with `fallback=` as
  established in `querysource/conf.py` (see lines 24–60 for examples).
- **Error wrapping**: Wrap QClient transport errors in `QueryException` with the worker
  address for diagnostics, matching the existing pattern where `MultiQS.query()` catches
  exceptions from ThreadSource and reports `t.slug` in the error message (multi/__init__.py:196–210).
- **Queue contract**: The executor MUST put `{name: DataFrame}` into the queue, matching
  the contract at `QueryObject.query()` line 203 and `ThreadSource.run()` line 107.
- **Thread safety**: RemoteExecutor runs inside a thread with its own event loop (via
  ThreadSource.run() line 102–103). QClient is instantiated fresh per-call inside the
  thread's loop — no shared state across threads.

### Known Risks / Gotchas

- **QClient inside a thread's event loop**: QClient normally grabs the running event loop
  at init time (qw/client.py:74). Inside ThreadSource.run(), a new loop is created
  (base.py:102). QClient must be instantiated AFTER `asyncio.set_event_loop(loop)` — i.e.,
  inside `RemoteExecutor.execute()`, not in `__init__()`.
- **cloudpickle DataFrame size**: Large DataFrames serialized via cloudpickle travel over
  TCP in one shot. For very large results (>100MB), this may cause memory pressure on both
  sides. v2 streaming addresses this.
- **Worker unreachable**: Remote queries add a network failure mode. Errors propagate
  (no fallback), but operators need monitoring on qworker availability.
- **Slug table sync**: The remote worker must have the same slug definitions as the
  QuerySource server. Stale slugs cause SlugNotFound on the worker side.
- **Thread join timeout**: MultiQS joins threads with a 30-second timeout
  (multi/__init__.py:191). Remote queries that take longer than 30s will be reported as
  timed out even if the qworker is still working. The `QWORKER_TIMEOUT` config should
  be coordinated with the join timeout.
- **QClient connection timeout vs. execution timeout**: QClient's `timeout` attribute
  (default 5s) governs TCP connection, not query execution. Long-running remote queries
  may need `asyncio.wait_for()` wrapping in RemoteExecutor.

### External Dependencies

| Package | Version | Reason |
|---|---|---|
| `qworker` | existing (internal) | QClient for remote job dispatch |
| `cloudpickle` | already in qworker deps | DataFrame serialization (transitive via qworker) |
| `navconfig` | existing in querysource | Central config for QWORKER_* settings |

No new external dependencies are introduced to querysource. `qworker` is an optional
dependency — RemoteExecutor imports `QClient` lazily (only when `remote: true`).

---

## Worktree Strategy

- **Default isolation**: `per-spec` — all tasks run sequentially in one worktree.
- **Rationale**: Tasks 1–4 are tightly coupled through the `QueryExecutor` interface.
  Splitting them into separate worktrees risks interface mismatches. Task 5 (config) and
  Task 7 (documentation) are independent but trivial enough to not warrant parallelization.
- **Cross-feature dependencies**: None. No in-flight specs touch ThreadQuery or the
  MultiQS dispatch layer.

---

## Qworker Interface Contract

This section documents what the qworker side must implement. A separate spec in the
qworker repository will cover implementation details.

### QueryTask Handler

The qworker must register a handler that:

1. **Receives** a job descriptor: `{slug: str, conditions: dict, options: dict}`
2. **Instantiates** a local `QueryObject(name=slug, query={"slug": slug, **conditions}, queue=internal_queue)`
3. **Executes** `await query_obj.build_provider()` then `await query_obj.query()`
4. **Returns** the raw `pd.DataFrame` result (cloudpickle-serialized by the existing protocol)

### Handler Signature

```
async def query_handler(slug: str, conditions: dict = None, **options) -> pd.DataFrame
```

### Error Contract

- `SlugNotFound` → propagated as-is (QClient returns BaseException instances)
- `QueryException` → propagated as-is
- `DriverError` → propagated as-is
- Connection/credential errors on the worker side → wrapped in `QueryException`

### v2 Streaming Extension (Future)

For chunked-row streaming, the contract extends to:
- Worker publishes chunks to a Redis stream keyed by `task_id`
- Each chunk is a serialized DataFrame slice (N rows)
- Final chunk includes a sentinel marker
- Client subscribes to the stream and feeds chunks into the asyncio.Queue as they arrive

This does NOT need to be implemented for v1.

---

## 8. Open Questions

- [ ] Should `QWORKER_HOST`/`QWORKER_PORT` support a comma-separated list for multiple workers (round-robin)? QClient already supports `worker_list` with round-robin scheduling. — *Owner: Jesus*
- [ ] What timeout should remote queries use? The current QClient default is 5s (connection), but query execution can take much longer. Should there be a `QWORKER_QUERY_TIMEOUT` separate from connection timeout? — *Owner: Jesus*
- [ ] Should the qworker QueryHandler support raw SQL queries (`query` key) in addition to slugs, or only slugs for security reasons? — *Owner: Jesus*
- [ ] For v2 streaming: should the Redis stream key include a namespace/prefix to avoid collisions with other qworker streams? — *Owner: Jesus*
- [ ] Should remote query results be cached (e.g., in Redis) to avoid re-dispatching identical slugs+conditions? — *Owner: Jesus*

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-05-26 | Jesus Lara | Initial draft from brainstorm |
