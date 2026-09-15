---
id: F006
query_id: Q006
type: read
executed_at: 2026-09-15T01:11:03Z
parent_id: null
depth: 0
---

# F006 — single-query

## Summary

QS loads a definition through get_slug, obtains its provider and computes the provider checksum before reading/writing the shared Redis result cache.

## Citations

- path: `querysource/queries/qs.py`
  lines: 164-225
  symbol: `QS.build_provider`

- path: `querysource/queries/qs.py`
  lines: 380-430
  symbol: `QS.query`

- path: `querysource/queries/qs.py`
  lines: 509-511
  symbol: `QS.query`

## Audit note

Related searches and targeted ranges were batched. Citations represent inspected ranges or exact search matches, not a claim that every cited file was read in full. The timestamp records digest assembly at research completion.
