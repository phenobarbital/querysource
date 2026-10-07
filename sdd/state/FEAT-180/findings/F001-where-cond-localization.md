---
id: F001
query_id: Q001
type: grep
intent: locate where `where_cond`/`filter` dict values are rendered into WHERE clauses
executed_at: 2026-10-07T00:05:00Z
duration_ms: 400
parent_id: null
depth: 0
---

# F001 — WHERE-clause builders live in `filter_conditions` of each Cython parser

## Summary

`where_cond`/`filter` are popped from the request in `AbstractParser._query_filter_sync`
(`querysource/parsers/abstract.pyx:308-325`) into `self.filter`. Each dialect renders
`self.filter` in a `filter_conditions(sql)` coroutine: `querysource/parsers/sql.pyx:113`
(generic `SQLParser`), `querysource/parsers/pgsql.pyx:305` (`pgSQLParser`, subclass of
`SQLParser`), plus `sqlserver.pyx:92`, `cql.pyx:37`, `bigquery.pyx`. Both `sql.pyx` and
`pgsql.pyx` first try a Rust fast path (`querysource.qs_parsers._qs_parsers`) and fall
back to a Cython implementation.

## Citations

- path: `querysource/parsers/abstract.pyx`
  lines: 308-325
  symbol: `_query_filter_sync`
  excerpt: |
    self.filter = self.conditions.pop('where_cond', {})
    ...
    self.filter = self.conditions.pop('filter', {})
- path: `querysource/parsers/sql.pyx`
  lines: 113-119
  symbol: `SQLParser.filter_conditions`
  excerpt: |
    if HAS_RUST and self.filter:
        return _rs.filter_conditions(sql, dict(self.filter), dict(self.cond_definition))
- path: `querysource/parsers/pgsql.pyx`
  lines: 305-313
  symbol: `pgSQLParser.filter_conditions`
- path: `querysource/parsers/sqlserver.pyx`
  lines: 92-180
  symbol: `filter_conditions`
- path: `querysource/parsers/cql.pyx`
  lines: 37-164
  symbol: `where_cond`
