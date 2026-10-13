# TASK-875: Rust allowlists env-aware; `is_pg_function` exact; `(` no longer raw

**Feature**: FEAT-167 — WHERE value hardening — `is_valid()` closure and idempotent literal quoting
**Spec**: `sdd/specs/where-value-hardening.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 1 (Rust half) / goals G2, G7; §8 Q1 (10-name default) and
Q4 (fix the env-override gap here). `rust/src/validators.rs` mirrors
`validators.pyx` but its allowlists are static (`UDF_LIST` line 21,
`PG_CONSTANTS` line 34, no `PG_UDF` at all) and `is_pg_function` (line 92)
returns `true` for anything containing `(`. `is_valid` (line 269) then
returns such a value verbatim (line 335), and `rust/src/safe_dict.rs:162`
uses it for raw-query placeholders. This task adds an `env_list` helper,
makes the three statics env-aware with the same defaults as the Cython side,
and makes `is_pg_function` an exact `PG_UDF` membership test. It runs
independently of the Cython tasks (no shared file).

---

## Scope

- `rust/src/validators.rs`: `use std::env;`, `fn env_list(...)`,
  `DEFAULT_UDF_LIST` (10 names), env-aware `UDF_LIST`, `PG_CONSTANTS`,
  new `PG_UDF`; `is_udf`/`is_pgconstant`/`is_pg_function` read them;
  `is_valid` unchanged in text (its `(` pass-through disappears through
  `is_pg_function`). New `#[cfg(test)]` cases.
- `tests/unit/test_rust_allowlists.py`: exactness, `(` quoting via
  `rs.is_valid`, `rs.is_udf('LDOPM')`, env parity subprocess.

**NOT in scope**: `quote_string`/`to_string`/`unquote_literal` and the
builder call sites (TASK-877); the Cython side (TASK-872/874).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `rust/src/validators.rs` | MODIFY | `use std::env` (`:6`), statics (`:20-35`), `is_udf`/`is_pgconstant`/`is_pg_function` (`:78-94`), tests (`:348+`) |
| `tests/unit/test_rust_allowlists.py` | CREATE | Python-facing tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.qs_parsers import _qs_parsers as rs   # verified: tests/unit/test_udf_keyword_resolution.py:15; exposes is_udf, is_pgconstant, is_pg_function, is_valid, quote_string, escape_string, to_string (rust/src/lib.rs:44-51)
from querysource.types.validators import udf_keywords, pg_udfs, pg_constants  # verified: validators.pyx:219, :227, :223
```
```rust
use once_cell::sync::Lazy;   // verified: rust/src/validators.rs:6 (once_cell = "1.20", rust/Cargo.toml:15)
use std::env;                // stdlib — NOT used anywhere in rust/src today
```

### Existing Signatures to Use
```rust
// rust/src/validators.rs
static UDF_LIST: Lazy<Vec<&str>> = Lazy::new(|| { vec!["CURRENT_YEAR", "CURRENT_MONTH", "TODAY", "YESTERDAY", "LAST_YEAR", "FDOM", "LDOM"] });  // line 21-31
static PG_CONSTANTS: Lazy<Vec<&str>> = Lazy::new(|| vec!["CURRENT_DATE", "CURRENT_TIMESTAMP"]);  // line 34-35
#[pyfunction] pub fn is_udf(value: &str) -> bool { UDF_LIST.contains(&value.to_uppercase().as_str()) }          // line 76-80
#[pyfunction] pub fn is_pgconstant(value: &str) -> bool { PG_CONSTANTS.contains(&value.to_uppercase().as_str()) } // line 83-87
#[pyfunction] pub fn is_pg_function(value: &str) -> bool { value.contains('(') || value == "now()" }           // line 90-94
#[pyfunction] pub fn is_valid(key: &str, value: &str, type_hint: Option<&str>, noquote: bool) -> String       // line 269
//   line 322-331: `if is_udf(&upper) { ... quote_string(value, true) }`; 332-334: is_pgconstant → upper;
//   line 335-337: `if is_pg_function(value) { return value.to_string(); }`  — keep; semantics change via is_pg_function
//   line 340-344: default quote_string(value, true) / noquote
#[cfg(test)] mod tests { use super::*; ... }   // line 348-349; test_is_valid_pg_function at 460 asserts is_valid("x","now()",None,false)=="now()"
// No other file in rust/src references UDF_LIST, PG_CONSTANTS, is_udf( or is_pgconstant( (verified by grep at task time)
// rust/src/lib.rs:44,45,46,51 — is_udf, is_pgconstant, is_pg_function, is_valid already exported as pyfunctions (no lib.rs change)
// rust/src/safe_dict.rs:162 — is_valid(key, value, type_hint, false) — benefits automatically

# querysource/types/validators.pyx:30-42 — the semantics to mirror:
#   _env_list(name, default, upper=True): env unset/blank → copy of default; else split(','), strip, drop blanks, upper() when upper
#   UDF_LIST upper=True; PG_CONSTANTS upper=True; PG_UDF upper=False (default ["now()"])

# tests/unit/test_udf_keyword_resolution.py:90-110 — subprocess env-override shape (`env["UDF_LIST"] = "today, fdom"`, cwd=_PROJECT_ROOT)
# tests/test_rust_parsers.py:174-183 — pins is_valid("x","CURRENT_DATE")=="CURRENT_DATE", is_valid("x","now()")=="now()", default → "'some_value'"
```

### Does NOT Exist
- ~~`PG_UDF` static in validators.rs~~ — created here.
- ~~`env_list` in Rust~~ — created here; do not reach for a crate (`std::env` suffices).
- ~~`rs.udf_keywords()` / `rs.pg_udfs()`~~ — Rust exposes no list accessors; observe the lists through `rs.is_udf`, `rs.is_pgconstant`, `rs.is_pg_function`.
- ~~runtime re-reading of env~~ — `Lazy` initialises once per process (same as Cython import-time parsing).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "rust/src/validators.rs", "action": "MODIFY"},
    {"path": "tests/unit/test_rust_allowlists.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:rust/src/validators.rs#is_pg_function",
    "sym:rust/src/validators.rs#is_udf",
    "sym:rust/src/validators.rs#is_pgconstant",
    "sym:rust/src/validators.rs#is_valid"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Rebuild and stage: `make build-rust && make stage-rust` (memory
  `rust-extension-stage-rust`: Python loads the source-tree `.so`, a stale
  one silently keeps the old behaviour). `cargo test --no-default-features`
  from `rust/` for the unit tests (memory `rust-cargo-test-no-default-features`;
  4 pre-existing failures as of 2026-10-07 are the baseline).
- `Vec<String>` statics: compare with `.iter().any(|k| k == ...)`.
- `tests/test_rust_parsers.py` must stay green (it pins `now()` raw and
  `CURRENT_DATE` upper).

### References in Codebase
- `rust/src/validators.rs:11-18` — `Lazy` static shape.

---

## Implementation Blueprint

### Steps (in order)
1. Add `use std::env;` below `use once_cell::sync::Lazy;` (line 6) — *why*: `env_list` needs it.
2. Replace lines 20-35 (the two statics and their doc comments) with the block below — *why*: G7 parity and the 10-name default (Q1).
3. Replace the three predicates (lines 75-94) with the block below — *why*: `Vec<String>` membership + exact `PG_UDF`.
4. Leave `is_valid` text unchanged; add the `#[test]`s inside `mod tests` — *why*: the `(` behaviour change is observable through `is_valid`.
5. `cargo test --no-default-features`, `make build-rust && make stage-rust`, then the Validation Commands.

### `rust/src/validators.rs` (MODIFY — statics)
```rust
// occurrences: 1 (verified: grep -c 'static UDF_LIST: Lazy<Vec<&str>> = Lazy::new(|| {' rust/src/validators.rs)
// REPLACE lines 20-35 (`/// UDF names recognized by the query system.` through the PG_CONSTANTS static) with:

/// Comma-separated env override with the Cython `_env_list` semantics
/// (validators.pyx:30): unset/blank → `default`; else split on ',', trim,
/// drop blanks, upper-case when `upper`.
fn env_list(name: &str, default: &[&str], upper: bool) -> Vec<String> {
    match env::var(name) {
        Ok(raw) if !raw.trim().is_empty() => raw
            .split(',')
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(|s| if upper { s.to_uppercase() } else { s.to_string() })
            .collect(),
        _ => default.iter().map(|s| s.to_string()).collect(),
    }
}

/// Default relative-date keywords — MUST equal the 10-name Python DEFAULT_UDF_LIST tuple owned by TASK-872.
const DEFAULT_UDF_LIST: &[&str] = &[
    "CURRENT_YEAR", "CURRENT_MONTH", "TODAY", "YESTERDAY", "LAST_YEAR",
    "FDOM", "LDOM", "FDOW", "LDOW", "LDOPM",
];

/// UDF names recognized by the query system (env `UDF_LIST`).
static UDF_LIST: Lazy<Vec<String>> = Lazy::new(|| env_list("UDF_LIST", DEFAULT_UDF_LIST, true));

/// PostgreSQL constant names (env `PG_CONSTANTS`).
static PG_CONSTANTS: Lazy<Vec<String>> =
    Lazy::new(|| env_list("PG_CONSTANTS", &["CURRENT_DATE", "CURRENT_TIMESTAMP"], true));

/// PostgreSQL function allowlist (env `PG_UDF`, case preserved) — the ONLY raw pass-through.
static PG_UDF: Lazy<Vec<String>> = Lazy::new(|| env_list("PG_UDF", &["now()"], false));
```
**Why this shape**: identical defaults and parsing to `validators.pyx:30-42`, so `tests/unit/test_udf_keyword_resolution.py` parity holds under any override.

### `rust/src/validators.rs` (MODIFY — predicates)
```rust
// occurrences: 1 (verified: grep -c 'pub fn is_pg_function(value: &str) -> bool {' rust/src/validators.rs)
// REPLACE lines 75-94 (is_udf, is_pgconstant, is_pg_function with their attributes/doc comments) with:
/// Check if a value string is a known UDF (case-insensitive).
#[pyfunction]
#[pyo3(signature = (value,))]
pub fn is_udf(value: &str) -> bool {
    let upper = value.to_uppercase();
    UDF_LIST.iter().any(|k| *k == upper)
}

/// Check if a value string is a known PG constant (case-insensitive).
#[pyfunction]
#[pyo3(signature = (value,))]
pub fn is_pgconstant(value: &str) -> bool {
    let upper = value.to_uppercase();
    PG_CONSTANTS.iter().any(|k| *k == upper)
}

/// True only for an exact member of PG_UDF (default `now()`).
/// A value that merely contains '(' is NOT a function (FEAT-167 G2).
#[pyfunction]
#[pyo3(signature = (value,))]
pub fn is_pg_function(value: &str) -> bool {
    PG_UDF.iter().any(|k| k == value)
}
```
**Why**: `is_valid` line 335 keeps `if is_pg_function(value) { return value.to_string(); }`; with the exact test, `pg_sleep(5)` falls to the default `quote_string` branch.

### `rust/src/validators.rs` (MODIFY — tests)
```rust
// occurrences: 1 (verified: grep -c 'fn test_is_valid_pg_function() {' rust/src/validators.rs)
// AFTER — insert below the closing `}` of test_is_valid_pg_function (verified: validators.rs:462)
    #[test]
    fn test_is_pg_function_exact() {
        assert!(is_pg_function("now()"));
        assert!(!is_pg_function("pg_sleep(5)"));
        assert!(!is_pg_function("(select 1)"));
        assert!(!is_pg_function("lower('x')"));
    }

    #[test]
    fn test_is_valid_parenthesis_is_quoted() {
        assert_eq!(is_valid("x", "pg_sleep(5)", None, false), "'pg_sleep(5)'");
        assert_eq!(is_valid("x", "x) OR (1=1", None, false), "'x) OR (1=1'");
        assert_eq!(is_valid("x", "pg_sleep(5)", None, true), "pg_sleep(5)");
    }

    #[test]
    fn test_default_udf_list_has_week_helpers() {
        assert!(is_udf("fdow") && is_udf("LDOW") && is_udf("Ldopm"));
        assert_eq!(UDF_LIST.len(), 10);
    }
```
**Why**: cargo-level guards for G2/Q1 that do not depend on the Python build.

### `tests/unit/test_rust_allowlists.py` (CREATE)
```python
"""FEAT-167 TASK-875 — Rust allowlists: exact PG_UDF, env parity, 10-name default (AC3, AC11)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from querysource.qs_parsers import _qs_parsers as rs

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("value", ["pg_sleep(5)", "(select 1)", "lower('x')", "x) OR (1=1"])
def test_rust_is_valid_parenthesis_quoted(value: str) -> None:
    """A '(' no longer bypasses quoting on the raw-query path (AC3)."""
    assert not rs.is_pg_function(value)
    # FILL IN: assert rs.is_valid("k", value, None, False) == "'" + value.replace("'", "''") + "'" — bounded by AC3


def test_rust_pg_udf_default() -> None:
    assert rs.is_pg_function("now()")
    assert rs.is_valid("k", "now()", None, False) == "now()"


def test_rust_default_udf_list_has_week_helpers() -> None:
    assert rs.is_udf("LDOPM") and rs.is_udf("fdow") and rs.is_udf("ldow")
    assert not rs.is_udf("midnight")


def test_rust_env_override_parity() -> None:
    """UDF_LIST / PG_UDF env overrides reach the Rust extension (AC11)."""
    env = os.environ.copy()
    env["UDF_LIST"] = "today, fdom"
    env["PG_UDF"] = "now(),my_fn()"
    code = (
        "from querysource.qs_parsers import _qs_parsers as rs; "
        "from querysource.types.validators import udf_keywords, pg_udfs; "
        "print(rs.is_udf('FDOM'), rs.is_udf('LDOM'), rs.is_pg_function('my_fn()'), "
        "rs.is_valid('x', 'my_fn()', None, False)); print(udf_keywords()); print(pg_udfs())"
    )
    out = subprocess.run([sys.executable, "-c", code], env=env, cwd=_PROJECT_ROOT,
                         capture_output=True, text=True, check=True).stdout.strip().splitlines()
    # FILL IN: assert out[-3] == "True False True my_fn()", out[-2] == "['TODAY', 'FDOM']", out[-1] == "['now()', 'my_fn()']" — bounded by AC11 (navconfig may print DEBUG lines first)
```
**Why**: same process-isolation trick as `tests/unit/test_udf_keyword_resolution.py:90`, because both engines read the env once.

### FILL IN checklist
- [ ] `test_rust_allowlists.py::test_rust_is_valid_parenthesis_quoted` — quoted-literal assertion; bounded by AC3
- [ ] `test_rust_allowlists.py::test_rust_env_override_parity` — assert the last three stdout lines; bounded by AC11

---

## Acceptance Criteria

- [ ] `rs.is_valid('k', 'pg_sleep(5)', None, False)` → `"'pg_sleep(5)'"`; `rs.is_valid('k', 'now()', None, False)` → `now()` (AC3).
- [ ] Env parity per AC11.
- [ ] `cargo test --no-default-features` (in `rust/`): new tests pass, no new failures vs the 2026-10-07 baseline (AC8).
- [ ] `tests/test_rust_parsers.py`, `tests/unit/test_udf_keyword_resolution.py` green.

---

## Validation Commands

- `pytest tests/unit/test_rust_allowlists.py -q`
- `pytest tests/test_rust_parsers.py -q`
- `pytest tests/unit/test_udf_keyword_resolution.py -q`

---

## Test Specification

See the `tests/unit/test_rust_allowlists.py` blueprint block above; Rust unit tests in `validators.rs::tests`.

---

## Agent Instructions

When you pick up this task:

1. **Work in the feature worktree** — never on `base_branch`
   (`python -m scripts.sdd.ensure_worktree --slug where-value-hardening --feature-id FEAT-167`)
2. **Read the spec** at the path listed above for full context
3. **Check dependencies** — none
4. **Verify the Codebase Contract** — before writing ANY code
5. **Update status** in `sdd/tasks/index/where-value-hardening.json` → `"in-progress"` and commit only that index file
6. **Implement** from the Implementation Blueprint; `make build-rust && make stage-rust` before pytest
7. **Verify** — run the Validation Commands and `cargo test --no-default-features`
8. **Commit the code** — stage only the files this task lists (never `target/`)
9. **Close the task** with `scripts/sdd/close_task.sh TASK-875 where-value-hardening verified`
10. **Fill in the Completion Note** below, then commit the staged SDD state

---

## Completion Note

Implemented by seat gpt-5.6-terra (codex), 1 attempt, 498s, merged.
- `rust/src/validators.rs`: env-aware `UDF_LIST`/`PG_CONSTANTS`/`PG_UDF` (same semantics as Cython `_env_list`); 10-name default; `is_pg_function` exact-match only; new Rust unit tests.
- Added `tests/unit/test_rust_allowlists.py`.
- Validation: merge tier 7 passed after building the wheel and staging `_qs_parsers` into the worktree (first run failed only on the stale staged .so).
- Note: Rust test `test_default_udf_list_has_week_helpers` asserts `UDF_LIST.len()==10`, which would fail if the `UDF_LIST` env is set in the test environment (minor).
- Review: orchestrator diff review, no fixes; coder-review recorded.
- Closed via close_task.sh (no durable review EvidenceRef for finalize_task).
