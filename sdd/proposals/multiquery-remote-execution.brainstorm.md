---
type: feature
base_branch: dev
---

# Brainstorm: MultiQuery Remote Execution

**Date**: 2026-05-26
**Author**: Jesus Lara
**Status**: exploration
**Recommended Option**: Option A

---

## Problem Statement

MultiQS orchestrates concurrent query execution via `ThreadQuery`, which spawns OS threads
with dedicated asyncio event loops to run `QueryObject` in-process. When QuerySource runs
behind an HTTP server, every ThreadQuery competes for the same CPU/memory/connection-pool
resources as the request-handling loop. Under heavy multi-query workloads, this starves the
HTTP server and degrades latency for all users.

**Who is affected**: Operators running QuerySource in production HTTP deployments with
resource-intensive multi-query slugs; end users experiencing degraded response times.

**Why now**: As MultiQS adoption grows and query complexity increases (joins, transforms,
large datasets), in-process execution becomes the bottleneck.

## Constraints & Requirements

- **Opt-in per query**: Only queries explicitly marked `remote: true` run on a remote worker; all others stay local.
- **Backward compatible**: Existing MultiQS configs without `remote` keys must work identically.
- **Composition over inheritance**: ThreadQuery gains remote capability via an injected executor strategy, not a new subclass.
- **Fail-fast on remote errors**: No silent fallback to local execution — propagate remote errors so operators can diagnose their setup.
- **Central config + per-query override**: A querysource-level setting defines the default qworker pool; individual queries can override with a `worker` key.
- **v1 bulk return**: QClient.run() sends the entire serialized DataFrame back in one shot via the existing cloudpickle protocol.
- **v2 streaming interface**: Design the internal interface so chunked-row streaming (via Redis pub/sub or extended TCP protocol) can be added later without breaking changes.
- **Worker self-contained**: The remote qworker has its own QuerySource install and credential configuration — no credential serialization across the wire.
- **Dedicated query handler**: Qworker receives a dedicated handler type (not a generic callable) that understands slugs and providers.
- **Cross-package contract**: This spec covers QuerySource changes in full and documents the required qworker API contract (implementation details are a separate qworker spec).

---

## Options Explored

### Option A: Composition-Based RemoteExecutor in ThreadQuery (Recommended)

ThreadQuery gains an optional `RemoteExecutor` strategy that is injected when the query
config contains `remote: true`. When present, `fetch()` delegates to the executor instead
of creating a local `QueryObject`. The executor uses `QClient.run()` to dispatch a
lightweight job descriptor (slug name + conditions) to a remote qworker that has its own
QuerySource install. The qworker runs a dedicated `QueryHandler` that resolves the slug
locally, executes it, and returns the raw DataFrame.

MultiQS reads a central `QWORKER_DEFAULT_HOST` / `QWORKER_DEFAULT_PORT` config and passes
it to ThreadQuery along with the per-query `worker` override if present. The executor
interface is an abstract class (`QueryExecutor`) with two concrete implementations:
`LocalExecutor` (current behavior, wraps QueryObject) and `RemoteExecutor` (wraps QClient).

The `QueryExecutor` protocol defines a single method:
`async def execute(name, query_dict, queue, request) -> None` — matching the current
`fetch()` contract where results go into the shared queue.

For v2, a `StreamingRemoteExecutor` can extend `RemoteExecutor` to subscribe to a Redis
stream and push chunked DataFrames into the queue incrementally.

✅ **Pros:**
- Minimal change to ThreadQuery — just an executor selection branch in `fetch()`
- Clean separation of concerns: transport logic lives in executor, not in ThreadQuery
- Easy to test: mock the executor interface
- v2 streaming is a new executor, no changes to ThreadQuery or MultiQS
- No new thread classes to register or maintain

❌ **Cons:**
- Slightly more indirection than a direct QClient call in fetch()
- Executor abstraction adds a new module and interface

