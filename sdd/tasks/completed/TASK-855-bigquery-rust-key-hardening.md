# TASK-855: BigQuery Rust builder: validate column and JSON member keys before rendering

**Feature**: FEAT-162 — BigQuery Rust filter-key hardening
**Spec**: `sdd/specs/fixgroup-34032c139334.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned
**discovered_from**: issue:aa699dd75abf

---

## Context

Promoted by `/sdd-fix` from ledger issue `issue:aa699dd75abf` (vulnerability, minor,
"BigQuery Rust path renders unsafe column keys for partial-matching operators",
discovered from `spec:FEAT-180`). Spec §1 names the two unvalidated render sites in
`rust/src/bigquery_parser.rs`: the top-level filter key in `process_entry` (line 142;
only numeric keys are handled, every other key is rendered verbatim — including into
the FEAT-180 `LIKE` forms) and the dict member key interpolated into
`JSON_VALUE(f, '$.<op>')` in `process_dict_value` (line 221). The Cython twin already
validates the top-level key (`bigquery.pyx:185`, FEAT-103) and the PostgreSQL Rust
builder has `pg_safe_identifier_key` (`pgsql_parser.rs:25`); the BigQuery Rust builder
has neither. This task implements spec §3 M1 + M2 (single task: one file is modified
and both wrappers depend on its test names).

Run Rust unit tests WITHOUT the default `extension-module` feature (see
`tests/rust_cargo_runner.py`). Baseline on `dev` `0c1bbb06`: `bigquery_parser::tests`
= 25 passed, 1 pre-existing failure (`test_process_str_negation`) — do NOT fix it and
name every new test `test_bqkey_*` so the wrapper's filter never selects it.

---

## Scope

- Add `JSON_MEMBER_PATTERN`, `bq_safe_identifier_key` and `bq_safe_json_member` to
  `rust/src/bigquery_parser.rs`.
- Call `bq_safe_identifier_key(key)?` in `process_entry` (replacing the numeric-only
  quoting block) and guard the JSON-extraction branch of `process_dict_value` with
  `bq_safe_json_member(op)`.
- Add `test_bqkey_*` unit tests in `mod tests`.
- Create `tests/test_rust_bqkey_units.py` (cargo wrapper) and
  `tests/test_bigquery_key_hardening.py` (compiled-extension probe, skip-guarded).

**NOT in scope**: the Cython twin (`bigquery.pyx:246-250`, sibling ledger issue);
raising errors for unsafe keys (spec §7 D1); other dialects; `bq_quote_string`.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `rust/src/bigquery_parser.rs` | MODIFY | validators, two render guards, `test_bqkey_*` tests |
| `tests/test_rust_bqkey_units.py` | CREATE | pytest wrapper over `run_cargo_lib_tests("tests::test_bqkey_")` |
| `tests/test_bigquery_key_hardening.py` | CREATE | hostile-key probes against `_rs.bq_filter_conditions` |

---

## Codebase Contract (Anti-Hallucination)

> **CRITICAL**: VERIFIED code references (dev `0c1bbb06`, 2026-10-07). Use these exact
> imports and signatures. **DO NOT** invent anything not listed here; verify first.

### Verified Imports
```rust
use regex::Regex;                                                          // verified: rust/src/bigquery_parser.rs:11
use std::sync::LazyLock;                                                   // verified: rust/src/bigquery_parser.rs:13
use crate::validators::{bq_quote_string, field_components, is_integer};   // verified: rust/src/bigquery_parser.rs:18
```
```python
from rust_cargo_runner import run_cargo_lib_tests      # verified: tests/rust_cargo_runner.py:23; same import as tests/test_rust_pm_bq_units.py:3
from querysource.parsers import bigquery as bqmod      # verified: tests/test_bigquery_partial_matching.py:22 (bqmod.HAS_RUST, bqmod._rs)
```

### Existing Signatures to Use
```rust
// rust/src/bigquery_parser.rs
static JSON_DOT_PATTERN: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^([a-zA-Z0-9_]+)\.([a-zA-Z0-9_.]+)$").unwrap());  // line 27
enum FilterValue { Str(String), Int(i64), Float(f64), Bool(bool), List(Vec<FilterValue>), Dict(Vec<(String, FilterValue)>), Null }  // line 36 (file-local)
struct FilterEntry { key: String, value: FilterValue, format_hint: Option<String> }  // line 63
fn process_entry(entry: &FilterEntry) -> Option<String>                              // line 142
//    // Quote numeric keys                                                           // line 147 (occurrences: 1)
//    let quoted_key = if key.parse::<i64>().is_ok() || is_integer(key) {            // line 148 (occurrences: 1) — block ends line 152 `};`
fn process_dict_value(field_expr: &str, entries: &[(String, FilterValue)]) -> Option<String>  // line 195
//        let json_expr = format!("JSON_VALUE({}, '$.{}')", field_expr, op);        // line 221 (occurrences: 1)
#[cfg(test)] mod tests { use super::*; ... }                                          // line 442
//    fn test_process_json_dict_extraction() {                                       // line 680 (occurrences: 1) — preceded by `#[test]` on line 679

