# TASK-852: qsurl grammar: ~* !~* ^=* $=* =~* tokens and i* expressions (M10, grammar half)

**Feature**: FEAT-180 — Partial-matching operators for where_cond / filter dict values
**Spec**: `sdd/specs/filter-with-partial-matching.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 10 (grammar half), resolved Q3: qsurl speaks the operator-table vocabulary.
New URL spellings with a trailing `*` (the PostgreSQL `~*` precedent) parse to
case-insensitive expressions:

| URL token | `expression` | capability |
|---|---|---|
| `~*` | `icontains` | `text_match` |
| `!~*` | `not_icontains` | `text_match` |
| `^=*` | `istartswith` | `text_match` |
| `$=*` | `iendswith` | `text_match` |
| `=~*` | `iregex` | `regex` |

The existing tokens keep their expression names (`contains`, `not_contains`, `startswith`,
`endswith`, `regex`); their **meaning** changes to case-sensitive in TASK-853 — no grammar
change is needed for that. Both back-ends (Rust `rust/qsurl`, Lark fallback) must produce
byte-identical IR (`tests/qsurl/test_parity.py`); `to_gbnf()` derives from `grammar.lark`
automatically.

Rust qsurl crate tests run without extra features: `cargo test --manifest-path
rust/qsurl/Cargo.toml --lib` (13/13 green on `dev`, 2026-10-07).

---

## Scope

- Add five `CmpOp` variants (`ast.rs`), five tokens (`parser.rs`, three-char tokens first),
  and the capability arms (`ir.rs`).
- Add the five tokens to `grammar.lark`'s `CMP` (longest first) and to `_fallback.py`
  (`_OPS` + lowering branch).
- Append corpus cases; create `tests/qsurl/test_case_insensitive_tokens.py`.
- `make build-rust && make stage-rust`.

**NOT in scope**: translate/residual/provider/docs changes (TASK-853).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `rust/qsurl/src/ast.rs` | MODIFY | five CmpOp variants + as_str arms |
| `rust/qsurl/src/parser.rs` | MODIFY | five tokens in cmp_op |
| `rust/qsurl/src/ir.rs` | MODIFY | TextMatch / Regex capability arms |
| `querysource/qsurl/grammar.lark` | MODIFY | CMP terminal |
| `querysource/qsurl/_fallback.py` | MODIFY | _OPS entries + lowering branch |
| `tests/qsurl/corpus.json` | MODIFY | append case-insensitive token cases |
| `tests/qsurl/test_case_insensitive_tokens.py` | CREATE | token → expression tests on both back-ends |

---

## Codebase Contract (Anti-Hallucination)

> **CRITICAL**: This section contains VERIFIED code references from the actual codebase
> (re-verified on `dev` at `71316ad4`, 2026-10-07). The implementing agent MUST use these exact
> imports, class names, and method signatures. **DO NOT** invent, guess, or assume any import,
> attribute, or method not listed here. If you need something not listed, VERIFY it exists first.

### Verified Imports
```python
import querysource.qsurl as qsurl                         # verified: tests/qsurl/test_parity.py:8
from querysource.qsurl import HAS_RUST, QSUrlError, _fallback, parse   # verified: test_parity.py:9, test_docs.py:9
from querysource.qsurl import to_gbnf                     # __all__ in querysource/qsurl/__init__.py:80
```

### Existing Signatures to Use
```rust
// rust/qsurl/src/ast.rs
pub enum CmpOp { Eq, Ne, Lt, Le, Gt, Ge, Contains, NotContains, StartsWith, EndsWith, Regex }
//   #[serde(rename = "regex")]            line 86 (variant `Regex` on line 87)
pub fn flipped(self) -> Option<CmpOp>      // text ops fall into `_ => return None` — new variants need nothing
pub fn as_str(self) -> &'static str        // `CmpOp::Regex => "regex",` line 118
// rust/qsurl/src/parser.rs
fn cmp_op<'a>() -> impl Parser<'a, &'a str, CmpOp, Err<'a>> + Clone   // line 171; choice((
//        just("==").to(CmpOp::Eq), just("!=").to(CmpOp::Ne),
//        just("!~").to(CmpOp::NotContains),      // line 176
//        ... just('~').to(CmpOp::Contains),      // line 186 ))
// rust/qsurl/src/ir.rs
//        (CmpOp::Regex, v) => { cx.need(Feature::Regex); ... }                                         // line 121
//        (CmpOp::Contains | CmpOp::NotContains | CmpOp::StartsWith | CmpOp::EndsWith, v) => {          // line 126
//            cx.need(Feature::TextMatch); ...
```
```python
# querysource/qsurl/grammar.lark:70
CMP: "==" | "!=" | "!~" | "<=" | ">=" | "=~" | "^=" | "$=" | "<" | ">" | "=" | "~"
# querysource/qsurl/_fallback.py
_OPS: dict[str, str] = {"=": "==", ..., "~": "contains", "!~": "not_contains", "^=": "startswith", "$=": "endswith", "=~": "regex"}   # lines 24-35
#    elif op == "regex":                                                       # line 268
#        requires.add(capabilities.REGEX)
#    elif op in ("contains", "not_contains", "startswith", "endswith"):        # line 272
#        requires.add(capabilities.TEXT_MATCH)
# tests/qsurl/corpus.json: list of {"id", "input", "ir_json"} or {"id", "input", "error", "message_match"}; 17 cases
```

### Does NOT Exist
- ~~`CmpOp::IContains`~~ etc. — added by THIS task.
- ~~a hand-written GBNF token list~~ — `gbnf.py` derives terminals from `grammar.lark` (`to_gbnf`, gbnf.py:198).

---

## Complexity Contract

> Declaration for deterministic complexity routing (FEAT-561). `targets` match the
> Files table exactly.

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "rust/qsurl/src/ast.rs",
      "action": "MODIFY"
    },
    {
      "path": "rust/qsurl/src/parser.rs",
      "action": "MODIFY"
    },
    {
      "path": "rust/qsurl/src/ir.rs",
      "action": "MODIFY"
    },
    {
      "path": "querysource/qsurl/grammar.lark",
      "action": "MODIFY"
    },
    {
      "path": "querysource/qsurl/_fallback.py",
      "action": "MODIFY"
    },
    {
      "path": "tests/qsurl/corpus.json",
      "action": "MODIFY"
    },
    {
      "path": "tests/qsurl/test_case_insensitive_tokens.py",
      "action": "CREATE"
    }
  ],
  "contract_symbols": [
    "sym:rust/qsurl/src/ast.rs#CmpOp",
    "sym:rust/qsurl/src/parser.rs#cmp_op",
    "sym:querysource/qsurl/_fallback.py#_OPS"
  ]
}
```

