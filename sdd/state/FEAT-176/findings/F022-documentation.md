---
id: F022
query_id: Q022
type: grep
executed_at: 2026-09-15T01:11:03Z
parent_id: null
depth: 0
---

# F022 — documentation

## Summary

Scheduler documentation explicitly describes public storage, slug-only IDs and restart reconstruction. Query component documentation treats extra slug keys as conditions, so tenant routing needs a documented reserved field.

## Citations

- path: `docs/QSSCHEDULER.md`
  lines: 1-17
  symbol: `Job kinds`

- path: `querysource/queries/multi/sources/query.catalog.yaml`
  lines: 15-40
  symbol: `Query component contract`

## Audit note

Related searches and targeted ranges were batched. Citations represent inspected ranges or exact search matches, not a claim that every cited file was read in full. The timestamp records digest assembly at research completion.
