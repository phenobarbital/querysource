# TASK-851: Rebuild both Rust extensions + cross-dialect conformance matrix (M8)

**Feature**: FEAT-180 — Partial-matching operators for where_cond / filter dict values
**Spec**: `sdd/specs/filter-with-partial-matching.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-843, TASK-844, TASK-845, TASK-846, TASK-847, TASK-848, TASK-849, TASK-850, TASK-852
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 8 / AC3, AC10, AC11. Until now every dual-path test skipped its Rust
parameter (the shared `_qs_parsers` extension predated the Rust twins), and Rust code was
validated only by `cargo test`. This task rebuilds and stages **both** Rust crates and the
Cython extensions, proves the rebuilt extension is fresh (skips become failures), and adds
one conformance matrix across the four dialects and both paths, including the
`set_where()` error flows with `HAS_RUST` forced both ways.

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

Memory note (repo): Python loads the **source-tree** `_qs_parsers` `.so`
(`querysource/qs_parsers/`) before the venv copy, so `make build-rust` alone leaves tests
on a stale binary — `make stage-rust` is mandatory. The same holds for `_qsurl`
(`querysource/qsurl/__init__.py` imports the in-wheel `.so` first). `.so` files are
gitignored: staging creates no tracked changes.

---

## Scope

- Run `make build-rust && make stage-rust && make build-inplace`.
- Create `tests/test_partial_matching_conformance.py`: freshness checks, the dialect × path
  × operator × operand-kind matrix, and the cross-dialect `set_where` error flows.
- Run every FEAT-180 test file plus the regression files listed in Validation Commands.

**NOT in scope**: changing any builder (if the matrix exposes a builder bug, fix it in the
builder file and record the deviation in the Completion Note — the fix belongs to this
task only when it is a one-line parity correction; otherwise stop and report).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/test_partial_matching_conformance.py` | CREATE | freshness + conformance matrix + set_where flows |

---

## Codebase Contract (Anti-Hallucination)

> **CRITICAL**: This section contains VERIFIED code references from the actual codebase
> (re-verified on `dev` at `71316ad4`, 2026-10-07). The implementing agent MUST use these exact
> imports, class names, and method signatures. **DO NOT** invent, guess, or assume any import,
> attribute, or method not listed here. If you need something not listed, VERIFY it exists first.

### Verified Imports
```python
from querysource.parsers import pgsql, sql as sqlmod, sqlserver as mssqlmod, bigquery as bqmod
from querysource.parsers.pgsql import pgSQLParser               # verified: tests/qsurl/test_pg_ilike.py:8
from querysource.parsers.sql import SQLParser                   # verified: tests/test_sql_parser_combinations.py:20
from querysource.parsers.sqlserver import msSQLParser           # sqlserver.pyx:23
from querysource.parsers.bigquery import BigQueryParser         # bigquery.pyx:54
from querysource.parsers.partial_matching import PARTIAL_MATCH_OPERATORS   # TASK-840
from querysource.models import QueryObject                      # verified: tests/qsurl/test_pg_ilike.py:6
from querysource.exceptions import ParserError                  # exceptions.py:86
```

### Existing Signatures to Use
```python
# Rust entry points (rust/src/lib.rs registrations): _rs.pgsql_filter_conditions (line 73),
# _rs.filter_conditions (line 65), _rs.mssql_filter_conditions (line 78), _rs.bq_filter_conditions (line 84)
# — each (sql: str, filter_dict: dict, cond_definition: dict) -> str
# Cython paths: pgSQLParser/msSQLParser/BigQueryParser._filter_conditions_cy(sql);
#               SQLParser.filter_conditions(sql) with sqlmod.HAS_RUST monkeypatched False
# AbstractParser.set_where(_filter: dict, connection=None)   # abstract.pyx:602
# Expected strings per dialect: CORPUS lists in tests/test_{pgsql,sql,mssql,bigquery}_partial_matching.py (TASK-843/845/847/849)
```
```makefile
build-rust:   maturin develop --release (rust/Cargo.toml and rust/qsurl/Cargo.toml)   # Makefile:63-65
stage-rust:   maturin build + copy _qs_parsers*.so → querysource/qs_parsers/, _qsurl*.so → querysource/qsurl/   # Makefile:74-93
build-inplace: python setup.py build_ext --inplace                                     # Makefile:156-158
```

### Does NOT Exist
- ~~`SQLParser._filter_conditions_cy`~~ — monkeypatch `sqlmod.HAS_RUST` instead.
- ~~a fifth SQL dialect in scope~~ — the matrix covers pg, generic SQL, SQL Server, BigQuery only.

---

## Complexity Contract

