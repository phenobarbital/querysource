---
id: F017
query_id: Q017
type: read
executed_at: 2026-09-15T01:11:03Z
parent_id: null
depth: 0
---

# F017 — configuration

## Summary

Default storage schema and table are already configurable; public.queries is the default, not the sole possible existing configuration. PBAC and scheduling are disabled by default.

## Citations

- path: `querysource/conf.py`
  lines: 352-354
  symbol: `QS_QUERIES_SCHEMA`

- path: `querysource/conf.py`
  lines: 356-358
  symbol: `ENABLE_QS_SCHEDULER`

- path: `querysource/conf.py`
  lines: 428-431
  symbol: `QS_PBAC_ENABLED`

- path: `querysource/conf.py`
  lines: 93-96
  symbol: `QUERYSET_REDIS`

## Audit note

Related searches and targeted ranges were batched. Citations represent inspected ranges or exact search matches, not a claim that every cited file was read in full. The timestamp records digest assembly at research completion.
