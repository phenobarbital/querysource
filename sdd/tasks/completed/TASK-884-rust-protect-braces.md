# TASK-884: Rust partial-match literals emit brace sentinels

**Feature**: FEAT-172 — Protect braces in partial-match LIKE literals from later format_map passes
**Spec**: `sdd/specs/parsers-deltatbl-tech-debt.spec.md`
**Status**: done
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned
**discovered_from**: issue:64c82d35d831

---

## Context

Spec §3 Module 2. The Rust filter builders (`sql_parser.rs:213`, `mssql_parser.rs:32`,
`bigquery_parser.rs:214`) quote LIKE patterns with `partial_match.rs` helpers that keep
raw `{ }`. Later Python `format_map` passes strip them. The Rust path is tried first
(`HAS_RUST`), so it must emit the same sentinels as the Python helpers.

## Scope

- Add `pub fn protect_braces(text: &str) -> String` (`{` becomes `\u{e000}`, `}` becomes `\u{e001}`).
- Apply it in `sql_like_literal`, `mssql_like_literal` and `bq_like_literal`, after the existing escaping.
- Add cargo unit tests.

**NOT in scope**: Python/Cython changes and the restore step (TASK-885).

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `rust/src/partial_match.rs` | MODIFY | protect_braces + apply in 3 literal fns + tests |

## Codebase Contract (Anti-Hallucination)

### Existing Signatures to Use
```rust
// rust/src/partial_match.rs
pub fn sql_like_literal(pattern: &str) -> String    // line 76
pub fn mssql_like_literal(pattern: &str) -> String  // line 80
pub fn bq_like_literal(pattern: &str) -> String     // line 84
// #[cfg(test)] assertions at lines 190-192
```

### Does NOT Exist
- ~~`partial_match::protect_braces`~~: created here.

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [{"path": "rust/src/partial_match.rs", "action": "MODIFY"}],
  "contract_symbols": [
    "sym:rust/src/partial_match.rs#sql_like_literal",
    "sym:rust/src/partial_match.rs#mssql_like_literal",
    "sym:rust/src/partial_match.rs#bq_like_literal"
  ]
}
```

## Implementation Blueprint

### Steps (in order)
1. Add `protect_braces` above `sql_like_literal`. The sentinels must match the Python `BRACE_OPEN_SENTINEL`/`BRACE_CLOSE_SENTINEL` exactly, because Python's `restore_braces` undoes both paths' output.
2. Wrap each literal's escaped body with `protect_braces`, because every `format_map` pass sees the quoted literal.
3. Add tests next to the existing literal assertions (line 190).

### `rust/src/partial_match.rs` (MODIFY)
```rust
// AFTER build_like_pattern (verified: line 70-73); occurrences: 1
/// Replace `{`/`}` with the private-use sentinels U+E000/U+E001 so later
/// `str.format_map` passes leave them alone; Python `restore_braces` undoes it.
pub fn protect_braces(text: &str) -> String {
    text.replace('{', "\u{e000}").replace('}', "\u{e001}")
}
// FILL IN: apply protect_braces to the escaped body in the three *_like_literal fns.
```

### FILL IN checklist
- [ ] apply in 3 literal fns
- [ ] cargo tests (AC5)

## Acceptance Criteria

- [ ] `sql_like_literal("a{b}") == "'a\u{e000}b\u{e001}'"`, and likewise for mssql/bq.
- [ ] Existing literal assertions (lines 190-192) still pass.
- [ ] `cargo test --no-default-features partial_match` passes.

## Validation Commands
- `pytest tests/test_rust_partial_match_units.py -q`

## Completion Note

**Completed**: 2026-10-13 by agent:sdd-fix (commit 5c807c4d)

- Added `protect_braces()`. `sql_like_literal`, `mssql_like_literal` and `bq_like_literal` now emit U+E000/U+E001 instead of `{`/`}`.
- New cargo test `literals_protect_braces`. The existing `literals` test is unchanged.
- `cargo test --no-default-features partial_match`: 10 passed. The full lib suite gives 365 passed and 4 failed; those are the same 4 failures as the 2026-10-07 baseline.
- `pytest tests/test_rust_partial_match_units.py`: passed, with the maturin wheel staged.
