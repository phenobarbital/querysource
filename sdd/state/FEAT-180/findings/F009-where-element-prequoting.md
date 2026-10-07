---
id: F009
query_id: Q008
type: read
intent: what happens to dict filter values before filter_conditions runs (is_valid pre-quoting)
executed_at: 2026-10-07T00:13:00Z
duration_ms: 300
parent_id: F003
depth: 1
---

# F009 — `_where_element` pre-processes dict values: single `{op: is_valid(v)}` with string pre-quoting

## Summary

`AbstractParser.set_where` runs `_where_element` per filter key (in parallel). For a dict
value it keeps only the **last** `(op, v)` pair and replaces `v` with
`is_valid(key, v, noquote=self.string_literal)`, which wraps non-numeric strings in
single quotes (and doubles embedded `'`) when `noquote=False` (pgSQLParser default). This
is the pinned invariant the ILIKE builders strip (commit f313e115). This hook is the one
place that runs **before** both Rust and Cython builders — a natural spot for operator
validation (e.g. `contains` minimum length) that must raise regardless of path (F005).

## Citations

- path: `querysource/parsers/abstract.pyx`
  lines: 564-574
  symbol: `_where_element`
  excerpt: |
    if isinstance(value, dict):
        if not value:
            return key, value
        op, v = next(reversed(value.items()))
        result = is_valid(key, v, noquote=self.string_literal)
        return key, {op: result}
- path: `querysource/parsers/abstract.pyx`
  lines: 602-609
  symbol: `set_where`
- path: `querysource/parsers/abstract.pyx`
  lines: 19
  symbol: `is_valid`, `Entity`, `field_components` imports from `..types.validators`
