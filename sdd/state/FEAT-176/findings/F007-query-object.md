---
id: F007
query_id: Q007
type: read
executed_at: 2026-09-15T01:11:03Z
parent_id: null
depth: 0
---

# F007 — query-object

## Summary

QueryObject extracts slug from its query dictionary and calls get_slug without tenant context. This is a separate definition lookup path used by local multi-query execution.

## Citations

- path: `querysource/queries/obj.py`
  lines: 28-62
  symbol: `QueryObject.__init__`

- path: `querysource/queries/obj.py`
  lines: 88-101
  symbol: `QueryObject.build_provider`

## Audit note

Related searches and targeted ranges were batched. Citations represent inspected ranges or exact search matches, not a claim that every cited file was read in full. The timestamp records digest assembly at research completion.
