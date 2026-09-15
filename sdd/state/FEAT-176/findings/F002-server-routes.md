---
id: F002
query_id: Q002
type: grep
executed_at: 2026-09-15T01:11:03Z
parent_id: null
depth: 0
---

# F002 — server-routes

## Summary

QuerySource registers distinct legacy single-query, multi-query, management and raw executor routes. qs_start currently only captures the running loop; discovery is new behavior.

## Citations

- path: `querysource/services.py`
  lines: 134-216
  symbol: `QuerySource.setup`

- path: `querysource/services.py`
  lines: 369-375
  symbol: `QuerySource.qs_start`

## Audit note

Related searches and targeted ranges were batched. Citations represent inspected ranges or exact search matches, not a claim that every cited file was read in full. The timestamp records digest assembly at research completion.
