---
id: F014
query_id: Q014
type: grep
executed_at: 2026-09-15T01:11:03Z
parent_id: null
depth: 0
---

# F014 — nested-queries

## Summary

MultiQS resolves the outer slug itself, then constructs ThreadQuery children. LocalExecutor uses QueryObject; RemoteExecutor passes slug and conditions to a worker callable without a separate tenant argument.

## Citations

- path: `querysource/queries/multi/__init__.py`
  lines: 197-241
  symbol: `MultiQS.query`

- path: `querysource/queries/multi/__init__.py`
  lines: 304-309
  symbol: `MultiQS.query`

- path: `querysource/queries/multi/sources/executors.py`
  lines: 85-112
  symbol: `LocalExecutor.execute`

- path: `querysource/queries/multi/sources/executors.py`
  lines: 180-208
  symbol: `RemoteExecutor.execute`

- path: `querysource/queries/multi/sources/query.py`
  lines: 64-85
  symbol: `ThreadQuery.fetch`

## Audit note

Related searches and targeted ranges were batched. Citations represent inspected ranges or exact search matches, not a claim that every cited file was read in full. The timestamp records digest assembly at research completion.
