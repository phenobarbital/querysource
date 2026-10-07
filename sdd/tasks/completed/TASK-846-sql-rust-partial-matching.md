# TASK-846: Generic SQL Rust builder: LIKE forms (M5, Rust half)

**Feature**: FEAT-180 — Partial-matching operators for where_cond / filter dict values
**Spec**: `sdd/specs/filter-with-partial-matching.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-841
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 5 (Rust half). `sql_parser::filter_conditions` (`rust/src/sql_parser.rs:218`)
is a serial loop holding the GIL, so `?` works anywhere. Its dict branch (lines 246-258)
reads the first entry and `continue`s on any operator outside the allowlist. This task
renders table operators there, with exactly the TASK-845 Cython strings.

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

- Add `fn sql_partial_match_condition(col, op, operand) -> PyResult<String>` (regex ⇒ `Err`).
- At the top of the dict branch, run `check_entries(key, .., false)?` and render.
- Add `test_pm_sql_*` tests.

**NOT in scope**: `validate_operator` / `VALID_OPERATORS` (unchanged); Cython half (TASK-845).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `rust/src/sql_parser.rs` | MODIFY | dict-branch rendering + unit tests |
| `tests/test_rust_pm_sql_units.py` | CREATE | pytest wrapper for cargo tests named test_pm_sql_* |

---

## Codebase Contract (Anti-Hallucination)

> **CRITICAL**: This section contains VERIFIED code references from the actual codebase
> (re-verified on `dev` at `71316ad4`, 2026-10-07). The implementing agent MUST use these exact
> imports, class names, and method signatures. **DO NOT** invent, guess, or assume any import,
> attribute, or method not listed here. If you need something not listed, VERIFY it exists first.

### Verified Imports
```rust
use crate::validators::{escape_string, field_components, quote_string};   // verified: rust/src/sql_parser.rs:11
use crate::partial_match::{build_like_pattern, check_entries, like_escape_bang, sql_like_literal, MatchKind, PartialMatchOp};  // TASK-841
```

### Existing Signatures to Use
```rust
// rust/src/sql_parser.rs
fn safe_identifier_key(key: &str) -> Result<String, String>   // line 27
pub fn filter_conditions(sql: &str, filter_dict: &Bound<'_, PyDict>, cond_definition: &Bound<'_, PyDict>) -> PyResult<String>  // line 218
//   let formatted_key = safe_identifier_key(&key).map_err(|e| PyValueError::new_err(e))?;   // ~line 228
//   if let Ok(dict_val) = value_obj.cast::<PyDict>() {                                      // line 246
//       if let Some((op_obj, v_obj)) = dict_val.iter().next() {
//           let op: String = op_obj.extract()?;
//           if validate_operator(&op).is_err() {                                             // line 251
#[cfg(test)] mod tests   // line 623
```

### Does NOT Exist
- ~~`VALID_OPERATORS` containing LIKE~~ — do not extend it.
- ~~`quote_string`/`escape_string` for patterns~~ — they strip quotes and rewrite `%`; use `sql_like_literal`.

---

## Complexity Contract

> Declaration for deterministic complexity routing (FEAT-561). `targets` match the
> Files table exactly.

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "rust/src/sql_parser.rs",
      "action": "MODIFY"
    },
    {
      "path": "tests/test_rust_pm_sql_units.py",
      "action": "CREATE"
    }
  ],
  "contract_symbols": [
    "sym:rust/src/sql_parser.rs#filter_conditions",
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
1. Add the `use` line and the helper — *why*: one shared table and literal helper (spec G7).
2. Insert the block at the top of the dict branch — *why*: table dicts must never reach `validate_operator`'s silent `continue`.
3. Add `test_pm_sql_*` tests; run the Validation Command.

### `rust/src/sql_parser.rs` (MODIFY — use)
```rust
// occurrences: 1 (verified: grep -c 'use crate::validators::{escape_string, field_components, quote_string};' rust/src/sql_parser.rs) — line 11
// AFTER — insert below line 11:
use crate::partial_match::{build_like_pattern, check_entries, like_escape_bang, sql_like_literal, MatchKind, PartialMatchOp};
```

### `rust/src/sql_parser.rs` (MODIFY — helper, insert before `pub fn filter_conditions(`)
```rust
// occurrences: 1 (verified: grep -c 'pub fn filter_conditions(' rust/src/sql_parser.rs) — insert above its doc comment (~line 205)
/// Render one partial-matching operator for generic SQL (FEAT-180, spec §2).
fn sql_partial_match_condition(col: &str, op: &PartialMatchOp, operand: &str) -> PyResult<String> {
    if op.kind == MatchKind::Regex {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "{} on '{}': regex operators are not supported by this query parser", op.name, col)));
    }
    let like = if op.negated { "NOT LIKE" } else { "LIKE" };
    let lit = sql_like_literal(&build_like_pattern(op, operand, like_escape_bang));
    let esc = if op.escape { " ESCAPE '!'" } else { "" };
    Ok(if op.insensitive {
        format!("LOWER({}) {} LOWER({}){}", col, like, lit, esc)
    } else {
        format!("{} {} {}{}", col, like, lit, esc)
    })
}
```

### `rust/src/sql_parser.rs` (MODIFY — dict branch)
```rust
// occurrences: 1 (verified: grep -c 'if let Ok(dict_val) = value_obj.cast::<PyDict>() {' rust/src/sql_parser.rs) — line 246
// AFTER — insert directly below line 246, before `if let Some((op_obj, v_obj)) = dict_val.iter().next() {`
            // FEAT-180: partial-matching operators (validated; Err surfaces as ParserError via sql.pyx fallthrough)
            // FILL IN: collect (String, Option<String>) pairs from dict_val, call
            // check_entries(&key, .., false)?; on Some((op, operand)) push
            // sql_partial_match_condition(&formatted_key, op, operand)? and `continue` — bounded by spec AC3, AC14
