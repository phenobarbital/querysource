---
id: F010
query_id: Q010
type: read
executed_at: 2026-09-15T01:11:03Z
parent_id: null
depth: 0
---

# F010 — cache-write

## Summary

AbstractQuery writes the supplied checksum unchanged into QUERYSET_REDIS from asynchronous/threaded cache work. Tenant context must be encoded in the key before this boundary.

## Citations

- path: `querysource/interfaces/queries.py`
  lines: 296-355
  symbol: `AbstractQuery.caching_data`

- path: `querysource/interfaces/queries.py`
  lines: 267-283
  symbol: `AbstractQuery.save_in_cache`

## Audit note

Related searches and targeted ranges were batched. Citations represent inspected ranges or exact search matches, not a claim that every cited file was read in full. The timestamp records digest assembly at research completion.
