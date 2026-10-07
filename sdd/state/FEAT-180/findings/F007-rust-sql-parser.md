---
id: F007
query_id: Q006
type: read
intent: Rust generic SQL builder — dict-value operator allowlist
executed_at: 2026-10-07T00:11:00Z
duration_ms: 300
parent_id: null
depth: 0
---

# F007 — Generic Rust `sql_parser::filter_conditions` allows only comparison operators in dict values

## Summary

`rust/src/sql_parser.rs` reads the **first** dict entry, validates `op` via
`validate_operator` (COMPARISON_TOKENS ∪ VALID_OPERATORS, where VALID_OPERATORS also
includes `IS`, `IS NOT`) and otherwise `continue`s (drops the condition). Value is
escaped with `safe_scalar_value` (quote_string). No LIKE support. This builder is the
fast path for `SQLParser` (MySQL/SQLite/generic) — `sql.pyx:118`.

## Citations

- path: `rust/src/sql_parser.rs`
  lines: 14-17
  symbol: `COMPARISON_TOKENS`, `VALID_OPERATORS`
  excerpt: |
    const COMPARISON_TOKENS: &[&str] = &[">=", "<=", "<>", "!=", "<", ">"];
    const VALID_OPERATORS: &[&str] = &["<", ">", ">=", "<=", "<>", "!=", "IS NOT", "IS"];
- path: `rust/src/sql_parser.rs`
  lines: 47-54
  symbol: `validate_operator`
- path: `rust/src/sql_parser.rs`
  lines: 246-258
  symbol: `filter_conditions` (dict branch)
  excerpt: |
    if let Ok(dict_val) = value_obj.cast::<PyDict>() {
        if let Some((op_obj, v_obj)) = dict_val.iter().next() {
            let op: String = op_obj.extract()?;
            if validate_operator(&op).is_err() { continue; }
            let safe_v = safe_scalar_value(&v);
            where_cond.push(format!("{formatted_key} {op} {safe_v}"));
