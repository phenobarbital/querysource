---
id: F002
query_id: Q002
type: read
intent: how the generic SQLParser (Cython) handles a dict-typed filter value
executed_at: 2026-10-07T00:06:00Z
duration_ms: 300
parent_id: null
depth: 0
---

# F002 — Generic Cython `SQLParser` only accepts COMPARISON_TOKENS inside a dict value

## Summary

In the Cython fallback of `SQLParser.filter_conditions`, a dict value is reduced to its
*last* `(op, v)` pair; if `op` is in `COMPARISON_TOKENS` (`>=,<=,<>,!=,<,>`) it renders
`key op quoted(v)`, otherwise the whole condition is **silently discarded** (`continue`).
There is no LIKE/ILIKE handling for dict values in the generic parser.

## Citations

- path: `querysource/parsers/sql.pyx`
  lines: 25
  symbol: `COMPARISON_TOKENS`
  excerpt: |
    COMPARISON_TOKENS = ('>=', '<=', '<>', '!=', '<', '>',)
- path: `querysource/parsers/sql.pyx`
  lines: 152-162
  symbol: `SQLParser.filter_conditions` (dict branch)
  excerpt: |
    if isinstance(value, dict):
        if not value:
            continue
        op, v = next(reversed(value.items()))  # never popitem()
        if op in COMPARISON_TOKENS:
            safe_v = Entity.quoteString(v) if isinstance(v, str) else str(v)
            where_cond.append(f"{key} {op} {safe_v}")
        else:
            # currently, discard any non-supported comparison token
            continue