📊 **Effort:** Medium

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `qworker` (QClient) | Remote job dispatch and result retrieval | Existing package, `QClient.run()` |
| `cloudpickle` | DataFrame serialization across process boundaries | Already used by qworker |
| `navconfig` | Central config for QWORKER_* settings | Already used in querysource |

🔗 **Existing Code to Reuse:**
- `querysource/queries/multi/sources/query.py` — ThreadQuery class, modified to accept executor
- `querysource/queries/multi/sources/base.py` — ThreadSource base class (unchanged)
- `querysource/queries/multi/__init__.py` — MultiQS dispatcher (minor: reads `remote` key, passes config)
- `querysource/conf.py` — Add QWORKER_* settings
- `qw/client.py` — QClient for remote dispatch

---

### Option B: New RemoteQuery Subclass of ThreadSource

Create a new `RemoteQuery` class extending `ThreadSource` that handles remote dispatch
exclusively. MultiQS checks `remote: true` and instantiates `RemoteQuery` instead of
`ThreadQuery`. RemoteQuery's `fetch()` creates a QClient, sends the slug+conditions, and
puts the returned DataFrame into the queue.

This keeps ThreadQuery completely unchanged but introduces a parallel class hierarchy.
MultiQS needs branching logic to choose between ThreadQuery and RemoteQuery.

✅ **Pros:**
- Zero changes to ThreadQuery — completely isolated
- Simple to understand: one class = one execution mode
- Can be registered in SOURCE_REGISTRY for explicit dispatch

❌ **Cons:**
- Code duplication: RemoteQuery and ThreadQuery share config parsing, slug resolution, error handling
- MultiQS needs explicit branching logic (`if remote: RemoteQuery() else: ThreadQuery()`)
- Adding a third execution mode (e.g., streaming) requires yet another subclass
- Harder to test — need integration tests for each parallel class

📊 **Effort:** Medium

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `qworker` (QClient) | Remote job dispatch | Same as Option A |
| `cloudpickle` | Serialization | Same as Option A |

🔗 **Existing Code to Reuse:**
- `querysource/queries/multi/sources/base.py` — ThreadSource base
- `querysource/queries/multi/__init__.py` — MultiQS (modified branching)
- `qw/client.py` — QClient

---

### Option C: AsyncIO-Native Remote Dispatch (No Threads)

Instead of running remote queries inside OS threads, make the remote dispatch an asyncio
coroutine that runs directly in MultiQS's event loop. Since the remote call is I/O-bound
(TCP to qworker, wait for result), there's no need for a thread. MultiQS would use
`asyncio.gather()` for remote queries alongside threaded local queries.

This changes the execution model: remote queries are async tasks, local queries are threads.
Results still go into the shared asyncio.Queue.

✅ **Pros:**
- No OS thread overhead for remote queries — lighter resource usage
- Natural async/await pattern for network I/O
- Could support streaming via async generators natively

❌ **Cons:**
- Breaks the uniform ThreadSource model — remote queries are async tasks, not threads
- MultiQS needs significant refactoring to manage two execution models (threads + async tasks)
- Thread join timeout logic doesn't apply to async tasks — need parallel timeout handling
- QClient.run() already works in async context, but integrating into MultiQS's thread-join pattern is complex
- Higher risk: changes the core orchestration model

📊 **Effort:** High

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `qworker` (QClient) | Remote dispatch | Same as Option A |
| `asyncio` | Native async orchestration | Stdlib |

🔗 **Existing Code to Reuse:**
- `querysource/queries/multi/__init__.py` — MultiQS (heavy refactor)
- `qw/client.py` — QClient

---

## Recommendation

**Option A** is recommended because:

