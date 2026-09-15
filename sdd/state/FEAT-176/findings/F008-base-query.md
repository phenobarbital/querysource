---
id: F008
query_id: Q008
type: read
executed_at: 2026-09-15T01:11:03Z
parent_id: null
depth: 0
---

# F008 — base-query

## Summary

BaseQuery forwards constructor kwargs to AbstractQuery and provides context manager and output mechanics. AbstractQuery stores program from conditions and generic kwargs; no explicit tenant field is present in the inspected constructor.

## Citations

- path: `querysource/queries/base.py`
  lines: 19-62
  symbol: `BaseQuery`

- path: `querysource/interfaces/queries.py`
  lines: 50-102
  symbol: `AbstractQuery.__init__`

## Audit note

Related searches and targeted ranges were batched. Citations represent inspected ranges or exact search matches, not a claim that every cited file was read in full. The timestamp records digest assembly at research completion.
