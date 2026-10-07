# TASK-848: SQL Server Rust builder: dialect-local dict handling (M6, Rust half)

**Feature**: FEAT-180 — Partial-matching operators for where_cond / filter dict values
**Spec**: `sdd/specs/filter-with-partial-matching.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-841
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 6 (Rust half). `mssql_filter_conditions` (`rust/src/mssql_parser.rs:23`)
uses the shared `filter_common::{extract_entries, process_entry}`, which have no dict
variant: a dict becomes `Str(str(dict))` and renders as an equality. SOQL and CQL use the
same helpers.

**Task-time decision (deviation from spec §3 M6, recorded here):** do NOT add a `Dict`
variant to `filter_common.rs`. Changing the shared extraction would change SOQL/CQL output
for dict values (today a stringified equality), breaking spec AC13; design-research S7
recommended a dialect-local representation instead. `mssql_parser.rs` therefore runs its
own Phase 1: for each filter item, a partial-matching dict is validated
(`check_entries(.., false)?`) and rendered while holding the GIL; every other item goes
through `filter_common::extract_filter_value` exactly as today. Order is preserved because
Phase 2 maps an ordered `Vec` with rayon's order-preserving `collect`.

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

- Add an mssql-local `enum MssqlItem { Done(String), Entry(FilterEntry) }` and
  `fn mssql_partial_match_condition(col, op, operand) -> PyResult<String>`.
- Rewrite Phase 1/2 of `mssql_filter_conditions` around it.
- Add `test_pm_mssql_*` tests.

**NOT in scope**: any change to `rust/src/filter_common.rs`, `soql_parser.rs`, `cql_parser.rs`.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `rust/src/mssql_parser.rs` | MODIFY | dialect-local Phase 1 + rendering + unit tests |
| `tests/test_rust_pm_mssql_units.py` | CREATE | pytest wrapper for cargo tests named test_pm_mssql_* |

---

## Codebase Contract (Anti-Hallucination)

> **CRITICAL**: This section contains VERIFIED code references from the actual codebase
> (re-verified on `dev` at `71316ad4`, 2026-10-07). The implementing agent MUST use these exact
> imports, class names, and method signatures. **DO NOT** invent, guess, or assume any import,
> attribute, or method not listed here. If you need something not listed, VERIFY it exists first.

### Verified Imports
```rust
use crate::filter_common::{apply_where_clause, extract_entries, process_entry};   // verified: rust/src/mssql_parser.rs:10
// extend to: use crate::filter_common::{apply_where_clause, extract_filter_value, process_entry, FilterEntry};
//   (extract_filter_value: pub, filter_common.rs:55; FilterEntry: pub struct, filter_common.rs:43-47)
use crate::partial_match::{build_like_pattern, check_entries, like_escape_bang, mssql_like_literal, MatchKind, PartialMatchOp};  // TASK-841
use crate::validators::field_components;   // only if needed for key handling — verified pub fn, validators.rs:120
```

### Existing Signatures to Use
```rust
// rust/src/filter_common.rs (READ ONLY for this task)
pub struct FilterEntry { pub key: String, pub value: FilterValue, pub format_hint: Option<String> }   // lines 43-47
pub fn extract_filter_value(obj: &Bound<'_, pyo3::types::PyAny>) -> FilterValue   // line 55
pub fn extract_entries(filter_dict, cond_definition) -> Vec<FilterEntry>           // line 82 — reproduce its format_hint lookup:
//   cond_definition.get_item(&key).ok().flatten().and_then(|v| v.extract().ok())
pub fn process_entry(entry: &FilterEntry) -> Option<String>                        // line 110
pub fn apply_where_clause(sql: &str, where_cond: &[String]) -> PyResult<String>    // line 206

// rust/src/mssql_parser.rs
pub fn mssql_filter_conditions(sql, filter_dict, cond_definition) -> PyResult<String>   // line 23
//    let entries = extract_entries(filter_dict, cond_definition);                     // line 29
//    let where_cond: Vec<String> = entries.par_iter().filter_map(|entry| process_entry(entry)).collect();   // lines 32-35
#[cfg(test)] mod tests   // line 41
```

### Does NOT Exist
- ~~`FilterValue::Dict` in `filter_common.rs`~~ — not added (decision above).
- ~~key validation in mssql Rust~~ — `process_entry` uses the raw key; the Cython path validates with an alnum check (`sqlserver.pyx:97-108`). Mirror that check for partial-matching keys (FILL IN below).

---

## Complexity Contract

> Declaration for deterministic complexity routing (FEAT-561). `targets` match the
> Files table exactly.

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "rust/src/mssql_parser.rs",
      "action": "MODIFY"
    },
    {
      "path": "tests/test_rust_pm_mssql_units.py",
      "action": "CREATE"
    }
  ],
  "contract_symbols": [
    "sym:rust/src/mssql_parser.rs#mssql_filter_conditions",
    "sym:rust/src/filter_common.rs#extract_filter_value",
    "sym:rust/src/filter_common.rs#process_entry",
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
1. Extend the `use` lines — *why*: Phase 1 is reimplemented locally.
2. Add the item enum and rendering helper — *why*: keep render output identical to TASK-847.
3. Rewrite the function body — *why*: validation needs `?` in the serial phase; order must be preserved.
4. Add `test_pm_mssql_*` tests (existing tests in `mod tests` stay untouched).

### `rust/src/mssql_parser.rs` (MODIFY — use)
```rust
// occurrences: 1 (verified: grep -c 'use crate::filter_common::{apply_where_clause, extract_entries, process_entry};' rust/src/mssql_parser.rs) — line 10
// REPLACE line 10 with:
use crate::filter_common::{apply_where_clause, extract_filter_value, process_entry, FilterEntry};
use crate::partial_match::{build_like_pattern, check_entries, like_escape_bang, mssql_like_literal, MatchKind, PartialMatchOp};
// NOTE: `mod tests` imports `crate::filter_common::{FilterEntry, FilterValue}` itself (line 44) — unaffected.
```

### `rust/src/mssql_parser.rs` (MODIFY — helpers + body)
```rust
// occurrences: 1 (verified: grep -c '    let entries = extract_entries(filter_dict, cond_definition);' rust/src/mssql_parser.rs) — line 29
// Insert above the doc comment of `pub fn mssql_filter_conditions(` and REPLACE its body (lines 28-38):
enum MssqlItem {
    Done(String),
    Entry(FilterEntry),
}

/// Render one partial-matching operator for SQL Server (FEAT-180, spec §2).
fn mssql_partial_match_condition(col: &str, op: &PartialMatchOp, operand: &str) -> PyResult<String> {
    if op.kind == MatchKind::Regex {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "{} on '{}': regex operators are not supported by this query parser", op.name, col)));
    }
    let like = if op.negated { "NOT LIKE" } else { "LIKE" };
    let lit = mssql_like_literal(&build_like_pattern(op, operand, like_escape_bang));
    let esc = if op.escape { " ESCAPE '!'" } else { "" };
    Ok(if op.insensitive {
        format!("LOWER({}) {} LOWER({}){}", col, like, lit, esc)
    } else {
        format!("{} {} {}{}", col, like, lit, esc)
    })
}

