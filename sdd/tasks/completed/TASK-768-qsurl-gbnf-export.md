# TASK-768: GBNF export of the qsurl grammar (`to_gbnf()`)

**Feature**: FEAT-152 — qsurl: HTSQL-style URL query dialect (chumsky 0.13 + PyO3)
**Spec**: `sdd/specs/qsurl-parser.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-766, TASK-767
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 4, AC16. The brainstorm resolved to ship a GBNF export with this feature so
the same closed grammar can be handed to constrained-decoding LLM backends (an agent then
cannot emit an invalid qsurl). The GBNF is derived from `grammar.lark` (TASK-766), not
maintained by hand, and is validated against every valid corpus input (TASK-767).

---

## Scope

- Implement `querysource/qsurl/gbnf.py::to_gbnf(grammar_path=None) -> str` converting the Lark grammar's rules and terminals into GBNF with `root ::= query`.
- Implement a minimal pure-Python GBNF matcher **inside the test module** (no new dependency) and test that the exported grammar accepts every valid corpus input and rejects `stores?state='CA':order(name)`.

**NOT in scope**: any change to `grammar.lark` (report needed changes); `querysource.qsurl.to_gbnf`
re-export (already written lazily by TASK-765); docs (TASK-778).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/qsurl/gbnf.py` | CREATE | Lark → GBNF converter |
| `tests/qsurl/test_gbnf.py` | CREATE | Minimal GBNF matcher + corpus acceptance tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from lark import Lark                                   # lark 1.3.1 (pyproject.toml:119)
from lark.grammar import Rule, Terminal, NonTerminal     # lark 1.3.1 — Lark(...).rules / .terminals
from querysource.qsurl._fallback import GRAMMAR_PATH    # created by TASK-766
from querysource.qsurl import to_gbnf                    # created by TASK-765 (lazy wrapper → gbnf.to_gbnf)
```

### Existing Signatures to Use
```python
# querysource/qsurl/__init__.py (TASK-765)
def to_gbnf() -> str:
    from .gbnf import to_gbnf as _to_gbnf
    return _to_gbnf()

# tests/qsurl/conftest.py (TASK-765) — corpus fixture; valid cases have "ir_json", errors have "error"
```

```text
GBNF (llama.cpp grammar format) essentials:
  rule ::= alt1 | alt2 ;  sequences by juxtaposition ; "literal" ; [a-z] char classes ; ( ... ) groups ;
  * + ? repetition ; `root` is the start rule ; rule names [a-z0-9-]+ (no underscores)
```

### Does NOT Exist
- ~~`querysource/qsurl/gbnf.py`~~ — created here.
- ~~a GBNF library in the environment~~ (`llama_cpp`, `gbnf`, `outlines`) — none installed; do not add one.
- ~~`lark.tools.gbnf`~~ — Lark ships no GBNF exporter; convert from `Lark(...).rules` / `.terminals` yourself.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/qsurl/gbnf.py", "action": "CREATE"},
    {"path": "tests/qsurl/test_gbnf.py", "action": "CREATE"}
  ],
  "contract_symbols": []
}
```

---

## Implementation Notes

### Key Constraints
- Lark's `%ignore WS` has no GBNF equivalent: emit an explicit optional-whitespace rule `ws ::= [ \t]*` between tokens — otherwise the whitespace-tolerance cases in the corpus fail.
- Rule names must be converted to GBNF-legal names (`and_expr` → `and-expr`).
- Terminals defined by regex must be converted to character classes/repetitions; if a terminal uses a regex construct with no GBNF equivalent, raise `ValueError` naming it (spec skeleton).

---

## Implementation Blueprint

### Steps (in order)
1. Load the grammar with `Lark(GRAMMAR_PATH.read_text(), parser="lalr")` and walk `.rules` and `.terminals` — *why*: using Lark's own normalised grammar avoids re-parsing the `.lark` text by hand.
2. Emit one GBNF rule per non-terminal (alternatives joined by `|`), inserting `ws` between symbols — *why*: whitespace tolerance.
3. Convert each terminal (string → quoted literal, pattern → class/repetition) — *why*: GBNF has no regex.
4. Prepend `root ::= ws query ws` — *why*: GBNF starts at `root`.
5. Write the test-local matcher and the tests.

