---
# SDD flow type and base branch (FEAT-145).
type: feature
base_branch: dev
projects: [parsers, rust-parsers]
tags: [parsers, partial-matching, key-suffix, negation, rust, bug, sdd-fix]
---

# Feature Specification: Partial-match keys honour suffixes: base column, `!` negates, others rejected

**Feature ID**: FEAT-173
**Date**: 2026-10-13
**Author**: Jesus Lara (spec drafted with Claude Code via /sdd-fix)
**Status**: approved
**Target version**: next patch
**Ledger issues**: `issue:36f51186eac1` (bug, minor, discovered from spec:FEAT-180; supersedes `issue:9b7bf46c7d08`)

---

## 1. Motivation & Business Requirements

### Problem Statement

A filter key may carry a suffix (`| ! ~ # @ :`). The dialect docs (§5.5) say
"The suffix is stripped from the column name", and that `!` means negation.
The FEAT-180 partial-matching path ignores both rules. Reproduced on
2026-10-13 for generic SQL, SQL Server, BigQuery and PostgreSQL, on both the
Rust and Cython paths:

| filter | rendered |
|---|---|
| `{"n!": {"contains": "abc"}}` | `WHERE n! LIKE '%abc%'` |
| `{"n~": {"contains": "abc"}}` | `WHERE n~ LIKE '%abc%'` |
| `{"n\|": {"contains": "abc"}}` | `WHERE n\| LIKE '%abc%'` |

The output is invalid SQL, or SQL with the wrong meaning: `n!` asks for
negation but gets a positive match on a column that doesn't exist.

### Goals (semantics decided by the user on 2026-10-13)

- G1: partial-match conditions always render the **base** column (`base_key(key)`).
- G2: a key suffix of exactly `!` flips the operator to its counterpart
  (`contains` ↔ `not_contains`, `ilike` ↔ `not_ilike`, `regex` ↔ `not_regex`, and so on).
- G3: any other suffix (`|`, `~`, `#`, `@`, `:`, `!!`, or a combination) on a
  key whose value is a partial-match dict raises `ParserError`:
  `"<op> on '<key>': key suffix '<suffix>' is not supported with partial-matching operators (only '!' to negate)"`.
- G4: the Rust and Cython paths behave the same. The rule lives in one place per
  language: `validate_partial_match_dict` (Python) and `check_entries` (Rust).

### Non-Goals

- Suffix behaviour for non-partial-match values.
- The FEAT-172 brace protection (a separate feature, which touches different lines).

## 2. Architectural Design

```python
# querysource/parsers/partial_matching.py
def resolve_key_suffix(key: str, op: PartialMatchOp) -> PartialMatchOp: ...
#   suffix = key[len(base_key(key)):]
#   "" -> op ; "!" -> PARTIAL_MATCH_OPERATORS[counterpart(op.name)] ; else ParserError
# validate_partial_match_dict(...) returns resolve_key_suffix(key, entry)
```
```rust
// rust/src/partial_match.rs
pub fn resolve_key_suffix(key: &str, op: &'static PartialMatchOp) -> PyResult<&'static PartialMatchOp>;
// check_entries(...) returns the resolved op
```

Render sites switch from `key` to the base column:

| Dialect | Cython | Rust |
|---|---|---|
| generic SQL | `sql.pyx` `sql_partial_match_condition(key, …)` | `sql_parser.rs` `formatted_key` → `safe_identifier_key(base_key(&key))` |
| SQL Server | `sqlserver.pyx` `mssql_partial_match_condition(key, …)` | `mssql_parser.rs` `mssql_partial_match_condition(&key, …)` |
| BigQuery | `bigquery.pyx` `field_expr` | `bigquery_parser.rs` `process_entry` Dict branch |
| PostgreSQL | `pgsql.pyx` `partial_match_condition(key, …)` | `pgsql_parser.rs` `pg_partial_match_from_dict` |

`abstract.pyx:604` already calls `validate_partial_match_dict`, so a bad suffix
fails before any builder runs.

## 3. Module Breakdown

- **Module 1 (Rust)**: `partial_match.rs` (`resolve_key_suffix` and its use in
  `check_entries`), plus the four Rust render sites. Cargo unit tests.
- **Module 2 (Python/Cython)**: `partial_matching.py`, the four `.pyx` render
  sites, and `tests/test_partial_match_key_suffix.py` (sql/mssql/bq/pg ×
  rust/cython). Depends on Module 1 for its Rust-path tests.

## 4. Test Specification

- `{"n!": {"contains": "abc"}}` renders the base column `n` with NOT LIKE on every dialect and path (pg: `NOT LIKE`).
- `{"n!": {"not_contains": "abc"}}` renders LIKE.
- `{"n~": …}`, `{"n|": …}`, `{"n!~": …}` raise `ParserError`.
- An unsuffixed key is unchanged.
- `resolve_key_suffix` unit tests in Python and Rust.

## 5. Acceptance Criteria

- [ ] AC1: no suffix character ever appears in a rendered partial-match column.
- [ ] AC2: `!` flips the operator; a double flip returns the original.
- [ ] AC3: other suffixes raise `ParserError` (Rust: `ValueError`, which falls back to Cython and its `ParserError`).
- [ ] AC4: the existing partial-matching suites pass.
- [ ] AC5: `cargo test --no-default-features partial_match` passes, and the rest of the lib suite shows no new failures against the 4-failure baseline.
