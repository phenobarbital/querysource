# TASK-877: Idempotent `quote_string`/`to_string` in Rust; builders stop pre-stripping quotes

**Feature**: FEAT-167 — WHERE value hardening — `is_valid()` closure and idempotent literal quoting
**Spec**: `sdd/specs/where-value-hardening.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-875
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 3 (Rust half) / goal G4. On the Rust path the pgsql and
generic SQL builders quote every scalar with
`quote_string(&escape_string(v), true)` (`rust/src/pgsql_parser.rs` ×7,
`rust/src/sql_parser.rs` ×6). `escape_string` (`validators.rs:138`) deletes
`'` and `"`, so `{"name": "O'Brien"}` renders `name='OBrien'` — lossy, and
different from the Cython path. `quote_string` (`validators.rs:164`) also
strips a lone leading/trailing quote and re-doubles without undoing `''`.
This task adds `unquote_literal` (same grammar as TASK-876), makes
`quote_string` and `to_string` idempotent, and replaces the 13 builder call
sites with plain `quote_string(v, true)`. Depends on TASK-875 only because
both edit `validators.rs`.

---

## Scope

- `validators.rs`: `pub fn unquote_literal(&str) -> Option<String>`;
  `quote_string` unwraps via it before escaping; `to_string` =
  `quote_string(value, true)`; new `#[test]`s.
- `pgsql_parser.rs`: the 7 sites (499, 520, 581, 589-590, 602, 667, 674) use
  `quote_string(v, true)`; the BETWEEN bounds (589-590) use `quote_string`
  and the format string drops its hand-written `'{}'`.
- `sql_parser.rs`: the 6 sites (64, 332, 368, 398, 402, 405).
- `tests/unit/test_rust_literal_quoting.py`.

**NOT in scope**: `escape_string` itself (still used by the `literal` hint
and `filter_common.rs:201`); `bq_quote_string` and the ILIKE/`pg_literal`
branches (already idempotent); the Cython side (TASK-876); allowlists
(TASK-875).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `rust/src/validators.rs` | MODIFY | `unquote_literal` (new), `quote_string` (`:164-200`), `to_string` (`:232-235`), tests |
| `rust/src/pgsql_parser.rs` | MODIFY | 7 quoting sites |
| `rust/src/sql_parser.rs` | MODIFY | 6 quoting sites |
| `tests/unit/test_rust_literal_quoting.py` | CREATE | Python-facing idempotency tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.qs_parsers import _qs_parsers as rs   # exposes quote_string(value, no_dblquoting=True), to_string, escape_string, is_valid — verified: rust/src/lib.rs:48-51
from querysource.types.validators import Entity         # verified: validators.pyx:489 (idempotent after TASK-876 — parity test only when both are merged; see Notes)
```
```rust
use crate::validators::{escape_string, quote_string};   // pgsql_parser.rs / sql_parser.rs already import these (verified by the call sites below)
```

### Existing Signatures to Use
```rust
// rust/src/validators.rs (line numbers at base 82214b25; TASK-875 shifts them — re-grep)
pub fn escape_string(value: &str) -> String            // line 138 — `'"' | '\'' => {}` strips quotes; UNCHANGED
pub fn quote_string(value: &str, no_dblquoting: bool) -> String   // line 164; body 165-200:
//   "None"→"''"; null/NULL passthrough; True/true/False/false passthrough; `"`→`'` when no_dblquoting;
//   strip leading/trailing ' (lone ones too); escaped = inner.replace('\'', "''"); format!("'{escaped}'")  — REPLACE strip step
pub fn bq_quote_string(value: &str) -> String          // line 214 — reference unwrap (`[1..len-1].replace("''", "'")`); UNCHANGED
pub fn to_string(value: &str) -> String { quote_string(&escape_string(value), true) }  // line 232-235 — REPLACED
#[cfg(test)] mod tests                                  // line 348; test_quote_string_* at 379-402 (already_quoted: quote_string("'hello'")=="'hello'"; internal: "it's"→"'it''s'")

