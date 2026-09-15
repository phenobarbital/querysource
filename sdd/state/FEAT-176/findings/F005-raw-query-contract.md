---
id: F005
query_id: Q005
type: read
executed_at: 2026-09-15T01:11:03Z
parent_id: null
depth: 0
---

# F005 — raw-query-contract

## Summary

Query and QueryResult describe arbitrary execution, driver and datasource parameters rather than persisted slug identity. A tenant routing field must not silently become a SQL parameter or alter datasource selection.

## Citations

- path: `querysource/queries/models.py`
  lines: 11-25
  symbol: `Query`

- path: `querysource/queries/models.py`
  lines: 28-43
  symbol: `QueryResult`

## Audit note

Related searches and targeted ranges were batched. Citations represent inspected ranges or exact search matches, not a claim that every cited file was read in full. The timestamp records digest assembly at research completion.