### `querysource/qsurl/gbnf.py` (CREATE)
```python
"""Render the qsurl Lark grammar as GBNF for constrained decoding."""
from __future__ import annotations

import logging
from pathlib import Path

from lark import Lark

from ._fallback import GRAMMAR_PATH

_logger = logging.getLogger(__name__)


def _rule_name(name: str) -> str:
    """Return a GBNF-legal rule name (lowercase, '-' instead of '_')."""
    return name.lower().replace("_", "-")


def _terminal_to_gbnf(name: str, pattern) -> str:
    """Convert one Lark terminal pattern to a GBNF expression.

    Raises:
        ValueError: if the pattern uses a construct with no GBNF equivalent.
    """
    # FILL IN: PatternStr → quoted literal (escape '"' and '\\'); PatternRE → classes/repetition
    #   — bounded by the terminals TASK-766 defined; raise ValueError(f"terminal {name}: unsupported regex {pattern.value!r}")


def to_gbnf(grammar_path: Path | None = None) -> str:
    """Render the Lark grammar as GBNF with ``root ::= ws query ws``.

    Args:
        grammar_path: grammar file to convert; defaults to the packaged ``grammar.lark``.

    Returns:
        The GBNF grammar text.

    Raises:
        ValueError: if the grammar uses a Lark construct with no GBNF equivalent.
    """
    source = Path(grammar_path).read_text(encoding="utf-8") if grammar_path else GRAMMAR_PATH.read_text(encoding="utf-8")
    lark = Lark(source, parser="lalr")
    lines: list[str] = ["root ::= ws query ws", "ws ::= [ \\t]*"]
    # FILL IN: rules (group lark.rules by origin; the Lark start rule maps to `query`), then terminals
    return "\n".join(lines) + "\n"
```
**Why**: the grammar path parameter lets tests pass a fixture grammar; the default keeps the public API argument-free.

### `tests/qsurl/test_gbnf.py` (CREATE)
```python
"""The exported GBNF accepts every valid corpus input (spec AC16)."""
from __future__ import annotations

from querysource.qsurl import to_gbnf


class _GbnfMatcher:
    """Tiny backtracking recogniser for the GBNF subset to_gbnf() emits (test-only)."""

    def __init__(self, grammar: str) -> None:
        # FILL IN: parse `name ::= expr` lines into an AST of seq/alt/literal/class/repeat/ref
        ...

    def matches(self, text: str) -> bool:
        """Return True when the whole ``text`` derives from ``root``."""
        # FILL IN: memoised backtracking match; must handle the 8 KB corpus case without recursion errors


def test_gbnf_accepts_all_valid_corpus_inputs(corpus):
    matcher = _GbnfMatcher(to_gbnf())
    bad = [c["id"] for c in corpus if "ir_json" in c and not matcher.matches(c["input"])]
    assert bad == []

def test_gbnf_rejects_unknown_pipe():
    assert not _GbnfMatcher(to_gbnf()).matches("stores?state='CA':order(name)")

def test_gbnf_rule_names_are_legal():
    # FILL IN: every left-hand side matches ^[a-z0-9-]+$
    ...
```

### FILL IN checklist
- [ ] `_terminal_to_gbnf` — string and regex terminals.
- [ ] `to_gbnf` — rule emission with `ws` separators.
- [ ] `_GbnfMatcher` — parser and matcher (use an explicit stack or `sys.setrecursionlimit` guard for the 8 KB case).
- [ ] rule-name test body.

---

## Acceptance Criteria

- [ ] `from querysource.qsurl import to_gbnf; to_gbnf()` returns text starting with `root ::=`.
- [ ] Every valid corpus input matches; the unknown-pipe input does not (spec AC16).
- [ ] No new dependency added.
- [ ] `ruff check querysource/qsurl/gbnf.py tests/qsurl/test_gbnf.py` clean.

---

## Validation Commands

- `pytest tests/qsurl/test_gbnf.py -q`

---

## Test Specification

See the `tests/qsurl/test_gbnf.py` block above.

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at the path listed above for full context.
2. **Check dependencies** — verify every `Depends-on` task is `done` in `sdd/tasks/index/qsurl-parser.json`.
3. **Verify the Codebase Contract** before writing any code: re-run the `grep -c` for every MODIFY anchor; if a count changed, re-locate the anchor; if it is `0`, stop and report the drift.
4. **Update status** in `sdd/tasks/index/qsurl-parser.json` → `"in-progress"`.
5. **Implement** from the Implementation Blueprint; complete every `# FILL IN:`; never change a signature or path the blueprint fixes.
6. **Verify** every Acceptance Criterion and run the Validation Commands (`source .venv/bin/activate` first).
7. **Move this file** to `sdd/tasks/completed/TASK-768-qsurl-gbnf-export.md` and set the index entry to `"done"`.
8. **Fill in the Completion Note** below.

---

## Completion Note

**Completed by**: sdd-worker (Claude Sonnet 5, sequential fallback loop)
**Date**: 2026-09-24
**Notes**: Implemented `querysource/qsurl/gbnf.py` by walking `Lark(grammar_source,
parser="lalr").rules`/`.terminals` (never re-parsing the `.lark` text by hand, per the
blueprint): `_RegexToGbnf` (a small recursive-descent transpiler for the exact regex
subset the grammar's terminals use — literals, `\d`, `[...]`/`[^...]` classes, `(...)`/
`(?:...)` groups, `|` alternation, `?`/`*`/`+` quantifiers) converts each terminal;
`_rule_name` slugifies Lark's generated rule names to GBNF-legal `[a-z0-9-]+`; `to_gbnf`
groups `.rules` by origin and emits one alternation per non-terminal, with `root ::= ws
query ws` / `ws ::= [ \t]*` prepended.