// rust/src/pgsql_parser.rs — all 7 sites (verified: grep -c 'quote_string(&escape_string(' = 7, plus bare escape_string at 589-590):
//   499  .map(|(op, v)| format!("{} {} {}", key, op, quote_string(&escape_string(&v.as_str()), true)))   (comparison dict)
//   520  let safe_v = quote_string(&escape_string(&v.as_str()), true);                                   (COMPARISON_TOKENS)
//   581  let safe_v = quote_string(&escape_string(&items[1].as_str()), true);                            (VALID_OPERATORS list)
//   589  let v0 = escape_string(&items[0].as_str());  590 let v1 = escape_string(&items[1].as_str());    (date BETWEEN; formatted as '{}' AND '{}' at 592/594)
//   602  .map(|v| quote_string(&escape_string(&v.as_str()), true))                                        (IN list)
//   667  format!("{} != {}", name, quote_string(&escape_string(value), true))                             (end == "!")
//   674  quote_string(&escape_string(&value[1..]), true)                                                  (leading !)
// rust/src/sql_parser.rs — 6 sites (verified: grep -c = 6):
//   64   _ => quote_string(&escape_string(value), true),            (safe_scalar_value)
//   332  .map(|v| quote_string(&escape_string(v), true))            (IN list)
//   368  quote_string(&escape_string(&s_str), true)                 (equality from PyAny str)
//   398  quote_string(&escape_string(value), true)                  (end == "!")
//   402  quote_string(&escape_string(&value[1..]), true)            (leading !)
//   405  quote_string(&escape_string(value), true)                  (equality)
// rust/src/pgsql_parser.rs:528-556 — PG_TEXT_OPERATORS branch: strip + replace("''","'") + pg_literal — UNCHANGED (already idempotent)
// rust/src/filter_common.rs:201 — escape_string(&value[1..]) — UNCHANGED (out of scope)

# Python pins to keep green: tests/test_rust_parsers.py:101-127 (quote_string basics, already_quoted, it's, null, None, True/false, '"hello"')
```

### Does NOT Exist
- ~~`unquote_literal` in Rust~~ — created here.
- ~~`pg_literal` in validators.rs~~ — lives in `pgsql_parser.rs` (ILIKE branch); not a general quoter.
- ~~a `quote_literal`/`safe_literal` helper~~ — not introduced; the idempotent `quote_string` IS the helper.
- ~~`sql_parser.rs` BETWEEN with bare `escape_string`~~ — only `pgsql_parser.rs:589-590` has that shape.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "rust/src/validators.rs", "action": "MODIFY"},
    {"path": "rust/src/pgsql_parser.rs", "action": "MODIFY"},
    {"path": "rust/src/sql_parser.rs", "action": "MODIFY"},
    {"path": "tests/unit/test_rust_literal_quoting.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:rust/src/validators.rs#quote_string",
    "sym:rust/src/validators.rs#to_string",
    "sym:rust/src/validators.rs#escape_string",
    "sym:rust/src/validators.rs#bq_quote_string"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Grammar identical to TASK-876's `unquote_literal` (spec §3 M3). Property:
  `quote_string(quote_string(x)) == quote_string(x)` and
  `quote_string(x) == "'" + x.replace("'", "''") + "'"` for raw `x`.
- Behaviour change to accept (same as Cython): a lone leading/trailing `'`
  is no longer stripped; only well-formed literals unwrap.
- `escape_string` stays and still strips quotes — do not route literal
  values through it any more; that is the whole point.
- Rebuild + stage (`make build-rust && make stage-rust`); `cargo test
  --no-default-features` in `rust/` (4 pre-existing failures baseline).
- The Rust/Cython parity assertion (`rs.quote_string(x) == Entity.quoteString(x)`)
  only holds once TASK-876 is merged too; keep it in TASK-878's e2e file,
  not here.

### References in Codebase
- `rust/src/validators.rs:214-226` — `bq_quote_string` unwrap shape.
- `rust/src/pgsql_parser.rs:528-556` — strip-then-requote comments (why idempotency matters).

---

## Implementation Blueprint

### Steps (in order)
1. Add `unquote_literal` above `quote_string`; replace `quote_string`'s strip step and `to_string`'s body — *why*: G4 in the engine that renders most production queries.
2. In `pgsql_parser.rs` and `sql_parser.rs` replace every `quote_string(&escape_string(X), true)` with `quote_string(X, true)` (13 sites) — *why*: `escape_string` deletes apostrophes; `quote_string` now escapes correctly on its own.
3. In `pgsql_parser.rs:586-596` (date BETWEEN) replace the two `escape_string` bounds with `quote_string(..., true)` and drop the literal `'{}'` quotes from the two `format!` strings — *why*: otherwise the bound would be quoted twice.
4. Add the `#[test]`s; `cargo test --no-default-features`; `make build-rust && make stage-rust`; Validation Commands.

### `rust/src/validators.rs` (MODIFY — `unquote_literal` + `quote_string`)
```rust
// occurrences: 1 (verified: grep -c 'pub fn quote_string(value: &str, no_dblquoting: bool) -> String {' rust/src/validators.rs)
// BEFORE — insert above the doc comment of quote_string (`/// Quote a string value for SQL queries.`, verified: validators.rs:158):
/// Inner text of a well-formed pre-quoted SQL literal, or None.
///
/// Well-formed: len >= 2, starts and ends with `'`, every interior `'` is part
/// of a `''` pair. The doubling is undone (`'O''Brien'` → `O'Brien`). Mirrors
/// `querysource/types/validators.pyx::unquote_literal` (FEAT-167 G4).
pub fn unquote_literal(value: &str) -> Option<String> {
    let bytes = value.as_bytes();
    if bytes.len() < 2 || bytes[0] != b'\'' || bytes[bytes.len() - 1] != b'\'' {
        return None;
    }
    let inner = &value[1..value.len() - 1];
    if inner.replace("''", "").contains('\'') {
        return None;
    }
    Some(inner.replace("''", "'"))
}

