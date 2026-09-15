# QWorker Query Handler — Interface Contract

**Feature**: FEAT-101 — MultiQuery Remote Execution
**Date**: 2026-05-26
**Status**: Approved
**Owner**: Jesus Lara

---

## Purpose

This document defines the interface contract that the qworker server must implement
to support remote query execution from QuerySource's MultiQS component.

When a MultiQS query has `remote: true` in its configuration, QuerySource's
`RemoteExecutor` uses `QClient.run()` to dispatch the query to a remote qworker
server. The qworker must have a handler registered that accepts this dispatch,
executes the query locally using its own QuerySource installation, and returns the
result DataFrame.

---

## Prerequisites

The qworker server must have:

1. **QuerySource installed** — the same version or a compatible version as the
   calling QuerySource server.
2. **Database credentials configured** — the qworker's own `env/` or navconfig
   environment must have credentials for all data sources that remote queries
   will access.
3. **Slug table synchronized** — the qworker's slug table must contain all slugs
   that will be dispatched remotely. Stale or missing slugs cause `SlugNotFound`
   on the worker side.
4. **`qworker` server running** — the TCP server must be listening on the
   configured host/port (default 0.0.0.0:8888).

---

## Handler Signature

```python
async def query_handler(slug: str = None, conditions: dict = None, **options) -> pd.DataFrame:
    """Execute a QuerySource query and return the result DataFrame.

    This handler is invoked by the qworker server when RemoteExecutor calls
    QClient.run("querysource.remote.query_handler", slug, conditions=conditions).

    Args:
        slug: The query slug to execute (from the stored slug table).
        conditions: Key-value filter conditions merged into the query dict.
            May be None or empty if the query has no runtime parameters.
        **options: Additional options (reserved for future use).

    Returns:
        pd.DataFrame: The raw query result. Serialized via cloudpickle by
            the qworker protocol before transmission.

    Raises:
        SlugNotFound: If the slug is not found in the local slug table.
        QueryException: If query execution fails (provider error, SQL error, etc.)
        DriverError: If the data source connection fails.
    """
```

---

## Input Format

`RemoteExecutor` dispatches jobs using the following call pattern:

```python
result = await client.run(
    "querysource.remote.query_handler",
    slug,                          # positional: str
    conditions=conditions,         # keyword: dict | None
)
```

The qworker server receives this as a function call with:

| Parameter | Type | Description |
|-----------|------|-------------|
| `slug` | `str` | The stored query slug name |
| `conditions` | `dict \| None` | Runtime filter conditions (e.g., `{"store_id": 42}`) |

Note: Raw SQL queries (without a slug) are passed differently — see the
"Raw Query Support" section below.

---

## Execution Flow

The handler must follow this sequence:

```python
import asyncio
import pandas as pd
from querysource.queries.obj import QueryObject


async def query_handler(slug: str = None, conditions: dict = None, **options) -> pd.DataFrame:
    queue = asyncio.Queue()

    # Build the query dict that QueryObject expects
    query = {"slug": slug}
    if conditions:
        query.update(conditions)

    query_obj = QueryObject(
        name=slug,
        query=query,
        queue=queue,
        request=None,    # no HTTP request context on the worker side
        loop=asyncio.get_running_loop(),
    )

    await query_obj.build_provider()
    await query_obj.query()

    # QueryObject puts {slug: DataFrame} into the queue
    result_dict = await queue.get()
    return result_dict[slug]
```

Step-by-step:

1. Create an `asyncio.Queue` to receive the result.
2. Build the query dict: `{"slug": slug, **conditions}`.
3. Instantiate `QueryObject(name=slug, query=query_dict, queue=queue, ...)`.
4. Call `await query_obj.build_provider()` — resolves the data source provider.
5. Call `await query_obj.query()` — executes the query and puts `{slug: DataFrame}` into the queue.
6. Drain the queue and return the DataFrame.

---

## Return Type

The handler must return a `pd.DataFrame`. The qworker protocol serializes the
return value via cloudpickle before transmitting it over TCP.

```python
return result_df   # type: pd.DataFrame
```

The calling `RemoteExecutor.execute()` will then put `{name: result_df}` into the
shared asyncio queue for MultiQS to collect.

---

## Raw Query Support

