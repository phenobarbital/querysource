# TASK-786: Rust port — element filters, pre-filter and `pgsql_unnest_plan`

**Feature**: FEAT-153 — Group & Aggregate by JSONB Array Elements (PostgreSQL)
**Spec**: `sdd/specs/group-aggregation-jsonb-columns.spec.md`
**Status**: done
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-779, TASK-783, TASK-785
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 2 (completion), AC10. Ports TASK-783 (element filters + opt-in pre-filter)
into `rust/src/pgsql_unnest.rs`, then exposes the full planner as
`_qs_parsers.pgsql_unnest_plan`, which `pgSQLParser._unnest_plan` (TASK-784) prefers when
present. Validation failures must raise `ValueError` with the Cython message verbatim —
TASK-784 re-raises them as `ParserError` without falling back.

---

## Scope

- Add `split_filters_core` (+ `unquote`, `filter_literal`, pre-filter doc builder) and call it
  from `build_plan_core` in place of the pass-through.
- Add `#[pyfunction] pgsql_unnest_plan` returning `None` or a dict with the exact plan keys;
  `row_filter` values: original Python objects for `RowEntry::Original`, PyO3-built
  dict/list for `RowEntry::Prefilter`.
- Register it in `rust/src/lib.rs`.
- Rust unit tests mirroring `tests/test_jsonb_unnest_filters.py`.

**NOT in scope**: Python parity tests (TASK-787).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `rust/src/pgsql_unnest.rs` | MODIFY | Filters, pre-filter, `pgsql_unnest_plan` |
| `rust/src/lib.rs` | MODIFY | Register `pgsql_unnest_plan` |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```rust
// already imported at the top of rust/src/pgsql_unnest.rs by TASK-785:
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyDict, PyList};
```

### Existing Signatures to Use
```rust
// rust/src/pgsql_unnest.rs (TASK-785)
pub(crate) enum UValue { None, Bool(bool), Int(i64), Float(f64), Str(String), List(Vec<UValue>), Dict(Vec<(String, UValue)>), Other }
pub(crate) fn from_py(obj: &Bound<'_, PyAny>) -> UValue
pub(crate) fn pg_literal(value: &str) -> String
pub(crate) fn parse_ref(text: &str) -> Result<Ref, String>
pub(crate) fn render_ref(r: &Ref, safe_cast: bool, implicit_cast: Option<&str>) -> String
pub(crate) struct Planner { cfg: Config, array_column: Option<String>, select_aliases: Vec<(String, Expr)> }
pub(crate) enum RowEntry { Original(String), Prefilter(String, UValue) }
pub(crate) struct Plan { select, group_by, order_by, having, element_where: Vec<String>, lateral: String, row_filter: Vec<RowEntry> }
pub(crate) fn build_plan_core(fields, grouping, ordering, filter: &[(String, UValue)], having: &UValue, config: &UValue) -> Result<Option<Plan>, String>
// rust/src/lib.rs:67 — pgsql_filter_conditions registration; TASK-785 added pgsql_unnest_wrap right below it
```
Reference implementation: `querysource/parsers/jsonb_unnest.pyx` — `FILTER_OPERATORS`,
`_unquote`, `_filter_literal`, `split_filters` (TASK-783), and its Implementation Notes tables.

