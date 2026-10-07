---
id: F004
query_id: Q003
type: read
intent: how dict values are first classified as JSONB vs comparison/text operators
executed_at: 2026-10-07T00:08:00Z
duration_ms: 300
parent_id: F003
depth: 1
---

# F004 — `jsonb_condition` must explicitly exempt new text operators or they become implicit JSONB containment

## Summary

`jsonb_condition(col, value)` inspects a dict value *before* the generic branch. It counts
keys that are COMPARISON or JSONB operators; when the count is zero it renders implicit
containment `col @> '<json>'::jsonb`. FEAT-152 had to add an early `return (False, None)`
for `PG_TEXT_OPERATORS` so `{"ILIKE": ...}` is not treated as a JSON document. Any new
operator vocabulary (`startswith`, `like`, `regex`, ...) needs the same exemption in both
the Cython and Rust `jsonb_condition`.

## Citations

- path: `querysource/parsers/pgsql.pyx`
  lines: 234-296
  symbol: `jsonb_condition`
  excerpt: |
    operators = sum(1 for k in value if k in COMPARISON_TOKENS or k in JSONB_OPERATORS)
    op, operand = next(iter(value.items()))
    if op in PG_TEXT_OPERATORS:
        # qsurl text-match operators (FEAT-152) are handled by the caller's dict branch
        return (False, None)
    ...
    if operators == 0:
        return (True, f"{col} @> {pg_literal(jsonb_dumps(value))}::jsonb")
- path: `rust/src/pgsql_parser.rs`
  lines: 343-400
  symbol: `jsonb_condition`
  excerpt: |
    if PG_TEXT_OPERATORS.contains(&op.as_str()) { ... NotJsonb }