In addition to slug-based queries, the handler supports raw SQL queries (per the
open question in FEAT-101 §8 resolved by the author). For raw queries, the
`slug` parameter will be `None` and the `conditions` dict will contain `query`,
`driver` or `datasource` keys:

```python
# Called from RemoteExecutor when the query dict has 'query' instead of 'slug':
result = await client.run(
    "querysource.remote.query_handler",
    None,                          # slug = None
    conditions={
        "query": "SELECT * FROM stores WHERE id = :store_id",
        "driver": "pg",
        "store_id": 42,
    }
)
```

The handler adapts by using the `query` key directly:

```python
if slug is None and conditions and "query" in conditions:
    query = dict(conditions)  # {"query": "SELECT ...", "driver": "pg", ...}
    name = "raw"
    query_obj = QueryObject(name=name, query=query, queue=queue, ...)
```

---

## Error Contract

The qworker protocol returns exceptions to the caller as deserialized Python
exception instances. The following exceptions propagate as-is through QClient
to `RemoteExecutor`:

| Exception | Source | Behavior |
|-----------|--------|----------|
| `SlugNotFound` | querysource | Propagates as-is to MultiQS |
| `QueryException` | querysource | Propagates as-is to MultiQS |
| `DriverError` | querysource | Propagates as-is to MultiQS |
| `DataNotFound` | querysource | Propagates as-is to MultiQS |

Transport-level errors (TCP connection failure, timeout, serialization error)
are caught by `RemoteExecutor` and wrapped in `QueryException` with the worker
address included in the message for diagnostics.

**Important**: There is no automatic fallback to local execution. Any error
from a remote query propagates and causes the entire MultiQS pipeline to fail
for that query. Operators must ensure qworker availability independently.

---

## Registration

The handler must be registered with the qworker server so that
`QClient.run("querysource.remote.query_handler", ...)` routes to it correctly.

The registration mechanism depends on the qworker version. The string
`"querysource.remote.query_handler"` is the handler identifier that the calling
`RemoteExecutor` uses.

Example registration (qworker-side):

```python
# In the qworker's startup configuration:
from querysource.remote import query_handler

server.register("querysource.remote.query_handler", query_handler)
```

The module `querysource.remote` is the recommended location for the handler
implementation in the querysource package (future implementation spec).

---

## Versioned Tenant Handler — `tenant_query_handler_v1` (FEAT-147)

FEAT-147 (Per-tenant Queries) introduces a second, **distinct** handler
identifier for any query whose resolved `QueryStore.contract == "tenant"`.
This handler is **not implemented in this repository** — `querysource/
remote.py` does not exist here; it is an external contract the qworker
deployment must implement and register on its own timeline. Until a worker
registers it, `RemoteExecutor.execute()` on the calling side raises a
`QueryException` (missing/unknown handler on the worker side) — there is
**no automatic fallback** to `querysource.remote.query_handler` or to local
execution for a tenant-owned query.

### Why a distinct callable, not a new keyword on the existing handler

`querysource.remote.query_handler` accepts `**options` and silently ignores
unrecognized keywords. Adding an `owner=` keyword to that existing handler
would mean an **old, unupgraded worker silently executes the query against
its own default (public) schema**, discarding tenant ownership without any
error — the worst possible failure mode for a feature whose entire purpose
is definition ownership isolation. A distinct handler name instead makes an
old worker fail loudly (unknown handler / dispatch error), which the
calling `RemoteExecutor` propagates as-is.

### Required Signature

```python
async def tenant_query_handler_v1(
    slug: str | None = None,
    conditions: dict | None = None,
    *,
    owner: TenantOwnerEnvelope,
    **options: Any,
) -> pd.DataFrame:
    """Validate protocol/registry on the worker and execute exactly the
    selected owner — never search, never fall back to another schema.

    Args:
        slug: The query slug to execute (from the OWNED store's slug
            table — see `owner`), or None for a raw query (see
            "Raw Query Support" above; the same slug/None + conditions
            shape applies here as it does for the legacy handler).
        conditions: Key-value filter conditions merged into the query
            dict, with all routing keys (`slug`, `remote`, `worker`)
            already stripped by the caller (see `RemoteExecutor.execute()`,
            `querysource/queries/multi/sources/executors.py`).
        owner: Required, keyword-only `TenantOwnerEnvelope` — see schema
            below. This is the ONLY argument the worker may use to decide
            which physical store owns the query; never re-derive
            ownership from the caller's request/session.
        **options: Reserved for future use.

    Returns:
        pd.DataFrame: The raw query result, serialized via cloudpickle by
            the qworker protocol before transmission (same as the legacy
            handler).

    Raises:
        Handler-not-found / dispatch error: if the worker has not
            registered `tenant_query_handler_v1` (old/unupgraded worker) —
            the calling RemoteExecutor propagates this error as-is (or, if
            it manifests as a connection failure, wrapped in
            QueryException with the worker address), never falls back.
        An explicit, typed error (see "Owner Validation" below): if
            `owner.version` is unsupported, or `owner` does not resolve to
            a store the worker actually has access to.
        SlugNotFound / QueryException / DriverError / DataNotFound: same
            meaning as for the legacy handler, once ownership is validated.
    """
```