// body of mssql_filter_conditions:
    // Phase 1: serial, holds the GIL — partial-matching dicts are validated and rendered here.
    let mut items: Vec<MssqlItem> = Vec::with_capacity(filter_dict.len());
    for (key_obj, value_obj) in filter_dict.iter() {
        let key: String = key_obj.extract().unwrap_or_default();
        // FILL IN: if value_obj is a PyDict, collect (String, Option<String>) pairs and call
        // check_entries(&key, .., false)?; on Some((op, operand)) skip unsafe keys (same alnum/'_'/'.'
        // rule as sqlserver.pyx:97-108 after stripping '|!~#@:') and push
        // MssqlItem::Done(mssql_partial_match_condition(&key, op, operand)?); `continue`.
        // Otherwise push MssqlItem::Entry(FilterEntry { key, value: extract_filter_value(&value_obj),
        // format_hint }) with format_hint looked up like filter_common.rs:82 — bounded by spec AC3, AC13, AC14
    }
    // Phase 2: parallel, order-preserving.
    let where_cond: Vec<String> = items
        .par_iter()
        .filter_map(|item| match item {
            MssqlItem::Done(c) => Some(c.clone()),
            MssqlItem::Entry(e) => process_entry(e),
        })
        .collect();
    apply_where_clause(sql, &where_cond)
```

### `tests/test_rust_pm_mssql_units.py` (CREATE)
```python
"""FEAT-180: cargo unit tests of the mssql Rust builder (test_pm_mssql_*)."""
from rust_cargo_runner import run_cargo_lib_tests


def test_pm_mssql_cargo_units():
    assert run_cargo_lib_tests("tests::test_pm_mssql_") >= 1
```
**Why**: the validation contract needs a pytest command declared by this task; the runner
(TASK-841) fails when the filter matches zero tests.

### FILL IN checklist
- [ ] Phase-1 loop body — bounded by spec AC3, AC13, AC14.
- [ ] `test_pm_mssql_*` tests over `mssql_partial_match_condition` with the TASK-847 CORPUS strings and a regex `Err` — bounded by spec AC3, AC5, AC7.

---

## Acceptance Criteria

- [ ] `mssql_partial_match_condition` output equals TASK-847's Cython strings; regex returns `Err`.
- [ ] Non-dict filters render byte-identically (existing `mod tests` cases pass unchanged).
- [ ] `git diff --stat rust/src/filter_common.rs rust/src/soql_parser.rs rust/src/cql_parser.rs` is empty.
- [ ] `cargo check` passes; `pytest tests/test_rust_pm_mssql_units.py` passes (≥ 1 `test_pm_mssql_*` test ran).

---

## Validation Commands

> File-level pytest only — no directories, no package roots.

- `pytest tests/test_rust_pm_mssql_units.py -q`

---

## Test Specification

```rust
#[test]
fn test_pm_mssql_startswith_bracket() {
    let op = crate::partial_match::lookup("startswith").unwrap();
    assert_eq!(mssql_partial_match_condition("n", op, "a[b").unwrap(), "n LIKE 'a![b%' ESCAPE '!'");
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
9. **Close the task** with `scripts/sdd/close_task.sh TASK-848 filter-with-partial-matching verified`
   — it moves this file to `sdd/tasks/completed/` and marks it `"done"` in the
   index; never move or copy the file by hand
10. **Fill in the Completion Note** below, then commit the staged SDD state

---

## Completion Note

Seat: sonnet · Backend: native · Model: sonnet · Attempts: 1 · Duration: 77s · Tokens: n/a

**Completed by**: sdd-worker (execution e22852df)
**Date**: 2026-10-07
**Notes**: Rust dialect partial-matching implemented; cargo unit tests via the pytest wrapper pass. Review: zero corrections. Known gap: an unsafe key with an invalid dict raises in Rust but is silently skipped in Cython; to be checked in TASK-851.

**Deviations from spec**: none
