# TASK-841: Rust twin of the operator table + cargo/parity pytest wrapper (M2)

**Feature**: FEAT-180 — Partial-matching operators for where_cond / filter dict values
**Spec**: `sdd/specs/filter-with-partial-matching.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-840
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 2: the four Rust builders (TASK-844/846/848/850) need the same table,
escaping, literal helpers and validation as the Python module from TASK-840. This task
creates `rust/src/partial_match.rs` (internal module, no `#[pyfunction]`), registers it
in `lib.rs`, and creates the pytest wrapper that every Rust task uses as its Validation
Command (the validation contract requires pytest commands).

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

- Create `rust/src/partial_match.rs` with the table, `lookup`, `like_escape`,
  `like_escape_bang`, `build_like_pattern`, `sql_like_literal`, `mssql_like_literal`,
  `bq_like_literal`, `validate`, `check_entries` and `#[cfg(test)] mod tests`.
- Add `mod partial_match;` to `rust/src/lib.rs`.
- Create `tests/rust_cargo_runner.py` (cargo runner helper) and
  `tests/test_rust_partial_match_units.py` (source-level parity test + this module's cargo tests).

**NOT in scope**: using the module from any builder (TASK-844/846/848/850); rebuilding
the shared extension (TASK-851).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `rust/src/partial_match.rs` | CREATE | Rust twin of the operator table |
| `rust/src/lib.rs` | MODIFY | register `mod partial_match;` |
| `tests/rust_cargo_runner.py` | CREATE | cargo test runner helper for pytest |
| `tests/test_rust_partial_match_units.py` | CREATE | table parity + partial_match cargo tests |

---

## Codebase Contract (Anti-Hallucination)

> **CRITICAL**: This section contains VERIFIED code references from the actual codebase
> (re-verified on `dev` at `71316ad4`, 2026-10-07). The implementing agent MUST use these exact
> imports, class names, and method signatures. **DO NOT** invent, guess, or assume any import,
> attribute, or method not listed here. If you need something not listed, VERIFY it exists first.

### Verified Imports
```rust
use pyo3::exceptions::PyValueError;   // verified: rust/src/pgsql_parser.rs:7
use pyo3::prelude::*;                 // verified: rust/src/pgsql_parser.rs:8
use regex::Regex;                     // verified: rust/src/bigquery_parser.rs:11 (crate dependency present)
use std::sync::LazyLock;              // verified: rust/src/bigquery_parser.rs:13
```
```python
from querysource.parsers.partial_matching import PARTIAL_MATCH_OPERATORS   # created by TASK-840
```

### Existing Signatures to Use
```rust
// rust/src/lib.rs
mod filter_common;   // line 12
mod parseqs;         // line 16  ← insert `mod partial_match;` after it (alphabetical)
mod pgsql_parser;    // line 17
#[pymodule] fn _qs_parsers(m: &Bound<'_, PyModule>) -> PyResult<()>   // line 36-37 — do NOT register anything here
```
```toml
# rust/Cargo.toml
[features] default = ["extension-module"]   # line 27-29 — cargo test needs --no-default-features
```

### Does NOT Exist
- ~~`crate::partial_match`~~ — created by THIS task.
- ~~a `#[pyfunction]` for partial matching~~ — the module is internal; Python never calls it directly.
- ~~`cargo test` with default features~~ — fails to link (see Context).

---

## Complexity Contract

> Declaration for deterministic complexity routing (FEAT-561). `targets` match the
> Files table exactly.

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "rust/src/partial_match.rs",
      "action": "CREATE"
    },
    {
      "path": "rust/src/lib.rs",
      "action": "MODIFY"
    },
    {
      "path": "tests/rust_cargo_runner.py",
      "action": "CREATE"
    },
    {
      "path": "tests/test_rust_partial_match_units.py",
      "action": "CREATE"
    }
  ],
  "contract_symbols": [
    "sym:querysource/parsers/partial_matching.py#PARTIAL_MATCH_OPERATORS"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Table rows MUST use the one-line `op(...)` constructor form shown — the parity test parses it with a regex.
- Error messages are byte-identical to TASK-840's docstring list (`PyValueError::new_err(msg)`).
- Character counts use `value.chars().count()` (Python `len` counts code points).
- `check_entries` exists because callers hold different shapes (`&Bound<PyDict>`, `Vec<(String, FilterValue)>`); they convert to `&[(&str, Option<&str>)]` (`None` = non-string operand).

---

## Implementation Blueprint

> **CRITICAL — Executor-ready starting point.** Write each block below to its declared
> path nearly verbatim, then complete every `# FILL IN:` marker. Blocks were derived
> from the spec's Interface Skeletons and re-verified against the Codebase Contract
> above when this task was written. Never change a signature, class name, or file path
> the blueprint fixes.

### Steps (in order)
1. Create `partial_match.rs` from the block — *why*: names are fixed by spec §3 M2 and used by four tasks.
2. Register the module in `lib.rs` — *why*: otherwise the file is not compiled.
3. Create the runner and the test file — *why*: Rust tasks need a pytest Validation Command (FEAT-563).
4. Run the two Validation Commands.

### `rust/src/partial_match.rs` (CREATE)
```rust
// partial_match.rs — FEAT-180 operator table shared by the Rust WHERE builders.
// Twin of querysource/parsers/partial_matching.py: same 20 rows, same order, same messages.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use regex::Regex;
use std::sync::LazyLock;

pub const CONTAINS_MIN_LENGTH: usize = 3;
pub const MAX_REGEX_PATTERN_LENGTH: usize = 200;

static NESTED_QUANTIFIER_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\([^()]*[+*][^()]*\)[+*]").unwrap());

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MatchKind { Like, Regex }

#[derive(Debug, Clone, Copy)]
pub struct PartialMatchOp {
    pub name: &'static str,
    pub kind: MatchKind,
    pub negated: bool,
    pub insensitive: bool,
    pub prefix: &'static str,
    pub suffix: &'static str,
    pub escape: bool,
    pub min_length: usize,
}

const fn op(name: &'static str, kind: MatchKind, negated: bool, insensitive: bool,
            prefix: &'static str, suffix: &'static str, escape: bool, min_length: usize) -> PartialMatchOp {
    PartialMatchOp { name, kind, negated, insensitive, prefix, suffix, escape, min_length }
}

pub const PARTIAL_MATCH_OPERATORS: &[PartialMatchOp] = &[
    op("like", MatchKind::Like, false, false, "", "", false, 0),
    op("not_like", MatchKind::Like, true, false, "", "", false, 0),
    // FILL IN: the remaining 18 rows, one per line, same order and values as
    // querysource/parsers/partial_matching.py — bounded by AC1 (parity test parses this form)
];

pub fn lookup(op: &str) -> Option<&'static PartialMatchOp> {
    PARTIAL_MATCH_OPERATORS.iter().find(|o| o.name == op)
}

pub fn like_escape(value: &str) -> String {
    value.replace('\\', "\\\\").replace('%', "\\%").replace('_', "\\_")
}

pub fn like_escape_bang(value: &str) -> String {
    value.replace('!', "!!").replace('%', "!%").replace('_', "!_").replace('[', "![")
}

pub fn build_like_pattern(op: &PartialMatchOp, value: &str, escaper: fn(&str) -> String) -> String {
    let body = if op.escape { escaper(value) } else { value.to_string() };
    format!("{}{}{}", op.prefix, body, op.suffix)
}

pub fn sql_like_literal(pattern: &str) -> String {
    format!("'{}'", pattern.replace('\\', "\\\\").replace('\'', "''"))
}

pub fn mssql_like_literal(pattern: &str) -> String {
    format!("'{}'", pattern.replace('\'', "''"))
}

pub fn bq_like_literal(pattern: &str) -> String {
    format!("\"{}\"", pattern.replace('\\', "\\\\").replace('"', "\\\""))
}

/// Twin of `validate_partial_match` for a string operand (non-string handled by `check_entries`).
pub fn validate(key: &str, op: &PartialMatchOp, value: &str, supports_regex: bool) -> PyResult<()> {
    // FILL IN: min-length, regex support, empty, too-long, nested-quantifier checks in the
    // Python order and with the Python messages — bounded by AC2
    Ok(())
}

/// Twin of `validate_partial_match_dict`. `entries` = (key, Some(str) | None for non-string).
/// Ok(None): no table operator present. Ok(Some((op, operand))): single valid pair.
pub fn check_entries<'a>(
    key: &str,
    entries: &[(&'a str, Option<&'a str>)],
    supports_regex: bool,
) -> PyResult<Option<(&'static PartialMatchOp, &'a str)>> {
    // FILL IN: no table key → Ok(None); >1 entries with a table key → multi-key error;
    // None operand → "requires a string operand"; else validate() — bounded by AC2
    Ok(None)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn table_has_twenty_rows() {
        assert_eq!(PARTIAL_MATCH_OPERATORS.len(), 20);
    }
    // FILL IN: escape helpers, literals, build_like_pattern, every validate/check_entries error — bounded by AC2
}
```
**Why this shape**: mirrors TASK-840 one-to-one so a single parity test proves the
contract; `check_entries` lets each builder validate in its serial (GIL) extraction phase
where `?` can return a `PyErr` (rayon phases cannot).

### `rust/src/lib.rs` (MODIFY)
```rust
// occurrences: 1 (verified: grep -c 'mod parseqs;' rust/src/lib.rs)
// AFTER — insert below `mod parseqs;` (verified: rust/src/lib.rs:16)
mod partial_match;
```
**Why**: compiles the module; nothing is exported to Python.

### `tests/rust_cargo_runner.py` (CREATE)
```python
"""Run the qs_parsers crate's Rust unit tests from pytest (FEAT-180 validation contract).

Not a test module: imported by tests/test_rust_partial_match_units.py and the
tests/test_rust_pm_<dialect>_units.py wrappers (pytest puts tests/ on sys.path,
there is no tests/__init__.py).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_RESULT_RE = re.compile(r"test result: ok\. (\d+) passed")


def run_cargo_lib_tests(cargo_filter: str) -> int:
    """Run `cargo test --lib --no-default-features -- <cargo_filter>`; return tests passed.

    Skips when cargo is not installed. Fails (pytest.fail) on a non-zero exit code or
    when the filter matched zero tests — a filter that selects nothing proves nothing.
    """
    if shutil.which("cargo") is None:
        pytest.skip("cargo not installed")
    env = dict(os.environ, PYO3_PYTHON=sys.executable)
    libdir = sysconfig.get_config_var("LIBDIR") or ""
    env["LD_LIBRARY_PATH"] = os.pathsep.join(p for p in (libdir, env.get("LD_LIBRARY_PATH", "")) if p)
    proc = subprocess.run(
        ["cargo", "test", "--manifest-path", str(ROOT / "rust" / "Cargo.toml"), "--lib",
         "--no-default-features", "--", cargo_filter],
        capture_output=True, text=True, env=env, timeout=1800, check=False,
    )
    # FILL IN: pytest.fail with the stdout/stderr tail when returncode != 0; sum _RESULT_RE
    # matches; pytest.fail when the sum is 0; return the sum — bounded by AC3
    return 0
```

### `tests/test_rust_partial_match_units.py` (CREATE)
```python
"""FEAT-180: Rust operator-table parity with the Python table, and its cargo unit tests."""
from __future__ import annotations

import re

from querysource.parsers.partial_matching import PARTIAL_MATCH_OPERATORS
from rust_cargo_runner import ROOT, run_cargo_lib_tests

RS = ROOT / "rust" / "src" / "partial_match.rs"
ROW_RE = re.compile(
    r'op\("([a-z_]+)", MatchKind::(Like|Regex), (true|false), (true|false), "(%?)", "(%?)", (true|false), (\d+)\)'
)


def test_rust_table_matches_python():
    rows = ROW_RE.findall(RS.read_text(encoding="utf-8"))
    # FILL IN: compare each parsed row (name, kind lowercased, bools, prefix, suffix, escape,
    # min_length) to PARTIAL_MATCH_OPERATORS in order — bounded by AC1
    assert len(rows) == len(PARTIAL_MATCH_OPERATORS)


def test_partial_match_cargo_units():
    assert run_cargo_lib_tests("partial_match::tests::") >= 1
```
**Why**: the parity test proves AC1 without the shared rebuild; the runner turns
`cargo test` into pytest results for the validation contract. Each Rust dialect task
(TASK-844/846/848/850) creates its own `tests/test_rust_pm_<dialect>_units.py` calling
`run_cargo_lib_tests("tests::test_pm_<dialect>_")`.

### FILL IN checklist
- [ ] `PARTIAL_MATCH_OPERATORS` rows 3–20 — bounded by AC1.
- [ ] `validate` / `check_entries` bodies and messages — bounded by AC2.
- [ ] `mod tests` cases — bounded by AC2.
- [ ] runner result handling and parity comparison — bounded by AC1, AC3.

---

## Acceptance Criteria

- [ ] AC1: `test_rust_table_matches_python` passes (20 rows, same order and fields as TASK-840).
- [ ] AC2: `partial_match::tests` covers every helper and every error message; messages equal the Python ones.
- [ ] AC3: `test_partial_match_cargo_units` passes; `run_cargo_lib_tests` fails when a filter matches zero tests.
- [ ] `cargo check --manifest-path rust/Cargo.toml` passes with no new warnings in `partial_match.rs` (dead-code warnings for helpers used only by later tasks may be silenced with `#[allow(dead_code)]` on the module items — remove nothing).

---

## Validation Commands

> File-level pytest only — no directories, no package roots.

- `pytest tests/test_rust_partial_match_units.py::test_rust_table_matches_python -q`
- `pytest tests/test_rust_partial_match_units.py::test_partial_match_cargo_units -q`

---

## Test Specification

See the `tests/test_rust_partial_match_units.py` block and the Rust `mod tests` block above.

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
9. **Close the task** with `scripts/sdd/close_task.sh TASK-841 filter-with-partial-matching verified`
   — it moves this file to `sdd/tasks/completed/` and marks it `"done"` in the
   index; never move or copy the file by hand
10. **Fill in the Completion Note** below, then commit the staged SDD state

---

## Completion Note

Seat: sonnet · Backend: native · Model: sonnet · Attempts: 1 · Duration: 90s · Tokens: n/a

**Completed by**: sdd-worker (execution e22852df)
**Date**: 2026-10-07
**Notes**: Rust partial_match.rs (twin of the Python operator table), lib.rs wiring, cargo runner helper and parity/cargo tests. pytest 2 passed. Review: zero corrections. Residual lint B905 (zip strict) left for /sdd-done.

**Deviations from spec**: none
