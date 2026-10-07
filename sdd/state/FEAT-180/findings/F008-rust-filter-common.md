---
id: F008
query_id: Q007
type: read
intent: shared Rust filter helpers used by mssql/bq/soql builders — dict support?
executed_at: 2026-10-07T00:12:00Z
duration_ms: 300
parent_id: null
depth: 0
---

# F008 — `filter_common.rs` has no Dict variant: mssql (and other builders on it) drop dict values entirely

## Summary

`rust/src/filter_common.rs::extract_filter_value` maps Python values to
`Bool|Int|Float|Str|List|Null` — a dict falls to the `str()` fallback and is rendered as
`key = "{'startswith': 'andre'}"`-ish text or dropped. `mssql_parser.rs` uses
`extract_entries`/`process_entry` from this module. `sqlserver.pyx` likewise has no
`isinstance(value, dict)` branch. `bigquery.pyx:209-215` has a dict branch limited to
COMPARISON_TOKENS. Partial matching for non-PG dialects would be new work, not an
extension of an existing branch.

## Citations

- path: `rust/src/filter_common.rs`
  lines: 55-78
  symbol: `extract_filter_value`
- path: `rust/src/filter_common.rs`
  lines: 110-133
  symbol: `process_entry`
  excerpt: |
    match value {
        FilterValue::List(items) => process_list_value(...),
        FilterValue::Str(s) => process_str_value(key, s, &name, &end),
        FilterValue::Int(i) => ..., FilterValue::Bool(b) => ..., FilterValue::Float(f) => ...,
        FilterValue::Null => None,
    }
- path: `rust/src/mssql_parser.rs`
  lines: 22-30
  symbol: `mssql_filter_conditions`
- path: `querysource/parsers/sqlserver.pyx`
  lines: 108-135
  symbol: `filter_conditions` (no dict branch)
- path: `querysource/parsers/bigquery.pyx`
  lines: 209-215
  symbol: `filter_conditions` (dict branch, COMPARISON_TOKENS only)
