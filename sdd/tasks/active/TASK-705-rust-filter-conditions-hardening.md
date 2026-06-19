# TASK-705: Harden Rust `filter_conditions` to escape every interpolation site

**Feature**: FEAT-103 — Malforming Query-Slug Issue (SQLi & Datasource Credential Exposure)
**Spec**: `sdd/specs/malforming-queryslug-issue.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-704
**Assigned-to**: unassigned

---

## Context

For non-raw slugs the WHERE clause is built by `filter_conditions`, which in
production runs the Rust path (`_rs.filter_conditions`, taken when `HAS_RUST` — the
`.so` is built). The current interpolation escapes some string values but leaves the
condition **key** (identifier), **operator**, and **dict/list/BETWEEN values** raw —
matching the PoC's identifier breakout. This task hardens the Rust `filter_conditions`
(and the pgsql variant) so every interpolation site is escaped or rejected.
Implements spec §3 Module 2.

---

## Scope

- In `rust/src/sql_parser.rs` `filter_conditions` and `rust/src/pgsql_parser.rs`
  `pgsql_filter_conditions`:
  - **Keys** → emit as a safely double-quoted identifier; reject keys that aren't
    valid identifiers (after stripping known QS suffix/operator tokens).
  - **Operators** → accept only from an explicit allowlist of comparison/keyword
    tokens; reject anything else.
  - **Values** (scalar, list/IN, BETWEEN, dict-comparison) → escape via
    `validators::escape_string`/`quote_string`; reject SQL-shaped tokens.
- Reuse `rust/src/filter_common.rs` helpers where shared logic already exists.
- Add Rust unit tests covering malicious key, malicious operator, and malicious
  list/BETWEEN value.

**NOT in scope**: the Cython fallback (TASK-708), `safe_format_map_validated`
(TASK-704), provider wiring (TASK-707). Do not change the public Python signature of
`filter_conditions`.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `rust/src/sql_parser.rs` | MODIFY | Escape/reject key, operator, value sites in `filter_conditions` |
| `rust/src/pgsql_parser.rs` | MODIFY | Same for `pgsql_filter_conditions` |
| `rust/src/filter_common.rs` | MODIFY (if needed) | Shared escape/allowlist helpers |

---

## Codebase Contract (Anti-Hallucination)

> Verified 2026-06-19 on `dev`.

### Existing Signatures to Use
```rust
// rust/src/validators.rs
pub fn escape_string(value: &str) -> String { }                      // line 138
pub fn quote_string(value: &str, no_dblquoting: bool) -> String { }  // line 164
pub fn is_valid(key: &str, value: &str, type_hint: Option<&str>, noquote: bool) -> String { } // line 257
pub fn field_components(...) { }                                     // (registered lib.rs:43)

// rust/src/lib.rs — already registered (no new registration needed)
m.add_function(wrap_pyfunction!(sql_parser::filter_conditions, m)?)?;     // line 58
m.add_function(wrap_pyfunction!(pgsql_parser::pgsql_filter_conditions, m)?)?; // line 66
```

### Cython callers (for behavioral parity reference — DO NOT edit here)
```python
# querysource/parsers/sql.pyx:118-119  → return _rs.filter_conditions(sql, dict(self.filter), dict(self.cond_definition))
# querysource/parsers/pgsql.pyx:36     → pgsql variant
```

### Does NOT Exist
- ~~a single "sanitize_where" entrypoint~~ — hardening is per interpolation site
  inside the existing functions.
- ~~changes to the Python-facing argument shape~~ — keep `(sql, filter, cond_def)`.

---

## Implementation Notes

### Key Constraints
- This is the **production** path (`HAS_RUST` true). Correctness here is the primary
  non-raw-slug fix; the Cython fallback (TASK-708) only matters when Rust is absent.
- Default-deny: reject ambiguous operators/keys rather than passing them through.
- Preserve output for legitimate filters (golden test against representative slugs).

### References in Codebase
- `rust/src/filter_common.rs:216-242` — existing `safe_format_map_rust` cleanup usage.
- `querysource/parsers/sql.pyx:122-198` — the Cython logic being mirrored (key-quote,
  operator, list/BETWEEN/value branches).

---

## Acceptance Criteria

- [ ] Malicious condition **key** (identifier breakout via `"`) is identifier-quoted
      or rejected — not interpolated raw.
- [ ] Operators are allowlisted; unknown operators rejected.
- [ ] list/IN, BETWEEN, and dict-comparison values are escaped or rejected.
- [ ] Legitimate filters produce identical SQL to pre-fix (golden test).
- [ ] `cargo test` passes; `maturin build` succeeds.

---

## Test Specification

```rust
#[test]
fn rejects_malicious_key() {
    // key carries an identifier breakout
    let r = filter_conditions_rust("SELECT * FROM t {where_cond}",
        &map(&[("client_slug\" IS NULL UNION SELECT version()--", "1")]), &map(&[]));
    assert!(r.is_err() || !r.unwrap().contains("UNION SELECT version()"));
}

#[test]
fn escapes_in_list_values() {
    let r = filter_conditions_rust("SELECT * FROM t {where_cond}",
        &map_list(&[("status", vec!["a';DROP--", "b"])]), &map(&[])).unwrap();
    assert!(!r.contains("DROP--") || r.contains("''"));  // escaped
}
```

---

## Agent Instructions

1. Read the spec; verify the contract against `rust/src/*_parser.rs`.
2. Update index → `in-progress`.
3. Implement; reuse `validators` + `filter_common`. Keep Python signatures stable.
4. `cargo test` + `maturin develop`.
5. Move to `completed/`, update index, fill Completion Note.

---

## Completion Note

**Completed by**: <id>
**Date**: YYYY-MM-DD
**Notes**:
**Deviations from spec**: none