### `TenantOwnerEnvelope` Schema

Defined in `querysource/tenants.py` (created by TASK-716) as a `TypedDict`
— it carries only the physical-store identity needed to route the query,
never database credentials or connection objects:

| Field | Type | Description |
|-------|------|--------------|
| `version` | `Literal[1]` | Envelope protocol version. Workers that only understand `query_handler` never see this field; workers implementing `tenant_query_handler_v1` MUST reject any `version` they do not explicitly support (see below) rather than guessing at forward/backward compatibility. |
| `database_namespace` | `str` | Physical database identity (e.g. `"localhost:5432/querysource"`) — matches `QueryStore.database_namespace` on the calling side. |
| `schema` | `str` | The owning tenant's schema (or `"public"` for the literal legacy/public store). |
| `table` | `str` | The queries table name within that schema (normally `"queries"`). |
| `contract` | `Literal["legacy", "tenant"]` | Always `"tenant"` when this handler is invoked — `RemoteExecutor` only routes `"legacy"`-contract stores through `querysource.remote.query_handler`. Present for parity with `QueryStore` and to let the worker assert the caller's own routing decision. |

### Owner Validation (worker-side requirement)

The worker implementation MUST, before executing anything:

1. Reject any `owner.version` it does not explicitly implement (currently
   only `1`) with a typed, explicit error — never silently coerce or
   ignore unknown versions.
2. Resolve `(owner.database_namespace, owner.schema, owner.table)` against
   its own local tenant registry/allowlist and reject with a typed,
   explicit error if that exact tuple is not a store the worker is
   configured to serve — no partial match, no nearest-schema fallback, no
   silent default to `public`.
3. Execute the query identity (`slug`/`conditions`) strictly within the
   resolved owner's store — the same "no owner fallback/search if slug is
   missing" rule the calling side enforces (TASK-727,
   `querysource/queries/multi/__init__.py`).

### Error Contract

Extends the legacy handler's error contract (see "Error Contract" above)
with tenant-specific failure modes:

| Failure | Behavior |
|---------|----------|
| Worker has not registered `tenant_query_handler_v1` | Handler-not-found / dispatch error at the transport layer; propagates as-is through `QClient` to `RemoteExecutor` (or, if it manifests as a connection failure, is wrapped in `QueryException` by the existing `(ConnectionError, OSError)` handling) — no fallback to `query_handler`. |
| `owner.version` unsupported by the worker | Worker raises an explicit, typed error; propagates as-is through `QClient` to `RemoteExecutor` — no fallback. |
| `owner` does not resolve to a store the worker recognizes | Worker raises an explicit, typed error (never a silent default-schema execution); propagates as-is — no fallback. |
| Timeout / TCP failure | Same as the legacy handler: `RemoteExecutor` wraps it in `QueryException` with the worker address for diagnostics. |
| Query execution failure once ownership is validated | `SlugNotFound` / `QueryException` / `DriverError` / `DataNotFound` propagate as-is, exactly like the legacy handler. |

**Important**: exactly as with the legacy handler, there is no automatic
fallback to local execution, and — new for this contract — no fallback
between `tenant_query_handler_v1` and `query_handler` in either direction.
`RemoteExecutor` decides which handler to call based solely on the
resolved `QueryStore.contract` before dispatch; it never retries a tenant
dispatch against the legacy handler, and never sends a legacy dispatch
through the tenant handler.

### Legacy Coexistence

