# TASK-864: Rust builders — parity for BETWEEN, comparison dicts, typed values

**Feature**: FEAT-165 — JSON Dialect Filter Pre-processing Fixes
**Spec**: `sdd/specs/json-dialect-filter-fixes.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §2 E / §3 Module 5. The Rust fast path (`_qs_parsers`) renders filters
whenever it is available, so it must match the Cython changes of TASK-863
byte-for-byte, and fix two Rust-only defects found while writing the spec:

- `filter_common.rs::process_str_value` wraps a `BETWEEN` value that contains no
  `'` in quotes: `BETWEEN 100 AND 500` → `(amount 'BETWEEN 100 AND 500')`
  (SQL Server Rust path).
- `pgsql_parser.rs` `array` (string), `tsrange`/`tstzrange`, `daterange` branches
  emit the value **unquoted** (`vip::character varying = ANY(tags)`), relying on a
  pre-quoted value; after TASK-862 the value arrives raw, as Cython expects.

---

## Scope

- `filter_common.rs`: add `pub(crate) fn base_key(key: &str) -> &str` and
  `pub(crate) fn is_canonical_between(value: &str) -> bool`; make
  `process_str_value` use `is_canonical_between` and render `({key} {value})`
  verbatim (remove the quoting branch).
- `pgsql_parser.rs`: `format_hint` looked up with `base_key(&key)`;
  `process_dict_value` renders every entry AND-ed when all are comparison tokens
  (single entry unchanged); string BETWEEN anchored; typed `array`/`tsrange`/
  `tstzrange`/`daterange` values quoted with `pg_literal()`.
- `sql_parser.rs`: `_format` with `base_key`; comparison dict AND-ed; anchored BETWEEN.
- `bigquery_parser.rs`: comparison dict AND-ed; anchored BETWEEN.
- `mssql_parser.rs`: `format_hint` with `base_key`.
- Rust unit tests next to the changed code; Python test
  `tests/test_rust_builder_filter_fixes.py` calling `_rs.*_filter_conditions`.

