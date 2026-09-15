---
id: F001
query_id: Q001
type: grep
executed_at: 2026-09-15T01:11:03Z
parent_id: null
depth: 0
---

# F001 — storage-map

## Summary

Stored query identity is a single query_slug primary key with one configured model schema. Runtime consumers cluster in connection lookup, management, scheduling, providers and parsers.

## Citations

- path: `querysource/models.py`
  lines: 48-107
  symbol: `QueryModel`

- path: `querysource/interfaces/connections.py`
  lines: 444-488
  symbol: `Connection.get_query_slug`

## Audit note

Related searches and targeted ranges were batched. Citations represent inspected ranges or exact search matches, not a claim that every cited file was read in full. The timestamp records digest assembly at research completion.
