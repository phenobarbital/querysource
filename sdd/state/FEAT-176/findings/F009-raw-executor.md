---
id: F009
query_id: Q009
type: read
executed_at: 2026-09-15T01:11:03Z
parent_id: null
depth: 0
---

# F009 — raw-executor

## Summary

Executor resolves datasources or drivers for raw execution and introspection. The existing schema endpoint introspects the target datasource, so tenant discovery must explicitly use the main query metadata database instead.

## Citations

- path: `querysource/queries/executor.py`
  lines: 17-55
  symbol: `Executor.introspect`

- path: `querysource/queries/executor.py`
  lines: 57-64
  symbol: `Executor.start`

## Audit note

Related searches and targeted ranges were batched. Citations represent inspected ranges or exact search matches, not a claim that every cited file was read in full. The timestamp records digest assembly at research completion.
