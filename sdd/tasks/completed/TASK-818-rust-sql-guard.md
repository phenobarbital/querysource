# TASK-818: Rust `sql_guard` lexer, splitter and statement classifier

**Feature**: FEAT-156 — MultiQuery ExecuteSQL Destination
**Spec**: `sdd/specs/multi-executesql.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §2 ("Rust guard") and §3 Module 1. `ExecuteSQL` runs maintenance SQL on the
full-access `DB*` connection, so the only safety net is a guard that splits a script
into top-level statements with a real lexer and rejects destructive, privilege-changing
and transaction-control statements **before anything is sent to the database**.
This task writes that guard as a pure-Rust core plus a thin `#[pyfunction]`, in a new
file of the `_qs_parsers` crate. Registering it in the PyO3 module (`rust/src/lib.rs`)
and exposing it to Python is TASK-819.

---

## Scope

- Create `rust/src/sql_guard.rs` with `BlockedKind`, a private lexer (`lex`),
  `split_statements`, `keyword_tokens`, `classify`, `guard` and the `#[pyfunction] sql_guard`.
- Implement the lexer states of spec §2: `'…'` (`''` escape), `E'…'` (backslash
  escapes), `"…"` (`""` escape), `$tag$…$tag$`, `-- …`, nested `/* … */`.
- Implement the classification table of spec §2 exactly (8 blocked kinds, everything else allowed).
- Write `#[cfg(test)] mod tests` covering every row of the §2 blocked table and the
  edge cases listed under Module 1.

**NOT in scope**:
- `rust/src/lib.rs` (`mod sql_guard;` + `add_function`) and the Python re-export — TASK-819.
  You add `mod sql_guard;` **only temporarily, uncommitted**, to run the Rust tests (see Steps).
- Any Python file; rebuilding the extension (`maturin develop`) — TASK-819.
- New crates (`sqlparser`, …) — forbidden by spec §6.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `rust/src/sql_guard.rs` | CREATE | Lexer + splitter + classifier + `sql_guard` pyfunction + Rust unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```rust
use pyo3::exceptions::PyValueError;   // pattern verified: rust/src/safe_dict.rs:236 (pyo3::exceptions::PyValueError::new_err)
use pyo3::prelude::*;                  // verified: rust/src/safe_dict.rs:6, rust/src/lib.rs:6
```

### Existing Signatures to Use
```text
rust/Cargo.toml            pyo3 = { version = "0.29" }; no other parsing crates (rayon, regex, once_cell; dev: proptest)
rust/Cargo.toml            [features] default = ["extension-module"] — `cargo test` MUST use --no-default-features
rust/src/lib.rs:8-23       mod list (alphabetical; `mod soql_parser;` :21, `mod sql_parser;` :22) — NOT edited here
rust/src/safe_dict.rs:201-202   #[pyfunction] / #[pyo3(signature = (template, conditions, cond_definition))]  (pyfunction pattern)
rust/src/safe_dict.rs:236       .map_err(|e| pyo3::exceptions::PyValueError::new_err(e))                   (Err(String) → ValueError pattern)
rust/src/safe_dict.rs:304-305   #[cfg(test)] mod tests {                                                    (test module pattern)
```

### Does NOT Exist
- ~~A SQL grammar / AST parser in `rust/`~~ — `*_parser.rs` build WHERE/ORDER fragments; `safe_dict.rs:17-33` checks **values** for injection markers. Nothing classifies statements.
- ~~`sqlparser` / `sqlglot` crates~~ — not in `rust/Cargo.toml`; do not add any dependency.
- ~~`rust/src/sql_guard.rs`~~ — created by this task.
- ~~`py.allow_threads`~~ — renamed in pyo3 0.29; not needed here (the guard is short, CPU-light).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "rust/src/sql_guard.rs", "action": "CREATE"}
  ],
  "contract_symbols": []
}
```

---

## Implementation Notes

### Lexer rules (bounds for the `lex` FILL IN)
- **L1** Walk `sql.as_bytes()` by index. Every delimiter is ASCII, so slicing `sql[a..b]` at
  delimiter positions is always on a UTF-8 boundary. Identifier bytes = ASCII alphanumeric,
  `_`, `$` (never first) and any byte `>= 0x80`.
- **L2** `Normal` + `'`: flush the pending token; if the flushed token is exactly `E`/`e`
  and ended at this byte (adjacent), **discard it** and enter `EscapeString`, else `SingleQuote`.