Both handlers are registered independently on the worker and remain
simultaneously available:

- `querysource.remote.query_handler` — unchanged, used for every
  `store is None` (no tenant feature configured) or `store.contract ==
  "legacy"` dispatch (the pre-existing FEAT-101 default).
- `querysource.remote.tenant_query_handler_v1` — used exclusively when
  `store.contract == "tenant"`.

A worker may implement only the legacy handler (pre-FEAT-147 deployment),
only the tenant handler (unsupported/unused configuration), or both. The
calling side never inspects worker capabilities in advance — it always
calls the handler its own routing decision selected, and lets a
handler-not-found error surface as a normal `QueryException` if the
worker hasn't caught up yet.

### Deployment Gate

`querysource.remote.tenant_query_handler_v1` is an **external contract
only** — this repository does not ship a worker-side implementation
(`querysource/remote.py` does not exist here, and this task does not
create it; see TASK-728). Before any tenant-owned query can be dispatched
with `remote: true`, the target qworker deployment MUST:

1. Implement and register `tenant_query_handler_v1` per the signature and
   validation rules above.
2. Maintain its own tenant registry/allowlist capable of resolving a
   `TenantOwnerEnvelope` to a real, permitted store — independently of
   (and never trusting) the calling QuerySource instance's own registry.
3. Be verified against a real multi-tenant deployment before enabling
   `remote: true` for tenant-owned stored queries in production — this is
   explicitly **not verified** by this repository's test suite, which can
   only exercise the calling (`RemoteExecutor`) side against a fake
   `QClient`.

Until a target deployment satisfies all three, tenant-owned remote
dispatch will consistently raise a `QueryException` (handler-not-found) —
this is the intended, safe failure mode, not a bug.

---

## Example Usage (End-to-End)

On the QuerySource (caller) side:

```yaml
# MultiQS YAML config
queries:
  revenue:
    slug: monthly_revenue
    store_id: 42
    remote: true
    worker: "qworker1.internal:8888"
```

This causes `RemoteExecutor` to call:

```python
client = QClient(worker_list=[("qworker1.internal", 8888)], timeout=60)
result = await client.run(
    "querysource.remote.query_handler",
    "monthly_revenue",
    conditions={"store_id": 42},
)
await queue.put({"revenue": result})
```

On the qworker side, `query_handler("monthly_revenue", conditions={"store_id": 42})`
runs the `monthly_revenue` slug locally with `store_id=42`, and returns the resulting
DataFrame.

---

## v2 Streaming Extension (Future — Not in Scope for v1)

This section documents the planned v2 extension for chunked-row streaming.
It is provided for design continuity; no implementation is required for FEAT-101.

### Motivation

For large DataFrames (>100MB), the current cloudpickle-over-TCP approach
transmits the entire result in one shot, causing memory pressure on both sides.
v2 streaming addresses this by chunking the result into smaller pieces.

### Protocol

1. **Worker publishes chunks to Redis**: After executing the query, the handler
   pushes DataFrame slices to a Redis stream keyed by `qworker:<task_id>:result`.
   - Each chunk is a cloudpickle-serialized `pd.DataFrame` slice of N rows.
   - The final chunk includes a sentinel: `{"__done__": True}`.
   - A namespace prefix prevents collisions: `qworker:<task_id>:result`.

2. **Client subscribes to the stream**: `RemoteExecutor` subscribes to the Redis
   stream and feeds chunks into the asyncio queue as they arrive. The queue
   contract is extended to accept chunks, with MultiQS performing a final
   `pd.concat()` after all chunks are received.

3. **Timeout**: A per-chunk timeout applies (default 30s). If a chunk is not
   received within the timeout, RemoteExecutor raises `QueryException`.

### Handler v2 Signature

```python
async def query_handler_v2(
    slug: str = None,
    conditions: dict = None,
    task_id: str = None,
    chunk_size: int = 10000,
    **options,
) -> None:
    """Stream query results to Redis in chunks.

    Returns None — results are published to the Redis stream, not returned
    directly. The caller subscribes to the stream independently.
    """
```

This extension is out of scope for FEAT-101 v1.

---

## Revision History

| Version | Date | Author | Change |
|---------|------|--------|--------|
| 1.0 | 2026-05-26 | Jesus Lara / claude-sonnet-4-6 | Initial contract from FEAT-101 spec |
