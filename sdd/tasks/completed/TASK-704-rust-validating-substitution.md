# TASK-704: Rust context-aware validating substitution (`safe_format_map_validated`)

**Feature**: FEAT-103 — Malforming Query-Slug Issue (SQLi & Datasource Credential Exposure)
**Spec**: `sdd/specs/malforming-queryslug-issue.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Root cause of V1 (SQL injection): raw-slug SQL templates are filled with untrusted
request conditions via Python `str.format_map(SafeDict(...))` — no escaping. This task
adds the secure replacement in Rust: a context-aware substitution that validates and
escapes each interpolated value/key for its placeholder context, or rejects.
Implements spec §2 (Overview, A2) and §3 Module 1.

The existing `safe_format_map` MUST be left unchanged (it has ~10 internal callers that
assemble already-built SQL fragments; escaping there would corrupt them).

---

## Scope

- Add a new Rust `#[pyfunction] safe_format_map_validated(template, conditions, cond_definition)`
  in `rust/src/safe_dict.rs` that:
  - walks `template`, locating each `{key}` placeholder (reuse the existing
    `safe_format_map_rust` scanning logic for placeholder detection);
  - detects the placeholder's surrounding context: **bare** (`= {x}`), inside a
    **single-quoted literal** (`'{x}'`), or inside a **double-quoted identifier**
    (`"{x}"`);
  - for each value, calls the existing `validators::is_valid` / `escape_string` /
    `quote_string` to produce a correctly escaped/quoted token for that context;
  - **rejects** (returns `PyResult::Err` → Python exception) any value/key that fails
    validation (contains comment markers `--` `/*`, stacked-statement `;`, unbalanced
    quotes, SQL keywords like `UNION`/`SELECT` where a scalar is expected, or control
    chars). Default-deny on ambiguity.
  - uses `cond_definition[key]` (if present) as the `type_hint` for `is_valid`;
    falls back to "quoted literal + reject SQL-shaped tokens" when absent.
- Register the function in `rust/src/lib.rs` `#[pymodule]` (alongside line 55).
- Add Rust unit tests + `proptest` fuzz tests in `rust/src/safe_dict.rs` covering the
  PoC payloads and the three placeholder contexts.

**NOT in scope**: the Python re-export (TASK-706), provider wiring (TASK-707),
`filter_conditions` hardening (TASK-705), Cython fallback (TASK-708), datasource (TASK-709).
Do NOT modify `safe_format_map`/`safe_format_map_rust` behavior.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `rust/src/safe_dict.rs` | MODIFY | Add `safe_format_map_validated` + tests; keep `safe_format_map` unchanged |
| `rust/src/lib.rs` | MODIFY | Register `safe_dict::safe_format_map_validated` in `#[pymodule]` |
| `rust/Cargo.toml` | MODIFY | Add `proptest` as a dev-dependency |

---

## Codebase Contract (Anti-Hallucination)

> Verified 2026-06-19 on `dev`.

### Existing Signatures to Use
```rust
// rust/src/validators.rs — REUSE these (do not reimplement escaping)
pub fn escape_string(value: &str) -> String { }                      // line 138
pub fn quote_string(value: &str, no_dblquoting: bool) -> String { }  // line 164
pub fn is_valid(key: &str, value: &str, type_hint: Option<&str>, noquote: bool) -> String { } // line 257
pub fn is_pgconstant(value: &str) -> bool { }                        // line 85
pub fn is_pg_function(value: &str) -> bool { }                       // line 92

// rust/src/safe_dict.rs — placeholder scanner to reuse for detection (DO NOT change behavior)
#[pyfunction]
pub fn safe_format_map(template: &str, replacements: &Bound<'_, PyDict>) -> String { } // line 22
pub fn safe_format_map_rust(template: &str, replacements: &HashMap<String, String>) -> String { } // line 35
//   - only replaces `{key}` where key matches `[A-Za-z0-9_]+`; leaves unmatched intact.

// rust/src/lib.rs — registration site (#[pymodule] starts line 33)
m.add_function(wrap_pyfunction!(safe_dict::safe_format_map, m)?)?;   // line 55  (register new fn near here)
```

