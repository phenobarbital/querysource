---
id: F005
query_id: Q004
type: read
intent: how the Rust fast path is dispatched and what happens on error
executed_at: 2026-10-07T00:09:00Z
duration_ms: 200
parent_id: F001
depth: 1
---

# F005 — The pgsql Rust fast path swallows every exception and falls back to Cython

## Summary

`pgSQLParser.filter_conditions` wraps `_rs.pgsql_filter_conditions` in a bare
`except Exception: pass` and then runs `_filter_conditions_cy`. Consequence: an error
raised by the Rust builder (e.g. a "contains value too short" validation) would be
silently discarded and the Cython path would re-render. Any user-visible error for the
new operators must therefore be raised either **before** dispatch (e.g. during
`set_where`/`_where_element`, F009) or identically in the Cython fallback. The generic
`SQLParser.filter_conditions` (sql.pyx:118) has no try/except: Rust errors propagate.
`ParserError` (subclass of `QueryException`) is the exception type pgsql.pyx already raises.

## Citations

- path: `querysource/parsers/pgsql.pyx`
  lines: 305-313
  symbol: `pgSQLParser.filter_conditions`
  excerpt: |
    if HAS_RUST and self.filter and isinstance(self.filter, dict):
        try:
            cond_def = self.cond_definition if self.cond_definition else {}
            return _rs.pgsql_filter_conditions(sql, self.filter, cond_def)
        except Exception:
            pass  # fall through to Cython implementation
    return await self._filter_conditions_cy(sql)
- path: `querysource/parsers/sql.pyx`
  lines: 118-119
  symbol: `SQLParser.filter_conditions`
- path: `querysource/exceptions.py`
  lines: 6, 86
  symbol: `QueryException`, `ParserError`
- path: `querysource/parsers/pgsql.pyx`
  lines: 593-599
  symbol: `raise ParserError(...)` (existing raise sites in build_query)