**NOT in scope**: Python pre-processing; Cython builders; changing the
`#[pyfunction]` signatures.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `rust/src/filter_common.rs` | MODIFY | helpers + `process_str_value` BETWEEN fix |
| `rust/src/pgsql_parser.rs` | MODIFY | base key, AND-ed dicts, anchored BETWEEN, typed quoting |
| `rust/src/sql_parser.rs` | MODIFY | base key, AND-ed dicts, anchored BETWEEN |
| `rust/src/bigquery_parser.rs` | MODIFY | AND-ed dicts, anchored BETWEEN |
| `rust/src/mssql_parser.rs` | MODIFY | base key |
| `tests/test_rust_builder_filter_fixes.py` | CREATE | Python-level checks of the staged extension |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.parsers import pgsql, sql   # pgsql._rs / sql._rs are the staged extension (pgsql.pyx:23-27)
```

### Existing Signatures to Use
```rust
// rust/src/pgsql_parser.rs
fn pg_literal(value: &str) -> String                                       // ~line 51
fn pg_validate_between(value: &str) -> bool                                // ~line 66
const COMPARISON_TOKENS: &[&str] = &[">=", "<=", "<>", "!=", "<", ">"];   // line 76
fn process_dict_value(key: &str, entries: &[(String, FilterValue)], _format: Option<&str>) -> Option<String>  // line 478
    let (op, v) = &entries[0];                                             // line 487 (occurrences: 1)
    // comparison: quote_string(&escape_string(&v.as_str()), true)
    if value.contains("BETWEEN") {                                         // line 629 (occurrences: 1)
    Some("array") => { let safe_val = escape_string(value); ... }          // lines ~656-664 (unquoted)
    Some("tsrange") | Some("tstzrange") => { ... }                         // ~681-685 (unquoted)
    Some("daterange") => { ... }                                           // ~686-689 (unquoted)
pub fn pgsql_filter_conditions(sql, filter_dict, cond_definition) -> PyResult<String>  // line 743
    .get_item(&key)                                                         // line 754 (format_hint)
// rust/src/sql_parser.rs
    .get_item(&key)?                                                        // line 252 (_format)
    if let Some((op_obj, v_obj)) = dict_val.iter().next() {                 // line 281
fn build_string_condition(...)  if value.contains("BETWEEN") {             // line 359 (validate_between_clause → PyValueError → Cython fallback)
// rust/src/bigquery_parser.rs
    let (op, v) = &entries[0];                                             // line 230
    if value.contains("BETWEEN") {                                         // line 315
// rust/src/mssql_parser.rs
        .get_item(&key)                                                     // line 85 (format_hint)
// rust/src/filter_common.rs
pub enum FilterValue { Str(String), Int(i64), ... }                        // line 20
pub fn process_str_value(key: &str, value: &str, name: &str, end: &str) -> Option<String>  // line ~173
    if value.contains("BETWEEN") { if !value.contains('\'') { quote_string(...) } ... }    // line 175 — the bug
```

### Does NOT Exist
- ~~`filter_common::base_key` / `is_canonical_between`~~ — this task creates them.
- ~~a comparison-dict branch in `mssql_parser.rs`~~ — out of scope.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "rust/src/filter_common.rs", "action": "MODIFY"},
    {"path": "rust/src/pgsql_parser.rs", "action": "MODIFY"},
    {"path": "rust/src/sql_parser.rs", "action": "MODIFY"},
    {"path": "rust/src/bigquery_parser.rs", "action": "MODIFY"},
    {"path": "rust/src/mssql_parser.rs", "action": "MODIFY"},
    {"path": "tests/test_rust_builder_filter_fixes.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:rust/src/pgsql_parser.rs#process_dict_value",
    "sym:rust/src/pgsql_parser.rs#pgsql_filter_conditions",
    "sym:rust/src/filter_common.rs#process_str_value"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Output must equal TASK-863's Cython output for the same input (spec AC).
- Single-entry comparison dicts render exactly as today.
- Build & stage: `make build-rust && make stage-rust` (Python loads the
  source-tree `_qs_parsers` `.so`; `build-rust` alone leaves it stale). Never `make build`.
- Rust unit tests: `cargo test --no-default-features` in `rust/`; 4 failures
  pre-exist on `dev` — report only new ones. Exclusive task.

---

## Implementation Blueprint

### Steps (in order)
1. Add the two helpers to `filter_common.rs` and fix `process_str_value` — *why*: shared by the parsers, and the quoting branch breaks numeric BETWEEN.
2. `pgsql_parser.rs`: base-key `format_hint`, AND-ed comparison entries, anchored BETWEEN, `pg_literal` for typed values — *why*: parity with Cython (spec §2 E).
3. Same three changes (as applicable) in `sql_parser.rs`, `bigquery_parser.rs`, `mssql_parser.rs`.
4. Add Rust unit tests; build, stage; add the Python test.

### `rust/src/filter_common.rs` (MODIFY)
```rust
// AFTER the `pub enum FilterValue` block (verified: filter_common.rs:20)
/// Column name without trailing key-suffix characters (`|!~#@:`).
pub(crate) fn base_key(key: &str) -> &str {
    key.trim_end_matches(|c: char| matches!(c, '|' | '!' | '~' | '#' | '@' | ':'))
}

/// True for the canonical clause produced by the Python pre-processing
/// (`BETWEEN <lo> AND <hi>` / `NOT BETWEEN <lo> AND <hi>`).
pub(crate) fn is_canonical_between(value: &str) -> bool {
    value.starts_with("BETWEEN ") || value.starts_with("NOT BETWEEN ")
}
```
```rust
// occurrences: 1 (verified: grep -c '    if value.contains("BETWEEN") {' filter_common.rs)
// REPLACE the BETWEEN block in process_str_value (filter_common.rs:175-180)
    if is_canonical_between(value) {
        return Some(format!("({} {})", key, value));
    }
```

### `rust/src/pgsql_parser.rs` (MODIFY)
```rust
// line 754 (occurrences: 1): .get_item(crate::filter_common::base_key(&key))
// line 629 (occurrences: 1): if crate::filter_common::is_canonical_between(value) {   (keep pg_validate_between inside)
// line 487 (occurrences: 1): FILL IN: when every entry's op is in COMPARISON_TOKENS, render each
//    "{key} {op} {quote_string(&escape_string(&v.as_str()), true)}" and join with " AND ",
//    wrapping in parentheses when > 1; otherwise keep the existing single-entry logic
//    — bounded by AC "single-operator output unchanged" and TASK-863's Cython format
// typed branches (~656-689): FILL IN: replace the unquoted `{safe_val}` with `pg_literal(value)`
//    for array (non-integer), tsrange/tstzrange and daterange — bounded by Cython pgsql.pyx output
//    ("'vip'::character varying = ANY(tags)")
```

### `rust/src/sql_parser.rs`, `rust/src/bigquery_parser.rs`, `rust/src/mssql_parser.rs` (MODIFY)
```rust
// sql_parser.rs:252  .get_item(crate::filter_common::base_key(&key))?
// sql_parser.rs:281  FILL IN: iterate every entry when all are comparison tokens; AND-ed, parenthesised when > 1
// sql_parser.rs:359  if crate::filter_common::is_canonical_between(value) {
// bigquery_parser.rs:230 FILL IN: same AND-ed rendering with bq_quote_string-quoted values (format of the existing branch)
// bigquery_parser.rs:315 if crate::filter_common::is_canonical_between(&value) {
// mssql_parser.rs:85 .get_item(crate::filter_common::base_key(&key))
```
**Why**: the three behaviours mirror TASK-863 exactly, so the parity tests of
TASK-865 can compare both paths byte-for-byte.

### `tests/test_rust_builder_filter_fixes.py` (CREATE)
```python
"""FEAT-165: Rust builders (staged _qs_parsers) — direct calls."""
import pytest

from querysource.parsers import pgsql

SQL = "SELECT * FROM t {where_cond}"
pytestmark = pytest.mark.skipif(not pgsql.HAS_RUST, reason="Rust extension not available")


def test_pg_multi_operator():
    out = pgsql._rs.pgsql_filter_conditions(SQL, {"x": {">": "'1'", "<": "'9'"}}, {})
    assert "(x > '1' AND x < '9')" in out


# FILL IN: typed array scalar quoted; tags| overlap via base key; canonical BETWEEN /
#          NOT BETWEEN; quoted value containing BETWEEN → equality; sql._rs.filter_conditions
#          and the mssql/bigquery entry points for the cases they support
#          — bounded by spec §2 E
```

### FILL IN checklist
- [ ] `pgsql_parser.rs::process_dict_value` AND-ed entries
- [ ] `pgsql_parser.rs` typed branches quoted
- [ ] `sql_parser.rs` / `bigquery_parser.rs` AND-ed entries
- [ ] Rust unit tests + Python tests

---

## Acceptance Criteria

- [ ] `cargo test --no-default-features` (in `rust/`) has no new failures
- [ ] After `make build-rust && make stage-rust`: `pytest tests/test_rust_builder_filter_fixes.py -v` passes (not skipped)
- [ ] `pytest tests/test_pgsql_partial_matching.py tests/test_sql_partial_matching.py tests/test_pgsql_jsonb_unnest_parity.py -q` still pass on the Rust path

## Validation Commands

- `pytest tests/test_rust_builder_filter_fixes.py -q`
- `pytest tests/test_pgsql_partial_matching.py -q`
- `pytest tests/test_sql_partial_matching.py -q`
- `pytest tests/test_pgsql_jsonb_unnest_parity.py -q`