### Does NOT Exist
- ~~`safe_dict::safe_format_map_validated`~~ — this task creates it.
- ~~any escaping inside `safe_format_map`/`safe_format_map_rust`~~ — they do plain,
  no-escape substitution; do NOT add escaping to them.
- ~~`validators::sanitize` / `validators::reject_injection`~~ — not present; build
  rejection logic from `is_valid` + explicit denylist checks.

---

## Implementation Notes

### Pattern to Follow
- PyO3 modern API: `#[pyfunction]`, accept `&Bound<'_, PyDict>` for `conditions` and
  `cond_definition`, return `PyResult<String>`; map rejection via
  `PyValueError::new_err(...)` (see `.claude/rules/rust-development.md`).
- Mirror the placeholder-scan loop in `safe_format_map_rust` (lines 35+) for `{key}`
  detection; extend it to inspect the byte immediately before `{` and after `}` to
  classify context (`'` → literal, `"` → identifier, else bare).
- When emitting into a literal context that already has surrounding quotes in the
  template, emit the **escaped inner** value (no extra quotes); when bare, emit a
  fully quoted token via `quote_string`. Keep this consistent and unit-tested.

### Key Constraints
- Security-critical: default-deny. Prefer rejecting a borderline value over emitting
  unsafe SQL. Never echo the offending value in the error message.
- Keep `safe_format_map` byte-for-byte behavior intact (a regression test asserts this).

### References in Codebase
- `rust/src/sql_parser.rs:270-297` — example of `safe_format_map_rust` usage (trusted fragments).

---

## Acceptance Criteria

- [ ] `safe_format_map_validated` rejects all PoC payloads (identifier breakout `"...`,
      `' UNION SELECT version()--`, `pg_database`/`pg_user` enumeration).
- [ ] Valid scalar values substitute to the same SQL the legacy path produced (golden).
- [ ] Correct escaping in `'{x}'`, `"{x}"`, and bare `{x}` contexts (unit tests).
- [ ] `safe_format_map` output unchanged for `{filter}`/`{fields}`/`{table}` inputs.
- [ ] `cargo test` passes (incl. proptest); `maturin build` succeeds.
- [ ] Function registered and importable after rebuild (verified in TASK-706).

---

## Test Specification

```rust
// rust/src/safe_dict.rs  (#[cfg(test)])
#[test]
fn rejects_identifier_breakout() {
    let r = safe_format_map_validated_rust(
        "SELECT a FROM t WHERE \"{k}\" = 1",
        &map(&[("k", "client_slug\" IS NULL UNION SELECT version()--")]),
        &map(&[]),
    );
    assert!(r.is_err());
}

#[test]
fn allows_benign_literal() {
    let r = safe_format_map_validated_rust(
        "WHERE client_slug = '{client_slug}'",
        &map(&[("client_slug", "acme")]),
        &map(&[]),
    ).unwrap();
    assert_eq!(r, "WHERE client_slug = 'acme'");
}

#[test]
fn safe_format_map_unchanged() {
    let mut m = HashMap::new();
    m.insert("filter".into(), "WHERE x = 'a'".into());
    assert_eq!(safe_format_map_rust("SELECT * FROM t {filter}", &m),
               "SELECT * FROM t WHERE x = 'a'");
}
```

---

## Agent Instructions

1. Read the spec for full context.
2. Verify the Codebase Contract (grep `rust/src/validators.rs`, `safe_dict.rs`, `lib.rs`).
3. Update index → `in-progress`.
4. Implement; keep the FFI boundary thin, heavy logic in pure Rust.
5. `cargo test` + `maturin develop` to validate locally.
6. Move file to `sdd/tasks/completed/`, update index → `done`, fill Completion Note.

---

## Completion Note

**Completed by**: <id>
**Date**: YYYY-MM-DD
**Notes**:
**Deviations from spec**: none

## Completion Notes
✅ Completed: 2026-06-19T15:38:15+00:00
✅ Status: verified