### Does NOT Exist
- ~~serde_json~~ — build pre-filter Python objects with `PyDict::new(py)` / `PyList::new(py, ...)`.
- ~~Rust-side rendering of row filters~~ — row filters stay for `pgsql_filter_conditions`.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "rust/src/pgsql_unnest.rs", "action": "MODIFY"},
    {"path": "rust/src/lib.rs", "action": "MODIFY"}
  ],
  "contract_symbols": [
    "sym:querysource/parsers/jsonb_unnest.pyx#split_filters",
    "sym:querysource/parsers/jsonb_unnest.pyx#unnest_plan"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Python signature (fixed by spec §3 M2 and called by TASK-784):
  `pgsql_unnest_plan(fields, grouping, ordering, filter_dict, having, config) -> dict | None`.
  `having` / `config` arrive as arbitrary Python objects (TASK-784 passes `{}` defaults) — take
  them as `&Bound<PyAny>` and convert with `from_py`, so a non-dict `having` yields the same
  `jsonb_unnest: having must be a mapping` error as Cython instead of a PyO3 `TypeError`.
- `fields`/`grouping`/`ordering` are `Vec<String>`; a non-str entry makes PyO3 raise
  `TypeError`, which TASK-784 treats as "fall back to Cython" — acceptable.
- Filter keys must keep insertion order (`PyDict::iter`).
- Result dict keys, exactly: `select`, `group_by`, `order_by`, `having`, `element_where`,
  `lateral`, `row_filter`; lists are Python `list`, `row_filter` a Python `dict` in order.

---

## Implementation Blueprint

### Steps (in order)
1. Port `split_filters` + helpers — *why*: completes planner parity.
2. Replace the pass-through in `build_plan_core` — *why*: filter paths count toward the single-array rule and must run before `lateral()`.
3. Add and register `pgsql_unnest_plan`; add tests; `cargo test --no-default-features`.
4. `make build-rust` + `make stage-rust` (exclusive).

### `rust/src/pgsql_unnest.rs` (MODIFY) — filters (insert before the `#[cfg(test)]` module)
```rust
// occurrences: 1 (verified: grep -c '^#\[cfg(test)\]' rust/src/pgsql_unnest.rs)
// BEFORE — insert above `#[cfg(test)]`

pub(crate) const FILTER_OPERATORS: &[&str] = &["=", ">=", "<=", "<>", "!=", "<", ">"];

/// Mirrors `_unquote` (jsonb_unnest.pyx / pgsql.pyx:282-285).
pub(crate) fn unquote(value: &str) -> String {
    if value.len() >= 2 && value.starts_with('\'') && value.ends_with('\'') {
        value[1..value.len() - 1].replace("''", "'")
    } else {
        value.to_string()
    }
}

/// Mirrors `_filter_literal`.
pub(crate) fn filter_literal(value: &UValue, key: &str) -> Result<String, String> {
    // FILL IN: Str -> pg_literal(unquote); Bool -> pg_literal("true"/"false"); Int/Float ->
    //   pg_literal(<Python str() form>) — floats must print like Python (e.g. 2.5 -> "2.5",
    //   1e21 -> "1e+21"); otherwise Err(invalid filter value) — bounded by TASK-783 notes.
    unimplemented!()
}

/// Mirrors `split_filters`: returns (element_where, row_filter entries).
pub(crate) fn split_filters_core(
    planner: &mut Planner, filter: &[(String, UValue)],
) -> Result<(Vec<String>, Vec<RowEntry>), String> {
    // FILL IN: classification, rendering table, opt-in pre-filter with '|' suffix rule, order:
    //   originals first then pre-filters — bounded by TASK-783 Implementation Notes.
    unimplemented!()
}
```

```rust
// In build_plan_core (TASK-785): replace the pass-through that maps every filter key to
// RowEntry::Original with:
    let (element_where, row_filter) = split_filters_core(&mut planner, filter)?;
// FILL IN: call it BEFORE computing the lateral clause (same ordering rule as TASK-783).
```

### `rust/src/pgsql_unnest.rs` (MODIFY) — pyfunction (insert before `#[cfg(test)]`)
```rust
/// Python entry point: build the JSONB-unnest plan (FEAT-153) or return None.
#[pyfunction]
#[pyo3(signature = (fields, grouping, ordering, filter_dict, having, config))]
pub fn pgsql_unnest_plan<'py>(
    py: Python<'py>,
    fields: Vec<String>,
    grouping: Vec<String>,
    ordering: Vec<String>,
    filter_dict: &Bound<'py, PyDict>,
    having: &Bound<'py, PyAny>,
    config: &Bound<'py, PyAny>,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let filter: Vec<(String, UValue)> = filter_dict
        .iter()
        .filter_map(|(k, v)| k.extract::<String>().ok().map(|key| (key, from_py(&v))))
        .collect();
    let plan = build_plan_core(&fields, &grouping, &ordering, &filter, &from_py(having), &from_py(config))
        .map_err(PyValueError::new_err)?;
    let Some(plan) = plan else { return Ok(None) };
    // FILL IN: build the result PyDict with the exact keys; row_filter: for Original(key) use
    //   filter_dict.get_item(key) (the ORIGINAL object), for Prefilter(key, v) convert v with a
    //   `to_py` helper (Dict -> PyDict, List -> PyList, Str -> str) — bounded by Key Constraints.
    let _ = py;
    unimplemented!()
}
```
**Why**: converting once at the boundary and returning the original objects for row filters
guarantees `pgsql_filter_conditions` receives exactly what it received before FEAT-153.

### `rust/src/lib.rs` (MODIFY)
```rust
// occurrences: 1 (verified: grep -c 'pgsql_unnest::pgsql_unnest_wrap' rust/src/lib.rs — added by TASK-785)
// AFTER — insert below `    m.add_function(wrap_pyfunction!(pgsql_unnest::pgsql_unnest_wrap, m)?)?;`
    m.add_function(wrap_pyfunction!(pgsql_unnest::pgsql_unnest_plan, m)?)?;
```

### Tests (append inside the existing `mod tests`)
```rust
    #[test]
    fn test_element_filter_prequoted() {
        let mut p = Planner { cfg: validate_config(&UValue::Dict(vec![])).unwrap(), array_column: None, select_aliases: vec![] };
        let (w, rows) = split_filters_core(&mut p, &[
            ("graduation_details[].course".into(), UValue::Str("'Pilates Studio'".into())),
            ("licensee".into(), UValue::Str("'Asia'".into())),
        ]).unwrap();
        assert_eq!(w, vec!["(_qs_e0.elem ->> 'course') = 'Pilates Studio'".to_string()]);
        assert_eq!(rows, vec![RowEntry::Original("licensee".into())]);
    }
    // FILL IN: port every case from tests/test_jsonb_unnest_filters.py (negation, IN, NULL,
    //   numbers as text literals, bool, injection, invalid value/operator, prefilter opt-in,
    //   '|' suffix collision, nested doc) — bounded by AC10.
```

### FILL IN checklist
- [ ] `filter_literal` — Python-identical number formatting
- [ ] `split_filters_core` — rules + pre-filter
- [ ] `build_plan_core` hook — before lateral
- [ ] `pgsql_unnest_plan` result dict + `to_py`
- [ ] tests
- [ ] no `unimplemented!()` left

---

## Acceptance Criteria

- [ ] `cd rust && cargo test --no-default-features` passes.
- [ ] After `make build-rust && make stage-rust`, `hasattr(_rs, 'pgsql_unnest_plan')` is True.
- [ ] `pytest tests/test_pgsql_jsonb_unnest_regression.py -q` still passes (the Rust path now plans when candidates appear; non-plan output unchanged).
- [ ] No `unimplemented!()` / `todo!()` remains.

---

## Validation Commands

- `pytest tests/test_pgsql_jsonb_unnest_regression.py -q`
- `pytest tests/test_rust_parsers.py -q`

(plus `cd rust && cargo test --no-default-features` — run explicitly)

---

## Test Specification

Rust `#[cfg(test)]` cases above; Python parity is TASK-787.

---

## Agent Instructions

1. Read TASK-783's Implementation Notes and the Cython `split_filters`; confirm TASK-783 and TASK-785 are completed.
2. Implement; `make build-rust` + `make stage-rust` (exclusive resource).
3. Move this file to `sdd/tasks/completed/`, set index status `"done"`, fill the Completion Note.

---

## Completion Note

**Completed by**: sdd-worker (sequential fallback, Claude)
**Date**: 2026-09-28T22:41:49+00:00
**Notes**: 29 cargo unnest tests pass; wheel built + staged locally; hasattr(_rs,'pgsql_unnest_plan') True; regression/rust_parsers/filters/unnest suites pass on Rust path. float_repr reimplements Python repr exactly (replaced the {:?} approximation from TASK-785, also used by having values). Same 4 pre-existing cargo failures as baseline.

**Deviations from spec**: `having`/`config` typed `&Bound<PyAny>` (spec skeleton said `&Bound<PyDict>`) so non-mapping input yields the Cython-identical ValueError.
