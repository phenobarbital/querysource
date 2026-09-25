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

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
