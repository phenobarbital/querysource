# TASK-766: Lark fallback parser (`grammar.lark` + `_fallback.py`)

**Feature**: FEAT-152 — qsurl: HTSQL-style URL query dialect (chumsky 0.13 + PyO3)
**Spec**: `sdd/specs/qsurl-parser.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-764, TASK-765
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 3 (parser half). The brainstorm resolved that installs without the Rust
extension (sdist, unsupported platforms) must still parse qsurl, through a pure-Python
Lark parser driven by a **versioned** `grammar.lark`. Its output must be byte-identical to
the Rust IR for every corpus case (TASK-767 enforces this), so this task reproduces the
reference grammar AND every lowering rule of `ir.rs`, including the serialisation quirks
observed at task time (alphabetical object keys, `requires` in declaration order).

---

## Scope

- Write `querysource/qsurl/grammar.lark` (LALR with Lark's contextual lexer; header comment carries `# qsurl grammar v1` and states LALR/Earley choice).
- Write `querysource/qsurl/_fallback.py`: `parse(src) -> dict`, `requires(src) -> list[str]`, raising `QSUrlError` with Rust-compatible `kind`/`offset`/`message`/`pointer`.
- Write `tests/qsurl/test_fallback.py`: one test per lowering rule (spec §3 M3 rules 1–10), run with `QSURL_FORCE_FALLBACK`-independent direct calls to `_fallback.parse`.

**NOT in scope**: the parity corpus and its byte comparison (TASK-767); GBNF (TASK-768);
packaging `*.lark` in wheels (TASK-777).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/qsurl/grammar.lark` | CREATE | Versioned Lark grammar, phase 1 |
| `querysource/qsurl/_fallback.py` | CREATE | Lark parse + transformer + lowering to IR |
| `tests/qsurl/test_fallback.py` | CREATE | Rule-by-rule tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.qsurl.errors import QSUrlError             # created by TASK-765 (querysource/qsurl/errors.py)
from querysource.qsurl import capabilities                  # created by TASK-765; capabilities.ALL = Rust declaration order
import lark                                                 # verified: lark 1.3.1 installed; pyproject.toml:119 "lark>=1.3.1"
from lark import Lark, Transformer, v_args                  # lark 1.3.1 public API
from lark.exceptions import UnexpectedInput, UnexpectedCharacters, UnexpectedToken, UnexpectedEOF
```

### Existing Signatures to Use
```text
Grammar (reference parser.rs doc comment — reproduce exactly):
query      := prefix? slug selection? filter? pipe*
prefix     := '/queries/' | 'queries/' | '/'            (tried in this order)
slug       := [A-Za-z0-9_-]+
selection  := '{' field (',' field)* ','? '}'
field      := path (':as(' ident ')')?
path       := ident ('.' ident)*                        ident = [A-Za-z_][A-Za-z0-9_]*
filter     := '?' expr
expr       := expr '|' expr  (prec 1, left) | expr '&' expr (prec 2, left) | '!' expr (prec 3, prefix)
            | '(' expr ')' | operand (cmp operand)?
cmp        := '==' | '=' | '!=' | '<=' | '>=' | '<' | '>' | '~' | '!~' | '^=' | '$=' | '=~'
operand    := list | literal | call | path
list       := '(' literal (',' literal)* ')'
call       := ident '(' (operand (',' operand)*)? ')'
literal    := null | true | false | datetime | date | number | string
date       := DDDD-DD-DD ; datetime := date 'T' DD:DD (':' DD)? ('Z' | ('+'|'-') DD:DD)?
number     := '-'? int ('.' digits)?        → int unless it contains '.', then float
string     := '\'' ( '\'\'' | [^'] )* '\''  |  '"' ( '\"' | [^"] )* '"'
pipe       := ':sort(' sortkey (',' sortkey)* ')' | ':top(' int ')' | ':limit(' int ')'
            | ':skip(' int ')' | ':offset(' int ')' | ':distinct'
sortkey    := ('+' | '-')? path
Whitespace is tolerated around every token; the input is .trim()'d first.
```