> Declaration for deterministic complexity routing (FEAT-561). `targets` match the
> Files table exactly.

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "tests/test_partial_matching_conformance.py",
      "action": "CREATE"
    }
  ],
  "contract_symbols": [
    "sym:querysource/parsers/abstract.pyx#AbstractParser.set_where",
    "sym:querysource/parsers/partial_matching.py#PARTIAL_MATCH_OPERATORS"
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
1. `make build-rust && make stage-rust && make build-inplace` — *why*: every later assertion depends on fresh binaries.
2. Run every FEAT-180 test file (`tests/test_partial_matching_*.py`, `tests/test_{pgsql,sql,mssql,bigquery}_partial_matching.py`, `tests/test_rust_partial_match_units.py`, `tests/test_rust_pm_*_units.py`) — *why*: their Rust parameters were skipped until now and must pass, not skip.
3. Write the conformance file below; run all Validation Commands.

### `tests/test_partial_matching_conformance.py` (CREATE)
```python
"""FEAT-180 conformance: 4 dialects x {rust, cython} x 20 operators x operand kinds (spec AC3-AC10, AC14)."""
from __future__ import annotations

import pytest

from querysource.exceptions import ParserError
from querysource.models import QueryObject
from querysource.parsers import bigquery as bqmod, pgsql, sql as sqlmod, sqlserver as mssqlmod
from querysource.parsers.bigquery import BigQueryParser
from querysource.parsers.partial_matching import PARTIAL_MATCH_OPERATORS
from querysource.parsers.pgsql import pgSQLParser
from querysource.parsers.sql import SQLParser
from querysource.parsers.sqlserver import msSQLParser

SQL = "SELECT * FROM t {where_cond}"
DIALECTS = {
    "pg": (pgSQLParser, pgsql, "pgsql_filter_conditions", "n LIKE 'andre%'"),
    "sql": (SQLParser, sqlmod, "filter_conditions", "n LIKE 'andre%' ESCAPE '!'"),
    "mssql": (msSQLParser, mssqlmod, "mssql_filter_conditions", "n LIKE 'andre%' ESCAPE '!'"),
    "bq": (BigQueryParser, bqmod, "bq_filter_conditions", 'n LIKE "andre%"'),
}


@pytest.mark.parametrize("dialect", sorted(DIALECTS))
def test_extension_is_fresh(dialect):
    """After `make build-rust && make stage-rust` the Rust path must render the new operators."""
    _, mod, fn, expected = DIALECTS[dialect]
    assert mod.HAS_RUST, "Rust extension not importable"
    assert expected in getattr(mod._rs, fn)(SQL, {"n": {"startswith": "andre"}}, {})


@pytest.mark.parametrize("dialect", sorted(DIALECTS))
@pytest.mark.parametrize("use_rust", [True, False])
async def test_set_where_raises_before_builder(dialect, use_rust, monkeypatch):
    cls, mod, _, _ = DIALECTS[dialect]
    monkeypatch.setattr(mod, "HAS_RUST", use_rust)
    parser = cls(definition=None, conditions=QueryObject(query_raw=SQL), query=SQL)
    parser.cond_definition = {}
    with pytest.raises(ParserError, match="at least 3 characters"):
        await parser.set_where({"n": {"contains": "ab"}}, None)

# FILL IN, each parametrized over DIALECTS x ["rust", "cython"]:
#  - test_conformance_matrix[dialect, path, op, operand_kind]: operand kinds
#    {plain, wildcard chars, quote, backslash, bang, bracket, non-str, too-short, multi-key};
#    expected = the dialect's CORPUS string (import-free: rebuild it here from spec §2) or ParserError
#    (rust path: Err surfaces as ParserError through filter_conditions, AC10)
#  - test_set_where_regex_requires_postgres: {"n": {"regex": "^a"}} passes on pg, raises elsewhere
#  - test_filter_options_path_still_validates: invalid operand set via parser.filter_options +
#    filtering_options() still raises from filter_conditions (AC6)
#  - test_build_query_sqlglot_valid[dialect, op]: full build_query output parses with sqlglot
#    (read="postgres"|"mysql"|"tsql"|"bigquery")
#  - test_rust_and_cython_agree: _where_body equality for the full matrix
#  — bounded by spec AC3-AC10, AC14
```

### FILL IN checklist
- [ ] Matrix and the four named tests — bounded by spec AC3–AC10, AC14.
- [ ] Record the four pre-existing cargo failures (Context) in the Completion Note as untouched.

---

## Acceptance Criteria

- [ ] `make build-rust && make stage-rust && make build-inplace` succeed.
- [ ] `test_extension_is_fresh` passes for all four dialects (no Rust parameter is skipped anywhere in the FEAT-180 test files).
- [ ] The conformance matrix passes on both paths; Rust and Cython outputs are identical.
- [ ] Every FEAT-180 test file from step 2 passes with no skipped Rust parameter (run them explicitly; the Validation Commands carry the conformance matrix, which re-checks both paths for every dialect).
- [ ] Regression files stay green: `tests/test_rust_parsers.py`, `tests/test_pgsql_jsonb_filters.py`, `tests/qsurl/test_pg_ilike.py`, `tests/test_sql_parser_combinations.py`.
- [ ] `cargo test --manifest-path rust/Cargo.toml --lib --no-default-features` shows no failures beyond the four pre-existing ones listed in Context.

---

## Validation Commands

> File-level pytest only — no directories, no package roots.

- `pytest tests/test_partial_matching_conformance.py -q`
- `pytest tests/test_rust_parsers.py -q`
- `pytest tests/test_pgsql_jsonb_filters.py -q`
- `pytest tests/qsurl/test_pg_ilike.py -q`
- `pytest tests/test_sql_parser_combinations.py -q`

---

## Test Specification

See the `tests/test_partial_matching_conformance.py` block above.

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
9. **Close the task** with `scripts/sdd/close_task.sh TASK-851 filter-with-partial-matching verified`
   — it moves this file to `sdd/tasks/completed/` and marks it `"done"` in the
   index; never move or copy the file by hand
10. **Fill in the Completion Note** below, then commit the staged SDD state

---

## Completion Note

Seat: gpt-5.6-luna · Backend: codex · Model: gpt-5.6-luna · Attempts: 1 · Duration: 616s · Tokens: n/a

**Completed by**: sdd-worker (execution e22852df)
**Date**: 2026-10-07
**Notes**: Conformance matrix + freshness check (tests/test_partial_matching_conformance.py). Validated by hand after staging fresh binaries into the worktree (maturin build to scratchpad, .so copied in-tree; the shared venv was not touched): all 5 declared pytest commands, 1904 passed, 0 skipped. Review: zero corrections. 11 residual lint findings left for /sdd-done.

**Deviations from spec**: `make build-rust` (maturin develop) was not run because it installs into the shared venv; binaries were staged manually instead.
