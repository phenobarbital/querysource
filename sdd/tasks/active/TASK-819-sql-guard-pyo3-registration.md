# TASK-819: Register `sql_guard` in `_qs_parsers`, re-export it, rebuild, Python tests

**Feature**: FEAT-156 — MultiQuery ExecuteSQL Destination
**Spec**: `sdd/specs/multi-executesql.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-818
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 2 and §4 (M2 tests). TASK-818 wrote `rust/src/sql_guard.rs`, but the file is
not compiled until `rust/src/lib.rs` declares it. This task registers the pyfunction in the
`_qs_parsers` PyO3 module, adds the explicit `sql_guard` re-export in both import branches of
`querysource/qs_parsers/__init__.py`, rebuilds the extension, and pins the Python-visible
behaviour with `tests/test_sql_guard.py`. TASK-820 imports `sql_guard` from `querysource.qs_parsers`.

This task is **exclusive** (`parallel: false`): it rebuilds the compiled extension, which
`maturin develop` installs into the shared project `.venv` (currently as the top-level
`_qs_parsers` package in site-packages). Any concurrently running task would see a
half-installed or different extension.

---

## Scope

- `rust/src/lib.rs`: add `mod sql_guard;` to the mod list and register `sql_guard::sql_guard` after the SafeDict block.
- `querysource/qs_parsers/__init__.py`: explicit `sql_guard` re-export in the in-wheel **and** local-dev branches.
- Rebuild: `cd rust && cargo test --no-default-features` then `.venv/bin/maturin develop --release --manifest-path rust/Cargo.toml` from the repo root.
- Write `tests/test_sql_guard.py` (spec §4 M2 tests).

**NOT in scope**:
- Changing `rust/src/sql_guard.rs` (TASK-818). If a test here exposes a guard bug, fix it in
  `sql_guard.rs` only when it is a one-line lexer fix and record it in the Completion Note; otherwise stop and report.
- `querysource/interfaces/guarded_sql.py` (TASK-820), `rust/qsurl/` (untouched).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `rust/src/lib.rs` | MODIFY | `mod sql_guard;` + `add_function(wrap_pyfunction!(sql_guard::sql_guard, m))` |
| `querysource/qs_parsers/__init__.py` | MODIFY | explicit `sql_guard` re-export (both branches) |
| `tests/test_sql_guard.py` | CREATE | Python-level guard tests (spec §4 M2) |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.qs_parsers import HAS_RUST            # verified: querysource/qs_parsers/__init__.py:16,24,26
from querysource.qs_parsers import sql_guard           # created by this task (re-export)
```
```rust
use pyo3::prelude::*;                                  // verified: rust/src/lib.rs:6
```

### Existing Signatures to Use
```text
rust/src/lib.rs:20      mod safe_dict;
rust/src/lib.rs:21      mod soql_parser;                  ← insert `mod sql_guard;` after it (alphabetical; rustfmt reorder_modules)
rust/src/lib.rs:22      mod sql_parser;
rust/src/lib.rs:32      /// - SafeDict: safe_format_map (placeholder replacement)
rust/src/lib.rs:35      fn _qs_parsers(m: &Bound<'_, PyModule>) -> PyResult<()> {
rust/src/lib.rs:55-57   // -- SafeDict -- block; :57 m.add_function(wrap_pyfunction!(safe_dict::safe_format_map_validated, m)?)?;
rust/src/sql_guard.rs   pub fn sql_guard(sql: &str) -> PyResult<Vec<String>>   (TASK-818, #[pyfunction])
querysource/qs_parsers/__init__.py:12   from ._qs_parsers import *  # noqa: F401,F403        (in-wheel branch)
querysource/qs_parsers/__init__.py:15   from ._qs_parsers import safe_format_map_validated  # noqa: F401
querysource/qs_parsers/__init__.py:20   from _qs_parsers import *  # noqa: F401,F403          (local-dev branch)
querysource/qs_parsers/__init__.py:23   from _qs_parsers import safe_format_map_validated  # noqa: F401
Makefile:17,63-64       MATURIN := .venv/bin/maturin; build-rust: $(MATURIN) develop --release --manifest-path rust/Cargo.toml
rust/pyproject.toml     module-name = "querysource.qs_parsers._qs_parsers"; maturin>=1.15,<2.0
```

