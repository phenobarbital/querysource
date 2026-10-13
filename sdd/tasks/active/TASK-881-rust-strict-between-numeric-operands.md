# TASK-881: Strict canonical BETWEEN + numeric comparison operands (Rust)

**Feature**: FEAT-168 — Builder parity: numeric comparison operands and strict canonical BETWEEN
**Spec**: `sdd/specs/src-bigquery-parser-tech-debt.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned
**discovered_from**: issue:fc81723e95ac, issue:7a248a32aa07

---

## Context

Spec §1 A/B, §3 Module 2. `filter_common::is_canonical_between` is a prefix test,
so the Rust BigQuery and generic builders splice any `BETWEEN …` string verbatim.
`pgsql_parser::process_dict_value` quotes numeric operands (`x > '1'`) while
Cython renders `x > 1`; `bigquery_parser::process_dict_value` quotes them as
strings (`x > "1"`, a BigQuery type error) and has a branch that renders a
string operand **raw** (`v.as_str()`).

---

## Scope

- `filter_common.rs`: add `pub(crate) fn has_between_prefix`; make
  `is_canonical_between` the strict whole-string grammar (spec §2) using a
  `std::sync::OnceLock<regex::Regex>`; update `process_str_value` (`:196`) to
  drop (`return None`) a prefixed-but-non-canonical value.
- `bigquery_parser.rs` `process_str_value` (`:331`): same drop rule.
- `pgsql_parser.rs` `process_str_value` (`:652`): same drop rule (keep `pg_validate_between`).
- `sql_parser.rs` `build_string_condition` (`:387`): prefixed-but-non-canonical
  → return the existing `PyValueError` (the Cython fallback then drops it, TASK-880).
- `pgsql_parser.rs` `process_dict_value` (`:479`) and `bigquery_parser.rs`
  `process_dict_value` (`:223`): numeric operands (`FilterValue::Int`/`Float`)
  render bare via a private `dict_operand`; the BigQuery single-token branch
  (`return Some(format!("{} {} {}", field_expr, op, v.as_str()));`) uses `dict_operand`.
- Rust unit tests in each file's `mod tests`.

**NOT in scope**: Python/Cython (TASK-880); Python parity tests (TASK-882);
removing `pg_validate_between` / `validate_between_clause`; `sql_parser` dict branch.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `rust/src/filter_common.rs` | MODIFY | strict grammar, `has_between_prefix`, drop in `process_str_value` |
| `rust/src/bigquery_parser.rs` | MODIFY | drop non-canonical BETWEEN, `dict_operand` |
| `rust/src/pgsql_parser.rs` | MODIFY | drop non-canonical BETWEEN, `dict_operand` |
| `rust/src/sql_parser.rs` | MODIFY | reject non-canonical BETWEEN |

---

## Codebase Contract (Anti-Hallucination)

### Existing Signatures to Use
```rust
// rust/src/filter_common.rs (verified at dda6d6b6)
use crate::validators::{field_components, is_integer, quote_string};          // :12
pub enum FilterValue { Str(String), Int(i64), Float(f64), Bool(bool), List(Vec<FilterValue>), Null }  // :20
pub(crate) fn is_canonical_between(value: &str) -> bool                      // :35 (prefix test today)
pub fn extract_filter_value(obj) -> FilterValue  // :65 — bool checked before i64; integral f64 → Int
pub fn process_str_value(key: &str, value: &str, name: &str, end: &str) -> Option<String>  // :194
#[cfg(test)] mod tests                                                        // :264
// rust/src/bigquery_parser.rs
use crate::filter_common::is_canonical_between;                              // :16
enum FilterValue { Str, Int(i64), Float(f64), Bool, List, Dict, Null }       // :42 (PRIVATE, separate enum)
fn process_dict_value(field_expr: &str, entries: &[(String, FilterValue)]) -> Option<String>  // :223
fn process_str_value(field_expr: &str, value: &str, name: &str, end: &str) -> Option<String>  // :324
// rust/src/pgsql_parser.rs
use crate::filter_common::{base_key, is_canonical_between};                  // :14
fn pg_validate_between(value: &str) -> bool                                  // :67
fn process_dict_value(key: &str, entries: &[(String, FilterValue)], _format: Option<&str>) -> Option<String>  // :479
fn process_str_value(key, value, name, end, _format) -> Option<String>       // :630
// rust/src/sql_parser.rs
use crate::filter_common::{base_key, is_canonical_between};                  // :11
fn validate_between_clause(value: &str) -> Result<(), String>                // :72
fn build_string_condition(key, value, name, end, where_cond: &mut Vec<String>) -> PyResult<()>  // :380
// rust/src/validators.rs
pub fn quote_string(value: &str, no_dblquoting: bool) -> String              // :198
pub fn bq_quote_string(value: &str) -> String                                // :250
// rust/Cargo.toml:14 — regex = "1.11" (already a dependency)
```

### Does NOT Exist
- ~~a shared `FilterValue` between bigquery_parser.rs and filter_common.rs~~ — write `dict_operand` once per module
- ~~`lazy_static` / `once_cell` crates~~ — use `std::sync::OnceLock`
- ~~`filter_common::has_between_prefix`~~ — this task creates it

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "rust/src/filter_common.rs", "action": "MODIFY"},
    {"path": "rust/src/bigquery_parser.rs", "action": "MODIFY"},
    {"path": "rust/src/pgsql_parser.rs", "action": "MODIFY"},
    {"path": "rust/src/sql_parser.rs", "action": "MODIFY"}
  ],
  "contract_symbols": [
    "sym:rust/src/filter_common.rs#is_canonical_between",
    "sym:rust/src/filter_common.rs#process_str_value",
    "sym:rust/src/bigquery_parser.rs#process_dict_value",
    "sym:rust/src/bigquery_parser.rs#process_str_value",
    "sym:rust/src/pgsql_parser.rs#process_dict_value",
    "sym:rust/src/pgsql_parser.rs#process_str_value"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Grammar must be byte-identical to TASK-880's Python regex:
  `^(?:NOT )?BETWEEN (?:-?\d+(?:\.\d+)?|'(?:[^'\\]|'')*') AND (?:-?\d+(?:\.\d+)?|'(?:[^'\\]|'')*')$`.
- `cargo test --no-default-features` (see memory: plain `cargo test` fails to link);
  some tests already fail on `dev` — compare with the base commit.
- After building, run `make build-rust` **and** the stage-rust step so Python
  loads the new `_qs_parsers` .so (stale .so is silent).

---

## Implementation Blueprint

### Steps (in order)
1. Rewrite `is_canonical_between`, add `has_between_prefix` in `filter_common.rs` — *why*: one Rust grammar for all builders.
2. Update the four call sites to `if has_between_prefix(v) { if !is_canonical_between(v) { drop } … }` — *why*: G3.
3. Add `dict_operand` to pgsql/bigquery and use it at every comparison operand — *why*: G1, G2.
4. Add unit tests; `cargo test --no-default-features`; `make build-rust` + stage — *why*: AC6; TASK-882 needs the fresh .so.

### `rust/src/filter_common.rs` (MODIFY)
```rust
// occurrences: 1 (verified: grep -c 'pub(crate) fn is_canonical_between(value: &str) -> bool {' rust/src/filter_common.rs)
// REPLACE lines 34-37 (doc comment + fn) with:
/// True for a value that starts like a builder BETWEEN clause (attempt, not proof).
pub(crate) fn has_between_prefix(value: &str) -> bool {
    value.starts_with("BETWEEN ") || value.starts_with("NOT BETWEEN ")
}

/// True only for the exact canonical clause `BetweenClause.render()` emits (FEAT-168).
pub(crate) fn is_canonical_between(value: &str) -> bool {
    static CANONICAL: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    CANONICAL
        .get_or_init(|| {
            regex::Regex::new(
                r"^(?:NOT )?BETWEEN (?:-?\d+(?:\.\d+)?|'(?:[^'\\]|'')*') AND (?:-?\d+(?:\.\d+)?|'(?:[^'\\]|'')*')$",
            )
            .expect("canonical BETWEEN regex")
        })
        .is_match(value)
}
```
```rust
// process_str_value, occurrences: 1 (verified: grep -c '    if is_canonical_between(value) {' rust/src/filter_common.rs)
    if has_between_prefix(value) {
        if !is_canonical_between(value) {
            return None; // SECURITY (FEAT-168): non-canonical BETWEEN is never spliced
        }
        return Some(format!("({} {})", key, value));
    }
```
**Why**: the existing test at `:271-273` stays valid; add rejects for
`BETWEEN 1 AND 2 OR 1=1`, `BETWEEN 1 AND 2;`, `between 1 and 2`, `BETWEEN 'a\' AND 'b'`.

### `rust/src/bigquery_parser.rs` (MODIFY)
```rust
// import, occurrences: 1 → use crate::filter_common::{has_between_prefix, is_canonical_between};
// process_str_value :331, occurrences: 1 — same has_between_prefix/is_canonical_between shape, emitting
//   format!("({} {})", field_expr, value)
/// Comparison-dict operand: numbers bare, everything else a BigQuery string literal (FEAT-168).
fn dict_operand(v: &FilterValue) -> String {
    match v {
        FilterValue::Int(i) => i.to_string(),
        FilterValue::Float(f) => f.to_string(),
        other => bq_quote_string(&other.as_str()),
    }
}
// :237 occurrences 1: .map(|(op, v)| format!("{} {} {}", field_expr, op, dict_operand(v)))
// :257 occurrences 1: return Some(format!("{} {} {}", field_expr, op, dict_operand(v)));
```

### `rust/src/pgsql_parser.rs` (MODIFY)
```rust
// import :14 → use crate::filter_common::{base_key, has_between_prefix, is_canonical_between};
// process_str_value :652 — has_between_prefix → (!is_canonical_between || !pg_validate_between) → None; else emit.
fn dict_operand(v: &FilterValue) -> String {
    match v {
        FilterValue::Int(i) => i.to_string(),
        FilterValue::Float(f) => f.to_string(),
        other => quote_string(&other.as_str(), true),
    }
}
// FILL IN: disambiguate — `quote_string(&v.as_str(), true)` occurs 3× in pgsql_parser.rs.
//   Replace ONLY the two inside process_dict_value: the all_comparisons `.map(|(op, v)| format!(..., quote_string(&v.as_str(), true)))`
//   (≈:495-503, line 499) and `let safe_v = quote_string(&v.as_str(), true);` (:520). Leave the third untouched.
```

### `rust/src/sql_parser.rs` (MODIFY)
```rust
// import :11 → use crate::filter_common::{base_key, has_between_prefix, is_canonical_between};
// build_string_condition :387, occurrences: 1
    if has_between_prefix(value) {
        if !is_canonical_between(value) {
            return Err(pyo3::exceptions::PyValueError::new_err("Non-canonical BETWEEN clause"));
        }
        // SECURITY: validate the BETWEEN clause for injection markers
        validate_between_clause(value)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e))?;
        where_cond.push(format!("({key} {value})"));
    } else if value == "null" || value == "NULL" {
```
**Why**: this builder already reports BETWEEN rejection as `PyValueError`
(Cython fallback drops it); keep that contract rather than silently skipping.

### FILL IN checklist
- [ ] pgsql `process_dict_value` — replace exactly the two in-function quote sites
- [ ] Rust tests: filter_common strict table; bigquery `Int(1)` → `x > 1`, `Float(2.5)` → `x >= 2.5`, mixed dict string operand quoted; pgsql `Int(1)` → `x > 1`; non-canonical BETWEEN → `None` in bq/pg/filter_common

---

## Acceptance Criteria

- [ ] `cargo test --no-default-features` — new tests pass, no new failures vs base
- [ ] No Rust comparison-dict branch renders `v.as_str()` unquoted
- [ ] `make build-rust` + stage-rust done; `python -c "from querysource.qs_parsers import _qs_parsers"` loads the new build

---

## Validation Commands

- `pytest tests/test_dialect_filter_preprocessing.py -q`
- `pytest tests/unit/test_rust_literal_quoting.py -q`

---

## Agent Instructions

1. Work in the feature worktree (`python -m scripts.sdd.ensure_worktree --slug src-bigquery-parser-tech-debt --feature-id FEAT-168`).
2. Verify anchors, implement from the blueprint, build, run `cargo test --no-default-features` and the Validation Commands.
3. Commit only the listed files; close with `scripts/sdd/close_task.sh TASK-881 src-bigquery-parser-tech-debt verified`.

---

## Completion Note

*(Agent fills this in when done)*
