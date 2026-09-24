# TASK-769: PostgreSQL `ILIKE` / `NOT ILIKE` dict operator (Rust + Cython builders)

**Feature**: FEAT-152 — qsurl: HTSQL-style URL query dialect (chumsky 0.13 + PyO3)
**Spec**: `sdd/specs/qsurl-parser.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 9, AC11. qsurl's text operators (`~` contains, `!~` not contains, `^=`
startswith, `$=` endswith) are case-insensitive on every engine, and PostgreSQL must push
them down. Spec-time verification showed no free key suffix exists, so the PG filter
builders gain a **dict-operator token**: `{col: {"ILIKE": pattern}}` → `col ILIKE '<pattern>'`
and `{col: {"NOT ILIKE": pattern}}`. The translator (TASK-771) builds the escaped pattern;
this task only validates the operator and quotes the literal. Both builders (Rust fast path
and Cython fallback) must render identical SQL.

**Task-time correction to spec §3 M9**: the spec names `Entity.quoteString` for the Cython
side, but `Entity.quoteString` strips a leading/trailing `'` from the value
(`querysource/types/validators.pyx:586-591`), which would corrupt a pattern such as `'abc%`.
Use the module's existing `cdef str pg_literal(str value)` (`querysource/parsers/pgsql.pyx:31`),
which is byte-compatible with the Rust `pg_literal` (`rust/src/pgsql_parser.rs:50`).

---

## Scope

- Rust: add `PG_TEXT_OPERATORS`, accept them in `pg_validate_operator`, render them in `process_dict_value` via `pg_literal` for string values only.
- Cython: accept the same tokens in the `_filter_conditions_cy` dict branch, render via `pg_literal`.
- Tests: `tests/qsurl/test_pg_ilike.py` (both paths, parametrised like `tests/test_pgsql_jsonb_filters.py`) and one Rust-extension case appended to `tests/test_rust_parsers.py`.
- Rebuild both extensions (`make build-rust`, `make build-inplace`) before running tests.

**NOT in scope**: other dialects (MySQL, MSSQL, BigQuery…); `sql.pyx` / `sql_parser.rs`; LIKE-pattern escaping (TASK-771 `like_escape`).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `rust/src/pgsql_parser.rs` | MODIFY | `PG_TEXT_OPERATORS`, validator, dict rendering |
| `querysource/parsers/pgsql.pyx` | MODIFY | `PG_TEXT_OPERATORS` + dict-branch rendering |
| `tests/qsurl/test_pg_ilike.py` | CREATE | Rust + Cython parity tests |
| `tests/test_rust_parsers.py` | MODIFY | Append one `pgsql_filter_conditions` ILIKE case |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.parsers import pgsql                           # verified: tests/test_pgsql_jsonb_filters.py:19
from querysource.parsers.pgsql import pgSQLParser               # verified: querysource/parsers/pgsql.pyx:157
from querysource.models import QueryObject                      # verified: tests/test_pgsql_jsonb_filters.py:18
from querysource import qs_parsers                              # verified: tests/test_rust_parsers.py:9
```

### Existing Signatures to Use
```rust
// rust/src/pgsql_parser.rs
fn pg_validate_operator(op: &str) -> bool {                      // line 37
    COMPARISON_TOKENS.contains(&op)
        || VALID_OPERATORS.contains(&op)
        || JSONB_OPERATORS.contains(&op)
}
fn pg_literal(value: &str) -> String                             // line 50 — '' doubling; E'..' with \\ \x7b \x7d when { } \ present
const COMPARISON_TOKENS: &[&str] = &[">=", "<=", "<>", "!=", "<", ">"];                     // line 74
const VALID_OPERATORS: &[&str] = &["<", ">", ">=", "<=", "<>", "!=", "IS NOT", "IS"];     // line 77
const JSONB_OPERATORS: &[&str] = &["@>", "<@", "->", "->>"];                                 // line 80
enum FilterValue { Str(String), Int(i64), Float(f64), Bool(bool), List(..), Dict(..), Condition(String), Null }   // line 88 (pgsql_parser.rs's own enum)
fn process_dict_value(key: &str, entries: &[(String, FilterValue)], _format: Option<&str>) -> Option<String>   // line 342
    // let (op, v) = &entries[0];  (351) ; if !pg_validate_operator(op) { return None; } (354-356)
    // if COMPARISON_TOKENS.contains(&op.as_str()) { ... return Some(format!("{} {} {}", key, op, safe_v)); } (359-363)
    // None  (365)
