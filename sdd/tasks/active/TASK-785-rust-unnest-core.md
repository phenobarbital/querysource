# TASK-785: Rust port — grammar, config, rendering and `pgsql_unnest_wrap`

**Feature**: FEAT-153 — Group & Aggregate by JSONB Array Elements (PostgreSQL)
**Spec**: `sdd/specs/group-aggregation-jsonb-columns.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-782
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 2, AC10/AC11 (Rust parity, resolved in the brainstorm: port the planner to
Rust too). This task ports TASK-780 + TASK-782 (grammar, config validation, detection,
rendering, plan assembly WITHOUT element filters, wrap) into a new pure-Rust core
`rust/src/pgsql_unnest.rs`, and registers only `pgsql_unnest_wrap`. TASK-786 adds element
filters and registers `pgsql_unnest_plan` — registering the plan function now would expose a
planner that silently passes path filters to the row filter.

The Cython module is the **reference implementation**: every rendered string and every
error message must be byte-identical.

---

## Scope

- Create `rust/src/pgsql_unnest.rs`: `UValue` (Python-value tree), `from_py`, `Ref`/`Expr`/
  `Item`, `parse_ref`/`parse_expr`/`parse_select_item`/`parse_order_item`, `Config` +
  `validate_config`, `is_plan_candidate`, rendering helpers, `Planner`, `Plan`,
  `build_plan_core` (no element filters yet), `wrap_sql`, `#[pyfunction] pgsql_unnest_wrap`.
- Register `mod pgsql_unnest;` and `pgsql_unnest_wrap` in `rust/src/lib.rs`.
- `#[cfg(test)]` unit tests mirroring `tests/test_jsonb_unnest_grammar.py` and
  `tests/test_jsonb_unnest_plan.py` golden strings.

**NOT in scope**: element filters, pre-filter, `pgsql_unnest_plan` pyfunction (TASK-786); Python tests (TASK-787).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `rust/src/pgsql_unnest.rs` | CREATE | Pure-Rust planner core + `pgsql_unnest_wrap` |
| `rust/src/lib.rs` | MODIFY | `mod pgsql_unnest;` + register `pgsql_unnest_wrap` |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```rust
use pyo3::exceptions::PyValueError;          // verified: rust/src/pgsql_parser.rs:7
use pyo3::prelude::*;                        // verified: rust/src/pgsql_parser.rs:8
use pyo3::types::{PyAny, PyDict, PyList};    // verified: rust/src/pgsql_parser.rs:9 (PyString also exists there)
use once_cell::sync::Lazy;                   // verified: rust/Cargo.toml once_cell = "1.20"
use regex::Regex;                            // verified: rust/Cargo.toml regex = "1.11"
```

### Existing Signatures to Use
```rust
// rust/src/pgsql_parser.rs:51 — PRIVATE; reimplement identically in pgsql_unnest.rs
fn pg_literal(value: &str) -> String {
    let escaped = value.replace('\'', "''");
    if escaped.contains(['{', '}', '\\']) {
        let escaped = escaped.replace('\\', "\\\\").replace('{', "\\x7b").replace('}', "\\x7d");
        format!("E'{}'", escaped)
    } else { format!("'{}'", escaped) }
}
// rust/src/pgsql_parser.rs:139 — extraction order pattern (bool before int; dict via cast::<PyDict>())
fn extract_filter_value(obj: &Bound<'_, pyo3::types::PyAny>) -> FilterValue
// rust/src/lib.rs:17 `mod pgsql_parser;`  |  rust/src/lib.rs:67 registration line
// rust/Cargo.toml: pyo3 0.29; tests run with `cargo test --no-default-features` (extension-module feature gate)
```
Reference implementation (Python, Cython-compiled): `querysource/parsers/jsonb_unnest.pyx`
— `parse_ref`, `parse_expr`, `parse_select_item`, `parse_order_item`, `validate_config`,
`is_plan_candidate`, `render_ref`, `render_expr`, `default_alias`, `_Planner`,
`unnest_plan`, `unnest_wrap`, `SAFE_CAST_PATTERNS` (TASK-780/782). Read it before porting.

