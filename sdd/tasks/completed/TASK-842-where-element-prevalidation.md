# TASK-842: Pre-dispatch validation in AbstractParser._where_element (M3)

**Feature**: FEAT-180 — Partial-matching operators for where_cond / filter dict values
**Spec**: `sdd/specs/filter-with-partial-matching.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-840
**Assigned-to**: unassigned

---

## Context

Spec §2 stage 1 / §3 Module 3. `_where_element` runs inside `set_options()` →
`set_where()` **before** any builder, and before the pgsql/mssql/bq Rust wrappers that
swallow exceptions (`pgsql.pyx:308-312`). Raising `ParserError` here makes the
`contains`-too-short, non-string, multi-key and regex-unsupported errors reach the user
on every path (spec AC4–AC6, AC14).

For a dict whose single operator is in the table, the raw string is returned untouched:
**no `is_valid()` pre-quoting** (spec §7 "quote once"). Every other dict keeps today's
`is_valid()` path byte for byte — FEAT-152's `ILIKE` strip logic depends on it.

A new `cdef public bint supports_regex_filter` (default False) tells the validator whether
the parser renders PostgreSQL regex; TASK-843 sets it True on `pgSQLParser`.

---

## Scope

- Add `cdef public bint supports_regex_filter` to `AbstractParser` in `abstract.pxd`.
- Default it to False in `AbstractParser.set_attributes` (called from `__cinit__`).
- Call `validate_partial_match_dict` first in the dict branch of `_where_element`.
- Rebuild the Cython extensions in place and write the tests.

**NOT in scope**: builder rendering (TASK-843/845/847/849); setting the flag True (TASK-843).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/parsers/abstract.pxd` | MODIFY | declare `supports_regex_filter` |
| `querysource/parsers/abstract.pyx` | MODIFY | import validator, default flag, dict-branch validation |
| `tests/test_partial_matching_prevalidation.py` | CREATE | set_where tests on SQLParser |

---

## Codebase Contract (Anti-Hallucination)

> **CRITICAL**: This section contains VERIFIED code references from the actual codebase
> (re-verified on `dev` at `71316ad4`, 2026-10-07). The implementing agent MUST use these exact
> imports, class names, and method signatures. **DO NOT** invent, guess, or assume any import,
> attribute, or method not listed here. If you need something not listed, VERIFY it exists first.

### Verified Imports
```python
from ..types.validators import Entity, is_valid, field_components   # verified: querysource/parsers/abstract.pyx:19
from .partial_matching import validate_partial_match_dict            # created by TASK-840
from querysource.models import QueryObject                           # verified: tests/qsurl/test_pg_ilike.py:6
from querysource.parsers.sql import SQLParser                        # verified: tests/test_sql_parser_combinations.py:20
from querysource.exceptions import ParserError                       # verified: querysource/exceptions.py:86
```

### Existing Signatures to Use
```cython
# querysource/parsers/abstract.pxd
cdef class AbstractParser:
    cdef public bint string_literal          # line 50 (noquote flag for is_valid)
    cdef void set_attributes(self)           # declared line 53

# querysource/parsers/abstract.pyx
def __cinit__(self, *args, definition, conditions, query=None, **kwargs)   # line 31; calls self.set_attributes() at line 49
cdef void set_attributes(self)                                              # line 69; ends with lines 93-95:
#         self._add_fields = False
#         self._safe_substitution = False
#         self.c_length = 0
async def _where_element(self, key, value, connection)                      # line 564; dict branch 567-574:
#        if isinstance(value, dict):
#            if not value:
#                return key, value
#            # Read the (last) operator without popitem(): ...
#            # (e.g. a linked dashboard ...); mutating it empties the filter.
#            op, v = next(reversed(value.items()))                         # line 572
#            result = is_valid(key, v, noquote=self.string_literal)         # line 573
#            return key, {op: result}
async def set_where(self, _filter: dict, connection: object) -> object      # line 602 — connection may be None in tests
```
```python
# querysource/parsers/sql.pyx
cdef class SQLParser(AbstractParser)   # line 85; constructor used by tests:
#   SQLParser(definition=None, conditions=QueryObject(query_raw=q), query=q)   (tests/test_sql_parser_combinations.py:34-36)
```