pub fn pgsql_filter_conditions(sql: &str, filter_dict: &Bound<'_, PyDict>, cond_definition: &Bound<'_, PyDict>) -> PyResult<String>   // line 540
```

```cython
# querysource/parsers/pgsql.pyx
HAS_RUST (module global, line 20/22) — tests toggle it with monkeypatch.setattr(pgsql, "HAS_RUST", ...)
cdef str pg_literal(str value)                         # line 31 — same output as the Rust pg_literal
COMPARISON_TOKENS = ('>=', '<=', '<>', '!=', '<', '>',)  # line 25
async def filter_conditions(self, sql)                  # line 164 — Rust fast path, falls back on exception
async def _filter_conditions_cy(self, sql)              # line 174
    # dict branch (218-231):
    #   handled, cond = jsonb_condition(key, value) ... op, v = value.popitem()   (224)
    #   if op in COMPARISON_TOKENS:                       (225)
    #       safe_v = Entity.quoteString(v) if isinstance(v, str) else str(v)
    #       where_cond.append(f"{key} {op} {safe_v}")
    #   else: continue                                    (229-231)
```

```python
# tests/test_pgsql_jsonb_filters.py:33-45 — helpers to copy
def _make_parser(query: str, filter_: dict) -> pgSQLParser:
    parser = pgSQLParser(definition=None, conditions=QueryObject(query_raw=query), query=query)
    parser.cond_definition = {}
    parser.filter = filter_
    return parser
async def _render(path: str, filter_: dict, query: str = SQL) -> str:
    if path == "rust":
        return pgsql._rs.pgsql_filter_conditions(query, filter_, {})
    return await _make_parser(query, filter_)._filter_conditions_cy(query)