- **L3** `SingleQuote`: `''` stays inside; a lone `'` returns to `Normal`.
- **L4** `EscapeString`: `\` skips the next byte; `''` stays inside; a lone `'` returns to `Normal`.
- **L5** `Normal` + `"` → `DoubleQuote`; `""` stays inside; a lone `"` returns to `Normal`.
  Nothing inside a quoted identifier becomes a keyword token.
- **L6** `Normal` + `$`: if the previous byte is an identifier byte, it is part of the
  identifier. Otherwise try to read a tag `$` `[A-Za-z_][A-Za-z0-9_]*`? `$`. On a match, find the
  next occurrence of the exact same delimiter after it (`sql[i..].find(delim)`); the body is
  skipped; no match → `Err`. No match of a tag (`$1` parameter, lone `$`) → ordinary code byte.
- **L7** `--` → `LineComment` until `\n` (EOF also ends it — not an error). `/*` →
  `BlockComment(1)`; inside, `/*` increments and `*/` decrements; depth 0 → `Normal`.
  Comments separate tokens.
- **L8** `;` in `Normal` closes the current segment `[start, i)`; `start = i + 1`. At EOF close
  `[start, len)`. EOF in `SingleQuote`/`EscapeString`/`DoubleQuote`/`Dollar`/`BlockComment` →
  `Err(format!("unterminated {what} starting at byte {pos}"))`, `what` ∈ `string literal`,
  `quoted identifier`, `dollar-quoted body`, `block comment` (fail closed).
- **L9** `has_code` = a non-whitespace byte was seen in `Normal`, or a literal / identifier /
  dollar body started, inside the segment. Segments with `has_code == false` (empty or
  comment-only) are discarded by `split_statements`.
- **L10** Keyword tokens = maximal runs of identifier bytes in `Normal` that start with an
  ASCII letter or `_` (runs starting with a digit are numbers: skipped), `to_ascii_uppercase()`d.

### Key Constraints
- Pure-Rust core (`lex`, `split_statements`, `keyword_tokens`, `classify`, `guard`) + thin
  `#[pyfunction]`; `Err(String)` → `PyValueError`. No panics on any input (no `unwrap` on
  user-controlled indexing, no byte slicing inside multi-byte chars — the 80-char preview uses `.chars()`).
- Error message format is fixed by the spec: `statement <n>: <kind> is not allowed: <first 80 chars>`,
  `n` 1-based, `kind` = `BlockedKind::as_str()`.
- `ALTER … DROP` is blocked whenever **any** token after `ALTER` is `DROP` (so
  `ALTER COLUMN c DROP DEFAULT` is blocked too — intentionally conservative).
- The `ROLE` check (`CREATE|ALTER` + `ROLE|USER|GROUP`) is evaluated before `AlterDrop`.
- `SET`/`RESET` with a `ROLE` or `AUTHORIZATION` token is also `role`. This was added after task review, because `SET ROLE` / `SET SESSION AUTHORIZATION` would otherwise let a script switch identity. Plain `SET statement_timeout`, `SET search_path`, etc. stay allowed. Test: `test_blocks_set_role` covering `SET ROLE admin`, `SET LOCAL ROLE x`, `SET SESSION AUTHORIZATION x`, `RESET ROLE`, plus `SET search_path = x` allowed.

