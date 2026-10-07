# TASK-850: BigQuery Rust builder: partial-matching before JSON_VALUE extraction (M7, Rust half)

**Feature**: FEAT-180 — Partial-matching operators for where_cond / filter dict values
**Spec**: `sdd/specs/filter-with-partial-matching.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-841
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 7 (Rust half). `bq_filter_conditions` (`rust/src/bigquery_parser.rs:301`)
extracts serially (Phase 1, GIL) and renders in parallel; `process_dict_value` (line 182)
renders comparison tokens, else `JSON_VALUE(f, '$.<key>') = ...` for single-entry dicts.
Validation goes into Phase 1 (`check_entries(.., false)?`); rendering goes at the top of
`process_dict_value`, before the comparison/JSON branches, with exactly the TASK-849
strings (`bq_like_literal`, no ESCAPE clause).

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

- Add `fn bq_partial_match_condition(field_expr, op, operand) -> String`.
- Validate partial-matching dicts in Phase 1; make the Phase-1 closure return `PyResult`.
- Render them at the top of `process_dict_value`.
- Add `test_pm_bq_*` tests.

**NOT in scope**: `bq_quote_string` (unchanged); Cython half (TASK-849).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `rust/src/bigquery_parser.rs` | MODIFY | Phase-1 validation, rendering, unit tests |
| `tests/test_rust_pm_bq_units.py` | CREATE | pytest wrapper for cargo tests named test_pm_bq_* |

---

## Codebase Contract (Anti-Hallucination)

> **CRITICAL**: This section contains VERIFIED code references from the actual codebase
> (re-verified on `dev` at `71316ad4`, 2026-10-07). The implementing agent MUST use these exact
> imports, class names, and method signatures. **DO NOT** invent, guess, or assume any import,
> attribute, or method not listed here. If you need something not listed, VERIFY it exists first.

### Verified Imports
```rust
use crate::validators::{bq_quote_string, field_components, is_integer};   // verified: rust/src/bigquery_parser.rs:17
use crate::partial_match::{bq_like_literal, build_like_pattern, check_entries, like_escape, lookup, MatchKind, PartialMatchOp};  // TASK-841
```

### Existing Signatures to Use
```rust
// rust/src/bigquery_parser.rs
enum FilterValue { ..., Dict(Vec<(String, FilterValue)>), ... }   // private; Dict built in extract_filter_value (line 97, Dict at ~114-123)
fn process_entry(entry: &FilterEntry) -> Option<String>           // line 141; FilterValue::Dict(entries) => process_dict_value(&field_expr, entries)
fn process_dict_value(field_expr: &str, entries: &[(String, FilterValue)]) -> Option<String>   // line 182
//    let (op, v) = &entries[0];
//    // Standard comparison tokens                                  // line 192 (one-line anchor: occurs once in this file)
//    // For BigQuery, dict values with non-comparison keys are JSON extraction   // line 197
pub fn bq_filter_conditions(sql, filter_dict, cond_definition) -> PyResult<String>   // line 301
//            let value = extract_filter_value(&value_obj);         // line 316 (Phase 1, inside .map closure)
#[cfg(test)] mod tests   // line 406
```

### Does NOT Exist
- ~~an ESCAPE clause for BigQuery~~.
- ~~`bq_quote_string` doubling backslashes~~ (validators.rs:214-225) — use `bq_like_literal`.

---

## Complexity Contract

> Declaration for deterministic complexity routing (FEAT-561). `targets` match the
> Files table exactly.

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "rust/src/bigquery_parser.rs",
      "action": "MODIFY"
    },
    {
      "path": "tests/test_rust_pm_bq_units.py",
      "action": "CREATE"
    }
  ],
  "contract_symbols": [
    "sym:rust/src/bigquery_parser.rs#bq_filter_conditions",
    "sym:rust/src/bigquery_parser.rs#process_dict_value",
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
1. Add the `use` line and helper — *why*: one shared table/literal (spec G7).
2. Validate in Phase 1 — *why*: `?` is only available while holding the GIL serially.
3. Render first in `process_dict_value` — *why*: table names win over `JSON_VALUE` (spec AC8).
4. Add `test_pm_bq_*` tests.

### `rust/src/bigquery_parser.rs` (MODIFY — use + helper)
```rust
// occurrences: 1 (verified: grep -c 'use crate::validators::' rust/src/bigquery_parser.rs) — line 17
// AFTER — insert below line 17:
use crate::partial_match::{bq_like_literal, build_like_pattern, check_entries, like_escape, lookup, MatchKind, PartialMatchOp};