- It respects the user's explicit decision for **composition over inheritance** (ThreadQuery gains capability, no new classes).
- The executor abstraction is minimal (one abstract method) but provides a clean seam for v2 streaming without touching ThreadQuery again.
- It preserves the existing ThreadSource threading model entirely — remote queries still run in threads, they just do network I/O instead of local DB queries. This means MultiQS's join/timeout/queue-drain logic works unchanged.
- Option B's class duplication would create maintenance burden as the query config grows. Option C's dual execution model is high-risk refactoring for a v1 that just needs to dispatch to QClient.

**Tradeoff accepted**: The executor abstraction adds a small layer of indirection, but it pays for itself immediately in testability and future extensibility.

---

## Feature Description

### User-Facing Behavior

Users add `remote: true` (and optionally `worker: "host:port"`) to individual query entries
in their MultiQS YAML/JSON config:

```yaml
queries:
  revenue:
    slug: monthly-revenue
    remote: true
    worker: "qworker1:8888"
  costs:
    slug: monthly-costs
    # runs locally (default)
```

Behavior is identical from the response perspective — the user gets the same combined
DataFrame result. The only observable differences:
- Remote queries execute on the qworker, freeing HTTP server resources.
- Errors from remote queries include the worker address in the error message.
- A new `X-Remote-Queries` response header lists which queries ran remotely.

A central querysource config provides defaults:
```
QWORKER_HOST=qworker1
QWORKER_PORT=8888
```

If `remote: true` is set but no `worker` key and no central config, the query fails with a
clear configuration error.

### Internal Behavior

1. **MultiQS.query()** iterates `self._queries`. For each entry:
   - Checks for `remote: true` in the query dict.
   - If remote: pops the `remote` and `worker` keys, resolves worker address (per-query override → central config), passes them to ThreadQuery.
   - If local: creates ThreadQuery as before.

2. **ThreadQuery.__init__()** receives an optional `remote_config: dict | None` parameter.
   - If `remote_config` is provided, instantiates a `RemoteExecutor(host, port, timeout)`.
   - Otherwise, uses the default `LocalExecutor()` (current QueryObject path).

3. **ThreadQuery.fetch()** delegates to the selected executor:
   - `LocalExecutor.execute()`: Current behavior — creates QueryObject, builds provider, queries, result goes into queue.
   - `RemoteExecutor.execute()`: Creates QClient, calls `QClient.run(query_task_fn, slug, conditions)` where `query_task_fn` is a reference to the qworker-side handler. The returned DataFrame is put into the queue.

4. **Qworker side** (contract — separate spec for implementation):
   - A dedicated `QueryTask` handler receives `{slug, conditions, options}`.
   - The handler instantiates a local QueryObject (qworker has QuerySource installed), builds the provider, executes the query, and returns the raw DataFrame.
   - The DataFrame is serialized via cloudpickle and sent back through the existing TCP result protocol.

### Edge Cases & Error Handling

- **Worker unreachable**: QClient raises `ConnectionError` → RemoteExecutor wraps it in `QueryException` with worker address in message → ThreadQuery captures in `self.exc` → MultiQS reports which query failed and why.
- **Worker timeout**: QClient has a configurable timeout (default 5s for connection, configurable for execution). On timeout, same error propagation path.
- **Serialization failure**: If the DataFrame can't be cloudpickle'd (rare — pandas DataFrames are pickle-safe), the error propagates as-is.
- **Slug not found on worker**: The worker's QueryObject raises `SlugNotFound` → serialized back as exception → QClient returns it as BaseException → RemoteExecutor re-raises.
- **Mixed remote/local failure**: One remote query failing doesn't affect local queries. MultiQS's existing error collection (checking `t.exc` after join) handles this naturally.
- **No worker configured**: If `remote: true` but no worker address resolvable, raise `DriverError` at ThreadQuery init time, before the thread starts.
- **Worker has stale slug definitions**: This is the operator's responsibility — keep slug tables in sync across deployments.

---

## Capabilities

### New Capabilities
- `remote-query-execution`: Ability to dispatch individual MultiQS queries to a remote qworker server via QClient
- `query-executor-strategy`: Pluggable executor interface (LocalExecutor / RemoteExecutor) for ThreadQuery
- `qworker-query-handler`: Dedicated query handler on the qworker side that executes QuerySource slugs (contract only — implementation in qworker repo)