// REPLACE inside quote_string the block from `let start_quote = v.starts_with('\'');` through
// `let escaped = inner.replace('\'', "''");` (verified: validators.rs:183-198) with:
    // Idempotent: unwrap a well-formed pre-quoted literal, then escape from scratch.
    let inner = unquote_literal(&v).unwrap_or(v);
    let escaped = inner.replace('\'', "''");
```
**Why this shape**: the prologue (`None`, null, booleans, `"`→`'`) is untouched; only the lossy strip becomes the grammar-checked unwrap. `v` is already an owned `String` at that point, so `unwrap_or(v)` moves it.

### `rust/src/validators.rs` (MODIFY — `to_string` + tests)
```rust
// occurrences: 1 (verified: grep -c 'pub fn to_string(value: &str) -> String {' rust/src/validators.rs)
// REPLACE the body of to_string (verified: validators.rs:232-235) with:
pub fn to_string(value: &str) -> String {
    quote_string(value, true)
}

// occurrences: 1 (verified: grep -c 'fn test_quote_string_internal_quotes() {' rust/src/validators.rs)
// AFTER — insert below the closing `}` of test_quote_string_internal_quotes (verified: validators.rs:392):
    #[test]
    fn test_unquote_literal_grammar() {
        assert_eq!(unquote_literal("'O''Brien'"), Some("O'Brien".to_string()));
        assert_eq!(unquote_literal("''"), Some(String::new()));
        assert_eq!(unquote_literal("O'Brien"), None);
        assert_eq!(unquote_literal("'a'b'"), None);
        assert_eq!(unquote_literal("'"), None);
        assert_eq!(unquote_literal("'abc"), None);
    }

    #[test]
    fn test_quote_string_idempotent() {
        for raw in ["O'Brien", "plain", "it''s", "50%", "a\\b", "x) OR (1=1"] {
            let once = quote_string(raw, true);
            assert_eq!(once, format!("'{}'", raw.replace('\'', "''")));
            assert_eq!(quote_string(&once, true), once);
        }
        assert_eq!(to_string("O'Brien"), "'O''Brien'");
    }
```
**Why**: cargo-level guard for the property; `test_quote_string_already_quoted` (line 384) keeps passing.

### `rust/src/pgsql_parser.rs` (MODIFY)
```rust
// occurrences: 7 (verified: grep -c 'quote_string(&escape_string(' rust/src/pgsql_parser.rs)
// FILL IN: disambiguate — apply the same textual rewrite at each site, quoting its line context:
//   :499  `quote_string(&escape_string(&v.as_str()), true)`        → `quote_string(&v.as_str(), true)`
//   :520  `let safe_v = quote_string(&escape_string(&v.as_str()), true);` → `let safe_v = quote_string(&v.as_str(), true);`
//   :581  `let safe_v = quote_string(&escape_string(&items[1].as_str()), true);` → `... quote_string(&items[1].as_str(), true);`
//   :602  `.map(|v| quote_string(&escape_string(&v.as_str()), true))`  → `.map(|v| quote_string(&v.as_str(), true))`
//   :667  `quote_string(&escape_string(value), true)`               → `quote_string(value, true)`
//   :674  `quote_string(&escape_string(&value[1..]), true)`         → `quote_string(&value[1..], true)`
// Date BETWEEN (verified: pgsql_parser.rs:586-596) — REPLACE:
//   let v0 = escape_string(&items[0].as_str());
//   let v1 = escape_string(&items[1].as_str());
//   ... format!("{} NOT BETWEEN '{}' AND '{}'", name, v0, v1) / format!("{} BETWEEN '{}' AND '{}'", name, v0, v1)
// WITH:
    let v0 = quote_string(&items[0].as_str(), true);
    let v1 = quote_string(&items[1].as_str(), true);
    if end == "!" {
        return Some(format!("{} NOT BETWEEN {} AND {}", name, v0, v1));
    } else {
        return Some(format!("{} BETWEEN {} AND {}", name, v0, v1));
    }
// Then remove `escape_string` from this file's `use crate::validators::{...}` if the compiler reports it unused.
```
**Why**: every site is a literal position; `quote_string` now provides the escaping `escape_string` was (wrongly) supplying.