```text
Lowering (reference ir.rs) — rules to reproduce, verbatim messages:
R1 column on the right is flipped for == != < <= > >= (Lt↔Gt, Le↔Ge); text ops with the column on the right →
   lower "operator `<expr>` requires the column on the left-hand side"   (<expr> = contains|not_contains|startswith|endswith|regex)
R2 col=null → {"column","expression":"is_null"}; col!=null → not_null; bare col → not_null; !col → is_null (no "not" node, no Not feature);
   any other !expr → {"not": <node>} + Not
R3 list only with ==/!= (+ in_list); other op with a list → lower "operator `<op>` does not accept a list; use `=` or `!=` for membership"
   (<op> = as_str: "<", "contains", ...)
R4 bare literal → lower "a bare literal is not a condition; compare it against a column";
   no column on either side → lower "a comparison needs a column or function on at least one side"
R5 "dtype" ONLY for unquoted date/datetime literals, value = the ISO string as written
R6 filter root: a single leaf (or a single "not") is wrapped as {"and":[x]}; an and/or root is kept
R7 requires = features used, ordered by capabilities.ALL; select iff fields non-empty; filter iff a filter exists;
   alias per aliased field; navigation for any dotted path (fields, operands, sort keys); functions for any call
   (call → {"fn": name, "args": [operands...]}, column position: {"column": {"fn":..,"args":..}})
R8 duplicate :top/:limit → parse offset 0 ":top/:limit given more than once"; duplicate :skip/:offset →
   ":skip/:offset given more than once"; unknown :op → parse at the op's ident offset
   "unknown pipeline operator `:<op>`; expected one of: :sort, :top, :limit, :skip, :offset, :distinct"
R9 '=' and '==' are synonyms; strings unescape '' and \"
R10 generic syntax error: offset = char offset of the offending token (end of input → len), message
   "unexpected `<c>` at position <N>" / "unexpected end of query at position <N>" (+ "; expected ..." allowed to differ);
   pointer = src + "\n" + " "*char_col + "^"
Precedence observed: "s?a=1|b=2&!c=3" → {"or":[a, {"and":[b, {"not":c}]}]}; "and"/"or" chains are FLATTENED
   (a&b&c → {"and":[a,b,c]}), not nested.
```

```text
Serialisation facts observed by running the reference crate (TASK-764):
- object keys alphabetical: {"distinct","fields","filter","limit","offset","requires","slug","sort"};
  leaf {"column","dtype"?,"expression","value"?}; alias {"alias","column"}; sort {"column","order"}
- `requires` NOT sorted alphabetically (declaration order)
- a leaf with is_null/not_null has NO "value" key
- `sort` default [] ; `limit`/`offset` default null ; `fields` default [] ; `filter` default null
```

### Does NOT Exist
- ~~`querysource/qsurl/grammar.lark`~~, ~~`querysource/qsurl/_fallback.py`~~ — created here.
- ~~`lark` Earley as a requirement~~ — use `parser="lalr"`; switch to Earley only if LALR conflicts cannot be resolved, and say so in the grammar header.
- ~~a Python AST module mirroring `ast.rs`~~ — do not create one; the transformer may build plain tuples/dicts internally.
- ~~`pkg_resources`~~ — locate the grammar with `importlib.resources.files("querysource.qsurl") / "grammar.lark"`.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/qsurl/grammar.lark", "action": "CREATE"},
    {"path": "querysource/qsurl/_fallback.py", "action": "CREATE"},
    {"path": "tests/qsurl/test_fallback.py", "action": "CREATE"}
  ],
  "contract_symbols": []
}
```

---

## Implementation Notes

### Key Constraints
- Byte parity is the contract: `json.dumps(ir, separators=(",", ":"), ensure_ascii=False, sort_keys=True)` of your dict must equal the Rust JSON. `sort_keys=True` reproduces the alphabetical object keys; `requires` stays a list in declaration order.
- Keywords `null`, `true`, `false`, `sort`, `top`, `limit`, `skip`, `offset`, `distinct`, `as` must not steal identifiers such as `nullable`, `topic`, `distinctive` (chumsky uses `keyword`, which checks the full identifier).
- Compile the Lark parser once (module-level cache), not per call.
- No `eval`, no regex built from user input.

---

## Implementation Blueprint

### Steps (in order)
1. Write `grammar.lark` from the grammar above; make whitespace an `%ignore`d terminal — *why*: whitespace is tolerated around every token.
2. Write `_fallback.py`: load the grammar via `importlib.resources`, build `Lark(..., parser="lalr", propagate_positions=True)` once — *why*: positions give error offsets; LALR keeps it fast enough (AC21: < 100 ms for 8 KB).
3. Transform the tree into an intermediate form, then lower it with rules R1–R9 — *why*: lowering errors are `kind: "lower"` and must not be confused with syntax errors.
4. Implement pipeline post-processing (R8) exactly like the Rust `try_map`: iterate pipes in order, duplicate top/skip → parse error at offset 0.
5. Map `lark.exceptions.UnexpectedInput` to `QSUrlError("parse", ...)` with R10 — *why*: callers only ever see `QSUrlError`.
6. Write `test_fallback.py`.

### `querysource/qsurl/grammar.lark` (CREATE)
```lark
// qsurl grammar v1 — mirrors rust/qsurl/src/parser.rs (phase 1). Parser: LALR (contextual lexer).
// Any change here MUST be mirrored in the Rust crate and covered by tests/qsurl/corpus.json.
start: prefix? SLUG selection? filter? pipe*