### Why the Rust tests need a temporary `mod` line
`rust/src/sql_guard.rs` is only compiled once `lib.rs` declares `mod sql_guard;`, which
belongs to TASK-819. Chosen option: **verify here with a temporary, never-committed mod
line**, so this task proves its own code (deferring all Rust tests to TASK-819 would
let a broken lexer pass this task's gate). Add `mod sql_guard;` below `mod soql_parser;`,
run the tests, then `git checkout -- rust/src/lib.rs` and confirm `git status --porcelain rust/src/lib.rs`
prints nothing before committing. A `dead_code` warning for `sql_guard` is expected until TASK-819
registers it. TASK-819 re-runs the full `cargo test --no-default-features` with the real registration.

### References in Codebase
- `rust/src/safe_dict.rs` — pyfunction + `PyValueError` + test-module pattern.

---

## Implementation Blueprint

### Steps (in order)
1. Write `rust/src/sql_guard.rs` from the block below — *why*: fixes types, signatures, classifier and the pyfunction so only the lexer body is judgement.
2. Implement `lex` per rules L1–L10 — *why*: it is the single source of truth for both splitting and tokenising.
3. Complete the test module (every FILL IN test name) — *why*: every §2 table row and every Module 1 edge case must be pinned.
4. Temporarily add `mod sql_guard;` below `mod soql_parser;` in `rust/src/lib.rs`, run `cd rust && cargo test --no-default-features sql_guard` and `cargo clippy --no-default-features` (if installed), then `git checkout -- rust/src/lib.rs` — *why*: the file is not compiled otherwise, and `lib.rs` belongs to TASK-819.
5. Run the Validation Commands and commit only `rust/src/sql_guard.rs` — *why*: regression gate on the existing extension.

### `rust/src/sql_guard.rs` (CREATE)
```rust
// Copyright (C) 2018-present Jesus Lara
//
// sql_guard.rs — lexical guard for maintenance SQL (FEAT-156).
// Splits a PostgreSQL script into top-level statements and rejects destructive,
// privilege and transaction-control statements before anything reaches the database.
// Lexical, not semantic: DDL hidden inside functions is out of reach (spec §7).

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

/// Why a statement was rejected.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BlockedKind { Drop, Truncate, AlterDrop, DoBlock, Privilege, Role, CopyProgram, TransactionControl }

impl BlockedKind {
    /// snake_case name used in the `ValueError` message.
    pub fn as_str(self) -> &'static str {
        match self {
            BlockedKind::Drop => "drop",
            BlockedKind::Truncate => "truncate",
            BlockedKind::AlterDrop => "alter_drop",
            BlockedKind::DoBlock => "do_block",
            BlockedKind::Privilege => "privilege",
            BlockedKind::Role => "role",
            BlockedKind::CopyProgram => "copy_program",
            BlockedKind::TransactionControl => "transaction_control",
        }
    }
}

const TRANSACTION_CONTROL: &[&str] = &["BEGIN", "START", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE", "ABORT"];
const ROLE_OBJECTS: &[&str] = &["ROLE", "USER", "GROUP"];

/// Lexer states (spec §2).
#[derive(Debug, Clone, PartialEq, Eq)]
enum State { Normal, SingleQuote, EscapeString, DoubleQuote, Dollar(String), LineComment, BlockComment(u32) }

/// One top-level statement found by `lex`: byte range, content flag, keyword tokens.
struct Segment { start: usize, end: usize, has_code: bool, tokens: Vec<String> }

fn lex(sql: &str) -> Result<Vec<Segment>, String> {
    // FILL IN: byte-wise state machine over `sql.as_bytes()` with `State` — bounded by rules L1–L10
    unimplemented!()
}

/// Split `sql` into top-level statements (trimmed, non-empty, comment-only dropped).
pub fn split_statements(sql: &str) -> Result<Vec<String>, String> {
    Ok(lex(sql)?.into_iter().filter(|s| s.has_code).map(|s| sql[s.start..s.end].trim().to_string()).collect())
}

/// Upper-cased keyword tokens of one statement, skipping literals, identifiers and comments.
fn keyword_tokens(stmt: &str) -> Vec<String> {
    lex(stmt).map(|segs| segs.into_iter().flat_map(|s| s.tokens).collect()).unwrap_or_default()
}

/// Classify one statement; `None` = allowed.
pub fn classify(stmt: &str) -> Option<BlockedKind> {
    let tokens = keyword_tokens(stmt);
    let first = tokens.first()?.as_str();
    let second = tokens.get(1).map(String::as_str).unwrap_or("");
    match first {
        "DROP" => Some(BlockedKind::Drop),
        "TRUNCATE" => Some(BlockedKind::Truncate),
        "DO" => Some(BlockedKind::DoBlock),
        "GRANT" | "REVOKE" => Some(BlockedKind::Privilege),
        "CREATE" | "ALTER" if ROLE_OBJECTS.contains(&second) => Some(BlockedKind::Role),
        // SET [SESSION|LOCAL] ROLE …, SET SESSION AUTHORIZATION …, RESET ROLE / RESET SESSION AUTHORIZATION
        "SET" | "RESET" if tokens[1..].iter().any(|t| t == "ROLE" || t == "AUTHORIZATION") => Some(BlockedKind::Role),
        "ALTER" if tokens[1..].iter().any(|t| t == "DROP") => Some(BlockedKind::AlterDrop),
        "COPY" if tokens.iter().any(|t| t == "PROGRAM") => Some(BlockedKind::CopyProgram),
        t if TRANSACTION_CONTROL.contains(&t) => Some(BlockedKind::TransactionControl),
        _ => None,
    }
}

/// Pure-Rust entry point used by the pyfunction and by `cargo test`.
pub fn guard(sql: &str) -> Result<Vec<String>, String> {
    let statements = split_statements(sql)?;
    for (index, stmt) in statements.iter().enumerate() {
        if let Some(kind) = classify(stmt) {
            let preview: String = stmt.chars().take(80).collect();
            return Err(format!("statement {}: {} is not allowed: {}", index + 1, kind.as_str(), preview));
        }
    }
    Ok(statements)
}

/// Python: `sql_guard(sql: str) -> list[str]`; raises `ValueError`.
#[pyfunction]
#[pyo3(signature = (sql))]
pub fn sql_guard(sql: &str) -> PyResult<Vec<String>> {
    guard(sql).map_err(|e| PyValueError::new_err(e))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn blocked(sql: &str) -> String { guard(sql).expect_err(sql) }

    #[test]
    fn blocks_drop_any_case() {
        assert!(blocked("DROP TABLE x").starts_with("statement 1: drop is not allowed: DROP TABLE x"));
        assert!(blocked("dRoP schema s cascade").contains(": drop is not allowed"));
    }

    #[test]
    fn keywords_in_literals_and_comments_are_ignored() {
        for sql in ["SELECT 'drop table x'", "SELECT 1 -- DROP", "SELECT /* DROP */ 1", "SELECT $$DROP$$", "SELECT $fn$ DROP $fn$", "SELECT \"drop\" FROM t"] {
            assert_eq!(guard(sql).unwrap().len(), 1, "{sql}");
        }
    }

    #[test]
    fn semicolon_in_literal_does_not_split() {
        assert_eq!(guard("DELETE FROM t WHERE a = ';'; INSERT INTO t VALUES (1);").unwrap().len(), 2);
    }

    // FILL IN: remaining tests — bounded by the FILL IN checklist (one #[test] per name)
}
```
**Why this shape**: the classifier, `guard` message format and pyfunction are decided by spec §2,
so they are fixed; only the lexer is judgement. `lex` is shared by `split_statements` and
`keyword_tokens` so splitting and classification can never disagree about what is a literal.
Do not change any `pub` signature — TASK-819 registers `sql_guard::sql_guard` and FEAT-157 relies on it.

### FILL IN checklist
- [ ] `sql_guard.rs::lex` — rules L1–L10; bounded by "unterminated → Err", "no panic on any input".
- [ ] tests `blocks_truncate`, `blocks_alter_drop_column`, `blocks_alter_drop_constraint`, `blocks_do_block` (`DO $$ BEGIN EXECUTE 'DROP TABLE x'; END $$`).
- [ ] tests `blocks_grant`, `blocks_revoke`, `blocks_create_role`, `blocks_alter_user`, `blocks_create_group`.
- [ ] tests `blocks_copy_program` (`COPY t FROM PROGRAM 'x'`) and `allows_copy_without_program` (`COPY t TO STDOUT`).
- [ ] test `blocks_transaction_control` — each of BEGIN, START TRANSACTION, COMMIT, END, ROLLBACK, SAVEPOINT s, RELEASE s, ABORT.
- [ ] test `allows_dml_and_ddl` — WITH, SELECT, INSERT, UPDATE, DELETE, MERGE, CREATE TABLE/INDEX/VIEW, `ALTER TABLE t ADD COLUMN c int`, CALL, REFRESH MATERIALIZED VIEW, ANALYZE.
- [ ] test `allows_data_modifying_cte` — the Test Specification refresh CTE → exactly 1 statement.
- [ ] tests `nested_block_comment`, `escape_string_backslash_quote` (`SELECT E'it\'s; DROP'` → 1 statement, allowed), `dollar_param_is_not_a_quote` (`SELECT $1; DELETE FROM t WHERE id = $2` → 2).
- [ ] test `drops_empty_and_comment_only` (`DELETE FROM t; ; -- c\n INSERT INTO t VALUES (1);` → 2).
- [ ] tests `unterminated_literal`, `unterminated_escape_string`, `unterminated_identifier`, `unterminated_dollar_body`, `unterminated_block_comment` → `Err` containing `unterminated`.
- [ ] test `first_blocked_statement_is_reported` (`SELECT 1; DROP TABLE x; TRUNCATE y` → message starts `statement 2: drop`).
- [ ] test `preview_is_80_chars_and_utf8_safe` (a blocked statement with multi-byte chars, > 80 chars).

---

## Acceptance Criteria

- [ ] `rust/src/sql_guard.rs` exists with the exact `pub` signatures of the blueprint.
- [ ] With a temporary `mod sql_guard;` in `lib.rs`: `cd rust && cargo test --no-default-features sql_guard` passes (all rows of spec §2 + Module 1 edge cases); the temporary line is reverted and not committed.
- [ ] No new crate in `rust/Cargo.toml`; no change to any other file.
- [ ] Existing Python regression gate passes (the installed extension is untouched).

## Validation Commands

- `pytest tests/test_rust_parsers.py -q`

---

## Test Specification

```rust
// rust/src/sql_guard.rs — #[cfg(test)] mod tests (fixture for allows_data_modifying_cte)
const REFRESH_CTE: &str = "WITH params AS (SELECT (date_trunc('month', CURRENT_DATE) - INTERVAL '5 months')::date AS start_date), \
deleted AS (DELETE FROM wm_assembly.employee_detail_profile p USING params \
WHERE p.activity_date >= params.start_date AND p.activity_date < CURRENT_DATE RETURNING 1) \
INSERT INTO wm_assembly.employee_detail_profile (employee_id, activity_date) \
SELECT ad.employee_id, ad.activity_date FROM activity_days ad, params WHERE ad.activity_date >= params.start_date";

#[test]
fn allows_data_modifying_cte() {
    assert_eq!(guard(REFRESH_CTE).unwrap().len(), 1);
}
```

---

## Agent Instructions

When you pick up this task:

1. **Work in the feature worktree** — never on `base_branch`
   (`python -m scripts.sdd.ensure_worktree --slug multi-executesql --feature-id FEAT-156`)
2. **Read the spec** at the path listed above for full context (§2 lexer + table, §3 Module 1).
3. **Check dependencies** — none.
4. **Verify the Codebase Contract** before writing any code.
5. **Update status** in `sdd/tasks/index/multi-executesql.json` → `"in-progress"` (set `started_at`) and commit only that index file.
6. **Implement** from the blueprint; complete every `# FILL IN:` / `// FILL IN:`; never change a `pub` signature.
7. **Verify** — run the Rust tests with the temporary `mod` line (Steps 4), revert `rust/src/lib.rs`, then run the Validation Commands.
8. **Commit the code** — stage only `rust/src/sql_guard.rs` (never `git add .` / `-A`).
9. **Close the task** with `scripts/sdd/close_task.sh TASK-818 multi-executesql verified`.
10. **Fill in the Completion Note** below, then commit the staged SDD state.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: sdd-worker (sequential fallback)
**Date**: 2026-09-30
**Notes**: Implemented rust/src/sql_guard.rs (lexer L1-L10, classifier incl. SET/RESET ROLE|AUTHORIZATION, pyfunction). 30 cargo tests pass with a temporary `mod sql_guard;` (reverted, not committed). cargo run with CARGO_HOME copied to scratchpad because ~/.cargo is read-only.

**Deviations from spec**: none
