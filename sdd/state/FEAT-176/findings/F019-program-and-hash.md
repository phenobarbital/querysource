---
id: F019
query_id: Q019
type: grep
executed_at: 2026-09-15T01:11:03Z
parent_id: null
depth: 0
---

# F019 — program-and-hash

## Summary

program_slug also feeds provider defaults and compiled parser filter callbacks. Base and overridden provider checksums omit explicit tenant context. Tenant storage must be distinct from program semantics and execution datasource.

## Citations

- path: `querysource/providers/abstract.py`
  lines: 73-79
  symbol: `BaseProvider`

- path: `querysource/providers/abstract.py`
  lines: 229-230
  symbol: `BaseProvider.checksum`

- path: `querysource/parsers/abstract.pyx`
  lines: 162-166
  symbol: `AbstractParser._program_slug_sync`

- path: `querysource/parsers/pgsql.pyx`
  lines: 295-304
  symbol: `pgSQLParser.build_query`

- path: `querysource/providers/external.py`
  lines: 68-70
  symbol: `externalProvider.checksum`

- path: `querysource/providers/sources/abstract.py`
  lines: 150-161
  symbol: `baseSource.checksum`

## Audit note

Related searches and targeted ranges were batched. Citations represent inspected ranges or exact search matches, not a claim that every cited file was read in full. The timestamp records digest assembly at research completion.

## Additional exact search citations

- path: `querysource/parsers/abstract.pxd`
  lines: 22-22
  symbol: `program_slug`
- path: `querysource/parsers/sql.pyx`
  lines: 372-372
  symbol: `program_slug`
- path: `querysource/parsers/cql.pyx`
  lines: 178-178
  symbol: `program_slug`
- path: `querysource/parsers/sosql.pyx`
  lines: 217-217
  symbol: `program_slug`

These are callback/property references located by search, not full parser reviews.
