---
# SDD flow type and base branch (FEAT-145).
type: feature
base_branch: dev
projects: [parsers, rust-parsers]
tags: [parsers, partial-matching, format-map, braces, rust, tech-debt, sdd-fix]
---

# Feature Specification: Protect braces in partial-match LIKE literals from later format_map passes

**Feature ID**: FEAT-172
**Date**: 2026-10-13
**Author**: Jesus Lara (spec drafted with Claude Code via /sdd-fix)
**Status**: approved
**Target version**: next patch
**Ledger issues**: `issue:64c82d35d831` (tech_debt, minor, discovered from spec:FEAT-180; supersedes `issue:3279a499fd6a`)

---

## 1. Motivation & Business Requirements

### Problem Statement

FEAT-180's partial-matching operators render LIKE literals with
`sql_like_literal`, `mssql_like_literal` and `bq_like_literal`
(`querysource/parsers/partial_matching.py:87-99`, Rust twins
`rust/src/partial_match.rs:76-86`). Those helpers leave `{` and `}` raw. The
rendered filter is inserted into the template, and then `SQLParser.build_query`
(`sql.pyx:433`) runs more `format_map` passes over the whole SQL: `limiting`,
`SafeDict(**self._conditions)` and finally `NullDefault()`. The last pass
replaces every unknown `{name}` with an empty string.

Reproduced on 2026-10-13, on both the Rust and Cython paths, for generic SQL,
SQL Server and BigQuery:

| operand (`contains`) | rendered |
|---|---|
| `a{b}c` | `n LIKE '%ac%' ESCAPE '!'` |
| `{limit}` | `n LIKE '%%' ESCAPE '!'`, which matches every row |
| `x{{y}}z` | `n LIKE '%xz%' ESCAPE '!'` |

PostgreSQL is not affected: `pg_literal` writes braces as `\x7b`/`\x7d` inside
`E''` (`pgsql.pyx:62`). Generic SQL (MySQL, the default/sql providers,
DeltaTable, Iceberg) and T-SQL have no portable escape for a brace inside a
string literal.

### Goals

- G1: a partial-match operand containing `{` or `}` reaches the final SQL with
  its braces intact, for the generic SQL, SQL Server and BigQuery parsers, on
  both the Rust and Cython paths.
- G2: one mechanism shared by every dialect that uses these helpers. The
  literal helpers replace `{`/`}` with the private-use sentinels `U+E000`/`U+E001`,
  which `format_map` ignores. `build_query` restores them after its last
  `format_map` pass.

### Non-Goals

- PostgreSQL / jsonb_unnest (already protected).
- Plain (non partial-match) string filter values; tracked separately if needed.
- Raw-query substitution in `providers/sql.py` (`raw_query`/`get_raw_query`),
  which never receives parser-built filters.

## 2. Architectural Design

```
operand -> build_like_pattern -> *_like_literal -> protect_braces ({->U+E000, }->U+E001)
        -> filter text inserted into template -> group/order/limit/conditions/NullDefault format_map passes
        -> restore_braces (U+E000->{, U+E001->}) -> query_parsed
```

Restore points (every `build_query` that can carry SQLParser filter output):
`SQLParser.build_query` (`sql.pyx`; inherited by `msSQLParser` and
`BigQueryParser`), `DeltaTableParser.build_query` (`deltatbl.pyx`),
`IcebergParser.build_query` (`iceberg.pyx`).

A caller-supplied `U+E000`/`U+E001` in an operand is restored to a brace inside
the same quoted literal. That is harmless, because no `format_map` pass runs
after the restore.

### New Public Interfaces

```python
# querysource/parsers/partial_matching.py
BRACE_OPEN_SENTINEL: str = ""
BRACE_CLOSE_SENTINEL: str = ""
def protect_braces(text: str) -> str: ...
def restore_braces(sql: str) -> str: ...
```
```rust
// rust/src/partial_match.rs
pub fn protect_braces(text: &str) -> String;  // same sentinels
```

## 3. Module Breakdown

### Module 1: Cython/Python protection and restore (TASK A)
- `partial_matching.py`: sentinels, `protect_braces`, `restore_braces`, and the
  three literal helpers apply `protect_braces`.
- `sql.pyx`, `deltatbl.pyx`, `iceberg.pyx`: `sql = restore_braces(sql)` right
  before `self.query_parsed = sql`.
- Tests: `tests/test_partial_matching_operators.py`, plus an end-to-end
  `build_query` test per dialect.

### Module 2: Rust twins (TASK B)
- `rust/src/partial_match.rs`: `protect_braces`; the three `*_like_literal`
  functions apply it; cargo unit tests.
- Restore happens in Python (`build_query`), so the Rust output only has to
  emit sentinels.

## 4. Test Specification

- `sql_like_literal("a{b}")` returns `"'ab'"`, and likewise for mssql/bq;
  `restore_braces` inverts it.
- For each of sql / mssql / bq and each of rust / cython: `build_query` with
  `{"n": {"contains": "a{b}c"}}` contains `%a{b}c%`; `{limit}` contains `%{limit}%`.
- Existing literal tests (quotes, backslashes) are unchanged.
- cargo: `sql_like_literal("a{b}")` returns `'a\u{e000}b\u{e001}'`.

## 5. Acceptance Criteria

- [ ] AC1: brace operands survive `build_query` for sql, mssql and bq on both paths.
- [ ] AC2: `filter_conditions` output carries sentinels, never raw braces, for partial-match literals.
- [ ] AC3: DeltaTable and Iceberg `build_query` restore sentinels.
- [ ] AC4: existing partial-matching suites pass (`test_sql_partial_matching.py`,
      `test_mssql_partial_matching.py`, `test_bigquery_partial_matching.py`,
      `test_partial_matching_operators.py`, `test_partial_matching_conformance.py`).
- [ ] AC5: `cargo test --no-default-features partial_match` passes.

## 6. Codebase Contract

### Verified
```python
# querysource/parsers/partial_matching.py
def build_like_pattern(op, value, *, escaper) -> str:   # :82
def sql_like_literal(pattern: str) -> str:              # :87
def mssql_like_literal(pattern: str) -> str:            # :92
def bq_like_literal(pattern: str) -> str:               # :97
# querysource/parsers/sql.pyx
from .partial_matching import (                          # :16
async def build_query(self, querylimit: int = None, offset: int = None):  # :433
        self.query_parsed = sql                          # :477 (1 occurrence)
# querysource/parsers/deltatbl.pyx: `self.query_parsed = sql` :114 (1); imports :11-13
# querysource/parsers/iceberg.pyx:  `self.query_parsed = sql` :109 (1); imports :11-13
```
```rust
// rust/src/partial_match.rs
pub fn sql_like_literal(pattern: &str) -> String   // :76
pub fn mssql_like_literal(pattern: &str) -> String // :80
pub fn bq_like_literal(pattern: &str) -> String    // :84
// tests at :190-192
```

### Does NOT Exist
- ~~`restore_braces` / `protect_braces`~~: created here.
- ~~a brace-escape in MySQL/T-SQL string literals~~: none is portable, hence the sentinels.

## 7. Implementation Notes & Constraints

- `.pyx` and Rust changes need rebuilds (`python setup.py build_ext --inplace`;
  maturin wheel staged into `querysource/qs_parsers/`) before Python tests.
- Rust unit tests: `cargo test --no-default-features` (see repo notes).