### Modified Capabilities
- `multiqs-query-dispatch`: MultiQS query dispatch extended to detect `remote: true` and pass worker config to ThreadQuery
- `thread-query-fetch`: ThreadQuery.fetch() refactored to delegate to an executor strategy

---

## Impact & Integration

| Affected Component | Impact Type | Notes |
|---|---|---|
| `querysource/queries/multi/sources/query.py` | modifies | ThreadQuery gains executor injection |
| `querysource/queries/multi/sources/base.py` | unchanged | ThreadSource base unaffected |
| `querysource/queries/multi/__init__.py` | modifies | MultiQS reads `remote`/`worker` keys, passes config |
| `querysource/conf.py` | extends | Add QWORKER_HOST, QWORKER_PORT, QWORKER_TIMEOUT settings |
| `querysource/queries/multi/sources/__init__.py` | extends | Export new executor classes |
| `qw/client.py` | unchanged | QClient used as-is |
| `qw/server.py` | extends (contract) | Needs dedicated QueryTask handler |
| JSON schema / catalog metadata | extends | Add `remote`, `worker` to ThreadQuery's JSON schema |

---

## Code Context

### User-Provided Code

No code snippets provided by the user. Feature described in prose.

### Verified Codebase References

#### Classes & Signatures
```python
# From querysource/queries/multi/sources/query.py:10
class ThreadQuery(ThreadSource):
    def __init__(self, name: str, query: dict, request: web.Request, queue: asyncio.Queue):  # line 127
    @property
    def slug(self):  # line 138
    async def fetch(self) -> pd.DataFrame | None:  # line 150

# From querysource/queries/multi/sources/base.py:11
class ThreadSource(threading.Thread, ABC):
    def __init__(self, name: str, options: dict, request: web.Request, queue: asyncio.Queue) -> None:  # line 22
    def resolve_credential(self, key: str, value: str) -> str:  # line 37
    @property
    def slug(self) -> str:  # line 64
    @abstractmethod
    async def fetch(self) -> pd.DataFrame:  # line 74
    def run(self) -> None:  # line 90

# From querysource/queries/multi/__init__.py:53
class MultiQS(BaseQuery):
    def __init__(self, slug=None, queries=None, files=None, query=None, conditions=None, request=None, loop=None, user_session=None, **kwargs):  # line 59
    async def query(self):  # line 108
    # ThreadQuery instantiation at line 152:
    #   t = ThreadQuery(name, query, self._request, self._queue)

# From querysource/queries/obj.py:20
class QueryObject(BaseQuery):
    def __init__(self, name, query, conditions=None, request=None, queue=None, loop=None):  # line 26
    async def build_provider(self):  # line 65
    async def query(self):  # line 183

# From qw/client.py:58
class QClient:
    def __init__(self, worker_list: list = None, timeout: int = 5):  # line 72
    async def run(self, fn: Any, *args, use_wrapper: bool = False, **kwargs):  # line 326
    async def queue(self, fn: Any, *args, use_wrapper: bool = True, **kwargs):  # line 420
    async def get_worker_connection(self):  # line 243
    async def sendto_worker(self, func, writer):  # line 260
    async def get_result(self, reader, writer):  # line 280

# From qw/wrappers/func.py:7
class FuncWrapper(QueueWrapper):
    def __init__(self, host, func, *args, **kwargs):  # line 9
    async def __call__(self):  # line 15
```

#### Verified Imports
```python
# These imports have been confirmed to work:
from querysource.queries.multi.sources.query import ThreadQuery  # sources/__init__.py:4
from querysource.queries.multi.sources.base import ThreadSource  # sources/__init__.py:2
from querysource.queries.multi.sources import SOURCE_REGISTRY  # sources/__init__.py:24
from querysource.queries.obj import QueryObject  # query.py:6
from querysource.conf import *  # conf.py uses navconfig.config
from qw.client import QClient  # qw/client.py:58
from qw.wrappers import FuncWrapper  # qw/wrappers/__init__.py
```

