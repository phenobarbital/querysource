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