### Does NOT Exist
- ~~`pub(crate) fn pg_literal`~~ in pgsql_parser.rs — it is private; do NOT change its visibility (keeps this task off `pgsql_parser.rs`).
- ~~`_rs.pgsql_unnest_plan`~~ — TASK-786.
- ~~serde / serde_json~~ in Cargo.toml — build Python objects with PyO3 directly.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "rust/src/pgsql_unnest.rs", "action": "CREATE"},
    {"path": "rust/src/lib.rs", "action": "MODIFY"}
  ],
  "contract_symbols": [
    "sym:querysource/parsers/jsonb_unnest.pyx#unnest_plan",
    "sym:querysource/parsers/jsonb_unnest.pyx#unnest_wrap",
    "sym:querysource/parsers/jsonb_unnest.pyx#validate_config"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- **Thin boundary** (`.claude/rules/rust-development.md`): all logic operates on Rust types
  (`&str`, `Vec<String>`, `UValue`); PyO3 only converts at the edge. Errors are
  `Result<_, String>`; the pyfunction maps `Err(msg)` → `PyValueError::new_err(msg)`.
- `UValue` must distinguish `Bool` from `Int` (config `strict: 1` must be rejected, like Cython).
- Preserve dict insertion order (`Vec<(String, UValue)>`), because having/filter render order matters.
- Error messages: copy the TASK-780 messages table and the TASK-782 Implementation Notes
  messages verbatim (`jsonb_unnest: ...`, quoted with single quotes, no escaping).
- Regexes compiled once with `Lazy<Regex>`; Rust `regex` has no look-around — mirror the
  Cython regexes, which do not use it.

---

## Implementation Blueprint

### Steps (in order)
1. Create the file from the blocks below — *why*: fixes the Rust-side contract TASK-786 extends.
2. Port each `FILL IN` from the Cython reference — *why*: byte-identical output (AC10).
3. Register in `lib.rs`; `cd rust && cargo test --no-default-features pgsql_unnest`.
4. `make build-rust` then `make stage-rust` (exclusive) — *why*: Python loads the source-tree `.so`.

### `rust/src/pgsql_unnest.rs` (CREATE) — part 1: values and literal
```rust
// Copyright (C) 2018-present Jesus Lara
//
// pgsql_unnest.rs — JSONB array unnest planner (FEAT-153). Rust fast path of
// querysource/parsers/jsonb_unnest.pyx; output and error messages must be identical.

use once_cell::sync::Lazy;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyDict, PyList};
use regex::Regex;

pub(crate) const ARRAY_ALIAS: &str = "_qs_e0";
pub(crate) const SOURCE_ALIAS: &str = "_qs_src";
pub(crate) const ALLOWED_CASTS: &[&str] = &[
    "text", "int", "integer", "bigint", "numeric", "float", "date", "timestamp", "timestamptz", "boolean",
];
pub(crate) const AGGREGATES: &[&str] = &["count", "min", "max", "sum", "avg"];
pub(crate) const BUCKETS: &[&str] = &["year", "quarter", "month", "week", "day"];

/// Python value tree converted once at the boundary (insertion order preserved).
#[derive(Debug, Clone, PartialEq)]
pub(crate) enum UValue {
    None,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(String),
    List(Vec<UValue>),
    Dict(Vec<(String, UValue)>),
    Other,
}

/// Convert a Python object into a `UValue` (bool before int — Python bool subclasses int).
pub(crate) fn from_py(obj: &Bound<'_, PyAny>) -> UValue {
    // FILL IN: None -> None; bool; i64; f64; str; dict (string keys only, else Other) via
    //   cast::<PyDict>(); list/tuple via cast::<PyList>() / extract::<Vec<Bound<PyAny>>>();
    //   anything else -> Other — bounded by pgsql_parser.rs:139 ordering.
    unimplemented!()
}

/// PostgreSQL literal — identical to pgsql_parser.rs:51 / jsonb_unnest.pyx `_pg_literal`.
pub(crate) fn pg_literal(value: &str) -> String {
    let escaped = value.replace('\'', "''");
    if escaped.contains(['{', '}', '\\']) {
        let escaped = escaped.replace('\\', "\\\\").replace('{', "\\x7b").replace('}', "\\x7d");
        format!("E'{}'", escaped)
    } else {
        format!("'{}'", escaped)
    }
}
```
**Why**: `UValue` lets `cargo test` exercise the whole planner without an interpreter.
`unimplemented!()` marks FILL IN bodies — none may remain at completion.

### `rust/src/pgsql_unnest.rs` — part 2: grammar and config
```rust
static IDENT_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$").unwrap());
static REF_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"^(?P<col>[A-Za-z_][A-Za-z0-9_]{0,62})(?:\[\]\.(?P<keys>[A-Za-z0-9_-]{1,128}(?:\.[A-Za-z0-9_-]{1,128})*))?(?:\s*::\s*(?P<cast>[A-Za-z]+))?$").unwrap()
});

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct Ref { pub column: String, pub keys: Vec<String>, pub cast: Option<String> }

#[derive(Debug, Clone, PartialEq)]
pub(crate) enum Expr {
    Ref(Ref),
    CountStar,
    Agg { func: String, distinct: bool, arg: Box<Expr> }, // arg: Expr::Ref or Expr::Bucket
    Bucket { unit: String, arg: Ref },
}

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct Item {
    pub text: String, pub expr: Expr, pub name: Option<String>,
    pub alias: Option<String>, pub direction: Option<String>, pub nulls: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct ColumnCfg { pub empty: String, pub safe_cast: Option<bool>, pub prefilter: bool }

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct Config {
    pub columns: Option<Vec<(String, ColumnCfg)>>, pub aliases: Vec<(String, String)>,
    pub strict: bool, pub safe_cast: bool,
}

pub(crate) fn parse_ref(text: &str) -> Result<Ref, String> { unimplemented!() }        // FILL IN: port parse_ref
pub(crate) fn parse_expr(text: &str) -> Result<Expr, String> { unimplemented!() }      // FILL IN: port parse_expr
pub(crate) fn parse_select_item(text: &str) -> Result<Item, String> { unimplemented!() } // FILL IN
pub(crate) fn parse_order_item(text: &str) -> Result<Item, String> { unimplemented!() }  // FILL IN
pub(crate) fn validate_config(config: &UValue) -> Result<Config, String> { unimplemented!() } // FILL IN
pub(crate) fn is_plan_candidate(
    fields: &[String], grouping: &[String], ordering: &[String],
    filter_keys: &[String], having: &UValue, config: &UValue,
) -> bool { unimplemented!() } // FILL IN: never panics; `having` truthy = non-empty Dict/List/Str, true Bool, non-zero number
```
**Why**: signatures mirror the Cython functions 1:1 so the parity tests (TASK-787) can map
each error message. `having` truthiness must equal Python `bool(having)`.

### `rust/src/pgsql_unnest.rs` — part 3: rendering, plan, wrap, pyfunction
```rust
pub(crate) const SAFE_CAST_PATTERNS: &[(&str, &str)] = &[
    // FILL IN: copy SAFE_CAST_PATTERNS from jsonb_unnest.pyx verbatim (TASK-782).
];

pub(crate) fn render_ref(r: &Ref, safe_cast: bool, implicit_cast: Option<&str>) -> String { unimplemented!() } // FILL IN
pub(crate) fn default_alias(expr: &Expr) -> String { unimplemented!() }                                      // FILL IN

/// Rust mirror of `_Planner` (jsonb_unnest.pyx, TASK-782).
pub(crate) struct Planner { pub cfg: Config, pub array_column: Option<String>, pub select_aliases: Vec<(String, Expr)> }

/// A row-filter entry of the plan: an original filter key (value passed through by the
/// pyfunction as the ORIGINAL Python object) or a pre-filter built by the planner (TASK-786).
#[derive(Debug, Clone, PartialEq)]
pub(crate) enum RowEntry { Original(String), Prefilter(String, UValue) }

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct Plan {
    pub select: Vec<String>, pub group_by: Vec<String>, pub order_by: Vec<String>,
    pub having: Vec<String>, pub element_where: Vec<String>, pub lateral: String,
    pub row_filter: Vec<RowEntry>,
}

/// Port of `unnest_plan` WITHOUT element filters: every filter key -> RowEntry::Original.
pub(crate) fn build_plan_core(
    fields: &[String], grouping: &[String], ordering: &[String],
    filter: &[(String, UValue)], having: &UValue, config: &UValue,
) -> Result<Option<Plan>, String> {
    // FILL IN: port unnest_plan (TASK-782) using Planner; element_where empty — bounded by the
    //   Cython reference; TASK-786 replaces the filter pass-through.
    unimplemented!()
}

/// Port of `unnest_wrap`.
pub(crate) fn wrap_sql(inner_sql: &str, select: &[String], lateral: &str, element_where: &[String]) -> String {
    let mut sql = format!("SELECT {} FROM ({}) AS {}", select.join(", "), inner_sql.trim(), SOURCE_ALIAS);
    if !lateral.is_empty() { sql = format!("{} {}", sql, lateral); }
    if !element_where.is_empty() { sql = format!("{} WHERE {}", sql, element_where.join(" AND ")); }
    sql
}

/// Python entry point: wrap `inner_sql` per a plan dict (keys `select`, `lateral`, `element_where`).
#[pyfunction]
#[pyo3(signature = (inner_sql, plan))]
pub fn pgsql_unnest_wrap(inner_sql: &str, plan: &Bound<'_, PyDict>) -> PyResult<String> {
    // FILL IN: extract the three keys (missing key -> PyValueError("jsonb_unnest: invalid plan"));
    //   call wrap_sql — bounded by unnest_wrap semantics (Python str.strip == trim of ASCII/Unicode
    //   whitespace; use .trim()).
    unimplemented!()
}
```
**Why**: `RowEntry::Original` keeps pass-through filter values as the caller's own Python
objects (no lossy round trip through `UValue`), which is what keeps row filters byte-identical.

### `rust/src/pgsql_unnest.rs` — part 4: tests (append)
```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_ref_path_cast() {
        let r = parse_ref("graduation_details[].course_date :: DATE").unwrap();
        assert_eq!(r.column, "graduation_details");
        assert_eq!(r.keys, vec!["course_date".to_string()]);
        assert_eq!(r.cast.as_deref(), Some("date"));
    }

    #[test]
    fn test_unknown_cast_message() {
        assert_eq!(parse_ref("a[].k::regclass").unwrap_err(), "jsonb_unnest: unknown cast 'regclass'");
    }
    // FILL IN: port every golden string and message from tests/test_jsonb_unnest_grammar.py and
    //   tests/test_jsonb_unnest_plan.py (spec example select/group/order/having/lateral, buckets,
    //   safe_cast, aliases, strict, empty include, errors) — bounded by AC10/AC11.
}
```

### `rust/src/lib.rs` (MODIFY)
```rust
// occurrences: 1 (verified: grep -c '^mod pgsql_parser;' rust/src/lib.rs)
// AFTER — insert below `mod pgsql_parser;` (verified: lib.rs:17)
mod pgsql_unnest;
// occurrences: 1 (verified: grep -c 'pgsql_parser::pgsql_filter_conditions' rust/src/lib.rs)
// AFTER — insert below `    m.add_function(wrap_pyfunction!(pgsql_parser::pgsql_filter_conditions, m)?)?;` (verified: lib.rs:67)
    m.add_function(wrap_pyfunction!(pgsql_unnest::pgsql_unnest_wrap, m)?)?;
```

### FILL IN checklist
- [ ] `from_py` — conversion order
- [ ] parsers + `validate_config` + `is_plan_candidate` — port TASK-780
- [ ] `SAFE_CAST_PATTERNS`, `render_ref`, `default_alias`, `Planner`, `build_plan_core` — port TASK-782
- [ ] `pgsql_unnest_wrap` — dict extraction
- [ ] tests — golden strings/messages from the two Python test files
- [ ] no `unimplemented!()` left

---

## Acceptance Criteria

- [ ] `cd rust && cargo test --no-default-features` passes (all existing + new tests) (AC11).
- [ ] After `make build-rust && make stage-rust`: `python -c "from querysource.qs_parsers import _qs_parsers as r; print(r.pgsql_unnest_wrap)"` works.
- [ ] `hasattr(_rs, 'pgsql_unnest_plan')` is still False (registered by TASK-786).
- [ ] No `unimplemented!()` / `todo!()` remains in `pgsql_unnest.rs`.

---

## Validation Commands

- `pytest tests/test_rust_parsers.py -q`
- `pytest tests/test_pgsql_jsonb_filters.py -q`

(plus `cd rust && cargo test --no-default-features` — not a pytest command, run it explicitly)

---

## Test Specification

Rust `#[cfg(test)]` module above. Python-level parity is TASK-787.

---

## Agent Instructions

1. Read spec §2, §3 M2 and the Cython reference `querysource/parsers/jsonb_unnest.pyx`; confirm TASK-782 is completed.
2. Implement; rebuild with `make build-rust` + `make stage-rust` (exclusive resource).
3. Move this file to `sdd/tasks/completed/`, set index status `"done"`, fill the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: `pgsql_unnest_plan` registration deferred to TASK-786 (avoids exposing a filter-less planner).
