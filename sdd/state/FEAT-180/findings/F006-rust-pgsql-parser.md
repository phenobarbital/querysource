---
id: F006
query_id: Q005
type: read
intent: Rust pgsql builder — operator allowlist and dict-value processing
executed_at: 2026-10-07T00:10:00Z
duration_ms: 600
parent_id: null
depth: 0
---

# F006 — `rust/src/pgsql_parser.rs` mirrors pgsql.pyx: `PG_TEXT_OPERATORS` allowlist + `process_dict_value`

## Summary

The Rust pgsql builder extracts filter values into a `FilterValue` enum (with a
`Dict(Vec<(String, FilterValue)>)` variant), validates the dict operator through
`pg_validate_operator` (COMPARISON ∪ VALID_OPERATORS ∪ JSONB ∪ PG_TEXT_OPERATORS), and
`process_dict_value` takes the **first** entry (Cython takes the last; equal for
single-key dicts). ILIKE/NOT ILIKE render via `pg_literal` after stripping the
`is_valid()` pre-quote. Non-string values for text operators return `None` (dropped).
`process_str_value` also handles the legacy `~`/`!~` key suffix as `ILIKE 'v%'`.
Module exported as `pgsql_filter_conditions` in `rust/src/lib.rs:73`.

## Citations

- path: `rust/src/pgsql_parser.rs`
  lines: 37-42
  symbol: `pg_validate_operator`
  excerpt: |
    COMPARISON_TOKENS.contains(&op) || VALID_OPERATORS.contains(&op)
        || JSONB_OPERATORS.contains(&op) || PG_TEXT_OPERATORS.contains(&op)
- path: `rust/src/pgsql_parser.rs`
  lines: 75-90
  symbol: `COMPARISON_TOKENS`, `PG_TEXT_OPERATORS`
  excerpt: |
    const PG_TEXT_OPERATORS: &[&str] = &["ILIKE", "NOT ILIKE"];
- path: `rust/src/pgsql_parser.rs`
  lines: 51-63
  symbol: `pg_literal`
- path: `rust/src/pgsql_parser.rs`
  lines: 139-176
  symbol: `extract_filter_value` (Dict variant)
- path: `rust/src/pgsql_parser.rs`
  lines: 474-537
  symbol: `process_dict_value`
  excerpt: |
    let (op, v) = &entries[0];
    if !pg_validate_operator(op) { return None; }
    if COMPARISON_TOKENS.contains(&op.as_str()) { ... }
    if PG_TEXT_OPERATORS.contains(&op.as_str()) {
        return match v {
            FilterValue::Str(s) => { /* strip pre-quote */ Some(format!("{} {} {}", key, op, pg_literal(&stripped))) }
            _ => None,
        };
    }
- path: `rust/src/pgsql_parser.rs`
  lines: 603-622
  symbol: `process_str_value` (`~` suffix ILIKE)
- path: `rust/src/pgsql_parser.rs`
  lines: 709-750
  symbol: `pgsql_filter_conditions`
- path: `rust/src/lib.rs`
  lines: 65-73
  symbol: `_qs_parsers` pymodule registration