prefix: "/queries/" | "queries/" | "/"
selection: "{" field ("," field)* ","? "}"
field: path (":as" "(" IDENT ")")?
path: IDENT ("." IDENT)*
filter: "?" expr

?expr: or_expr
?or_expr: and_expr ("|" and_expr)*
?and_expr: unary ("&" unary)*
?unary: "!" unary -> not_
      | atom
?atom: "(" expr ")"
     | comparison
// FILL IN: comparison / operand / list / call / literal / pipe / sortkey rules and the
//   terminals (SLUG, IDENT, CMP ops, DATE, DATETIME, NUMBER, STRING) — bounded by the grammar
//   in the Codebase Contract; `( literal , ... )` must win over `( expr )` only in operand position
//   (resolve with the contextual lexer / rule priorities, document the resolution here).

%import common.WS
%ignore WS
```
**Why this shape**: the three precedence levels map chumsky's pratt table (`!` 3, `&` 2, `|` 1). Chains of the same operator are collected into one list, which is how the Rust `Expr::and`/`Expr::or` builders flatten.

### `querysource/qsurl/_fallback.py` (CREATE)
```python
"""Pure-Python qsurl parser (Lark) — used when the Rust extension is unavailable.

Produces the same IR dict and the same ``QSUrlError`` as ``querysource.qsurl._qsurl``.
"""
from __future__ import annotations

from functools import lru_cache
from importlib.resources import files

from lark import Lark, Transformer, v_args
from lark.exceptions import UnexpectedInput

from . import capabilities
from .errors import QSUrlError

GRAMMAR_PATH = files("querysource.qsurl") / "grammar.lark"
PIPE_OPERATORS: tuple[str, ...] = ("sort", "top", "limit", "skip", "offset", "distinct")
FLIPPED: dict[str, str] = {"==": "==", "!=": "!=", "<": ">", "<=": ">=", ">": "<", ">=": "<="}
TEXT_OPS: dict[str, str] = {"~": "contains", "!~": "not_contains", "^=": "startswith", "$=": "endswith", "=~": "regex"}


@lru_cache(maxsize=1)
def _parser() -> Lark:
    """Compile the grammar once per process."""
    return Lark(GRAMMAR_PATH.read_text(encoding="utf-8"), parser="lalr", propagate_positions=True)


def _pointer(src: str, offset: int) -> str:
    """Return ``src`` plus a caret line under character ``offset`` (Rust ``pointer``)."""
    return f"{src}\n{' ' * len(src[:offset])}^"


class _ToQuery(Transformer):
    """Parse tree → intermediate query dict (no lowering, no validation)."""
    # FILL IN: one method per grammar rule; literals carry their dtype ("date"/"datetime") and
    #   strings are unescaped ('' → ', \" → "); numbers: int unless '.' present — bounded by R5, R9


def _lower(query: dict) -> dict:
    """Apply rules R1–R7 and return the IR dict (keys as in the Rust output)."""
    needed: set[str] = set()
    # FILL IN: fields / filter (R1–R6) / sort / limit / offset / distinct lowering
    requires = [cap for cap in capabilities.ALL if cap in needed]
    # FILL IN: return {"slug": ..., "fields": ..., "filter": ..., "sort": ..., "limit": ...,
    #   "offset": ..., "distinct": ..., "requires": requires}