---

## Implementation Blueprint

> **CRITICAL — Executor-ready starting point.** Write each block below to its declared
> path nearly verbatim, then complete every `# FILL IN:` marker. Blocks were derived
> from the spec's Interface Skeletons and re-verified against the Codebase Contract
> above when this task was written. Never change a signature, class name, or file path
> the blueprint fixes.

### Steps (in order)
1. Rust AST + parser + IR — *why*: the Rust back-end is the primary parser.
2. Lark grammar + fallback — *why*: parity with Rust (AC15).
3. Corpus cases: generate `ir_json` with the fallback, then confirm the Rust back-end matches — *why*: the corpus is the parity oracle.
4. `cargo test --manifest-path rust/qsurl/Cargo.toml --lib`, then `make build-rust && make stage-rust`, then the Validation Commands.

### `rust/qsurl/src/ast.rs` (MODIFY)
```rust
// occurrences: 1 (verified: grep -c '    #[serde(rename = "regex")]' rust/qsurl/src/ast.rs) — line 86
// AFTER — insert below the `Regex,` variant that follows line 86:
    #[serde(rename = "icontains")]
    IContains,
    #[serde(rename = "not_icontains")]
    NotIContains,
    #[serde(rename = "istartswith")]
    IStartsWith,
    #[serde(rename = "iendswith")]
    IEndsWith,
    #[serde(rename = "iregex")]
    IRegex,
// occurrences: 1 (verified: grep -c '            CmpOp::Regex => "regex",' rust/qsurl/src/ast.rs) — line 118
// AFTER — insert below line 118:
            CmpOp::IContains => "icontains",
            CmpOp::NotIContains => "not_icontains",
            CmpOp::IStartsWith => "istartswith",
            CmpOp::IEndsWith => "iendswith",
            CmpOp::IRegex => "iregex",
```