### Does NOT Exist
- ~~`_qs_parsers.sql_guard`~~ in the currently installed extension — `hasattr(_qs_parsers, "sql_guard")` is `False` until the rebuild.
- ~~`uv run --with maturin`~~ — Makefile:15-16 forbids it (installs into an ephemeral env).
- ~~A `rust/` cargo workspace~~ — `rust/qsurl/` is a separate crate; `cargo test` in `rust/` does not build it.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "rust/src/lib.rs", "action": "MODIFY"},
    {"path": "querysource/qs_parsers/__init__.py", "action": "MODIFY"},
    {"path": "tests/test_sql_guard.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/qs_parsers/__init__.py#HAS_RUST"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- **Exclusive task**: the rebuild replaces the extension in the shared `.venv`. Rebuild once,
  after both edits, from the repo root with the venv active (`source .venv/bin/activate`).
- **Stale-extension hazard**: the explicit `from ._qs_parsers import sql_guard` means an
  extension built *before* this change raises `ImportError` in the first branch, falls to the
  second, and — if that also lacks `sql_guard` — sets `HAS_RUST = False` for **all** Rust parsers.
  This is the same pattern FEAT-103 used for `safe_format_map_validated` (spec-decided), so after the
  rebuild you MUST confirm `python -c "from querysource.qs_parsers import HAS_RUST, sql_guard; assert HAS_RUST"`.
- Keep the re-export lines exactly `# noqa: F401` (ruff gate).

---

## Implementation Blueprint

### Steps (in order)
1. Edit `rust/src/lib.rs` (three insertions below) — *why*: compiles and registers the TASK-818 module.
2. `cd rust && cargo test --no-default-features` — *why*: full Rust suite incl. `sql_guard::tests` with the real registration.
3. Edit `querysource/qs_parsers/__init__.py` — *why*: callers import `sql_guard` from the package, not the raw extension.
4. From the repo root: `.venv/bin/maturin develop --release --manifest-path rust/Cargo.toml`, then the `HAS_RUST` check above — *why*: pytest only sees the rebuilt `.so`.
5. Write `tests/test_sql_guard.py`, run the Validation Commands, `ruff check querysource/qs_parsers/__init__.py tests/test_sql_guard.py` — *why*: pins the Python contract FEAT-156/157 rely on.

### `rust/src/lib.rs` (MODIFY)
```rust
// occurrences: 1 (verified: grep -c '^mod soql_parser;$' rust/src/lib.rs)
// AFTER — insert below `mod soql_parser;` (verified: rust/src/lib.rs:21)
mod sql_guard;

// occurrences: 1 (verified: grep -c '/// - SafeDict: safe_format_map (placeholder replacement)' rust/src/lib.rs)
// AFTER — insert below `/// - SafeDict: safe_format_map (placeholder replacement)` (verified: rust/src/lib.rs:32)
/// - SqlGuard: sql_guard (split a script + reject destructive statements, FEAT-156)

// occurrences: 1 (verified: grep -c 'm.add_function(wrap_pyfunction!(safe_dict::safe_format_map_validated, m)?)?;' rust/src/lib.rs)
// AFTER — insert below `    m.add_function(wrap_pyfunction!(safe_dict::safe_format_map_validated, m)?)?;` (verified: rust/src/lib.rs:57)

    // -- SQL Guard (FEAT-156) --
    m.add_function(wrap_pyfunction!(sql_guard::sql_guard, m)?)?;
```
**Why**: spec Module 2 — one `mod`, one `add_function`. The spec's anchor was `mod safe_dict;`;
the mod line goes after `mod soql_parser;` instead to keep the list alphabetical (rustfmt).

### `querysource/qs_parsers/__init__.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '    from ._qs_parsers import safe_format_map_validated  # noqa: F401' querysource/qs_parsers/__init__.py)
# AFTER — insert below `    from ._qs_parsers import safe_format_map_validated  # noqa: F401` (verified: querysource/qs_parsers/__init__.py:15)
    # Explicit re-export for the ExecuteSQL statement guard (FEAT-156)
    from ._qs_parsers import sql_guard  # noqa: F401

# occurrences: 1 (verified: grep -c '        from _qs_parsers import safe_format_map_validated  # noqa: F401' querysource/qs_parsers/__init__.py)
# AFTER — insert below `        from _qs_parsers import safe_format_map_validated  # noqa: F401` (verified: querysource/qs_parsers/__init__.py:23)
        # Explicit re-export for the ExecuteSQL statement guard (FEAT-156)
        from _qs_parsers import sql_guard  # noqa: F401
```
**Why**: both import branches (in-wheel `.so` and `maturin develop` top-level package) must expose it.

### `tests/test_sql_guard.py` (CREATE)
```python
"""Python-level tests for the Rust ``sql_guard`` (FEAT-156, TASK-819)."""
import pytest

from querysource.qs_parsers import HAS_RUST

pytestmark = pytest.mark.skipif(not HAS_RUST, reason="qs_parsers Rust extension not installed")

if HAS_RUST:
    from querysource.qs_parsers import sql_guard

