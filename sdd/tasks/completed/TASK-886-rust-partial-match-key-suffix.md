# TASK-886: Rust partial-match keys: base column, `!` flips operator, other suffixes rejected

**Feature**: FEAT-173 — Partial-match keys honour suffixes: base column, `!` negates, others rejected
**Spec**: `sdd/specs/parsers-bigquery-fixes.spec.md`
**Status**: done
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned
**discovered_from**: issue:36f51186eac1

---

## Context

Spec §2/§3 Module 1. The Rust partial-match builders render `key`, suffix included (`n! LIKE …`). Implement the
user-decided rule once in `check_entries`, then render `base_key(key)` at the four Rust sites.

## Scope

- Add `pub fn resolve_key_suffix(key: &str, op: &'static PartialMatchOp) -> PyResult<&'static PartialMatchOp>` to `partial_match.rs`.
- `check_entries` returns the resolved op.
- Render the base column in `sql_parser.rs`, `mssql_parser.rs`, `pgsql_parser.rs` and `bigquery_parser.rs`. In BigQuery, the `process_entry` Dict branch must also apply the flip, because `process_dict_value` re-looks-up the op.
- Add cargo tests.

**NOT in scope**: Python/Cython (TASK-887).

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `rust/src/partial_match.rs` | MODIFY | resolve_key_suffix + check_entries + tests |
| `rust/src/sql_parser.rs` | MODIFY | base column at the partial-match site |
| `rust/src/mssql_parser.rs` | MODIFY | base column |
| `rust/src/pgsql_parser.rs` | MODIFY | base column |
| `rust/src/bigquery_parser.rs` | MODIFY | base column + flipped op in process_entry |

## Codebase Contract (Anti-Hallucination)

### Existing Signatures to Use
```rust
pub(crate) fn base_key(key: &str) -> &str                       // filter_common.rs:30
pub fn lookup(name: &str) -> Option<&'static PartialMatchOp>    // partial_match.rs
pub fn check_entries<'a>(key: &str, entries: &[(&'a str, Option<&'a str>)], supports_regex: bool)
    -> PyResult<Option<(&'static PartialMatchOp, &'a str)>>    // partial_match.rs:129
// sql_parser.rs:277-278   check_entries(&key, …) / sql_partial_match_condition(&formatted_key, …)
// mssql_parser.rs:78-80   check_entries(&key, …) / mssql_partial_match_condition(&key, …)
// pgsql_parser.rs:752-758 check_entries(key, …) / pg_safe_identifier_key(key)
// bigquery_parser.rs:174-195 process_entry → process_dict_value(&field_expr, entries); :398 check_entries
```

### Does NOT Exist
- ~~`partial_match::resolve_key_suffix`~~: created here.

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "rust/src/partial_match.rs", "action": "MODIFY"},
    {"path": "rust/src/sql_parser.rs", "action": "MODIFY"},
    {"path": "rust/src/mssql_parser.rs", "action": "MODIFY"},
    {"path": "rust/src/pgsql_parser.rs", "action": "MODIFY"},
    {"path": "rust/src/bigquery_parser.rs", "action": "MODIFY"}
  ],
  "contract_symbols": [
    "sym:rust/src/partial_match.rs#check_entries",
    "sym:rust/src/bigquery_parser.rs#process_entry"
  ]
}
```

## Implementation Blueprint

### Steps (in order)
1. Add `resolve_key_suffix`. The suffix is `&key[base_key(key).len()..]`: `""` keeps the op, `"!"` returns `lookup` of the counterpart (strip or add `not_`), and anything else returns `PyValueError` with the spec §1 G3 message. This rule mirrors the Python side, so both paths agree.
2. Return the resolved op from `check_entries`, so every caller gets the flip without duplicating it.
3. Change each render site to use the base column, because §5.5 of the docs says the suffix is stripped.

### FILL IN checklist
- [ ] BigQuery `process_entry`: build `field_expr` from the base key and render the flipped op for a single partial-match entry
- [ ] cargo tests: flip, double flip, reject, base column per dialect

## Acceptance Criteria
- [ ] `n!` + contains renders `n NOT LIKE …`; `n!` + not_contains renders `n LIKE …`; `n~` returns Err.
- [ ] `cargo test --no-default-features` shows no new failures against the 4-failure baseline.

## Validation Commands
- `pytest tests/test_rust_partial_match_units.py -q`

## Completion Note

**Completed**: 2026-10-13 by agent:sdd-fix (commit 8ab3eb92)

- `resolve_key_suffix()` is in `partial_match.rs`, and `check_entries` returns the resolved op.
- The SQL, SQL Server and PostgreSQL builders render `base_key(key)`. In BigQuery, the `process_entry` Dict branch renders the base column with the flipped op.
- New cargo tests: `key_suffix_resolution` and `check_entries_applies_key_suffix`. The full lib suite gives 366 passed and 4 failed; those 4 match the baseline.