### `rust/qsurl/src/parser.rs` (MODIFY)
```rust
// occurrences: 1 (verified: grep -c '        just("!~").to(CmpOp::NotContains),' rust/qsurl/src/parser.rs) — line 176
// BEFORE — insert at the top of the choice((...)) list, above `just("==").to(CmpOp::Eq),` (line 174):
        // FEAT-180: three-character case-insensitive tokens first (longest match).
        just("!~*").to(CmpOp::NotIContains),
        just("^=*").to(CmpOp::IStartsWith),
        just("$=*").to(CmpOp::IEndsWith),
        just("=~*").to(CmpOp::IRegex),
        just("~*").to(CmpOp::IContains),
// FILL IN: if chumsky's `choice` tuple arity limit is exceeded (now 17 parsers), split into two
// nested `choice((...))` groups, keeping longest-first order — bounded by AC15 parity
```

### `rust/qsurl/src/ir.rs` (MODIFY)
```rust
// occurrences: 1 each (verified: grep -c '(CmpOp::Regex, v) => {' and
//   grep -c '(CmpOp::Contains | CmpOp::NotContains | CmpOp::StartsWith | CmpOp::EndsWith, v) => {' rust/qsurl/src/ir.rs) — lines 121, 126
// REPLACE line 121 with:
        (CmpOp::Regex | CmpOp::IRegex, v) => {
// REPLACE line 126 with:
        (CmpOp::Contains | CmpOp::NotContains | CmpOp::StartsWith | CmpOp::EndsWith
            | CmpOp::IContains | CmpOp::NotIContains | CmpOp::IStartsWith | CmpOp::IEndsWith, v) => {
```

### `querysource/qsurl/grammar.lark` (MODIFY)
```lark
# occurrences: 1 (verified: grep -c 'CMP: "==" | "!=" | "!~" | "<=" | ">=" | "=~" | "^=" | "$=" | "<" | ">" | "=" | "~"' querysource/qsurl/grammar.lark) — line 70
# REPLACE line 70 with (longest alternatives first):
CMP: "!~*" | "^=*" | "$=*" | "=~*" | "~*" | "==" | "!=" | "!~" | "<=" | ">=" | "=~" | "^=" | "$=" | "<" | ">" | "=" | "~"
```

### `querysource/qsurl/_fallback.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '    "=~": "regex",' querysource/qsurl/_fallback.py) — line 34
# AFTER — insert below line 34:
    "~*": "icontains",
    "!~*": "not_icontains",
    "^=*": "istartswith",
    "$=*": "iendswith",
    "=~*": "iregex",
# occurrences: 1 (verified: grep -c '    elif op == "regex":' querysource/qsurl/_fallback.py) — line 268
# REPLACE line 268 with:
    elif op in ("regex", "iregex"):
# occurrences: 1 (verified: grep -c '    elif op in ("contains", "not_contains", "startswith", "endswith"):' querysource/qsurl/_fallback.py) — line 272
# REPLACE line 272 with:
    elif op in ("contains", "not_contains", "startswith", "endswith",
                "icontains", "not_icontains", "istartswith", "iendswith"):
```

### `tests/qsurl/test_case_insensitive_tokens.py` (CREATE)
```python
"""FEAT-180: case-insensitive qsurl tokens parse identically on both back-ends."""
import pytest

from querysource.qsurl import HAS_RUST, _fallback, parse, to_gbnf

CASES = [
    ("s?name^=*'an'", "istartswith", "text_match"),
    ("s?city~*'san'", "icontains", "text_match"),
    ("s?code$=*'x'", "iendswith", "text_match"),
    ("s?n!~*'yy'", "not_icontains", "text_match"),
    ("s?r=~*'^a'", "iregex", "regex"),
]


@pytest.mark.parametrize("url,expr,cap", CASES)
def test_fallback_tokens(url, expr, cap):
    ir = _fallback.parse(url)
    leaf = ir["filter"]["and"][0] if "and" in ir["filter"] else ir["filter"]
    assert leaf["expression"] == expr and cap in ir["requires"]