def parse(src: str) -> dict:
    """Parse ``src`` and return the IR dict.

    Raises:
        QSUrlError: kind "parse" (grammar, duplicate/unknown pipes) or "lower".
    """
    src = src.strip()
    try:
        tree = _parser().parse(src)
    except UnexpectedInput as err:
        # FILL IN: unknown pipeline operator detection (R8) and generic message (R10);
        #   offset = err.pos_in_stream (EOF → len(src)); pointer=_pointer(src, offset)
        raise
    query = _ToQuery().transform(tree)
    # FILL IN: R8 duplicate :top/:limit and :skip/:offset → QSUrlError("parse", msg, offset=0, pointer=_pointer(src, 0))
    return _lower(query)


def requires(src: str) -> list[str]:
    """Return ``parse(src)["requires"]``."""
    return list(parse(src)["requires"])
```
**Why**: keeping the transformer free of validation mirrors the Rust split between `parser.rs` (syntax) and `ir.rs` (lowering), so error kinds line up.

### FILL IN checklist
- [ ] `grammar.lark` — remaining rules and terminals; keyword vs identifier handling; list-vs-parenthesised-expr resolution.
- [ ] `_ToQuery` — rule methods, literal typing and unescaping.
- [ ] `_lower` — R1–R7, exact messages.
- [ ] `parse` — R8 pipe checks and R10 error mapping.
- [ ] `test_fallback.py` — one test per rule.

---

## Acceptance Criteria

- [ ] `_fallback.parse("/queries/hisense_stores{store_id,name,city}?state_code='CA'&opened>=2024-01-01:sort(name):top(50)")` serialised with `sort_keys=True, separators=(",",":")` equals the Rust output for the same input (compare by running `cargo run --manifest-path rust/qsurl/Cargo.toml --example parse` if the crate is available; otherwise against the expected dict in the test).
- [ ] Every rule R1–R10 has a passing test with the exact messages from the Codebase Contract.
- [ ] `nullable=1`, `topic=1`, `distinctive=1` parse as columns.
- [ ] `ruff check querysource/qsurl/_fallback.py tests/qsurl/test_fallback.py` clean.

---

## Validation Commands

- `pytest tests/qsurl/test_fallback.py -q`

---

## Test Specification

```python
# tests/qsurl/test_fallback.py
import pytest
from querysource.qsurl import QSUrlError
from querysource.qsurl._fallback import parse


def test_example_query():
    ir = parse("/queries/hisense_stores{store_id,name,city}?state_code='CA'&opened>=2024-01-01:sort(name):top(50)")
    assert ir["requires"] == ["select", "filter", "sort", "limit"]
    assert ir["filter"]["and"][1] == {"column": "opened", "expression": ">=", "value": "2024-01-01", "dtype": "date"}

def test_precedence_and_flattening():
    assert parse("s?a=1|b=2&!c=3")["filter"] == {"or": [
        {"column": "a", "expression": "==", "value": 1},
        {"and": [{"column": "b", "expression": "==", "value": 2},
                 {"not": {"column": "c", "expression": "==", "value": 3}}]}]}

def test_unknown_pipe():
    with pytest.raises(QSUrlError) as exc:
        parse("stores?state='CA':order(name)")
    assert (exc.value.kind, exc.value.offset) == ("parse", 18)
    assert exc.value.message.startswith("unknown pipeline operator `:order`")

def test_duplicate_top(): ...          # FILL IN: offset 0, ":top/:limit given more than once"
def test_lowering_errors(): ...        # FILL IN: R1 text op on the right, R3 list with ~, R4 bare literal
def test_null_sugar(): ...             # FILL IN: R2 incl. "!fax" → is_null without Not in requires
def test_literals_typed(): ...         # FILL IN: R5/R9 incl. 'it''s', "q\"q", -2.5, true, quoted date has no dtype
def test_keywords_do_not_steal_identifiers(): ...   # FILL IN
def test_whitespace_tolerated(): ...   # FILL IN: "stores ? state = 'CA' & city ~ 'san' : sort( -opened , name ) : top( 5 )"
def test_generic_syntax_error_offsets(): ...   # FILL IN: "stores?state=" → 13, "s{a b}" → 4, "s?(a=1" → 6
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
7. **Move this file** to `sdd/tasks/completed/TASK-766-qsurl-lark-fallback.md` and set the index entry to `"done"`.
8. **Fill in the Completion Note** below.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
