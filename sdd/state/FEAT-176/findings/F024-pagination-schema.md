---
id: F024
query_id: Q024
type: read
executed_at: 2026-09-15T01:11:03Z
parent_id: F015
depth: 1
---

# F024 — pagination-schema

## Summary

Pagination derives filter fields once from QueryModel and explicitly includes program_slug in search/sort lists. Qualified table names are validated as bare identifiers; supported PostgreSQL schema names may be broader than this helper permits.

## Citations

- path: `querysource/handlers/_pagination.py`
  lines: 45-48
  symbol: `FILTERABLE_COLUMNS`

- path: `querysource/handlers/_pagination.py`
  lines: 52-62
  symbol: `SORTABLE_COLUMNS`

- path: `querysource/handlers/_pagination.py`
  lines: 64-70
  symbol: `SEARCHABLE_COLUMNS`

- path: `querysource/handlers/_pagination.py`
  lines: 280-300
  symbol: `build_where_clause`

- path: `querysource/handlers/_pagination.py`
  lines: 404-413
  symbol: `_validate_bare_identifier`

## Audit note

Related searches and targeted ranges were batched. Citations represent inspected ranges or exact search matches, not a claim that every cited file was read in full. The timestamp records digest assembly at research completion.