### Does NOT Exist
- ~~`AbstractParser.supports_regex_filter`~~ — added by THIS task.
- ~~`self.filter_options` validation~~ — `filtering_options()` (`abstract.pyx:450`) bypasses `_where_element`; builders re-validate (TASK-843/845/847/849). Do not touch it here.
- ~~`is_valid(..., noquote=True)` for table operators~~ — table operators skip `is_valid` entirely.

---

## Complexity Contract

> Declaration for deterministic complexity routing (FEAT-561). `targets` match the
> Files table exactly.

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "querysource/parsers/abstract.pxd",
      "action": "MODIFY"
    },
    {
      "path": "querysource/parsers/abstract.pyx",
      "action": "MODIFY"
    },
    {
      "path": "tests/test_partial_matching_prevalidation.py",
      "action": "CREATE"
    }
  ],
  "contract_symbols": [
    "sym:querysource/parsers/abstract.pyx#AbstractParser._where_element",
    "sym:querysource/parsers/abstract.pyx#AbstractParser.set_attributes",
    "sym:querysource/parsers/abstract.pyx#AbstractParser.set_where",
    "sym:querysource/parsers/partial_matching.py#validate_partial_match_dict"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- `.pxd` layout change ⇒ run `make build-inplace` (all parsers `cimport AbstractParser`); a stale `.so` fails to import with a size mismatch.
- Cython style: plain Python import of a `.py` module from a `.pyx` is fine (`abstract.pyx:14` already does `from . import QS_FILTERS`).
- Keep the existing comment block above line 572 intact.

---

## Implementation Blueprint

> **CRITICAL — Executor-ready starting point.** Write each block below to its declared
> path nearly verbatim, then complete every `# FILL IN:` marker. Blocks were derived
> from the spec's Interface Skeletons and re-verified against the Codebase Contract
> above when this task was written. Never change a signature, class name, or file path
> the blueprint fixes.

### Steps (in order)
1. Add the `.pxd` field — *why*: Cython attributes must be declared on the struct.
2. Add the import and the default — *why*: `__cinit__` → `set_attributes` runs for every subclass before `__init__`, so the default exists before TASK-843's override.
3. Insert the validation at the top of the dict branch — *why*: it must run before `is_valid` pre-quoting and before any builder.
4. `make build-inplace`, then write and run the tests.

### `querysource/parsers/abstract.pxd` (MODIFY)
```cython
# occurrences: 1 (verified: grep -c '    cdef public bint string_literal' querysource/parsers/abstract.pxd)
# AFTER — insert below `    cdef public bint string_literal` (verified: querysource/parsers/abstract.pxd:50)
    cdef public bint supports_regex_filter
```

### `querysource/parsers/abstract.pyx` (MODIFY — import)
```cython
# occurrences: 1 (verified: grep -c 'from ..types.validators import Entity, is_valid, field_components' querysource/parsers/abstract.pyx)
# AFTER — insert below `from ..types.validators import Entity, is_valid, field_components` (verified: abstract.pyx:19)
from .partial_matching import validate_partial_match_dict
```

### `querysource/parsers/abstract.pyx` (MODIFY — default flag)
```cython
# occurrences of the one-line anchor `        self.c_length = 0`: 2 (lines 95 and 353) — AMBIGUOUS,
# attach with this unique 3-line context (lines 93-95, end of set_attributes):
#         self._add_fields = False
#         self._safe_substitution = False
#         self.c_length = 0
# AFTER — insert below that context:
        self.supports_regex_filter = False
```

### `querysource/parsers/abstract.pyx` (MODIFY — dict branch)
```cython
# occurrences: 1 (verified: grep -c '            op, v = next(reversed(value.items()))' querysource/parsers/abstract.pyx)
# BEFORE — insert above the two comment lines that precede `            op, v = next(reversed(value.items()))`
#          (verified: abstract.pyx:570-572), i.e. directly below `                return key, value` (line 569)
            # FEAT-180: partial-matching operators are validated before any builder runs and
            # passed through raw — the dialect builder quotes them exactly once.
            if validate_partial_match_dict(key, value, supports_regex=self.supports_regex_filter) is not None:
                return key, dict(value)
```
**Why**: `validate_partial_match_dict` returns the entry only for a single-key table dict
(it raises for every invalid shape), so `dict(value)` is the raw `{op: operand}` — a copy,
because the caller owns the dict (comment at lines 570-571).

### `tests/test_partial_matching_prevalidation.py` (CREATE)
```python
"""FEAT-180: pre-dispatch validation in AbstractParser._where_element (spec AC4, AC6, AC14)."""
import pytest

from querysource.exceptions import ParserError
from querysource.models import QueryObject
from querysource.parsers.sql import SQLParser

SQL = "SELECT * FROM t {where_cond}"


def _parser() -> SQLParser:
    parser = SQLParser(definition=None, conditions=QueryObject(query_raw=SQL), query=SQL)
    parser.cond_definition = {}
    return parser


async def test_where_element_passthrough_raw():
    parser = _parser()
    await parser.set_where({"n": {"startswith": "o'brien"}}, None)
    assert parser.filter == {"n": {"startswith": "o'brien"}}

# FILL IN: contains "ab" raises; non-str raises; multi-key raises; regex raises (flag False);
# {">=": "5"} still pre-quoted by is_valid (regression guard); supports_regex_filter default False
```

### FILL IN checklist
- [ ] Remaining tests — bounded by AC4, AC6, AC14 and the regression criterion below.

---

## Acceptance Criteria

- [ ] `SQLParser(...).supports_regex_filter is False`.
- [ ] `set_where({"n": {"contains": "ab"}}, None)` raises `ParserError` ("requires at least 3 characters"); same for `icontains`/`not_contains`/`not_icontains`.
- [ ] Non-string operands, multi-key dicts with a table operator, and any `regex*` operator (flag False) raise `ParserError` from `set_where`.
- [ ] Table-operator operands come back raw (no added quotes); `{"n": {">=": "5"}}` and `{"city": {"ILIKE": "%san%"}}` come back exactly as before this task.
- [ ] `make build-inplace` succeeds; `tests/test_sql_parser_combinations.py` and `tests/qsurl/test_pg_ilike.py` stay green.

---

## Validation Commands

> File-level pytest only — no directories, no package roots.

- `pytest tests/test_partial_matching_prevalidation.py -q`
- `pytest tests/test_sql_parser_combinations.py -q`
- `pytest tests/qsurl/test_pg_ilike.py -q`

---

## Test Specification

See the `tests/test_partial_matching_prevalidation.py` block above.

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
9. **Close the task** with `scripts/sdd/close_task.sh TASK-842 filter-with-partial-matching verified`
   — it moves this file to `sdd/tasks/completed/` and marks it `"done"` in the
   index; never move or copy the file by hand
10. **Fill in the Completion Note** below, then commit the staged SDD state

---

## Completion Note

Seat: gpt-5.6-luna · Backend: codex · Model: gpt-5.6-luna · Attempts: 1 · Duration: 309s · Tokens: n/a

**Completed by**: sdd-worker (execution e22852df)
**Date**: 2026-10-07
**Notes**: Pre-dispatch validation wired into AbstractParser._where_element (abstract.pyx/.pxd). Validated by hand (engine selector rejects non-test paths) after building Cython in-tree: 136 passed. One reviewer fix (544a2a9b): the coder's baseline-behaviour test asserted an invented quoted value. Feedback coder-feedback:3e8115afca074cc54a2d387f; review coder-review:07f99b62db3409583d7d7464.

**Deviations from spec**: none
