---
id: F011
query_id: Q011
type: grep
executed_at: 2026-09-15T01:11:03Z
parent_id: null
depth: 0
---

# F011 — discovery-pattern

## Summary

An existing information_schema introspector enumerates columns and excludes system schemas. No schema-per-query tenant registry was found in the scoped runtime search; SharePoint tenant_id matches concern external authentication.

## Citations

- path: `querysource/datasources/introspection.py`
  lines: 121-133
  symbol: `AnsiSQLIntrospector._tables_sql`

- path: `querysource/datasources/introspection.py`
  lines: 135-142
  symbol: `AnsiSQLIntrospector._columns_sql`

## Audit note

Related searches and targeted ranges were batched. Citations represent inspected ranges or exact search matches, not a claim that every cited file was read in full. The timestamp records digest assembly at research completion.
