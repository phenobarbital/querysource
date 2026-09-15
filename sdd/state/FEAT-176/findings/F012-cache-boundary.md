---
id: F012
query_id: Q012
type: grep
executed_at: 2026-09-15T01:11:03Z
parent_id: null
depth: 0
---

# F012 — cache-boundary

## Summary

The active slug result cache is reached through QueryConnection, rather than solely through the generic cache package. SLUG_CACHE is declared but the inspected get_slug implementation reads from PostgreSQL on each call.

## Citations

- path: `querysource/connections.py`
  lines: 98-103
  symbol: `QueryConnection.in_cache`

- path: `querysource/connections.py`
  lines: 105-118
  symbol: `QueryConnection.from_cache`

- path: `querysource/interfaces/connections.py`
  lines: 45-48
  symbol: `SLUG_CACHE`

- path: `querysource/interfaces/connections.py`
  lines: 494-506
  symbol: `Connection.get_slug`

## Audit note

Related searches and targeted ranges were batched. Citations represent inspected ranges or exact search matches, not a claim that every cited file was read in full. The timestamp records digest assembly at research completion.
