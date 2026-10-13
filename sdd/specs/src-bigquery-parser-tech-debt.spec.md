---
# SDD flow type and base branch (FEAT-145).
type: feature
base_branch: dev
projects: [parsers, rust-parsers]
tags: [parsers, rust, bigquery, pgsql, between, quoting, parity, security, ledger-fix]
---

# Feature Specification: Builder parity — numeric comparison operands and strict canonical BETWEEN

**Feature ID**: FEAT-168
**Date**: 2026-10-13
**Author**: Jesus Lara (spec drafted with Claude Code via `/sdd-fix`)
**Status**: approved
**Target version**: 5.2.6
**Ledger issues**: `issue:7a248a32aa07` (tech_debt, major), `issue:fc81723e95ac` (vulnerability, major) — fix group `fixgroup:1552f9967f55`, both discovered from FEAT-165

---

## 1. Motivation & Business Requirements

### Problem Statement

FEAT-165 (`json-dialect-filter-fixes`) unified filter pre-processing and left two
review findings in the ledger. Both live in the dialect *builders* (the
`filter_conditions` step that runs after `AbstractParser._where_element`
pre-processing), in Rust and in their Cython fallbacks.

**A. Comparison-dict operand quoting diverges (`issue:7a248a32aa07`).**
A comparison dict such as `{"x": {">": 1, "<": 9}}` reaches the builders with its
numeric operands still typed as `int`/`float` (`abstract.pyx:609-613` runs
`is_valid()` per operand, which keeps numbers numeric). The builders then render
them differently:

| Builder | Path | `{">": 1}` renders |
|---|---|---|
| PostgreSQL | Cython (`pgsql.pyx:406-411`) | `x > 1` (`str(v)` for non-str) |
| PostgreSQL | Rust (`pgsql_parser.rs:479` `process_dict_value`) | `x > '1'` (`quote_string(v.as_str())`) |
| BigQuery | Cython (`bigquery.pyx:246-252`) | `x > "1"` (`bq_quote_string(str(v))`) |
| BigQuery | Rust (`bigquery_parser.rs:223` `process_dict_value`) | `x > "1"` (`bq_quote_string(v.as_str())`) |

PostgreSQL tolerates `'1'` (an unknown-typed literal is coerced), but the two
paths still emit different SQL for the same request. **BigQuery is strictly
typed: `INT64 > STRING` is a type error**, so a numeric comparison dict against
a numeric BigQuery column fails at query time on both paths. The parity test
hides all of this: `tests/test_dialect_filter_parity.py:79` `_numeric_literals()`
strips quotes from numeric literals on both sides of every assertion that uses it.

Additionally, Rust BigQuery `process_dict_value` has a second, single-entry
`COMPARISON_TOKENS` branch (reached for a *mixed* dict whose first key is a
comparison token) that renders `v.as_str()` **raw, unquoted**
(`format!("{} {} {}", field_expr, op, v.as_str())`). A string operand there is
spliced into the SQL text verbatim.

**B. Builders accept non-canonical BETWEEN (`issue:fc81723e95ac`).**
`filter_values.parse_between()` (`filter_values.py:95`) validates a
`[NOT] BETWEEN lo AND hi` string against a whole-string grammar and
`BetweenClause.render()` emits a canonical form. The builders, however, decide
"this is a BETWEEN clause, splice it verbatim" from a **prefix only**:

- Rust `filter_common::is_canonical_between` (`filter_common.rs:35`) is
  `starts_with("BETWEEN ") || starts_with("NOT BETWEEN ")`. Callers:
  `filter_common::process_str_value` (`:196`, used by the generic/MSSQL paths),
  `sql_parser.rs:387`, `pgsql_parser.rs:652`, `bigquery_parser.rs:331`.
  BigQuery and `filter_common` splice with **no further check**; PostgreSQL adds
  the `pg_validate_between` marker blacklist (`--`, `/*`, `;`, `UNION`, `SELECT`).
- Cython `sql.pyx:235`, `sqlserver.pyx:171`, `bigquery.pyx:290`, `pgsql.pyx:498`
  use `startswith(("BETWEEN ", "NOT BETWEEN "))` plus the same blacklist.

