# TASK-847: SQL Server Cython builder: partial-matching dict branch (M6, Cython half)

**Feature**: FEAT-180 — Partial-matching operators for where_cond / filter dict values
**Spec**: `sdd/specs/filter-with-partial-matching.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-840, TASK-842
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 6 (Cython half). `msSQLParser._filter_conditions_cy` has **no** dict branch:
a dict value falls through to the final `else` and renders
`{key} = {Entity.escapeString(value)}` (`sqlserver.pyx:174-177`). This task intercepts only
partial-matching dicts; every other dict keeps today's fallthrough (no behaviour change).

Rendering is the generic-SQL form (spec §2) with `mssql_like_literal` (T-SQL has no
backslash escapes, so only `'` is doubled) and `like_escape_bang`, which also escapes `[`
— T-SQL reads `[...]` as a character class inside `LIKE` (spec §8 Q2 resolved yes).
`LOWER()` and `ESCAPE '!'` are valid T-SQL.

The Rust parameter of the dual-path harness is skipped automatically while the shared
`_qs_parsers` extension predates the Rust twin task — exactly like
`tests/qsurl/test_pg_ilike.py:13-38`. TASK-851 rebuilds the extension and turns those
skips into failures if anything is missing. Do NOT run `make build-rust` in this task.

---

## Scope

- Import the helpers; add `cdef str mssql_partial_match_condition(...)`.
- Insert a partial-matching-only dict block before `if isinstance(value, list):`.
- Create `tests/test_mssql_partial_matching.py`.

**NOT in scope**: comparison-token support for SQL Server dicts; the Rust twin (TASK-848).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/parsers/sqlserver.pyx` | MODIFY | helper + partial-matching dict block |
| `tests/test_mssql_partial_matching.py` | CREATE | dual-path tests |

---

## Codebase Contract (Anti-Hallucination)

> **CRITICAL**: This section contains VERIFIED code references from the actual codebase
> (re-verified on `dev` at `71316ad4`, 2026-10-07). The implementing agent MUST use these exact
> imports, class names, and method signatures. **DO NOT** invent, guess, or assume any import,
> attribute, or method not listed here. If you need something not listed, VERIFY it exists first.

### Verified Imports
```python
from ..exceptions import EmptySentence                 # verified: querysource/parsers/sqlserver.pyx:12 (extend to: EmptySentence, ParserError)
from ..types.validators import Entity, field_components   # verified: sqlserver.pyx:11
from .partial_matching import (build_like_pattern, like_escape_bang, mssql_like_literal,
                               validate_partial_match_dict)   # created by TASK-840
from querysource.parsers import sqlserver as mssqlmod   # module object (HAS_RUST, _rs) for tests
from querysource.parsers.sqlserver import msSQLParser
```

### Existing Signatures to Use
```cython
# querysource/parsers/sqlserver.pyx
cdef class msSQLParser(SQLParser)                         # line 23; __init__(self, *args, bint is_procedure=False, **kwargs) line 25
    async def filter_conditions(self, sql)                # line 69 (Rust mssql_filter_conditions in try/except → Cython)
    async def _filter_conditions_cy(self, sql)            # line 80; per key: `_, name, end = field_components(key)[0]` (line 110)
#                if isinstance(value, list):              # line 114  ← insert above
#                elif isinstance(value, (str, int)):      # line 134
#                else:
#                    where_cond.append(f"{key} = {Entity.escapeString(value)}")   # lines 174-177 (today's dict outcome)
```

### Does NOT Exist
- ~~`msSQLParser` dict branch~~ — none today; this task adds a partial-matching-only one.
- ~~`ESCAPE '\'` on SQL Server~~ — use `!`.

---

## Complexity Contract

