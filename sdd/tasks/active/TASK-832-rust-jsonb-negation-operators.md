# TASK-832: Rust JSONB negation operators (`@!`, `@$`) and multi-operator dispatch

**Feature**: FEAT-179 — JSONB NOT (`@!`) and NOT-ALL (`@$`) filter operators
**Spec**: `sdd/specs/jsonb-not-or-operators.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Implements spec Module 2 (§3), the Rust twin of TASK-831. `filter_conditions()`
(pgsql.pyx:209-217) tries this Rust path first and only falls back to Cython
when Rust raises. If Rust renders the old behavior without raising, that result
wins. So the semantics have to land in Rust too, and the extension has to be
rebuilt.

---

## Scope

- Add `"@!"` and `"@$"` to `JSONB_OPERATORS`.
- Add `jsonb_containment_terms`, `jsonb_none_of_condition` and
  `jsonb_not_all_condition` (each returns `PyResult<Option<String>>`).
- Rework the operator branch of `jsonb_condition` to render every operator key
  of the dict, AND the groups, and return `JsonbOutcome::Skip` if any group is
  `None`/`Err`.
- Drop dicts that mix comparison tokens with JSONB tokens (`Skip`, spec S3).
- `cargo check`, rebuild with `make build-rust`, run the existing JSONB tests.

**NOT in scope**: Cython (TASK-831); new pytest cases and the version bump (TASK-833).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `rust/src/pgsql_parser.rs` | MODIFY | tokens, three helpers, multi-operator dispatch |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
No new `use` items. `PyList`, `PyDict`, `PyString`, `Bound`, `PyAny` and
`PyResult` are already in scope (used by `jsonb_any_of_condition`,
rust/src/pgsql_parser.rs:219-235).

### Existing Signatures to Use
```rust
// rust/src/pgsql_parser.rs
const JSONB_OPERATORS: &[&str] = &["@>", "<@", "@>|", "->", "->>"];  // line 82
const JSONB_KEY_SUFFIXES: &[char]                                     // line 86
enum JsonbOutcome { NotJsonb, Skip, Condition(String) }               // line 185
fn jsonb_operand(obj: &Bound<'_, PyAny>) -> PyResult<String>          // line 205
fn jsonb_any_of_condition(col: &str, operand: &Bound<'_, PyAny>) -> PyResult<Option<String>>  // line 219 — template
fn jsonb_path_condition(col: &str, op: &str, operand: &Bound<'_, PyAny>) -> PyResult<Option<String>>  // line 242
fn jsonb_condition(key: &str, dict: &Bound<'_, PyDict>) -> JsonbOutcome  // line 290-340
pub fn pgsql_filter_conditions(...) -> PyResult<String>              // line 625
// pg_literal(&str) -> String and pg_safe_identifier_key(&str) -> Option<String> are used at lines 228 / 320
```

Current operator branch (pgsql_parser.rs:323-339):
```rust
    let rendered = if operators == 0 {
        orjson_dumps(dict.as_any()).map(|j| Some(format!("{} @> {}::jsonb", col, pg_literal(&j))))
    } else if operators != dict.len() {
        Ok(None)
    } else {
        let op: String = op_obj.extract().unwrap_or_default();
        match op.as_str() {
            "@>" | "<@" => jsonb_operand(&operand)
                .map(|j| Some(format!("{} {} {}::jsonb", col, op, pg_literal(&j)))),
            "@>|" => jsonb_any_of_condition(&col, &operand),
            _ => jsonb_path_condition(&col, &op, &operand),
        }
    };
    match rendered {
        Ok(Some(cond)) => JsonbOutcome::Condition(cond),
        _ => JsonbOutcome::Skip,
    }
```

### Does NOT Exist
- ~~`jsonb_none_of_condition` / `jsonb_not_all_condition` / `jsonb_containment_terms` (Rust)~~ — this task creates them.
- ~~Rust unit tests for the JSONB helpers~~ — the helpers need the GIL (`Bound<PyAny>`). Coverage comes from pytest (TASK-833), not `cargo test`.
- ~~`maturin develop` from the repo root without `--manifest-path`~~ — use `make build-rust` (Makefile:63).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "rust/src/pgsql_parser.rs",
      "action": "MODIFY"
    }
  ],
  "contract_symbols": [
    "sym:rust/src/pgsql_parser.rs#pgsql_filter_conditions"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- The output must match TASK-831 byte for byte. The normative table is spec §2.
  The pytest cases in TASK-833 run the same expected strings through both paths.
- Build every term with `jsonb_operand(&item)?` and then `pg_literal(..)`.
- Single-operator output must not change.
- Fail closed: if any group returns `Ok(None)` or `Err`, return `JsonbOutcome::Skip`.

---

## Implementation Blueprint

### Steps (in order)
1. Extend `JSONB_OPERATORS`. *Why*: `is_operator` counts only these keys.
2. Insert the three helpers above `jsonb_path_condition`. *Why*: they belong
   next to the `@>|` helper they mirror.
3. Replace the `else { match op ... }` arm with a loop over `dict.iter()`.
   *Why*: the spec requires multi-operator dicts.
4. Add the mixed-class rule (S3).
5. Run `cargo check --manifest-path rust/Cargo.toml`, then `make build-rust`,
   then the Validation Commands. *Why*: without a rebuild, Python keeps loading
   the old `.so`.

### `rust/src/pgsql_parser.rs` (MODIFY) — tokens
```rust
// occurrences: 1 (verified: grep -c 'const JSONB_OPERATORS: &[&str] = &["@>", "<@", "@>|", "->", "->>"];' rust/src/pgsql_parser.rs)
// REPLACE line (verified: rust/src/pgsql_parser.rs:82)
const JSONB_OPERATORS: &[&str] = &["@>", "<@", "@>|", "@!", "@$", "->", "->>"];
```

### `rust/src/pgsql_parser.rs` (MODIFY) — helpers
```rust
// occurrences: 1 (verified: grep -c "fn jsonb_path_condition(col: &str, op: &str, operand: &Bound<'_, PyAny>) -> PyResult<Option<String>> {" rust/src/pgsql_parser.rs)
// BEFORE — insert above the `/// Render {"->>": ...}` doc comment of jsonb_path_condition (verified: rust/src/pgsql_parser.rs:237-242)

/// Render each item of a list operand as `col @> '<json>'::jsonb`.
/// Returns `None` when the operand is not a non-empty list.
fn jsonb_containment_terms(col: &str, operand: &Bound<'_, PyAny>) -> PyResult<Option<Vec<String>>> {
    let Ok(items) = operand.cast::<PyList>() else {
        return Ok(None);
    };
    if items.is_empty() {
        return Ok(None);
    }
    let mut parts: Vec<String> = Vec::with_capacity(items.len());
    for item in items.iter() {
        parts.push(format!("{} @> {}::jsonb", col, pg_literal(&jsonb_operand(&item)?)));
    }
    Ok(Some(parts))
}

/// Render `{"@!": [a, b, ...]}` as `NOT (col @> a OR col @> b ...)` (none-of).
/// An empty or non-list operand yields `None`: the condition is dropped.
fn jsonb_none_of_condition(col: &str, operand: &Bound<'_, PyAny>) -> PyResult<Option<String>> {
    Ok(jsonb_containment_terms(col, operand)?.map(|parts| format!("NOT ({})", parts.join(" OR "))))
}

/// Render `{"@$": [a, b, ...]}` as `((NOT col @> a) OR (NOT col @> b) ...)` (not-all).
/// One operand renders `NOT (col @> a)`. An empty or non-list operand yields
/// `None`: the condition is dropped (no filter, not a logical FALSE).
fn jsonb_not_all_condition(col: &str, operand: &Bound<'_, PyAny>) -> PyResult<Option<String>> {
    Ok(jsonb_containment_terms(col, operand)?.map(|parts| {
        if parts.len() == 1 {
            format!("NOT ({})", parts[0])
        } else {
            let negated: Vec<String> = parts.iter().map(|p| format!("(NOT {})", p)).collect();
            format!("({})", negated.join(" OR "))
        }
    }))
}

/// Render one `op: operand` pair of a JSONB filter dict.
fn jsonb_operator_condition(col: &str, op: &str, operand: &Bound<'_, PyAny>) -> PyResult<Option<String>> {
    match op {
        "@>" | "<@" => jsonb_operand(operand)
            .map(|j| Some(format!("{} {} {}::jsonb", col, op, pg_literal(&j)))),
        "@>|" => jsonb_any_of_condition(col, operand),
        "@!" => jsonb_none_of_condition(col, operand),
        "@$" => jsonb_not_all_condition(col, operand),
        _ => jsonb_path_condition(col, op, operand),
    }
}
```
**Why**: `jsonb_containment_terms` validates the operand the same way `jsonb_any_of_condition` does
(`cast::<PyList>`; tuples are not accepted in Rust today, so do not add them
here). `jsonb_any_of_condition` stays unchanged so `@>|` output is
byte-identical.

### `rust/src/pgsql_parser.rs` (MODIFY) — dispatch
```rust
// occurrences: 1 (verified: grep -c '            "@>|" => jsonb_any_of_condition(&col, &operand),' rust/src/pgsql_parser.rs)
// REPLACE the final `} else { let op ... match op.as_str() { ... } }` arm of `let rendered`
//   (rust/src/pgsql_parser.rs:327-334; the "@>|" anchor at :332 sits inside it)
    } else {
        // FILL IN: mixed-class rule — if any key extracts to a COMPARISON_TOKENS member,
        //   evaluate to Ok(None) (→ Skip). Only reachable when the first key is a JSONB
        //   token. Bounded by spec §2 / S3; must match TASK-831.
        let mut groups: Vec<String> = Vec::with_capacity(dict.len());
        let mut failed = false;
        for (op_obj, operand) in dict.iter() {
            let op: String = op_obj.extract().unwrap_or_default();
            match jsonb_operator_condition(&col, &op, &operand) {
                Ok(Some(cond)) => groups.push(cond),
                _ => {
                    failed = true;
                    break;
                }
            }
        }
        if failed {
            Ok(None)
        } else if groups.len() == 1 {
            Ok(groups.pop())
        } else {
            Ok(Some(format!("({})", groups.join(" AND "))))
        }
    };
```
**Why**: the `match rendered` that follows already maps `Ok(None)` and `Err`
to `Skip`, which keeps the code fail-closed. Leave the earlier
`operators == 0` and `operators != dict.len()` arms unchanged, because they
still drop dicts that mix operators with plain keys. Update the
`jsonb_condition` doc comment (lines 276-289) to list `@!`, `@$` and the
AND-of-groups rule. The `operand` binding from `dict.iter().next()` at
line 300 may become unused in this arm; remove it only if the compiler warns.

### FILL IN checklist
- [ ] mixed-class rule (S3), the same as TASK-831.
- [ ] `jsonb_condition` doc comment updated.
- [ ] `cargo check` is clean with no new warnings.

---

## Acceptance Criteria

- [ ] Rendered SQL matches spec §2 for `@!`, `@$` and combined dicts on the Rust path.
- [ ] Empty, non-list and invalid operands, and mixed comparison/JSONB dicts, all produce `Skip`.
- [ ] `cargo check --manifest-path rust/Cargo.toml` and `cargo test --manifest-path rust/Cargo.toml` pass.
- [ ] Extension rebuilt with `make build-rust`. `python -c "from querysource.parsers import pgsql; assert pgsql.HAS_RUST"` succeeds.
- [ ] Existing `tests/test_pgsql_jsonb_filters.py` cases pass on the rust path with expected strings unchanged.

---

## Validation Commands

- `pytest tests/test_pgsql_jsonb_filters.py -q`

---

## Test Specification

Coverage is in TASK-833 (dual-path parametrized). Quick smoke check after the rebuild:
```python
from querysource.parsers import pgsql
pgsql._rs.pgsql_filter_conditions("SELECT * FROM t {where_cond}",
                                  {"g": {"@$": [{"a": 1}, {"b": 2}]}}, {})
```

---

## Agent Instructions

1. Work in the feature worktree (`python -m scripts.sdd.ensure_worktree --slug jsonb-not-or-operators --feature-id FEAT-179`).
2. Read spec §2 and §6, and check this contract against the current source.
3. Set the status to `"in-progress"` in `sdd/tasks/index/jsonb-not-or-operators.json`.
4. Implement from the blueprint, then run `cargo check`, `make build-rust` and the Validation Commands.
5. Commit only `rust/src/pgsql_parser.rs`.
6. Close with `scripts/sdd/close_task.sh TASK-832 jsonb-not-or-operators verified`.

---

## Completion Note

*(Agent fills this in when done)*
