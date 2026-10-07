# TASK-844: PostgreSQL Rust builder: partial-matching operators (M4, Rust half)

**Feature**: FEAT-180 — Partial-matching operators for where_cond / filter dict values
**Spec**: `sdd/specs/filter-with-partial-matching.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-841
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 4 (Rust half). `pgsql_filter_conditions` (`rust/src/pgsql_parser.rs:709`)
extracts entries serially while holding the GIL (Phase 1), then renders in parallel with
rayon (Phase 2). Validation must happen in Phase 1 — the only place `?` can return a
`PyErr`. The existing `FilterValue::Condition(String)` variant (rendered while holding the
GIL, used by JSONB) is reused: a valid partial-matching dict becomes a pre-rendered
`Condition`, so Phase 2 is untouched. The Python wrapper (`pgsql.pyx:305-313`) swallows
Rust errors and re-renders through Cython, which raises `ParserError` (spec §2 stage 3).

Run Rust unit tests WITHOUT the default `extension-module` feature — with it the test binary
fails to link (verified 2026-10-07, rust-lld undefined Python symbols):

```bash
export PYO3_PYTHON=$PWD/.venv/bin/python   # or the host python on PATH in a no-shell seat
export LD_LIBRARY_PATH=$($PYO3_PYTHON -c "import sysconfig;print(sysconfig.get_config_var('LIBDIR'))")
cargo test --manifest-path rust/Cargo.toml --lib --no-default-features -- <filter>
```

`tests/rust_cargo_runner.py::run_cargo_lib_tests(filter)` (TASK-841) wraps exactly this command;
each Rust task validates through its own one-test wrapper file built on it.
**Baseline on `dev` (2026-10-07): 4 unrelated unit tests already fail** —
`bigquery_parser::tests::test_process_str_negation`, `pgsql_parser::tests::test_process_comparison_token`,
`sql_parser::tests::test_build_string_condition_end_bang`, `validators::tests::test_field_components_no_prefix`.
Do NOT fix them (out of scope) and do NOT name new tests so that a filter selects them:
every new Rust test in this feature is named `test_pm_<dialect>_<case>` (`pm_pg`, `pm_sql`,
`pm_mssql`, `pm_bq`) or lives in `partial_match::tests`.

---

## Scope

- Add `fn pg_partial_match_from_dict(key, dict) -> PyResult<Option<FilterValue>>` and
  `fn pg_partial_match_condition(col, op, operand) -> String`.
- Call it in Phase 1 before `jsonb_condition`; make the Phase 1 closure return `PyResult`.
- Exempt table names in `jsonb_condition` (defence, mirrors the Cython task).
- Add `test_pm_pg_*` unit tests.

**NOT in scope**: `process_dict_value` and `pg_validate_operator` (table dicts never reach
them now); the Cython half (TASK-843); rebuilding the extension (TASK-851).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `rust/src/pgsql_parser.rs` | MODIFY | Phase-1 validation + pre-rendered Condition; JSONB exemption; unit tests |
| `tests/test_rust_pm_pg_units.py` | CREATE | pytest wrapper for cargo tests named test_pm_pg_* |

---

## Codebase Contract (Anti-Hallucination)

> **CRITICAL**: This section contains VERIFIED code references from the actual codebase
> (re-verified on `dev` at `71316ad4`, 2026-10-07). The implementing agent MUST use these exact
> imports, class names, and method signatures. **DO NOT** invent, guess, or assume any import,
> attribute, or method not listed here. If you need something not listed, VERIFY it exists first.

### Verified Imports
```rust
use crate::validators::{escape_string, field_components, is_camel_case, is_integer, quote_string};  // verified: rust/src/pgsql_parser.rs:14
use crate::partial_match::{build_like_pattern, check_entries, like_escape, MatchKind, PartialMatchOp};  // created by TASK-841
use pyo3::types::{PyAny, PyDict, PyList, PyString};   // verified: pgsql_parser.rs:9
```

### Existing Signatures to Use
```rust
// rust/src/pgsql_parser.rs
fn pg_safe_identifier_key(key: &str) -> Option<String>      // line 24
fn pg_literal(value: &str) -> String                        // line 51
enum FilterValue { Str, Int, Float, Bool, List, Dict(Vec<(String, FilterValue)>), Condition(String), Null }  // lines 97-108
fn jsonb_condition(key: &str, dict: &Bound<'_, PyDict>) -> JsonbOutcome   // line 343
//   inside: `if let Ok(op) = op_obj.extract::<String>() {` then
//           `        if PG_TEXT_OPERATORS.contains(&op.as_str()) {`  (line 361; this 8-space form occurs once,
//           a 4-space form occurs at line 519 — grep -c of the bare text returns 2)
fn process_entry(entry: &FilterEntry) -> Option<String>     // line 432; `let formatted_key = pg_safe_identifier_key(key)?;`
//   then `FilterValue::Condition(c) => Some(c.clone()),`
pub fn pgsql_filter_conditions(sql, filter_dict, cond_definition) -> PyResult<String>   // line 709; Phase 1:
//    let entries: Vec<FilterEntry> = filter_dict.iter().map(|(key_obj, value_obj)| { ...
//            let value = match value_obj.cast::<PyDict>() {
//                Ok(dict) => match jsonb_condition(&key, dict) {          // line 725
//                    JsonbOutcome::NotJsonb => extract_filter_value(&value_obj),
//                    JsonbOutcome::Skip => FilterValue::Null,
//                    JsonbOutcome::Condition(cond) => FilterValue::Condition(cond),
//                },
//                Err(_) => extract_filter_value(&value_obj),
//            };
//            FilterEntry { key, value, format_hint } }).collect();
#[cfg(test)] mod tests   // line 792
```

### Does NOT Exist
- ~~a `PyResult` inside the rayon Phase 2~~ — keep Phase 2 as `filter_map(process_entry)`.
- ~~`PG_TEXT_OPERATORS` extended with table names~~ — leave it as `["ILIKE", "NOT ILIKE"]`.

---

## Complexity Contract

> Declaration for deterministic complexity routing (FEAT-561). `targets` match the
> Files table exactly.

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "rust/src/pgsql_parser.rs",
      "action": "MODIFY"
    },
    {
      "path": "tests/test_rust_pm_pg_units.py",
      "action": "CREATE"
    }
  ],
  "contract_symbols": [
    "sym:rust/src/pgsql_parser.rs#pgsql_filter_conditions",
    "sym:rust/src/pgsql_parser.rs#jsonb_condition",
    "sym:rust/src/pgsql_parser.rs#pg_literal",
    "sym:rust/src/pgsql_parser.rs#pg_safe_identifier_key",
    "sym:rust/src/partial_match.rs#check_entries"
  ]
}
```

---

## Implementation Blueprint

> **CRITICAL — Executor-ready starting point.** Write each block below to its declared
> path nearly verbatim, then complete every `# FILL IN:` marker. Blocks were derived
> from the spec's Interface Skeletons and re-verified against the Codebase Contract
> above when this task was written. Never change a signature, class name, or file path
> the blueprint fixes.

### Steps (in order)
1. Add the `use crate::partial_match::...` line — *why*: one shared table (spec G7).
2. Add the two helpers — *why*: render once while holding the GIL, as JSONB already does.
3. Rewrite the Phase-1 `match` arm and turn the closure into `PyResult<FilterEntry>` — *why*: `?` must return the validation `PyErr`.
4. Add the JSONB exemption — *why*: defence for any other caller of `jsonb_condition`.
5. Add `test_pm_pg_*` tests; run the Validation Command.

### `rust/src/pgsql_parser.rs` (MODIFY — use)
```rust
// occurrences: 1 (verified: grep -c 'use crate::validators::' rust/src/pgsql_parser.rs) — line 14
// AFTER — insert below line 14
use crate::partial_match::{build_like_pattern, check_entries, like_escape, MatchKind, PartialMatchOp};
```

### `rust/src/pgsql_parser.rs` (MODIFY — helpers, insert before `pub fn pgsql_filter_conditions(`)
```rust
// occurrences: 1 (verified: grep -c 'pub fn pgsql_filter_conditions(' rust/src/pgsql_parser.rs)
// BEFORE — insert above the doc comment of `pub fn pgsql_filter_conditions(` (line ~703)
/// Render one partial-matching operator for PostgreSQL (FEAT-180, spec §2).
fn pg_partial_match_condition(col: &str, op: &PartialMatchOp, operand: &str) -> String {
    if op.kind == MatchKind::Regex {
        let sql_op = format!("{}{}", if op.negated { "!~" } else { "~" }, if op.insensitive { "*" } else { "" });
        return format!("{} {} {}", col, sql_op, pg_literal(operand));
    }
    let base = if op.insensitive { "ILIKE" } else { "LIKE" };
    let sql_op = if op.negated { format!("NOT {}", base) } else { base.to_string() };
    format!("{} {} {}", col, sql_op, pg_literal(&build_like_pattern(op, operand, like_escape)))
}

/// Ok(None): not a partial-matching dict. Ok(Some(Condition | Null)): rendered, or dropped
/// because the key is not a safe identifier (same outcome as process_entry today).
fn pg_partial_match_from_dict(key: &str, dict: &Bound<'_, PyDict>) -> PyResult<Option<FilterValue>> {
    // FILL IN: build Vec<(String, Option<String>)> from dict items (Some for str values),
    // borrow as &[(&str, Option<&str>)], call check_entries(key, .., true)?; on Some((op, operand))
    // return Condition(pg_partial_match_condition(&safe_key, op, operand)) or Null when
    // pg_safe_identifier_key(key) is None — bounded by spec AC3, AC8, AC14
    Ok(None)
}
```

### `rust/src/pgsql_parser.rs` (MODIFY — Phase 1)
```rust
// occurrences: 1 (verified: grep -c 'Ok(dict) => match jsonb_condition(&key, dict) {' rust/src/pgsql_parser.rs) — line 725
// REPLACE the Phase-1 `let value = match value_obj.cast::<PyDict>() { ... };` (lines 724-731) with:
            let value = match value_obj.cast::<PyDict>() {
                Ok(dict) => match pg_partial_match_from_dict(&key, dict)? {
                    Some(v) => v,
                    None => match jsonb_condition(&key, dict) {
                        JsonbOutcome::NotJsonb => extract_filter_value(&value_obj),
                        JsonbOutcome::Skip => FilterValue::Null,
                        JsonbOutcome::Condition(cond) => FilterValue::Condition(cond),
                    },
                },
                Err(_) => extract_filter_value(&value_obj),
            };
// FILL IN: make the enclosing `.map(|(key_obj, value_obj)| { ... })` closure return
// `PyResult<FilterEntry>` (`Ok(FilterEntry { .. })`) and collect with
// `.collect::<PyResult<Vec<FilterEntry>>>()?` — bounded by spec AC10
```

### `rust/src/pgsql_parser.rs` (MODIFY — JSONB exemption)
```rust
// occurrences of `if PG_TEXT_OPERATORS.contains(&op.as_str()) {`: 2 (lines 361 and 519) — AMBIGUOUS.
// Attach inside `fn jsonb_condition` using this unique context (lines 356-363):
//     if let Ok(op) = op_obj.extract::<String>() {
//         // qsurl text-match operators (FEAT-152) are handled by process_dict_value,
//         ...
//         if PG_TEXT_OPERATORS.contains(&op.as_str()) {
// BEFORE — insert above that 8-space-indented `if PG_TEXT_OPERATORS...` line:
        if crate::partial_match::lookup(&op).is_some() {
            return JsonbOutcome::NotJsonb;
        }
```

### `tests/test_rust_pm_pg_units.py` (CREATE)
```python
"""FEAT-180: cargo unit tests of the pg Rust builder (test_pm_pg_*)."""
from rust_cargo_runner import run_cargo_lib_tests


def test_pm_pg_cargo_units():
    assert run_cargo_lib_tests("tests::test_pm_pg_") >= 1
```
**Why**: the validation contract needs a pytest command declared by this task; the runner
(TASK-841) fails when the filter matches zero tests.

### FILL IN checklist
- [ ] `pg_partial_match_from_dict` body — bounded by spec AC3, AC8, AC14.
- [ ] Phase-1 closure → `PyResult` — bounded by spec AC10.
- [ ] `test_pm_pg_*` tests (pure-Rust: test `pg_partial_match_condition` for all 20 operators and the escaping corpus `o'brien`, `50%`, `a_b`, `a\b`, `{x}`; GIL-requiring tests use `Python::attach`/`with_gil` per the crate's existing tests, or are left to TASK-851's pytest) — bounded by spec AC3, AC7.

---

## Acceptance Criteria

- [ ] `pg_partial_match_condition` output equals the TASK-843 Cython output for every corpus row (spec §2 PostgreSQL column — the strings TASK-843 asserts).
- [ ] An invalid operand makes `pgsql_filter_conditions` return `Err(PyValueError)` with the TASK-840 message.
- [ ] `cargo check --manifest-path rust/Cargo.toml` passes; `pytest tests/test_rust_pm_pg_units.py` passes (≥ 1 `test_pm_pg_*` test ran).
- [ ] No change to `process_dict_value`, `PG_TEXT_OPERATORS`, or the `~`/`!~` suffix handling.

---

## Validation Commands

> File-level pytest only — no directories, no package roots.

- `pytest tests/test_rust_pm_pg_units.py -q`

---

## Test Specification

```rust
// in rust/src/pgsql_parser.rs `mod tests` (line 792)
#[test]
fn test_pm_pg_startswith() {
    let op = crate::partial_match::lookup("startswith").unwrap();
    assert_eq!(pg_partial_match_condition("full_name", op, "andre"), "full_name LIKE 'andre%'");
}
// FILL IN: the rest of the corpus — bounded by AC above
```

---

## Agent Instructions

When you pick up this task:

1. **Work in the feature worktree** — never on `base_branch`
   (`python -m scripts.sdd.ensure_worktree --slug filter-with-partial-matching --feature-id FEAT-180`)
2. **Read the spec** at the path listed above for full context (§2 rendered-forms table is normative)
3. **Check dependencies** — every `Depends-on` task must be `"done"` in the
   per-spec index `sdd/tasks/index/filter-with-partial-matching.json`
4. **Verify the Codebase Contract** — before writing ANY code:
   - Confirm every import in "Verified Imports" still exists (`grep` or `read` the source)
   - Re-run `grep -c` for every MODIFY anchor in the blueprint; a count that differs from the
     one recorded means the anchor moved — re-locate it; a count of `0` means STOP and report drift
   - **NEVER** reference an import, attribute, or method not in the contract without verifying it exists
5. **Update status** in `sdd/tasks/index/filter-with-partial-matching.json` → `"in-progress"`
   (set `started_at`) and commit only that index file
6. **Implement** — start from the Implementation Blueprint blocks, complete every
   `# FILL IN:` marker, and never change a signature or path the blueprint fixes
7. **Verify** all acceptance criteria are met — run the Validation Commands
8. **Commit the code** — stage only the files this task lists (never `git add .` / `-A`)
9. **Close the task** with `scripts/sdd/close_task.sh TASK-844 filter-with-partial-matching verified`
   — it moves this file to `sdd/tasks/completed/` and marks it `"done"` in the
   index; never move or copy the file by hand
10. **Fill in the Completion Note** below, then commit the staged SDD state

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