> Declaration for deterministic complexity routing (FEAT-561). `targets` match the
> Files table exactly.

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "querysource/parsers/sqlserver.pyx",
      "action": "MODIFY"
    },
    {
      "path": "tests/test_mssql_partial_matching.py",
      "action": "CREATE"
    }
  ],
  "contract_symbols": [
    "sym:querysource/parsers/sqlserver.pyx#msSQLParser._filter_conditions_cy",
    "sym:querysource/parsers/partial_matching.py#mssql_like_literal"
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
1. Add the import — *why*: builders look operators up in the shared table (spec §7 "operator lookup, never name matching").
2. Add the module-level `cdef str` rendering helper — *why*: one place per dialect assembles the SQL text from the table entry (spec §2 rendered forms).
3. Insert the dict-branch block — *why*: builders re-validate because `filter_options` reaches them without passing `_where_element` (spec §2 stage 3, AC6).
4. `make build-inplace`, then write the dual-path test file and run the Validation Commands.

### `querysource/parsers/sqlserver.pyx` (MODIFY — imports)
```cython
# occurrences: 1 (verified: grep -c 'from ..exceptions import EmptySentence' querysource/parsers/sqlserver.pyx) — line 12
# REPLACE line 12 with:
from ..exceptions import EmptySentence, ParserError
# AFTER — insert below line 11 (`from ..types.validators import Entity, field_components`, occurrences: 1):
from .partial_matching import (
    build_like_pattern, like_escape_bang, mssql_like_literal, validate_partial_match_dict,
)
```

### `querysource/parsers/sqlserver.pyx` (MODIFY — helper, insert before `cdef class msSQLParser(SQLParser):`)
```cython
# occurrences: 1 (verified: grep -c 'cdef class msSQLParser(SQLParser):' querysource/parsers/sqlserver.pyx) — line 23
cdef str mssql_partial_match_condition(str col, object entry, str operand):
    """Render one partial-matching operator for SQL Server (FEAT-180, spec §2).

    Raises:
        ParserError: for regex operators (PostgreSQL only).
    """
    cdef str like
    cdef str lit
    cdef str esc
    if entry.kind == 'regex':
        raise ParserError(f"{entry.name} on '{col}': regex operators are not supported by this query parser")
    like = 'NOT LIKE' if entry.negated else 'LIKE'
    lit = mssql_like_literal(build_like_pattern(entry, operand, escaper=like_escape_bang))
    esc = " ESCAPE '!'" if entry.escape else ""
    if entry.insensitive:
        return f"LOWER({col}) {like} LOWER({lit}){esc}"
    return f"{col} {like} {lit}{esc}"
```

### `querysource/parsers/sqlserver.pyx` (MODIFY — dict block)
```cython
# occurrences: 1 (verified: grep -c '                if isinstance(value, list):' querysource/parsers/sqlserver.pyx) — line 114
# BEFORE — insert above line 114:
                if isinstance(value, dict):
                    entry = validate_partial_match_dict(
                        key, value, supports_regex=self.supports_regex_filter
                    )
                    if entry is not None:
                        where_cond.append(
                            mssql_partial_match_condition(key, entry, next(iter(value.values())))
                        )
                        continue
                    # other dicts keep today's fallthrough (final else branch)
```

### `tests/test_mssql_partial_matching.py` (CREATE)
```python
"""FEAT-180: SQL Server partial-matching operators (Rust and Cython paths)."""
import pytest

from querysource.exceptions import ParserError
from querysource.models import QueryObject
from querysource.parsers import sqlserver as mssqlmod
from querysource.parsers.sqlserver import msSQLParser

SQL = "SELECT * FROM t {where_cond}"
CORPUS = [
    ({"n": {"startswith": "a[b"}}, "n LIKE 'a![b%' ESCAPE '!'"),
    ({"n": {"icontains": "o'brien"}}, "LOWER(n) LIKE LOWER('%o''brien%') ESCAPE '!'"),
    ({"n": {"contains": "a\\b"}}, "n LIKE '%a\\b%' ESCAPE '!'"),
    # FILL IN: all 16 LIKE-family operators — bounded by spec AC3, AC7
]
# FILL IN: rust probe via mssqlmod._rs.mssql_filter_conditions, PATHS, _render using
# parser._filter_conditions_cy for "cython"; test_mssql_rendering; test_mssql_regex_raises;
# test_mssql_other_dicts_unchanged ({"n": {">=": 5}} renders exactly what it rendered before
# this task — capture it from the pre-change code first) — bounded by spec AC3, AC5, AC13
```

### FILL IN checklist
- [ ] Test file completion — bounded by spec AC3, AC5, AC7, AC13.

---

## Acceptance Criteria

- [ ] The 16 LIKE-family operators render per spec §2 on the Cython path with `mssql_like_literal`; regex raises `ParserError`.
- [ ] Non-partial-matching dicts render exactly as before this task.
- [ ] `make build-inplace` succeeds.

---

## Validation Commands

> File-level pytest only — no directories, no package roots.

- `pytest tests/test_mssql_partial_matching.py -q`

---

## Test Specification

See the `tests/test_mssql_partial_matching.py` block above.

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
9. **Close the task** with `scripts/sdd/close_task.sh TASK-847 filter-with-partial-matching verified`
   — it moves this file to `sdd/tasks/completed/` and marks it `"done"` in the
   index; never move or copy the file by hand
10. **Fill in the Completion Note** below, then commit the staged SDD state

---

## Completion Note

Seat: sonnet · Backend: native · Model: sonnet · Attempts: 1 · Duration: 786s · Tokens: n/a

**Completed by**: sdd-worker (execution e22852df)
**Date**: 2026-10-07
**Notes**: SQL Server Cython partial-matching builder (sqlserver.pyx) + tests. Validated by hand after in-tree Cython build: 65 passed, 41 skipped (Rust-variant cases, TASK-851). Review: zero corrections.

**Deviations from spec**: none