```

### `tests/test_rust_pm_sql_units.py` (CREATE)
```python
"""FEAT-180: cargo unit tests of the sql Rust builder (test_pm_sql_*)."""
from rust_cargo_runner import run_cargo_lib_tests


def test_pm_sql_cargo_units():
    assert run_cargo_lib_tests("tests::test_pm_sql_") >= 1
```
**Why**: the validation contract needs a pytest command declared by this task; the runner
(TASK-841) fails when the filter matches zero tests.

### FILL IN checklist
- [ ] Dict-branch block — bounded by spec AC3, AC14.
- [ ] `test_pm_sql_*` tests over `sql_partial_match_condition` with the TASK-845 CORPUS strings and a regex `Err` — bounded by spec AC3, AC5, AC7.

---

## Acceptance Criteria

- [ ] `sql_partial_match_condition` output equals TASK-845's Cython strings for every LIKE-family operator; regex returns `Err`.
- [ ] `cargo check` passes; `pytest tests/test_rust_pm_sql_units.py` passes (≥ 1 `test_pm_sql_*` test ran).
- [ ] `validate_operator` and the comparison path are unchanged.

---

## Validation Commands

> File-level pytest only — no directories, no package roots.

- `pytest tests/test_rust_pm_sql_units.py -q`

---

## Test Specification

```rust
#[test]
fn test_pm_sql_istartswith() {
    let op = crate::partial_match::lookup("istartswith").unwrap();
    assert_eq!(sql_partial_match_condition("n", op, "andre").unwrap(),
               "LOWER(n) LIKE LOWER('andre%') ESCAPE '!'");
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
9. **Close the task** with `scripts/sdd/close_task.sh TASK-846 filter-with-partial-matching verified`
   — it moves this file to `sdd/tasks/completed/` and marks it `"done"` in the
   index; never move or copy the file by hand
10. **Fill in the Completion Note** below, then commit the staged SDD state

---

## Completion Note

Seat: gpt-5.6-luna · Backend: codex · Model: gpt-5.6-luna · Attempts: 1 · Duration: 181s · Tokens: n/a

**Completed by**: sdd-worker (execution e22852df)
**Date**: 2026-10-07
**Notes**: Rust dialect partial-matching implemented; cargo unit tests via the pytest wrapper pass. Review: zero corrections.

**Deviations from spec**: none