```

Observed at task time (current behaviour, before this change):
```text
_rs.pgsql_filter_conditions("SELECT * FROM t {filter}", {"a": {"ILIKE": "%x"}}, {}) → "SELECT * FROM t "   (operator discarded)
```

### Does NOT Exist
- ~~`ILIKE`/`LIKE` in any allowlist~~ — added here, PostgreSQL only.
- ~~`Entity.quoteString` preserving edge quotes~~ — it strips them (`validators.pyx:586-591`); do not use it for patterns.
- ~~a new key suffix (`col#`, `col&`) for contains/endswith~~ — rejected at spec time (EVAL_FIELD / identifier-strip mismatch).
- ~~pattern escaping in the builder~~ — the builder must NOT add `%` or escape `%`/`_`; the value arrives ready.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "rust/src/pgsql_parser.rs", "action": "MODIFY"},
    {"path": "querysource/parsers/pgsql.pyx", "action": "MODIFY"},
    {"path": "tests/qsurl/test_pg_ilike.py", "action": "CREATE"},
    {"path": "tests/test_rust_parsers.py", "action": "MODIFY"}
  ],
  "contract_symbols": [
    "sym:querysource/parsers/pgsql.pyx#pgSQLParser"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- **Exclusive task**: rebuilding `_qs_parsers` (maturin into `.venv`) and the Cython extensions in place changes shared state every other test run loads.
- Non-string values with `ILIKE`/`NOT ILIKE` are rejected (Rust returns `None`, Cython `continue`) — never `str(v)`.
- Existing behaviour for every other operator stays byte-identical (`tests/test_pgsql_jsonb_filters.py`, `tests/test_rust_parsers.py`, `tests/integration/test_slug_injection.py` must stay green).

---

## Implementation Blueprint

### Steps (in order)
1. Add the Rust constant and extend the validator — *why*: without it `process_dict_value` discards the operator at line 354.
2. Add the rendering branch in `process_dict_value` after the `COMPARISON_TOKENS` branch — *why*: comparison behaviour must stay untouched.
3. Mirror both in `pgsql.pyx` — *why*: the Cython fallback runs whenever the Rust call raises or the extension is missing.
4. `make build-rust && make build-inplace` — *why*: tests import the compiled modules.
5. Write/append tests and run them plus the existing PG suites.

### `rust/src/pgsql_parser.rs` (MODIFY — constant)
```rust
// occurrences: 1 (verified: grep -cF 'const VALID_OPERATORS: &[&str] = &["<", ">", ">=", "<=", "<>", "!=", "IS NOT", "IS"];' rust/src/pgsql_parser.rs)
// AFTER — insert below `const VALID_OPERATORS: &[&str] = &["<", ">", ">=", "<=", "<>", "!=", "IS NOT", "IS"];` (verified: rust/src/pgsql_parser.rs:77)

/// Case-insensitive pattern operators accepted as the key of a dict filter value
/// (qsurl text_match pushdown, FEAT-152). The value is a ready LIKE pattern.
const PG_TEXT_OPERATORS: &[&str] = &["ILIKE", "NOT ILIKE"];
```

### `rust/src/pgsql_parser.rs` (MODIFY — validator)
```rust
// occurrences: 1 (verified: grep -cF 'fn pg_validate_operator(op: &str) -> bool {' rust/src/pgsql_parser.rs)
// REPLACE the body of `fn pg_validate_operator(op: &str) -> bool {` (verified: rust/src/pgsql_parser.rs:37-41) with:
fn pg_validate_operator(op: &str) -> bool {
    COMPARISON_TOKENS.contains(&op)
        || VALID_OPERATORS.contains(&op)
        || JSONB_OPERATORS.contains(&op)
        || PG_TEXT_OPERATORS.contains(&op)
}
```

### `rust/src/pgsql_parser.rs` (MODIFY — rendering)
```rust
// occurrences: 1 (verified: grep -cF 'fn process_dict_value(' rust/src/pgsql_parser.rs)
// INSIDE `fn process_dict_value(` (rust/src/pgsql_parser.rs:342): insert after the COMPARISON_TOKENS block
// (the `return Some(format!("{} {} {}", key, op, safe_v));` + closing `}` at 362-363), before the final `None`:

    // Case-insensitive pattern match (FEAT-152): string values only, quoted by pg_literal.
    if PG_TEXT_OPERATORS.contains(&op.as_str()) {
        return match v {
            FilterValue::Str(s) => Some(format!("{} {} {}", key, op, pg_literal(s))),
            _ => None, // non-string values are rejected, never str()-ified
        };
    }
```
**Why**: `pg_literal` doubles quotes and switches to `E'...'` when braces/backslashes appear, which keeps later `format_map` passes safe and matches the Cython helper byte for byte.

### `querysource/parsers/pgsql.pyx` (MODIFY)
```cython
# occurrences: 1 (verified: grep -cF 'COMPARISON_TOKENS = (' querysource/parsers/pgsql.pyx)
# AFTER — insert below `COMPARISON_TOKENS = ('>=', '<=', '<>', '!=', '<', '>',)` (verified: querysource/parsers/pgsql.pyx:25)
# Case-insensitive pattern operators for dict filter values (qsurl text_match, FEAT-152).
PG_TEXT_OPERATORS = ('ILIKE', 'NOT ILIKE',)

# occurrences: 1 (verified: grep -cF '                    if op in COMPARISON_TOKENS:' querysource/parsers/pgsql.pyx)
# INSIDE the dict branch at querysource/parsers/pgsql.pyx:225-231 — between the COMPARISON_TOKENS branch and `else: continue`:
                    elif op in PG_TEXT_OPERATORS and isinstance(v, str):
                        where_cond.append(f"{key} {op} {pg_literal(v)}")
```
**Why**: the existing `else: continue` keeps discarding unknown operators and non-string ILIKE values.

### `tests/qsurl/test_pg_ilike.py` (CREATE)
```python
"""PostgreSQL ILIKE / NOT ILIKE dict operator — Rust and Cython builders agree (FEAT-152)."""
from __future__ import annotations

import pytest

from querysource.models import QueryObject
from querysource.parsers import pgsql
from querysource.parsers.pgsql import pgSQLParser

SQL = "SELECT * FROM t {where_cond}"
PATHS = [
    pytest.param("rust", marks=pytest.mark.skipif(not pgsql.HAS_RUST, reason="qs_parsers not built")),
    "cython",
]


def _make_parser(query: str, filter_: dict) -> pgSQLParser:
    parser = pgSQLParser(definition=None, conditions=QueryObject(query_raw=query), query=query)
    parser.cond_definition = {}
    parser.filter = filter_
    return parser


async def _render(path: str, filter_: dict) -> str:
    if path == "rust":
        return pgsql._rs.pgsql_filter_conditions(SQL, filter_, {})
    return await _make_parser(SQL, filter_)._filter_conditions_cy(SQL)


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("filter_,expected", [
    ({"city": {"ILIKE": "%san%"}}, "city ILIKE '%san%'"),
    ({"city": {"NOT ILIKE": "%san%"}}, "city NOT ILIKE '%san%'"),
    ({"name": {"ILIKE": "o''brien%"}}, None),   # FILL IN: expected for a value containing a single quote
    ({"name": {"ILIKE": "'abc%"}}, None),       # FILL IN: leading quote preserved (doubled), not stripped
    ({"code": {"ILIKE": "a\\%b%"}}, None),      # FILL IN: backslash → E'...' literal (pg_literal)
    ({"n": {"ILIKE": 5}}, None),                # FILL IN: non-string → no WHERE clause at all
])
async def test_ilike_rendering(path: str, filter_: dict, expected) -> None:
    sql = await _render(path, filter_)
    # FILL IN: assert the WHERE body equals expected (None → no " WHERE ")

async def test_builders_agree_on_every_case() -> None: ...   # FILL IN: skip without Rust; compare both paths per case
```

### `tests/test_rust_parsers.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -cF 'pytestmark = pytest.mark.skipif(not HAS_RUST, reason="qs_parsers not installed")' tests/test_rust_parsers.py)
# APPEND at the end of the file (module-level pytestmark at line 14 already skips without the extension):


class TestPgsqlIlikeOperator:
    """FEAT-152: ILIKE / NOT ILIKE accepted as dict-filter operators."""

    def test_ilike_dict_operator(self):
        sql = qs_parsers.pgsql_filter_conditions("SELECT * FROM t {filter}", {"city": {"ILIKE": "%san%"}}, {})
        assert "city ILIKE '%san%'" in sql
```

### FILL IN checklist
- [ ] `test_pg_ilike.py` — the four `None` expectations and the cross-builder test.

---

## Acceptance Criteria

- [ ] Both builders render `city ILIKE '%san%'` and `city NOT ILIKE '%san%'`; non-string values render nothing.
- [ ] Rust and Cython outputs are identical for every case in `test_pg_ilike.py` (spec AC11).
- [ ] `tests/test_pgsql_jsonb_filters.py`, `tests/test_rust_parsers.py`, `tests/integration/test_slug_injection.py` stay green.
- [ ] `cargo test --manifest-path rust/Cargo.toml --no-default-features` passes.

---

## Validation Commands

- `pytest tests/qsurl/test_pg_ilike.py -q`
- `pytest tests/test_rust_parsers.py -q`
- `pytest tests/test_pgsql_jsonb_filters.py -q`
- `pytest tests/integration/test_slug_injection.py -q`

---

## Test Specification

See the `tests/qsurl/test_pg_ilike.py` and `tests/test_rust_parsers.py` blocks above.

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at the path listed above for full context.
2. **Check dependencies** — verify every `Depends-on` task is `done` in `sdd/tasks/index/qsurl-parser.json`.
3. **Verify the Codebase Contract** before writing any code: re-run the `grep -c` for every MODIFY anchor; if a count changed, re-locate the anchor; if it is `0`, stop and report the drift.
4. **Update status** in `sdd/tasks/index/qsurl-parser.json` → `"in-progress"`.
5. **Implement** from the Implementation Blueprint; complete every `# FILL IN:`; never change a signature or path the blueprint fixes.
6. **Verify** every Acceptance Criterion and run the Validation Commands (`source .venv/bin/activate` first).
7. **Move this file** to `sdd/tasks/completed/TASK-769-pg-ilike-dict-operator.md` and set the index entry to `"done"`.
8. **Fill in the Completion Note** below.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