REFRESH_CTE = (
    "WITH params AS (SELECT (date_trunc('month', CURRENT_DATE) - INTERVAL '5 months')::date AS start_date), "
    "deleted AS (DELETE FROM wm_assembly.employee_detail_profile p USING params "
    "WHERE p.activity_date >= params.start_date AND p.activity_date < CURRENT_DATE RETURNING 1) "
    "INSERT INTO wm_assembly.employee_detail_profile (employee_id, activity_date) "
    "SELECT ad.employee_id, ad.activity_date FROM activity_days ad, params "
    "WHERE ad.activity_date >= params.start_date"
)

BLOCKED = [
    ("DROP TABLE x", "drop"),
    ("TRUNCATE wm_assembly.t", "truncate"),
    ("ALTER TABLE t DROP COLUMN c", "alter_drop"),
    ("ALTER TABLE t DROP CONSTRAINT t_pk", "alter_drop"),
    ("DO $$ BEGIN EXECUTE 'DROP TABLE x'; END $$", "do_block"),
    ("GRANT SELECT ON t TO bob", "privilege"),
    ("REVOKE SELECT ON t FROM bob", "privilege"),
    ("CREATE ROLE evil", "role"),
    ("ALTER USER bob WITH SUPERUSER", "role"),
    ("COPY t FROM PROGRAM 'curl http://x'", "copy_program"),
    ("BEGIN", "transaction_control"),
    ("COMMIT", "transaction_control"),
    ("ROLLBACK", "transaction_control"),
]


@pytest.mark.parametrize("sql,kind", BLOCKED)
def test_sql_guard_blocks_each_kind(sql: str, kind: str) -> None:
    with pytest.raises(ValueError, match=rf"statement 1: {kind} is not allowed"):
        sql_guard(sql)


def test_sql_guard_allows_dml_and_cte() -> None:
    assert sql_guard(REFRESH_CTE) == [REFRESH_CTE]


# FILL IN: test_sql_guard_ignores_keywords_in_literals, test_sql_guard_splits,
#          test_sql_guard_unterminated, test_sql_guard_reports_statement_index — bounded by spec §4 M2 rows
```
**Why this shape**: the parametrized table mirrors the spec §2 blocked table row by row
(`test_sql_guard_blocks_*`); the module skips when the extension is absent, like `tests/test_rust_parsers.py`.

### FILL IN checklist
- [ ] `test_sql_guard_ignores_keywords_in_literals` — `SELECT 'drop table x'`, `SELECT 1 -- DROP`, `SELECT /* DROP */ 1`, `SELECT $$DROP$$` each → 1 statement.
- [ ] `test_sql_guard_splits` — `DELETE FROM t WHERE a = 1; INSERT INTO t VALUES (';');` → 2 statements, the second contains `';'`.
- [ ] `test_sql_guard_unterminated` — `SELECT 'abc` → `ValueError` matching `unterminated`.
- [ ] `test_sql_guard_reports_statement_index` — `SELECT 1; DROP TABLE x` → `ValueError` matching `^statement 2: drop`.

---

## Acceptance Criteria

- [ ] `cd rust && cargo test --no-default-features` passes (includes `sql_guard::tests`).
- [ ] Extension rebuilt; `python -c "from querysource.qs_parsers import HAS_RUST, sql_guard; assert HAS_RUST"` succeeds.
- [ ] `tests/test_sql_guard.py` and the existing Rust-parser tests pass.
- [ ] `ruff check querysource/qs_parsers/__init__.py tests/test_sql_guard.py` is clean.

## Validation Commands

- `pytest tests/test_sql_guard.py -q`
- `pytest tests/test_rust_parsers.py -q`

---

## Test Specification

See the `tests/test_sql_guard.py` blueprint block above (spec §4 M2 rows:
`test_sql_guard_blocks_*`, `test_sql_guard_allows_dml_and_cte`,
`test_sql_guard_ignores_keywords_in_literals`, `test_sql_guard_splits`, `test_sql_guard_unterminated`).

---

## Agent Instructions

When you pick up this task:

1. **Work in the feature worktree** — never on `base_branch`
   (`python -m scripts.sdd.ensure_worktree --slug multi-executesql --feature-id FEAT-156`)
2. **Read the spec** at the path listed above (§3 Module 2, §4).
3. **Check dependencies** — TASK-818 must be `"done"` in `sdd/tasks/index/multi-executesql.json`.
4. **Verify the Codebase Contract** — re-run each `grep -c` anchor; all must print `1`.
5. **Update status** in `sdd/tasks/index/multi-executesql.json` → `"in-progress"` (set `started_at`) and commit only that index file.
6. **Implement** from the blueprint; complete every `# FILL IN:`. Do not run any other task while rebuilding (exclusive).
7. **Verify** — Rust tests, rebuild, `HAS_RUST` check, Validation Commands, ruff.
8. **Commit the code** — stage only the three listed files.
9. **Close the task** with `scripts/sdd/close_task.sh TASK-819 multi-executesql verified`.
10. **Fill in the Completion Note** below, then commit the staged SDD state.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