// rust/src/pgsql_parser.rs — pattern to copy (private fn, NOT importable)
fn pg_safe_identifier_key(key: &str) -> Option<String>   // line 25-35: trim_end_matches '|' '!' '~' '#' '@' ':'; numeric → "\"{key}\""; alnum/_/. → key; else None
```
```python
# tests/rust_cargo_runner.py
def run_cargo_lib_tests(cargo_filter: str) -> int   # line 23; skips without cargo, pytest.fail on exit≠0 or zero matches
```

### Does NOT Exist
- ~~`crate::validators::bq_safe_identifier_key`~~ / ~~`is_safe_identifier`~~ — no shared validator; write it file-local.
- ~~`crate::pgsql_parser::pg_safe_identifier_key`~~ — private; copy the rule, do not import.
- ~~`FilterValue::Dict` in `filter_common.rs`~~ — use the file-local enum.
- ~~`tests/__init__.py`~~ — `rust_cargo_runner` is imported as a top-level module.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "rust/src/bigquery_parser.rs",
      "action": "MODIFY"
    },
    {
      "path": "tests/test_rust_bqkey_units.py",
      "action": "CREATE"
    },
    {
      "path": "tests/test_bigquery_key_hardening.py",
      "action": "CREATE"
    }
  ],
  "contract_symbols": [
    "sym:rust/src/bigquery_parser.rs#process_entry",
    "sym:rust/src/bigquery_parser.rs#process_dict_value",
    "sym:rust/src/bigquery_parser.rs#bq_filter_conditions",
    "sym:rust/src/pgsql_parser.rs#pg_safe_identifier_key",
    "sym:rust/src/validators.rs#is_integer",
    "sym:tests/rust_cargo_runner.py#run_cargo_lib_tests"
  ]
}
```

---

## Implementation Blueprint

> Write each block to its path nearly verbatim, then complete every `# FILL IN:` marker.

### Steps (in order)
1. Add `JSON_MEMBER_PATTERN` below `JSON_DOT_PATTERN` — *why*: compiled once, reused on every rayon worker.
2. Add the two validators directly above `process_entry` — *why*: they are dialect-local (spec §7 D2), next to their only callers.
3. Replace the `quoted_key` block in `process_entry` with `bq_safe_identifier_key(key)?` — *why*: FEAT-103 parity; `?` drops the condition exactly like `pg_safe_identifier_key(key)?` (spec D1).
4. Guard the JSON-extraction branch of `process_dict_value` — *why*: `op` lands inside a string literal; only an identifier path may be interpolated (spec D2).
5. Add the `test_bqkey_*` tests, then the two pytest files — *why*: the validation contract needs file-level pytest commands.

### `rust/src/bigquery_parser.rs` (MODIFY — static)
```rust
// occurrences: 1 (verified: grep -c 'static JSON_DOT_PATTERN: LazyLock<Regex> =' rust/src/bigquery_parser.rs) — line 27
// AFTER — insert below the JSON_DOT_PATTERN definition (it ends on line 28 with `.unwrap());`):

/// Dotted JSON member path allowed inside `JSON_VALUE(f, '$.<member>')` (FEAT-162):
/// `region`, `address.city`. Rejects anything that could close the string literal.
static JSON_MEMBER_PATTERN: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$").unwrap());
```

### `rust/src/bigquery_parser.rs` (MODIFY — validators)
```rust
// occurrences: 1 (verified: grep -c 'fn process_entry(entry: &FilterEntry) -> Option<String> {' rust/src/bigquery_parser.rs) — line 142
// BEFORE — insert above the doc comment `/// Process a single filter entry into a WHERE condition string.` that precedes line 142:

/// Validate and produce a safe column key (FEAT-162; the FEAT-103 rule of
/// `bigquery.pyx` and `pg_safe_identifier_key` in `pgsql_parser.rs`).
///
/// Suffix markers (`|!~#@:`) are ignored for the check. Numeric keys are
/// double-quoted, alphanumeric/underscore/dot keys pass through unchanged, and
/// anything else — including a key that is empty once stripped — is rejected.
fn bq_safe_identifier_key(key: &str) -> Option<String> {
    let stripped = key.trim_end_matches(|c: char| matches!(c, '|' | '!' | '~' | '#' | '@' | ':'));
    if stripped.is_empty() {
        return None;
    }
    if stripped.parse::<i64>().is_ok() || is_integer(stripped) {
        return Some(format!("\"{}\"", key));
    }
    if stripped.chars().all(|c| c.is_alphanumeric() || c == '_' || c == '.') {
        Some(key.to_string())
    } else {
        None
    }
}