### `rust/src/sql_parser.rs` (MODIFY)
```rust
// occurrences: 6 (verified: grep -c 'quote_string(&escape_string(' rust/src/sql_parser.rs)
// FILL IN: disambiguate — same textual rewrite at :64, :332, :368, :398, :402, :405:
//   `quote_string(&escape_string(X), true)` → `quote_string(X, true)`   (X is `value`, `v`, `&s_str`, `&value[1..]`)
// Then remove `escape_string` from the `use crate::validators::{...}` line if unused.
```
**Why**: parity with the pgsql builder and with the Cython `sql.pyx` builder (which only ever doubled quotes).

### `tests/unit/test_rust_literal_quoting.py` (CREATE)
```python
"""FEAT-167 TASK-877 — Rust quote_string/to_string are idempotent (AC4)."""
from __future__ import annotations

import pytest

from querysource.qs_parsers import _qs_parsers as rs

RAW = ["O'Brien", "plain", "it''s", "50%", "a\\b", "x) OR (1=1", "Acme (US)"]


@pytest.mark.parametrize("raw", RAW)
def test_rust_quote_string_idempotent(raw: str) -> None:
    once = rs.quote_string(raw, True)
    assert once == "'" + raw.replace("'", "''") + "'"
    assert rs.quote_string(once, True) == once


def test_rust_to_string_keeps_apostrophe() -> None:
    assert rs.to_string("O'Brien") == "'O''Brien'"
    assert rs.is_valid("n", "O'Brien", "string", False) == "'O''Brien'"


def test_rust_quote_string_special_cases_unchanged() -> None:
    assert rs.quote_string("null", True) == "null"
    assert rs.quote_string("None", True) == "''"
    assert rs.quote_string("True", True) == "True"
    # FILL IN: '"hello"' with no_dblquoting=True → "'hello'" (tests/test_rust_parsers.py:124 shape) — bounded by current behaviour
```
**Why**: Python-visible guard independent of the Cython tasks; the builder-level renders are covered end-to-end in TASK-878.

### FILL IN checklist
- [ ] `pgsql_parser.rs` — 7 site rewrites + BETWEEN format strings; bounded by the context quoted above
- [ ] `sql_parser.rs` — 6 site rewrites; bounded by the context quoted above
- [ ] `test_rust_literal_quoting.py::test_rust_quote_string_special_cases_unchanged` — double-quote case

---

## Acceptance Criteria

- [ ] `rs.quote_string("'O''Brien'", True) == "'O''Brien'"` and `rs.quote_string("O'Brien", True) == "'O''Brien'"` (AC4).
- [ ] `grep -c "quote_string(&escape_string(" rust/src/pgsql_parser.rs rust/src/sql_parser.rs` → `0` for both (AC5).
- [ ] `cargo test --no-default-features`: new tests pass, no new failures vs baseline (AC8).
- [ ] `tests/test_rust_parsers.py`, `tests/qsurl/test_pg_ilike.py` green.

---

## Validation Commands

- `pytest tests/unit/test_rust_literal_quoting.py -q`
- `pytest tests/test_rust_parsers.py -q`
- `pytest tests/qsurl/test_pg_ilike.py -q`

---

## Test Specification

See the `tests/unit/test_rust_literal_quoting.py` blueprint block above; Rust unit tests in `validators.rs::tests`.

---

## Agent Instructions

When you pick up this task:

1. **Work in the feature worktree** — never on `base_branch`
   (`python -m scripts.sdd.ensure_worktree --slug where-value-hardening --feature-id FEAT-167`)
2. **Read the spec** at the path listed above for full context
3. **Check dependencies** — TASK-875 must be `"done"` in `sdd/tasks/index/where-value-hardening.json`
4. **Verify the Codebase Contract** — line numbers shift after TASK-875; re-grep every anchor
5. **Update status** in the per-spec index → `"in-progress"` and commit only that index file
6. **Implement** from the Implementation Blueprint; `make build-rust && make stage-rust` before pytest
7. **Verify** — run the Validation Commands and `cargo test --no-default-features`
8. **Commit the code** — stage only the files this task lists (never `target/`)
9. **Close the task** with `scripts/sdd/close_task.sh TASK-877 where-value-hardening verified`
10. **Fill in the Completion Note** below, then commit the staged SDD state

---

## Completion Note

Implemented by seat gpt-5.6-terra (codex), 1 attempt, 471s, merged.
- `rust/src/validators.rs`: `unquote_literal`, idempotent `quote_string`, `to_string`; Rust unit tests.
- `rust/src/pgsql_parser.rs` / `sql_parser.rs`: quoting sites routed through the idempotent helper.
- Added `tests/unit/test_rust_literal_quoting.py`.
- Validation: merge tier 9 passed after rebuilding and staging `_qs_parsers` in the worktree.
- Review: orchestrator scope/diff-stat review, no fixes.
- Closed via close_task.sh (no durable review EvidenceRef for finalize_task).
