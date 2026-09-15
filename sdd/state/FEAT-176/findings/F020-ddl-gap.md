---
id: F020
query_id: Q020
type: grep
executed_at: 2026-09-15T01:11:03Z
parent_id: null
depth: 0
---

# F020 — ddl-gap

## Summary

The scoped SQL search found sample inserts into public.queries and datasource DDL, but no canonical CREATE TABLE for query definitions. Production defaults, constraints, grants and triggers cannot be inferred completely from the Python model.

## Citations

- path: `docs/sample_sqlserver.sql`
  lines: 15-24
  symbol: `public.queries sample INSERT`

- path: `docs/sql/datasources_logic.sql`
  lines: 5-5
  symbol: `public.datasources DDL`

## Audit note

Related searches and targeted ranges were batched. Citations represent inspected ranges or exact search matches, not a claim that every cited file was read in full. The timestamp records digest assembly at research completion.