// and above `fn process_dict_value(` (occurrences: 1, line 182):
/// Render one partial-matching operator for BigQuery (FEAT-180, spec §2). Regex never reaches
/// here (rejected in Phase 1 with supports_regex = false).
fn bq_partial_match_condition(field_expr: &str, op: &PartialMatchOp, operand: &str) -> String {
    let like = if op.negated { "NOT LIKE" } else { "LIKE" };
    let lit = bq_like_literal(&build_like_pattern(op, operand, like_escape));
    if op.insensitive {
        format!("LOWER({}) {} LOWER({})", field_expr, like, lit)
    } else {
        format!("{} {} {}", field_expr, like, lit)
    }
}
```

### `rust/src/bigquery_parser.rs` (MODIFY — process_dict_value)
```rust
// occurrences: 1 (verified: grep -c '    // Standard comparison tokens' rust/src/bigquery_parser.rs) — line 192
// BEFORE — insert above line 192:
    if entries.len() == 1 {
        if let (Some(pm), FilterValue::Str(s)) = (lookup(op), v) {
            debug_assert!(pm.kind == MatchKind::Like);
            return Some(bq_partial_match_condition(field_expr, pm, s));
        }
    }
```

### `rust/src/bigquery_parser.rs` (MODIFY — Phase 1 validation)
```rust
// occurrences: 1 (verified: grep -c '            let value = extract_filter_value(&value_obj);' rust/src/bigquery_parser.rs) — line 316
// AFTER — insert below line 316:
            // FILL IN: if `value` is FilterValue::Dict(entries), build &[(&str, Option<&str>)]
            // (Some for FilterValue::Str) and call check_entries(&key, .., false)?; then make the
            // enclosing .map closure return PyResult<FilterEntry> and collect with
            // .collect::<PyResult<Vec<FilterEntry>>>()? — bounded by spec AC5, AC6, AC10, AC14
```

### `tests/test_rust_pm_bq_units.py` (CREATE)
```python
"""FEAT-180: cargo unit tests of the bq Rust builder (test_pm_bq_*)."""
from rust_cargo_runner import run_cargo_lib_tests


def test_pm_bq_cargo_units():
    assert run_cargo_lib_tests("tests::test_pm_bq_") >= 1
```
**Why**: the validation contract needs a pytest command declared by this task; the runner
(TASK-841) fails when the filter matches zero tests.

### FILL IN checklist
- [ ] Phase-1 validation + closure conversion — bounded by spec AC5, AC6, AC10, AC14.
- [ ] `test_pm_bq_*` tests over `bq_partial_match_condition` and `process_dict_value` precedence (`{"contains": "abc"}` LIKE vs `{"other": "x"}` JSON_VALUE) — bounded by spec AC3, AC7, AC8.

---

## Acceptance Criteria

- [ ] `bq_partial_match_condition` output equals TASK-849's Cython strings.
- [ ] Table names win over JSON extraction; other dict keys are unchanged.
- [ ] Invalid operands and regex operators make `bq_filter_conditions` return `Err(PyValueError)`.
- [ ] `cargo check` passes; `pytest tests/test_rust_pm_bq_units.py` passes (≥ 1 `test_pm_bq_*` test ran).

---

## Validation Commands

> File-level pytest only — no directories, no package roots.

- `pytest tests/test_rust_pm_bq_units.py -q`

---

## Test Specification

```rust
#[test]
fn test_pm_bq_contains_percent() {
    let op = crate::partial_match::lookup("contains").unwrap();
    assert_eq!(bq_partial_match_condition("n", op, "50%"), r#"n LIKE "%50\\%%""#);
}
// FILL IN: remaining cases — bounded by AC above
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
9. **Close the task** with `scripts/sdd/close_task.sh TASK-850 filter-with-partial-matching verified`
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