/// True when `member` may be interpolated into `JSON_VALUE(f, '$.<member>')`.
fn bq_safe_json_member(member: &str) -> bool {
    JSON_MEMBER_PATTERN.is_match(member)
}
```
**Why**: one rule per render site (spec D2); `stripped.is_empty()` is spec D3.

### `rust/src/bigquery_parser.rs` (MODIFY — process_entry)
```rust
// occurrences: 1 (verified: grep -c '    let quoted_key = if key.parse::<i64>().is_ok() || is_integer(key) {' rust/src/bigquery_parser.rs) — line 148
// REPLACE lines 147-152 (`    // Quote numeric keys` through the closing `    };`) with:
    // SECURITY (FEAT-162): the key must be a safe identifier; an unsafe key drops the condition.
    let quoted_key = bq_safe_identifier_key(key)?;
```
**Why**: numeric quoting is preserved inside the validator, so safe keys render byte-for-byte as before (spec G3).

### `rust/src/bigquery_parser.rs` (MODIFY — process_dict_value)
```rust
// occurrences: 1 (verified: grep -c "        let json_expr = format!(\"JSON_VALUE({}, '\$.{}')\", field_expr, op);" rust/src/bigquery_parser.rs) — line 221
// BEFORE — insert above that line (inside the `if entries.len() == 1 {` block, after `let val_str = v.as_str();`):
        // SECURITY (FEAT-162): `op` lands inside a string literal — only an identifier path may be rendered.
        if !bq_safe_json_member(op) {
            return None;
        }
```

### `rust/src/bigquery_parser.rs` (MODIFY — tests)
```rust
// occurrences: 1 (verified: grep -c '    fn test_process_json_dict_extraction() {' rust/src/bigquery_parser.rs) — line 680
// BEFORE — insert above the `#[test]` attribute that precedes line 680:
    // -- FEAT-162 key hardening (all named test_bqkey_* for the cargo filter) --

    #[test]
    fn test_bqkey_identifier_plain() {
        assert_eq!(bq_safe_identifier_key("name").as_deref(), Some("name"));
        assert_eq!(bq_safe_identifier_key("meta.region").as_deref(), Some("meta.region"));
        assert_eq!(bq_safe_identifier_key("col!").as_deref(), Some("col!"));
        assert_eq!(bq_safe_identifier_key("123").as_deref(), Some("\"123\""));
    }

    #[test]
    fn test_bqkey_identifier_rejects() {
        // FILL IN: `a b`, `a;drop`, `x') OR 1=1 --`, `` and `!` all return None — bounded by AC1/AC2 and spec D3
    }

    #[test]
    fn test_bqkey_json_member() {
        assert!(bq_safe_json_member("region"));
        assert!(bq_safe_json_member("a.b.c"));
        // FILL IN: rejects `x') = "" OR TRUE OR ('`, `a-b`, `.a`, `a.`, `` — bounded by AC3
    }

    #[test]
    fn test_bqkey_entry_unsafe_key_dropped() {
        // FILL IN: FilterEntry with key `n) = 1 OR 1=1 --` and Str / Dict{"contains"} / Int values → process_entry is None — bounded by AC2
    }

    #[test]
    fn test_bqkey_dict_unsafe_member_dropped() {
        // FILL IN: FilterEntry{key:"meta", Dict[("x') = \"\" OR TRUE OR ('", Str("v"))]} → None; and {"meta": {"region": "us"}} still renders JSON_VALUE(meta, '$.region') = "us" — bounded by AC3/G3
    }
```

### `tests/test_rust_bqkey_units.py` (CREATE)
```python
"""FEAT-162: cargo unit tests of the BigQuery Rust key validators (test_bqkey_*)."""

from rust_cargo_runner import run_cargo_lib_tests


def test_bqkey_cargo_units() -> None:
    """At least one test_bqkey_* cargo test ran and all of them passed."""
    assert run_cargo_lib_tests("tests::test_bqkey_") >= 1
```

### `tests/test_bigquery_key_hardening.py` (CREATE)
```python
"""FEAT-162: hostile filter keys never reach the SQL rendered by the Rust BigQuery builder."""
import pytest

from querysource.parsers import bigquery as bqmod  # verified: tests/test_bigquery_partial_matching.py:22

SQL = "SELECT * FROM t {where_cond}"
HOSTILE_MEMBER = "x') = \"\" OR TRUE OR ('"
HOSTILE_COLUMN = "n) = 1 OR 1=1 --"


