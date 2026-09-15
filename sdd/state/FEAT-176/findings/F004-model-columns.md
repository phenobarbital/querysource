---
id: F004
query_id: Q004
type: read
executed_at: 2026-09-15T01:11:03Z
parent_id: null
depth: 0
---

# F004 — model-columns

## Summary

The persistence model requires both program_id and program_slug and binds schema/table via Meta. Removing program_slug from tenant tables requires a different persisted field contract, not just different SQL qualification.

## Citations

- path: `querysource/models.py`
  lines: 48-107
  symbol: `QueryModel`

## Audit note

Related searches and targeted ranges were batched. Citations represent inspected ranges or exact search matches, not a claim that every cited file was read in full. The timestamp records digest assembly at research completion.
