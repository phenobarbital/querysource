# TASK-764: Port the qsurl Rust crate into `rust/qsurl/`

**Feature**: FEAT-152 — qsurl: HTSQL-style URL query dialect (chumsky 0.13 + PyO3)
**Spec**: `sdd/specs/qsurl-parser.spec.md`
**Status**: done
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: claude-fable-5-1 (interactive)

---

## Context

Spec §3 Module 1. The qsurl grammar, lowering to the engine-neutral IR, error JSON and
the pyo3 binding already exist as a user-provided reference crate. This task ports it into
the repository as a **separate crate** (`rust/qsurl/`, brainstorm Round 1 decision) whose
pyo3 dependency is optional behind the `python` feature, so `cargo test` needs no Python
interpreter. It is the source of truth the Lark fallback (TASK-766) and the parity corpus
(TASK-767) are measured against.

---

## Scope

- Extract the reference tarball and copy its sources verbatim into `rust/qsurl/`.
- Write `rust/qsurl/Cargo.toml` and `rust/qsurl/pyproject.toml` exactly as the blueprint fixes them.
- Rename the pymodule function from `qsurl` to `_qsurl` in `src/python.rs` (maturin's `module-name` last segment).
- Keep the 11 reference `cargo test`s and add two: `requires_is_declaration_ordered` and `lower_error_json_has_only_kind_and_message`.
- Verify `cargo test` and `cargo run --example parse` work.

**NOT in scope**: building/installing the extension into `.venv` or Makefile/CI wiring (TASK-777);
any Python code (TASK-765); changes to the existing `rust/Cargo.toml` crate.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `rust/qsurl/Cargo.toml` | CREATE | Crate manifest (edition 2024, rust-version 1.88, optional pyo3) |
| `rust/qsurl/pyproject.toml` | CREATE | maturin config, `module-name = "querysource.qsurl._qsurl"` |
| `rust/qsurl/src/lib.rs` | CREATE | Reference `lib.rs` verbatim + 2 new tests |
| `rust/qsurl/src/ast.rs` | CREATE | Reference verbatim |
| `rust/qsurl/src/parser.rs` | CREATE | Reference verbatim |
| `rust/qsurl/src/ir.rs` | CREATE | Reference verbatim |
| `rust/qsurl/src/python.rs` | CREATE | Reference with `#[pymodule] fn _qsurl` |
| `rust/qsurl/examples/parse.rs` | CREATE | Reference verbatim |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
Not applicable (Rust crate). Reference source location, verified at task time:
```text
~/Descargas/qsurl.tar.gz
  sha256 a25c08fed231fcc904f3ec7eefa19feb63f1f27703f370b5edefff301b4bba45
  qsurl/Cargo.toml  qsurl/README.md  qsurl/examples/parse.rs (11 lines)
  qsurl/src/ast.rs (202)  qsurl/src/ir.rs (276)  qsurl/src/lib.rs (297)
  qsurl/src/parser.rs (338)  qsurl/src/python.rs (45)
```

### Existing Signatures to Use
```rust
// reference qsurl/src/lib.rs — public surface (keep verbatim)
pub mod ast; pub mod ir; pub mod parser;
#[cfg(feature = "python")] mod python;
pub struct ParseError { pub offset: usize, pub message: String, pub found: Option<String>, pub expected: Vec<String>, pub pointer: String }
#[serde(tag = "kind", rename_all = "snake_case")] pub enum Error { Parse(ParseError), Lower { message: String } }
impl Error { pub fn to_json(&self) -> String }
pub fn parse(src: &str) -> Result<Query, ParseError>
pub fn parse_to_ir(src: &str) -> Result<serde_json::Value, Error>
pub fn parse_to_json(src: &str) -> Result<String, Error>
// reference qsurl/src/ir.rs — Feature enum, derives Ord in DECLARATION order:
// Select, Alias, Filter, Or, Not, InList, NullCheck, TextMatch, Regex, Functions, Navigation, Sort, Limit, Offset, Distinct
// reference qsurl/src/python.rs — #[pymodule] fn qsurl(...)   ← must become _qsurl
```

```toml
# rust/Cargo.toml (existing crate, DO NOT MODIFY) — values to mirror
pyo3 = { version = "0.29" }                      # line 12
[profile.release] opt-level = 3 / lto = true / codegen-units = 1   # lines 31-34
# no [workspace] table → a nested crate at rust/qsurl/ is independent
# rust/pyproject.toml — requires = ["maturin>=1.15,<2.0"], bindings = "pyo3"
```

Behaviour observed by running the reference crate at task time (pin these in the new tests):
```text
IR object keys are serialised ALPHABETICALLY (serde_json default Map = BTreeMap):
  {"distinct":..,"fields":..,"filter":..,"limit":..,"offset":..,"requires":..,"slug":..,"sort":..}
  leaf: {"column":..,"dtype"?:..,"expression":..,"value"?:..}; alias field: {"alias":..,"column":..}
`requires` keeps Feature DECLARATION order: ["select","alias","filter","null_check","sort","limit"]
Parse error JSON (struct order): {"kind":"parse","offset":18,"message":"unknown pipeline operator `:order`; expected one of: :sort, :top, :limit, :skip, :offset, :distinct","found":null,"expected":[],"pointer":"stores?state='CA':order(name)\n                  ^"}
Lower error JSON: {"kind":"lower","message":"operator `contains` does not accept a list; use `=` or `!=` for membership"}
Floats: 1.50 → 1.5 ; ints stay ints; `cargo test --lib` → 11 passed
```

### Does NOT Exist
- ~~`rust/qsurl/`~~ — created by this task.
- ~~a Cargo `[workspace]`~~ in `rust/Cargo.toml` — do not add one; the crates stay independent.
- ~~`pythonize`~~ — the FFI is a JSON string; do not add it.
- ~~`default = ["python"]`~~ — the default feature set is EMPTY so `cargo test` never links libpython (the existing crate needs `--no-default-features` for that; this one must not).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "rust/qsurl/Cargo.toml", "action": "CREATE"},
    {"path": "rust/qsurl/pyproject.toml", "action": "CREATE"},
    {"path": "rust/qsurl/src/lib.rs", "action": "CREATE"},
    {"path": "rust/qsurl/src/ast.rs", "action": "CREATE"},
    {"path": "rust/qsurl/src/parser.rs", "action": "CREATE"},
    {"path": "rust/qsurl/src/ir.rs", "action": "CREATE"},
    {"path": "rust/qsurl/src/python.rs", "action": "CREATE"},
    {"path": "rust/qsurl/examples/parse.rs", "action": "CREATE"}
  ],
  "contract_symbols": []
}
```

---

## Implementation Notes

### Key Constraints
- Copy the reference sources byte-for-byte except the one rename in `python.rs` and the two appended tests — every later task's parity depends on this crate's exact output.
- `cargo test` must not need `--no-default-features` or a Python interpreter.
- The first build downloads `chumsky` (not in the local cargo cache); network access is required once.

### References in Codebase
- `rust/Cargo.toml`, `rust/pyproject.toml` — manifest shape to mirror.

---

## Implementation Blueprint

### Steps (in order)
1. Verify the tarball hash, then extract: `sha256sum ~/Descargas/qsurl.tar.gz` must print `a25c08fe…4bba45`; `tar -xzf ~/Descargas/qsurl.tar.gz -C /tmp/qsurl-ref` — *why*: the port must be of the exact reviewed reference; a different tarball invalidates the spec's contract. If the file is missing or the hash differs, STOP and report — do not reconstruct the crate from memory.
2. `mkdir -p rust/qsurl/src rust/qsurl/examples`; copy `src/{ast,parser,ir,lib,python}.rs` and `examples/parse.rs` — *why*: verbatim port is the spec decision (§3 M1).
3. Write the two manifests from the blocks below (do NOT copy the reference `Cargo.toml`) — *why*: the reference lacks `rust-version`, `[lib] name`, the release profile and maturin config.
4. In `rust/qsurl/src/python.rs` rename `fn qsurl(` → `fn _qsurl(` — *why*: pyo3 requires the `#[pymodule]` function name to equal the last segment of `module-name` (`querysource.qsurl._qsurl`), otherwise import fails with "dynamic module does not define module export function".
5. Append the two tests from the `lib.rs` block inside the existing `#[cfg(test)] mod tests` — *why*: pin the `requires` order and the lower-error shape that the Python side must reproduce.
6. Run `cargo test --manifest-path rust/qsurl/Cargo.toml` and the example command — *why*: AC1.

### `rust/qsurl/Cargo.toml` (CREATE)
```toml
[package]
name = "qsurl"
version = "0.1.0"
edition = "2024"
rust-version = "1.88"
description = "qsurl — HTSQL-style URL query dialect parser for QuerySource"

[lib]
name = "qsurl"
crate-type = ["cdylib", "rlib"]

[features]
default = []
python = ["dep:pyo3"]

[dependencies]
chumsky = { version = "0.13", features = ["pratt"] }
pyo3 = { version = "0.29", optional = true, features = ["extension-module"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"

[profile.release]
opt-level = 3
lto = true
codegen-units = 1
```
**Why this shape**: edition 2024 + `rust-version = "1.88"` because `ir.rs` uses let-chains (brainstorm resolution). `rlib` keeps `cargo test` and the example linkable; `cdylib` is what maturin wraps. pyo3 stays on the same 0.29 major as `rust/Cargo.toml:12`.

### `rust/qsurl/pyproject.toml` (CREATE)
```toml
[build-system]
requires = ["maturin>=1.15,<2.0"]
build-backend = "maturin"

[project]
name = "qsurl"
version = "0.1.0"
requires-python = ">=3.10"

[tool.maturin]
module-name = "querysource.qsurl._qsurl"
bindings = "pyo3"
features = ["python", "pyo3/extension-module"]
```
**Why**: mirrors `rust/pyproject.toml`; `features` turns the optional binding on only for maturin builds.

### `rust/qsurl/src/python.rs` (CREATE)
```rust
// Copy reference qsurl/src/python.rs verbatim, then change ONLY this line:
#[pymodule]
fn _qsurl(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parse, m)?)?;
    m.add_function(wrap_pyfunction!(requires, m)?)?;
    Ok(())
}
```
**Why**: see Step 4. Also update the module doc comment's `import qsurl` example to name the installed module `_qsurl` (inside the querysource package) so it is not misleading.

### `rust/qsurl/src/lib.rs` (CREATE)
```rust
// Copy reference qsurl/src/lib.rs verbatim, then append inside `mod tests { ... }`:

    #[test]
    fn requires_is_declaration_ordered() {
        let ir = parse_to_ir("s{a,b:as(c)}?x=1&!y:sort(a):top(1)").unwrap();
        // FILL IN: assert ir["requires"] == json!(["select","alias","filter","null_check","sort","limit"])
        //   — bounded by the Feature enum declaration order (ir.rs), NOT alphabetical
    }

    #[test]
    fn lower_error_json_has_only_kind_and_message() {
        let err = parse_to_json("s?a~('x')").unwrap_err();
        // FILL IN: assert err.to_json() ==
        //   r#"{"kind":"lower","message":"operator `contains` does not accept a list; use `=` or `!=` for membership"}"#
    }
```
**Why**: the Python fallback (TASK-766) and `QSUrlError.from_json` (TASK-765) depend on these two exact shapes.

### `rust/qsurl/src/ast.rs`, `src/parser.rs`, `src/ir.rs`, `examples/parse.rs` (CREATE)
```text
Copy verbatim from the extracted tarball. No edits.
```
**Why**: they are the reviewed grammar and lowering rules reproduced in spec §6 "User-Provided Code".

### FILL IN checklist
- [x] `lib.rs::requires_is_declaration_ordered` — assertion; bounded by the observed order in the Codebase Contract.
- [x] `lib.rs::lower_error_json_has_only_kind_and_message` — exact JSON string assertion.

---

## Acceptance Criteria

- [x] `cargo test --manifest-path rust/qsurl/Cargo.toml` → 13 lib tests pass, no Python linked (spec AC1).
- [x] `cargo run --manifest-path rust/qsurl/Cargo.toml --example parse -- "/queries/x{a}?a=1:top(1)"` prints the IR.
- [x] `cargo build --manifest-path rust/qsurl/Cargo.toml --features python` compiles.
- [x] `grep -c 'fn _qsurl(' rust/qsurl/src/python.rs` → 1.
- [x] `rust/qsurl/target/` is not committed (covered by `.gitignore:107` `target/`).

---

## Validation Commands

> This task has no Python code; its gate is cargo. The pytest command below guards
> that the existing Rust extension tests still pass (the old crate is untouched).

- `pytest tests/test_rust_parsers.py -q`

---

## Test Specification

```rust
// rust/qsurl/src/lib.rs — the 11 reference tests plus:
#[test] fn requires_is_declaration_ordered() { /* see blueprint */ }
#[test] fn lower_error_json_has_only_kind_and_message() { /* see blueprint */ }
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at the path listed above for full context.
2. **Check dependencies** — verify every `Depends-on` task is `done` in `sdd/tasks/index/qsurl-parser.json`.
3. **Verify the Codebase Contract** before writing any code: re-run the `grep -c` for every MODIFY anchor; if a count changed, re-locate the anchor; if it is `0`, stop and report the drift.
4. **Update status** in `sdd/tasks/index/qsurl-parser.json` → `"in-progress"`.
5. **Implement** from the Implementation Blueprint; complete every `# FILL IN:`; never change a signature or path the blueprint fixes.
6. **Verify** every Acceptance Criterion and run the Validation Commands (`source .venv/bin/activate` first).
7. **Move this file** to `sdd/tasks/completed/TASK-764-qsurl-rust-crate.md` and set the index entry to `"done"`.
8. **Fill in the Completion Note** below.

---

## Completion Note

**Completed by**: Claude Fable 5.1 (interactive session, main repo user request)
**Date**: 2026-09-24
**Notes**: Verified the tarball hash (`a25c08fe…4bba45`, matches the Codebase Contract),
extracted it and copied `src/{ast,parser,ir}.rs` and `examples/parse.rs` byte-for-byte
(`cmp` identical). `Cargo.toml` / `pyproject.toml` written from the blueprint (edition 2024,
`rust-version = "1.88"`, empty default features, pyo3 0.29 optional behind `python`,
`module-name = "querysource.qsurl._qsurl"`). `python.rs`: `#[pymodule] fn qsurl` → `fn _qsurl`
and the doc-comment import example now names `querysource.qsurl._qsurl`. `lib.rs`: the 11
reference tests kept verbatim plus `requires_is_declaration_ordered` and
`lower_error_json_has_only_kind_and_message` with the exact assertions from the contract.
Also committed the reference `README.md` and the generated `rust/qsurl/Cargo.lock` (the sibling
`rust/Cargo.lock` is tracked too).
Results: `cargo test --manifest-path rust/qsurl/Cargo.toml` → 13 passed, 0 warnings, no
libpython linked; `cargo run --example parse -- "/queries/x{a}?a=1:top(1)"` prints the IR;
`cargo build --features python` compiles; `grep -c 'fn _qsurl('` → 1; `rust/qsurl/target/`
is git-ignored. Validation gate `pytest tests/test_rust_parsers.py -q` → 124 passed, 1 skipped
(existing `rust/` crate untouched).

**Deviations from spec**: none (README.md and Cargo.lock added beyond the listed files; no source changes)