def _rust_hardened() -> bool:
    """Probe the compiled extension: a stale build still renders the hostile member key."""
    if not bqmod.HAS_RUST:
        return False
    try:
        rendered = bqmod._rs.bq_filter_conditions(SQL, {"meta": {HOSTILE_MEMBER: "v"}}, {})
    except Exception:
        return False
    return "OR TRUE" not in rendered


pytestmark = pytest.mark.skipif(not _rust_hardened(), reason="stale _qs_parsers (TASK-855)")

# FILL IN: test_rust_rejects_hostile_dict_member / test_rust_rejects_hostile_column_key /
# test_rust_safe_keys_unchanged per spec §4 — bounded by AC2, AC3, G3
```

### FILL IN checklist
- [ ] `test_bqkey_identifier_rejects`, `test_bqkey_json_member` negatives — bounded by AC1–AC3, D3.
- [ ] `test_bqkey_entry_unsafe_key_dropped`, `test_bqkey_dict_unsafe_member_dropped` — bounded by AC2/AC3/G3.
- [ ] three Python probes in `tests/test_bigquery_key_hardening.py` — bounded by AC2/AC3/G3.

---

## Acceptance Criteria

- [ ] AC1. `bq_safe_identifier_key` accepts exactly what `bigquery.pyx:185-195` accepts and quotes numeric keys the same way.
- [ ] AC2. `process_entry` returns `None` for any rejected top-level key, for str, dict, list and int values.
- [ ] AC3. `process_dict_value` returns `None` instead of rendering `JSON_VALUE` for a non-identifier member key.
- [ ] AC4. `cargo test --lib --no-default-features -- bigquery_parser::tests`: no new failures vs. the 25-pass/1-fail baseline.
- [ ] AC5. `pytest tests/test_rust_bqkey_units.py tests/test_bigquery_key_hardening.py -q` passes (Python file may skip only as "stale _qs_parsers").
- [ ] AC6. `pytest tests/test_bigquery_partial_matching.py tests/test_rust_pm_bq_units.py -q` stays green.

---

## Validation Commands

> File-level pytest only — no directories, no package roots.

- `pytest tests/test_rust_bqkey_units.py -q`
- `pytest tests/test_bigquery_key_hardening.py -q`
- `pytest tests/test_rust_pm_bq_units.py -q`

---

## Test Specification

```rust
#[test]
fn test_bqkey_dict_unsafe_member_dropped() {
    let entry = FilterEntry {
        key: "meta".to_string(),
        value: FilterValue::Dict(vec![("x') = \"\" OR TRUE OR ('".to_string(), FilterValue::Str("v".to_string()))]),
        format_hint: None,
    };
    assert!(process_entry(&entry).is_none());
}
```

---

## Agent Instructions

When you pick up this task:

1. **Work in the feature worktree** — never on `base_branch`
   (`python -m scripts.sdd.ensure_worktree --slug fixgroup-34032c139334 --feature-id FEAT-162`)
2. **Read the spec** at the path listed above for full context
3. **Check dependencies** — none
4. **Verify the Codebase Contract** — re-run `grep -c` for every MODIFY anchor; a count of `0` means STOP and report drift
5. **Update status** in `sdd/tasks/index/fixgroup-34032c139334.json` → `"in-progress"`
   (set `started_at`) and commit only that index file
6. **Implement** — start from the Implementation Blueprint blocks, complete every `# FILL IN:` marker
7. **Verify** all acceptance criteria are met — run the Validation Commands
8. **Commit the code** — stage only the files this task lists (never `git add .` / `-A`)
9. **Close the task** with `scripts/sdd/close_task.sh TASK-855 fixgroup-34032c139334 verified`
10. **Fill in the Completion Note** below, then commit the staged SDD state

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: agent:sdd-fix (Claude Code session 5141c3af)
**Date**: 2026-10-07
**Notes**: Implemented in commit 3b4e28db. Added `JSON_MEMBER_PATTERN`, `bq_safe_identifier_key`
and `bq_safe_json_member` to `rust/src/bigquery_parser.rs`; `process_entry` now uses
`bq_safe_identifier_key(key)?` and the JSON-extraction branch of `process_dict_value` is
guarded by `bq_safe_json_member(op)`. Six `test_bqkey_*` cargo tests (module total 31 passed +
the pre-existing `test_process_str_negation` failure, unchanged). Validation in the worktree
after `python setup.py build_ext --inplace` and staging a freshly built `_qs_parsers` wheel:
`pytest tests/test_bigquery_key_hardening.py tests/test_rust_bqkey_units.py
tests/test_rust_pm_bq_units.py tests/test_bigquery_partial_matching.py -q` → 62 passed, 0 skipped.
Sibling Cython hole filed as `issue:f5d0b4764384`.

**Deviations from spec**: none (test operands use a 3-character `contains` value because
FEAT-180 pre-validation rejects shorter ones).