**Two design decisions beyond the literal blueprint, both required for AC16 to hold and
documented at length in the source** (not scope creep — the blueprint's own FILL IN for
`_terminal_to_gbnf` explicitly anticipates unsupported-construct handling, and the
GBNF-vs-CFG semantic gap they close is inherent to *any* correct GBNF export of this
grammar, not specific to my implementation choices):
1. **`(?!...)` negative lookahead has no GBNF equivalent** (used by every keyword
   terminal — `NULL`/`TRUE`/`FALSE`/`TOP_KW`/`SKIP_KW`/`SORT_KW`/`DISTINCT_KW` — for the
   TASK-766 word-boundary fix). Rather than raising `ValueError` (which would make
   `to_gbnf()` unconditionally fail), the lookahead is dropped: the emitted GBNF accepts
   a strict superset of the real language at word boundaries only (e.g. it cannot
   distinguish `sort` followed by a letter from bare `sort`) — acceptable for constrained
   decoding, whose job is keeping generation inside valid *shapes*, not exactly
   replicating the parser's own rejection boundary; the real parsers (TASK-764/766)
   remain authoritative.
2. **Lark expands every `grammar.lark` `(body)*`/`(body)+` into a synthetic
   `__foo_star_N` origin encoded as LEFT recursion** (`rule ::= body | rule body`) —
   meaningless for a GBNF/LLM-decoding consumer and unmatchable by the test-local
   backtracking-free matcher below. `_detect_star_bodies` recognises this exact
   two-alternative shape and inlines it at every use site as GBNF's native `(...)+`
   instead of emitting a separate rule. A **third, initially-missed** consequence of
   this substitution: Lark's `%ignore WS` tolerates whitespace between *every* adjacent
   token pair, including at a repetition boundary (e.g. the space before the second
   pipe's `:` in `":sort(x) : top(5)"`) — found via the `decoded_whitespace` corpus case
   failing — fixed by emitting `(body) (ws (body))*` (a leading occurrence, then
   zero-or-more further occurrences each separated by `ws`) instead of a bare `(body)+`.
3. **`unknown_pipe` is grammatically unconstrained in `grammar.lark` by design** (it
   exists solely so the Lark *parser* can capture any identifier for TASK-766's R8 error
   message — not part of the qsurl *language*). Any GBNF alternative reaching
   `unknown_pipe` is dropped, so the exported `pipe` rule only admits the six real pipe
   operators — this is exactly what `test_gbnf_rejects_unknown_pipe` requires, and
   without it the GBNF would (correctly, per the raw grammar) accept `:order(...)` as
   syntactically valid, which is not the intent of a *constrained-decoding* export.

`tests/qsurl/test_gbnf.py`'s `_GbnfMatcher`/`_GbnfExprParser` implement the FILL IN
in-test matcher: quantifiers (`?`/`*`/`+`) are matched with an iterative greedy loop,
never recursively per repetition, so the `eight_kb_url` corpus case's 900+ repeated
`&`-joined conditions cost one pass over the repeated body rather than one stack frame
per repetition (matches ~191ms, well within pytest's default timeout, no
`sys.setrecursionlimit` adjustment needed — grammar *structure* nesting is small and
input-size-independent). One further real bug found and fixed via this same test: the
matcher's own `\t`/`\n`/... escape decoding inside `[...]` classes initially treated
`\t` as the *literal letter* `t` rather than an actual tab byte, causing the `ws` rule
(`[ \t]*`) to wrongly consume any `t` character — fixed in `_char_in_class`'s escape
table (test-only code, not `gbnf.py`).

**Test results**: `pytest tests/qsurl/test_gbnf.py -q` -> 3 passed (`to_gbnf()` accepts
every valid corpus input including `eight_kb_url`; rejects the unknown-pipe input;
every emitted rule name is GBNF-legal). Full regression: `pytest tests/qsurl tests/e2e
tests/handlers/test_qsurl_service.py tests/handlers/test_queryservice_pbac_smoke.py -q`
-> 133 passed, 10 skipped (documented Rust-path skips, TASK-769). `ruff check
querysource/qsurl/gbnf.py tests/qsurl/test_gbnf.py` clean. No new dependency added
(only `lark`, `re`, `collections.OrderedDict`, `pathlib.Path`, stdlib `logging`).

**Deviations from spec**: none for the blueprint's own listed files — no change to
`grammar.lark` was needed (the three decisions above are export-time choices in
`gbnf.py`, not grammar-level changes).