#### Key Attributes & Constants
- `ThreadSource._queue` → `asyncio.Queue` (base.py:30)
- `ThreadSource.exc` → `Optional[Exception]` (base.py:31)
- `ThreadSource._name` → `str` (base.py:32)
- `MultiQS._queue` → `asyncio.Queue` (multi/__init__.py:79)
- `MultiQS._queries` → `Optional[list]` (multi/__init__.py:84)
- `QClient.timeout` → `int` (client.py:69, default 5)
- `WORKER_DEFAULT_HOST` → `str` (qw/conf.py:15, default '0.0.0.0')
- `WORKER_DEFAULT_PORT` → `int` (qw/conf.py:16, default 8888)

### Does NOT Exist (Anti-Hallucination)
- ~~`querysource.queries.multi.sources.remote`~~ — no remote module exists
- ~~`ThreadQuery.executor`~~ — no executor attribute; this is what we're adding
- ~~`ThreadQuery.remote`~~ — no remote flag exists yet
- ~~`QClient.run_stream()`~~ — QClient has no streaming method
- ~~`QClient.run_query()`~~ — no dedicated query method; only generic `run()`
- ~~`qw.handlers.query`~~ — no query handler module exists in qworker
- ~~`querysource.conf.QWORKER_HOST`~~ — no qworker config exists in querysource yet
- ~~`MultiQS.remote_queries`~~ — no remote query tracking exists

---

## Parallelism Assessment

- **Internal parallelism**: Tasks can be split into independent units:
  1. QueryExecutor interface + LocalExecutor (pure refactor, no behavior change)
  2. RemoteExecutor implementation (depends on interface but not on LocalExecutor)
  3. ThreadQuery modification to accept/use executor (depends on interface)
  4. MultiQS config parsing for `remote`/`worker` keys (independent of executor internals)
  5. Config additions to `conf.py` (independent)
  6. Qworker QueryHandler contract documentation (independent)

  Tasks 1 and 5 can run in parallel. Tasks 2 and 3 depend on 1. Task 4 is independent.

- **Cross-feature independence**: No known in-flight specs touch ThreadQuery or MultiQS dispatch logic. The `composite-datasets` brainstorm touches MultiQS operators (post-query pipeline) but not the dispatch/fetch layer.

- **Recommended isolation**: `per-spec` — tasks are tightly coupled through the executor interface; a single worktree avoids integration issues between the executor abstraction and its consumers.

- **Rationale**: The executor interface is the critical seam — if tasks 1-3 diverge in separate worktrees, merging them risks interface mismatches. Sequential implementation in one worktree with small commits is safer and not significantly slower given the medium effort level.

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

### Handler Registration

The handler should be a callable that QWorker's `TaskExecutor` can invoke:

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

This does NOT need to be implemented for v1 — just acknowledged as the design direction.

---

## Open Questions

- [ ] Should `QWORKER_HOST`/`QWORKER_PORT` support a comma-separated list for multiple workers (round-robin)? — *Owner: Jesus*
- [ ] What timeout should remote queries use? The current QClient default is 5s (connection), but query execution can take much longer. Should there be a `QWORKER_QUERY_TIMEOUT` separate from connection timeout? — *Owner: Jesus*
- [ ] Should the qworker QueryHandler support raw SQL queries (`query` key) in addition to slugs, or only slugs for security reasons? — *Owner: Jesus*
- [ ] For v2 streaming: should the Redis stream key include a namespace/prefix to avoid collisions with other qworker streams? — *Owner: Jesus*
- [ ] Should remote query results be cached (e.g., in Redis) to avoid re-dispatching identical slugs+conditions? — *Owner: Jesus*