Today the pre-processing is the only real gate. Any route that hands a builder a
filter value without going through `_where_element` (a future caller, a bug in
pre-processing, a typed-filter edge case) gets `BETWEEN 1 AND 2 OR 1=1`
spliced into the WHERE clause — the blacklist does not catch `OR`. Builders must
accept exactly the canonical form and nothing else (defence in depth).

### Goals
- G1: A numeric (`int`/`float`, not `bool`) comparison-dict operand renders as a
  bare numeric literal in PostgreSQL and BigQuery, on both the Rust and the
  Cython path; string operands keep their dialect quoting.
- G2: No comparison-dict branch in either BigQuery builder renders a string
  operand unquoted.
- G3: Every builder (Rust and Cython; generic SQL, SQL Server, PostgreSQL,
  BigQuery) splices a `BETWEEN`-prefixed string only when it matches the exact
  canonical grammar produced by `BetweenClause.render()`; otherwise the filter
  entry is dropped (same outcome as today's blacklist rejection).
- G4: The parity tests assert exact rendered SQL — `_numeric_literals()` is removed.

### Non-Goals (explicitly out of scope)
- Numeric-looking **strings** (`{">": "5"}`) — they stay quoted strings; typing
  request strings is the pre-processing layer's job.
- `bool` operands — current rendering is kept unchanged.
- The generic SQL Rust builder (`sql_parser.rs` dict branch) — it already renders
  numbers bare via `safe_scalar_value`; only its BETWEEN call site changes.
- BigQuery handling of `''` inside single-quoted BETWEEN bounds (BigQuery has no
  `''` escape; such a bound is a syntax error, not an injection). Recorded as a
  gotcha, not fixed here.
- Removing `pg_validate_between` / `validate_between_clause`; they become
  redundant but harmless and are left in place.

---

## 2. Architectural Design

### Overview

**Numeric operands.** Each builder's comparison-dict renderer picks the operand
rendering by the operand's *type*, not its text:

```
operand is int/float (not bool)  →  bare literal   (str(v) / i.to_string() / f.to_string())
anything else                    →  dialect quoter (Entity.quoteString | quote_string | bq_quote_string)
```

In Rust each parser module has its own `FilterValue` enum (`pgsql_parser.rs`
uses `filter_common::FilterValue`; `bigquery_parser.rs:42` defines a private
one), so each gets a small private helper `fn dict_operand(v: &FilterValue) -> String`.
Rust extraction (`filter_common.rs:66-77`) already maps Python `bool` →
`Bool` before `int`, and an integral float to `Int` (`9.0` → `9`); the Cython
path renders `str(9.0)` = `9.0`. Both are the same SQL numeric value — tests use
integers and non-integral floats only.

**Strict canonical BETWEEN.** The canonical grammar is exactly what
`BetweenClause.render()` emits:

```
CANONICAL := ("NOT ")? "BETWEEN " BOUND " AND " BOUND        (whole string, single spaces, upper-case keywords)
BOUND     := -?[0-9]+(\.[0-9]+)?                              (number)
           | '([^'\\]|'')*'                                   (single-quoted, '' doubled, no backslash)
```

- Python: new `filter_values.is_canonical_between(value) -> bool` (compiled regex,
  `fullmatch`). `_normalise_bound` additionally **rejects a backslash** in a bound
  (`ParserError`), so pre-processing never produces a value the builders refuse.
- Rust: `filter_common::is_canonical_between` keeps its name/signature but becomes
  the strict grammar (`regex` is already a dependency, `rust/Cargo.toml:14`; use a
  `std::sync::OnceLock<Regex>`). A new `pub(crate) fn has_between_prefix(value) -> bool`
  (the old prefix test) lets callers tell "a BETWEEN attempt" from "an ordinary
  string that happens to contain BETWEEN".
- Every builder call site becomes:
  ```
  if has_between_prefix(value):
      if is_canonical_between(value): emit "({key} {value})"
      else: drop the entry (Rust: return None; Cython: continue)
  ```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `AbstractParser._where_element` (`abstract.pyx:583`) | unchanged | still the primary gate (`parse_between`, `is_valid`) |
| `filter_values.BetweenClause.render()` | defines the grammar | builders must accept every value it can produce |
| Rust `pgsql_filter_conditions` / `bigquery` / `sql` / `mssql` | modified call sites | via `filter_common::is_canonical_between` |
| Cython `filter_conditions` (sql, sqlserver, bigquery, pgsql) | modified | Rust-failure fallback path |

### New Public Interfaces
```python
# querysource/parsers/filter_values.py
def is_canonical_between(value: object) -> bool:
    """True iff ``value`` is a str exactly matching BetweenClause.render() output."""

def has_between_prefix(value: object) -> bool:
    """True iff ``value`` is a str starting with 'BETWEEN ' or 'NOT BETWEEN '."""
```
```rust
// rust/src/filter_common.rs
pub(crate) fn has_between_prefix(value: &str) -> bool;
pub(crate) fn is_canonical_between(value: &str) -> bool;   // now strict (same signature)
```

---

## 3. Module Breakdown

#### Delegation-eligible modules

| Module | Eligible? | Decided patterns / exact contracts | Why not (if no) |
|---|---|---|---|
| M1: Python/Cython builders | yes | grammar, helper names, drop-on-mismatch, type-based operand rule all fixed above | — |
| M2: Rust builders | yes | same contracts; `OnceLock<Regex>`; per-module `dict_operand` | — |
| M3: Parity + hardening tests | yes | exact expected strings listed in §4 | — |

### Module 1: Python grammar + Cython builders
- **Paths**: `querysource/parsers/filter_values.py`, `querysource/parsers/sql.pyx`,
  `querysource/parsers/sqlserver.pyx`, `querysource/parsers/bigquery.pyx`,
  `querysource/parsers/pgsql.pyx`
- **Responsibility**: add `is_canonical_between` / `has_between_prefix`
  (exported in `__all__`); backslash rejection in `_normalise_bound`; replace the
  four `startswith(...)` + blacklist blocks with the strict check; BigQuery
  comparison-dict operands (both the all-comparisons comprehension at
  `bigquery.pyx:247` and the single-token branch at `:252`) render
  `int`/`float` (not `bool`) bare. `pgsql.pyx` comparison rendering already
  matches G1 and is not changed.
- **Depends on**: —

### Module 2: Rust builders
- **Paths**: `rust/src/filter_common.rs`, `rust/src/pgsql_parser.rs`,
  `rust/src/bigquery_parser.rs`, `rust/src/sql_parser.rs`
- **Responsibility**: strict `is_canonical_between` + `has_between_prefix`;
  the four call sites drop non-canonical BETWEEN; `pgsql_parser::process_dict_value`
  and `bigquery_parser::process_dict_value` render numeric operands bare via a
  private `dict_operand`; the BigQuery single-entry `COMPARISON_TOKENS` branch
  uses `dict_operand` instead of raw `v.as_str()`. Rust unit tests in each file's
  `mod tests` cover the new behaviour (including a non-canonical
  `BETWEEN 1 AND 2 OR 1=1` returning `None`).
- **Depends on**: —
- **Build**: `make build-rust` then the stage-rust step so Python loads the new
  `_qs_parsers` .so; `cargo test --no-default-features` for Rust unit tests.

### Module 3: Tests
- **Paths**: `tests/test_dialect_filter_parity.py`, `tests/unit/test_builder_between_strict.py` (new)
- **Responsibility**: delete `_numeric_literals` and assert exact strings; add the
  BigQuery/PG numeric and float cases; builder-level defence-in-depth tests that
  bypass pre-processing (assign `parser.filter` directly, then
  `await parser.filter_conditions(sql)`), on both paths (`HAS_RUST` patched off
  for Cython).
- **Depends on**: Module 1, Module 2

---

## 4. Test Specification

### Unit / parity tests

| Test | Module | Expected |
|---|---|---|
| `test_pg_full_pipeline` `{"x": {">": 1, "<": 9}}` | M3 | `(x > 1 AND x < 9)` both paths, exact |
| `test_pg_full_pipeline` `{"qty": {">": 0}}` | M3 | `qty > 0` both paths, exact |
| `test_other_dialects_render_all_comparison_operators` SQL | M3 | `(x > 1 AND x < 9)` |
| `test_other_dialects_render_all_comparison_operators` BigQuery | M3 | `(x > 1 AND x < 9)` |
| BigQuery/PG float `{"x": {">=": 2.5}}` | M3 | `x >= 2.5` both paths |
| BigQuery/PG string `{"x": {">": "b"}}` | M3 | PG `x > 'b'`, BQ `x > "b"` (unchanged) |
| `test_is_canonical_between_accepts` | M1 | `BETWEEN 1 AND 2`, `NOT BETWEEN -1.5 AND 3`, `BETWEEN '2025-01-01' AND 'O''Brien'` |
| `test_is_canonical_between_rejects` | M1 | `BETWEEN 1 AND 2 OR 1=1`, `BETWEEN 1 AND 2;`, `between 1 and 2`, `BETWEEN  1 AND 2`, `BETWEEN 'a\' AND 'b'`, `BETWEEN x AND y`, non-str |
| `test_render_is_canonical` | M1 | for every accepted `parse_between` corpus value, `is_canonical_between(clause.render())` is True |
| `test_backslash_bound_rejected` | M1 | `parse_between("d", "BETWEEN 'a\\' AND 'b'")` raises `ParserError` |
| `test_builder_drops_noncanonical_between[path×dialect]` | M3 | pre-processing bypassed; `{"amount": "BETWEEN 1 AND 2 OR 1=1"}` → no WHERE predicate for that key, for sql, sqlserver, bigquery, pgsql on rust and cython |
| `test_builder_keeps_canonical_between[path×dialect]` | M3 | `{"amount": "BETWEEN 100 AND 500"}` → `(amount BETWEEN 100 AND 500)` |
| Rust `filter_common::tests` | M2 | strict accept/reject table mirroring the Python one |
| Rust `bigquery_parser::tests` | M2 | `Int(1)` comparison → `x > 1`; mixed dict string operand quoted |

---

## 5. Acceptance Criteria

- [ ] AC1: Numeric comparison-dict operands render bare in PostgreSQL and BigQuery on both paths (G1).
- [ ] AC2: No BigQuery comparison-dict branch renders a string operand unquoted (G2).
- [ ] AC3: Every builder, both paths, drops a `BETWEEN`-prefixed value that is not canonical, and keeps canonical ones (G3).
- [ ] AC4: `_numeric_literals` no longer exists in `tests/`; parity assertions are exact (G4).
- [ ] AC5: `pytest tests/test_dialect_filter_parity.py tests/unit/test_builder_between_strict.py tests/test_dialect_filter_preprocessing.py tests/unit/test_where_value_hardening.py -q` passes.
- [ ] AC6: `cargo test --no-default-features` passes for the new/changed tests (pre-existing failures noted, not introduced).
- [ ] AC7: `ruff check` clean on changed Python files.

---

## 6. Codebase Contract

Verified against: `dda6d6b6`

### Verified Imports
```python
from querysource.parsers.filter_values import base_key, is_comparison_dict, parse_between, BetweenClause  # filter_values.py:44,49,95,31
from querysource.exceptions import ParserError  # filter_values.py:11
from querysource.types.validators import Entity, quote_sql_literal  # sql.pyx:14
from querysource.parsers import QS_VARIABLES, bigquery, pgsql, sql, sqlserver  # tests/test_dialect_filter_parity.py:24
```

### Existing Signatures
```python
# querysource/parsers/filter_values.py
_BOUND = r"(?:'(?:[^']|'')*'|[A-Za-z0-9_:./+-]+)"           # line 21
class BetweenClause: negated: bool; low: str; high: str      # line 31
    def render(self) -> str                                  # line 38 → f"{prefix} {low} AND {high}"
def _normalise_bound(key: str, bound: str) -> str            # line 76
def parse_between(key: str, value: str) -> BetweenClause | None  # line 95
# querysource/parsers/bigquery.pyx
cdef str bq_quote_string(object value)                       # line 40
```
```rust
// rust/src/filter_common.rs
pub(crate) fn is_canonical_between(value: &str) -> bool      // line 35
pub fn process_str_value(key: &str, value: &str, name: &str, end: &str) -> Option<String>  // line 194
// rust/src/pgsql_parser.rs
fn pg_validate_between(value: &str) -> bool                  // line 67
fn process_dict_value(key: &str, entries: &[(String, FilterValue)], _format: Option<&str>) -> Option<String>  // line 479
fn process_str_value(key, value, name, end, _format) -> Option<String>  // line 630
// rust/src/bigquery_parser.rs
enum FilterValue { Str, Int, Float, Bool, List, Dict, Null } // line 42 (private)
fn process_dict_value(field_expr: &str, entries: &[(String, FilterValue)]) -> Option<String>  // line 223
fn process_str_value(field_expr, value, name, end) -> Option<String>  // line 324
// rust/src/validators.rs
pub fn quote_string(value: &str, no_dblquoting: bool) -> String  // line 198
pub fn bq_quote_string(value: &str) -> String                // line 250
```

### Does NOT Exist (Anti-Hallucination)
- ~~`filter_values.is_canonical_between`~~ / ~~`has_between_prefix`~~ — created by this feature
- ~~`filter_common::has_between_prefix`~~ — created by this feature
- ~~a shared `FilterValue` between `bigquery_parser.rs` and `filter_common.rs`~~ — BigQuery has its own private enum

### Edit Sites (Blueprint Anchors)

| File | Action | Verbatim anchor line | Verified at | Occurrences |
|---|---|---|---|---|
| `querysource/parsers/filter_values.py` | MODIFY | `_BETWEEN_PREFIX = re.compile(r"^\s*(?:NOT\s+)?BETWEEN\s", re.IGNORECASE)` | `:22` | 1 |
| `querysource/parsers/filter_values.py` | MODIFY | `def _normalise_bound(key: str, bound: str) -> str:` | `:76` | 1 |
| `querysource/parsers/sql.pyx` | MODIFY | `if str_value.startswith(("BETWEEN ", "NOT BETWEEN ")):` | `:235` | 1 |
| `querysource/parsers/sqlserver.pyx` | MODIFY | `if str_value.startswith(("BETWEEN ", "NOT BETWEEN ")):` | `:171` | 1 |
| `querysource/parsers/bigquery.pyx` | MODIFY | `if str_value.startswith(("BETWEEN ", "NOT BETWEEN ")):` | `:290` | 1 |
| `querysource/parsers/bigquery.pyx` | MODIFY | `parts = [f"{field_expr} {op} {bq_quote_string(str(v))}" for op, v in value.items()]` | `:247` | 1 |
| `querysource/parsers/pgsql.pyx` | MODIFY | `elif str_value.startswith(("BETWEEN ", "NOT BETWEEN ")):` | `:498` | 1 |
| `rust/src/filter_common.rs` | MODIFY | `pub(crate) fn is_canonical_between(value: &str) -> bool {` | `:35` | 1 |
| `rust/src/filter_common.rs` | MODIFY | `    if is_canonical_between(value) {` (in `process_str_value`) | `:196` | 1 |
| `rust/src/sql_parser.rs` | MODIFY | `    if is_canonical_between(value) {` | `:387` | 1 |
| `rust/src/pgsql_parser.rs` | MODIFY | `    if is_canonical_between(value) {` | `:652` | 1 |
| `rust/src/pgsql_parser.rs` | MODIFY | `fn process_dict_value(` | `:479` | 1 |
| `rust/src/bigquery_parser.rs` | MODIFY | `    if is_canonical_between(value) {` | `:331` | 1 |
| `rust/src/bigquery_parser.rs` | MODIFY | `fn process_dict_value(` | `:223` | 1 |
| `tests/test_dialect_filter_parity.py` | MODIFY | `def _numeric_literals(predicate: str \| None) -> str \| None:` | `:79` | 1 |
| `tests/unit/test_builder_between_strict.py` | CREATE | — | — | — |

---

## 7. Implementation Notes & Constraints

### Known Risks / Gotchas
- **Stale extensions**: Python imports the source-tree `_qs_parsers` .so — after
  `make build-rust` run the stage-rust step, and rebuild Cython in place
  (`python setup.py build_ext --inplace`) in the worktree before Python tests.
  Never `make clean` (it deletes `.so` files inside `.venv`).
- **`bool` is an `int`** in Python: the operand rule must test `bool` first.
- **Integral floats**: Rust maps `9.0` → `Int(9)`; Cython emits `9.0`. Use
  integers or non-integral floats in parity tests.
- **Grammar drift**: `test_render_is_canonical` pins `BetweenClause.render()` to
  `is_canonical_between`; any future change to one must change the other.
- **BigQuery `''`**: canonical bounds may contain `''`; BigQuery rejects it as a
  syntax error (safe, out of scope).
- `cargo test` needs `--no-default-features`; some Rust tests fail on `dev`
  already — compare against the base commit, don't fix unrelated failures.

---

## 8. Open Questions

None — all design decisions are fixed above.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-10-13 | Jesus Lara / Claude Code | Initial spec from ledger fix group `fixgroup:1552f9967f55` |
