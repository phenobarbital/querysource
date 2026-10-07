# TASK-840: Python operator table, escaping/literal helpers and validator (M1)

**Feature**: FEAT-180 — Partial-matching operators for where_cond / filter dict values
**Spec**: `sdd/specs/filter-with-partial-matching.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §2 makes one **operator table** the contract for every builder; spec §3 Module 1
declares it in pure Python so Cython builders (`.pyx`), `AbstractParser._where_element`,
qsurl and the tests import the same object. This task creates that module and its unit
tests. Nothing calls it yet — later tasks wire it in.

Decisions taken at task time (record them, do not re-open):
- **Literal helpers live here** (`sql_like_literal`, `mssql_like_literal`, `bq_like_literal`),
  because `Entity.quoteString` strips a surrounding quote pair and maps `null`/`true` to
  keywords, and `escape_string` strips quotes and rewrites `%` → `\%` (verified
  `querysource/types/validators.pyx:428-443, 585-613`) — both corrupt a LIKE pattern.
  `bq_quote_string` (`bigquery.pyx:31`) does not escape `\`, and BigQuery treats `\` as a
  string-literal escape, so a `\%` LIKE escape must reach BigQuery as `"\\%"` (spec §8 Q1).
- `like_escape_bang` escapes `!`, `%`, `_` **and `[`** (spec §8 Q2 resolved yes).
- Regex safety mirrors `querysource/qsurl/residual.py:35-52`: length ≤ 200 **and** no
  nested quantifier (design-research S8).
- Multi-key dicts that contain a table operator are rejected (design-research S3, AC14).

---

## Scope

- Create `querysource/parsers/partial_matching.py` with the 20-entry table, the escaping
  helpers, the three literal helpers, `build_like_pattern`, `validate_partial_match` and
  `validate_partial_match_dict`, exactly as the blueprint fixes them.
- Create `tests/test_partial_matching_operators.py` covering every public name.

**NOT in scope**: wiring into any parser (TASK-842..850), the Rust twin (TASK-841), qsurl (TASK-853).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/parsers/partial_matching.py` | CREATE | operator table, helpers, validator |
| `tests/test_partial_matching_operators.py` | CREATE | unit tests |

---

## Codebase Contract (Anti-Hallucination)

> **CRITICAL**: This section contains VERIFIED code references from the actual codebase
> (re-verified on `dev` at `71316ad4`, 2026-10-07). The implementing agent MUST use these exact
> imports, class names, and method signatures. **DO NOT** invent, guess, or assume any import,
> attribute, or method not listed here. If you need something not listed, VERIFY it exists first.

### Verified Imports
```python
from ..exceptions import ParserError   # verified: querysource/exceptions.py:86 (class ParserError(QueryException): default_code = 400)
from querysource.exceptions import ParserError   # same class, absolute form for tests
```

### Existing Signatures to Use
```python
# querysource/exceptions.py
class QueryException(Exception)                      # line 6
class ParserError(QueryException): default_code = 400   # lines 86-87 — construct as ParserError("message")

# querysource/qsurl/residual.py  (policy mirrored here, NOT imported — parsers never import qsurl)
_MAX_REGEX_PATTERN_LENGTH = 200                                     # line 35
_NESTED_QUANTIFIER_RE = re.compile(r"\([^()]*[+*][^()]*\)[+*]")     # line 36

# querysource/qsurl/translate.py:21 — semantics of like_escape mirrored here:
#   value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
```

### Does NOT Exist
- ~~`querysource.parsers.partial_matching`~~ — created by THIS task.
- ~~`ParserError(code=...)`~~ keyword — use `ParserError("message")`.
- ~~`querysource.types.validators.like_escape`~~ — not a real function.
- ~~importing `querysource.qsurl` from `querysource/parsers/`~~ — forbidden direction (qsurl imports parsers, never the reverse).

---

## Complexity Contract

> Declaration for deterministic complexity routing (FEAT-561). `targets` match the
> Files table exactly.

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "querysource/parsers/partial_matching.py",
      "action": "CREATE"
    },
    {
      "path": "tests/test_partial_matching_operators.py",
      "action": "CREATE"
    }
  ],
  "contract_symbols": [
    "sym:querysource/exceptions.py#ParserError"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Pure Python, no Cython, no third-party imports (it is imported by `.pyx` modules and qsurl).
- Error messages are part of the contract: the Rust twin (TASK-841) reproduces them byte for byte.
- Length checks count characters (`len(str)`), applied to the **raw** operand before escaping.
- Google-style docstrings and full type hints (`.claude/rules/codebase-conventions.md`).

---

## Implementation Blueprint

> **CRITICAL — Executor-ready starting point.** Write each block below to its declared
> path nearly verbatim, then complete every `# FILL IN:` marker. Blocks were derived
> from the spec's Interface Skeletons and re-verified against the Codebase Contract
> above when this task was written. Never change a signature, class name, or file path
> the blueprint fixes.

### Steps (in order)
1. Write the module below verbatim — *why*: the table, helper names and messages are fixed by spec §2/§3 and mirrored by Rust.
2. Fill the two `FILL IN` validator bodies — *why*: the only judgement left is message assembly order, bounded by AC4/AC5/AC14.
3. Write the tests from the Test Specification and run the Validation Command.

### `querysource/parsers/partial_matching.py` (CREATE)
```python
"""Partial-matching operators for ``where_cond`` / ``filter`` dict values (FEAT-180).

One table is the contract for every WHERE builder (Cython and Rust twin in
``rust/src/partial_match.rs``). Builders never list operator names: they look the
name up here and render the entry for their dialect (spec §2 rendered forms).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from ..exceptions import ParserError

CONTAINS_MIN_LENGTH: int = 3
MAX_REGEX_PATTERN_LENGTH: int = 200
LIKE_ESCAPE_CHAR_BANG: str = "!"
NESTED_QUANTIFIER_RE: re.Pattern[str] = re.compile(r"\([^()]*[+*][^()]*\)[+*]")


@dataclass(frozen=True, slots=True)
class PartialMatchOp:
    """One row of the operator table (spec §2 Data Models)."""

    name: str
    kind: str          # "like" | "regex"
    negated: bool
    insensitive: bool
    prefix: str        # "" | "%"
    suffix: str        # "" | "%"
    escape: bool       # True: operand is a raw value the builder escapes
    min_length: int    # 3 for the contains family, else 0


def _op(name: str, kind: str, negated: bool, insensitive: bool,
        prefix: str, suffix: str, escape: bool, min_length: int) -> PartialMatchOp:
    return PartialMatchOp(name, kind, negated, insensitive, prefix, suffix, escape, min_length)


# Order is part of the contract: rust/src/partial_match.rs lists the same 20 rows in the same order.
PARTIAL_MATCH_OPERATORS: dict[str, PartialMatchOp] = {o.name: o for o in (
    _op("like", "like", False, False, "", "", False, 0),
    _op("not_like", "like", True, False, "", "", False, 0),
    _op("ilike", "like", False, True, "", "", False, 0),
    _op("not_ilike", "like", True, True, "", "", False, 0),
    _op("startswith", "like", False, False, "", "%", True, 0),
    _op("not_startswith", "like", True, False, "", "%", True, 0),
    _op("istartswith", "like", False, True, "", "%", True, 0),
    _op("not_istartswith", "like", True, True, "", "%", True, 0),
    _op("endswith", "like", False, False, "%", "", True, 0),
    _op("not_endswith", "like", True, False, "%", "", True, 0),
    _op("iendswith", "like", False, True, "%", "", True, 0),
    _op("not_iendswith", "like", True, True, "%", "", True, 0),
    _op("contains", "like", False, False, "%", "%", True, CONTAINS_MIN_LENGTH),
    _op("not_contains", "like", True, False, "%", "%", True, CONTAINS_MIN_LENGTH),
    _op("icontains", "like", False, True, "%", "%", True, CONTAINS_MIN_LENGTH),
    _op("not_icontains", "like", True, True, "%", "%", True, CONTAINS_MIN_LENGTH),
    _op("regex", "regex", False, False, "", "", False, 0),
    _op("not_regex", "regex", True, False, "", "", False, 0),
    _op("iregex", "regex", False, True, "", "", False, 0),
    _op("not_iregex", "regex", True, True, "", "", False, 0),
)}


def like_escape(value: str) -> str:
    """Escape ``\\``, ``%`` and ``_`` with a backslash (PostgreSQL / BigQuery LIKE)."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def like_escape_bang(value: str) -> str:
    """Escape ``!``, ``%``, ``_`` and ``[`` with ``!`` (generic SQL / SQL Server, ``ESCAPE '!'``)."""
    return (value.replace("!", "!!").replace("%", "!%")
            .replace("_", "!_").replace("[", "!["))


def build_like_pattern(op: PartialMatchOp, value: str, *, escaper: Callable[[str], str]) -> str:
    """Return ``prefix + (escaper(value) if op.escape else value) + suffix``. Never quotes."""
    return f"{op.prefix}{escaper(value) if op.escape else value}{op.suffix}"


def sql_like_literal(pattern: str) -> str:
    """Quote a pattern for the generic SQL parser (MySQL-safe: doubles ``\\`` and ``'``)."""
    return "'" + pattern.replace("\\", "\\\\").replace("'", "''") + "'"


def mssql_like_literal(pattern: str) -> str:
    """Quote a pattern for SQL Server (T-SQL has no backslash escapes: doubles ``'`` only)."""
    return "'" + pattern.replace("'", "''") + "'"


def bq_like_literal(pattern: str) -> str:
    """Quote a pattern as a BigQuery double-quoted literal (escapes ``\\`` then ``"``)."""
    return '"' + pattern.replace("\\", "\\\\").replace('"', '\\"') + '"'


def validate_partial_match(key: str, op: str, value: object, *, supports_regex: bool) -> PartialMatchOp:
    """Validate one ``(op, operand)`` pair and return its table entry.

    Messages (part of the contract, mirrored in Rust):
        "unknown partial-matching operator '<op>' on '<key>'"
        "<op> on '<key>' requires a string operand"
        "<op> on '<key>' requires at least 3 characters (got <n>)"
        "<op> on '<key>': regex operators are not supported by this query parser"
        "<op> on '<key>': empty regex pattern"
        "<op> on '<key>': regex pattern too long (<n> > 200 chars)"
        "<op> on '<key>': regex pattern has a nested quantifier"

    Raises:
        ParserError: on any rule above, checked in that order.
    """
    entry = PARTIAL_MATCH_OPERATORS.get(op)
    if entry is None:
        raise ParserError(f"unknown partial-matching operator '{op}' on '{key}'")
    # FILL IN: the remaining six checks in the docstring order — bounded by AC4, AC5, AC6
    return entry


def validate_partial_match_dict(key: str, value: dict, *, supports_regex: bool) -> PartialMatchOp | None:
    """Entry point for ``AbstractParser._where_element`` and the Cython builders.

    Returns None when no key of ``value`` is a table operator (caller keeps today's
    behaviour). Raises ParserError("one operator per field: '<key>' combines a
    partial-matching operator with other keys") when a table operator is combined with
    any other key; otherwise validates the single pair via validate_partial_match.
    """
    # FILL IN: detect table keys, enforce single-key rule, delegate — bounded by AC14
    return None
```
**Why this shape**: the table is data so builders cannot drift (spec G7); the escape flag
fixes escaping ownership per operator (design-research S5); dedicated literal helpers
avoid `quoteString`/`escape_string` side effects documented in Context. Do not add
operators, rename fields, or reorder rows — TASK-841's parity test compares order.

### `tests/test_partial_matching_operators.py` (CREATE)
```python
# Start from the scaffold in "## Test Specification" below (imports, EXPECTED, two tests), verbatim.
# FILL IN: one test per Acceptance Criterion bullet — bounded by AC list
```
**Why**: the scaffold fixes the import surface the module must expose; every AC bullet maps to one test.

### FILL IN checklist
- [ ] `validate_partial_match` — six ordered checks with the exact docstring messages; regex rules apply only when `entry.kind == "regex"`; `min_length` applies to the raw operand; bounded by AC4/AC5/AC6.
- [ ] `validate_partial_match_dict` — `None` when no key is in the table; multi-key error otherwise; bounded by AC14.

---

## Acceptance Criteria

- [ ] `PARTIAL_MATCH_OPERATORS` has exactly the 20 lowercase names in the blueprint order (spec AC1, Python half).
- [ ] `like_escape("a\\b%c_d") == "a\\\\b\\%c\\_d"` and `like_escape_bang("a!%_[b") == "a!!!%!_![b"`.
- [ ] `build_like_pattern` yields `v%`, `%v`, `%v%` (escaped) for the startswith/endswith/contains families and the raw value for like/ilike/regex.
- [ ] `sql_like_literal("o'b\\") == "'o''b\\\\'"`, `mssql_like_literal("o'b\\") == "'o''b\\'"`, `bq_like_literal('a"\\%') == '"a\\"\\\\%"'`.
- [ ] `validate_partial_match` raises `ParserError` with the documented messages: contains family `"ab"`, non-str operands, regex with `supports_regex=False`, empty, 201-char and `(a+)+` patterns; `"abc"` and a 1-char `startswith` pass.
- [ ] `validate_partial_match_dict` returns `None` for `{">=": 1}` and `{"@>": {...}}`, and raises the multi-key error for `{"startswith": "a", "endswith": "z"}` and `{"contains": "abc", ">=": 1}`.
- [ ] `ruff check querysource/parsers/partial_matching.py tests/test_partial_matching_operators.py` is clean.

---

## Validation Commands

> File-level pytest only — no directories, no package roots.

- `pytest tests/test_partial_matching_operators.py -q`

---

## Test Specification

```python
# tests/test_partial_matching_operators.py
import pytest

from querysource.exceptions import ParserError
from querysource.parsers.partial_matching import (
    PARTIAL_MATCH_OPERATORS, build_like_pattern, bq_like_literal, like_escape,
    like_escape_bang, mssql_like_literal, sql_like_literal, validate_partial_match,
    validate_partial_match_dict,
)

EXPECTED = ["like", "not_like", "ilike", "not_ilike", "startswith", "not_startswith",
            "istartswith", "not_istartswith", "endswith", "not_endswith", "iendswith",
            "not_iendswith", "contains", "not_contains", "icontains", "not_icontains",
            "regex", "not_regex", "iregex", "not_iregex"]


def test_table_has_twenty_lowercase_names_in_order():
    assert list(PARTIAL_MATCH_OPERATORS) == EXPECTED


@pytest.mark.parametrize("op", ["contains", "icontains", "not_contains", "not_icontains"])
@pytest.mark.parametrize("value", ["a", "ab"])
def test_contains_min_length(op, value):
    with pytest.raises(ParserError, match="requires at least 3 characters"):
        validate_partial_match("n", op, value, supports_regex=True)

# FILL IN: escaping, literal, pattern, non-str, regex-bounds, multi-key and None cases per AC
```

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
9. **Close the task** with `scripts/sdd/close_task.sh TASK-840 filter-with-partial-matching verified`
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