# FILL IN: same assertions through parse() with the Rust back-end (skip if not HAS_RUST);
# legacy tokens (~ !~ ^= $= =~) still give contains/not_contains/startswith/endswith/regex;
# to_gbnf() output contains each new token — bounded by AC15
```

### `tests/qsurl/corpus.json` (MODIFY)
```json
// Append to the top-level JSON list (17 entries today; keys: "id", "input", "ir_json" — or
// "error" + "message_match" for error cases). Example shape (existing entry
// "null_checks_lists_text_ops"): {"id": "...", "input": "s?...", "ir_json": "<compact JSON, sort_keys>"}
{"id": "case_insensitive_text_ops", "input": "s?name^=*'An'&city~*'san'&code$=*'X'&n!~*'yy'&r=~*'^a'", "ir_json": "FILL IN"}
// FILL IN: ir_json = json.dumps(_fallback.parse(input), separators=(",", ":"), ensure_ascii=False, sort_keys=True)
// (the _compact() of tests/qsurl/test_parity.py:12), then confirm the Rust back-end yields the same string;
// add a mixed legacy+new case and a list-operand lowering-error case for `~*` — bounded by AC15
```
**Why**: the corpus is the parity oracle for both back-ends and for `to_gbnf()` (`test_gbnf_accepts_all_valid_corpus_inputs`).

### FILL IN checklist
- [ ] chumsky `choice` arity check — bounded by AC15.
- [ ] Corpus cases (≥ 3: all five tokens in one URL, a mixed legacy+new URL, a `~*` used with a list → lowering error identical to `~`) with generated `ir_json` — bounded by AC15.
- [ ] Rust-backend and GBNF tests — bounded by AC15.

---

## Acceptance Criteria

- [ ] AC15 (grammar half): both back-ends accept `~* !~* ^=* $=* =~*` with identical IR; `requires` uses the fixed capability order.
- [ ] Legacy tokens produce exactly the IR they produced before (existing corpus cases unchanged).
- [ ] `cargo test --manifest-path rust/qsurl/Cargo.toml --lib` passes; `make build-rust && make stage-rust` succeed.

---

## Validation Commands

> File-level pytest only — no directories, no package roots.

- `pytest tests/qsurl/test_case_insensitive_tokens.py -q`
- `pytest tests/qsurl/test_parity.py -q`
- `pytest tests/qsurl/test_gbnf.py -q`
- `pytest tests/qsurl/test_fallback.py -q`

---

## Test Specification

See the `tests/qsurl/test_case_insensitive_tokens.py` block above.

---

## Agent Instructions

When you pick up this task:

1. **Work in the feature worktree** — never on `base_branch`
   (`python -m scripts.sdd.ensure_worktree --slug filter-with-partial-matching --feature-id FEAT-180`)
2. **Read the spec** at the path listed above for full context (§2 rendered-forms table is normative)
3. **Check dependencies** — every `Depends-on` task must be `"done"` in the
   per-spec index `sdd/tasks/index/filter-with-partial-matching.json`
4. **Verify the Codebase Contract** — before writing ANY code:
   - Confirm every import in "Verified Imports" still exists (`grep` or `read` the source)
   - Re-run `grep -c` for every MODIFY anchor in the blueprint; a count that differs from the
     one recorded means the anchor moved — re-locate it; a count of `0` means STOP and report drift
   - **NEVER** reference an import, attribute, or method not in the contract without verifying it exists
5. **Update status** in `sdd/tasks/index/filter-with-partial-matching.json` → `"in-progress"`
   (set `started_at`) and commit only that index file
6. **Implement** — start from the Implementation Blueprint blocks, complete every
   `# FILL IN:` marker, and never change a signature or path the blueprint fixes
7. **Verify** all acceptance criteria are met — run the Validation Commands
8. **Commit the code** — stage only the files this task lists (never `git add .` / `-A`)
9. **Close the task** with `scripts/sdd/close_task.sh TASK-852 filter-with-partial-matching verified`
   — it moves this file to `sdd/tasks/completed/` and marks it `"done"` in the
   index; never move or copy the file by hand
10. **Fill in the Completion Note** below, then commit the staged SDD state

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
