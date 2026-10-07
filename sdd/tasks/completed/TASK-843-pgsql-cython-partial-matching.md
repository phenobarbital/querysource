# TASK-843: PostgreSQL Cython builder: partial-matching operators (M4, Cython half)

**Feature**: FEAT-180 — Partial-matching operators for where_cond / filter dict values
**Spec**: `sdd/specs/filter-with-partial-matching.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-840, TASK-842
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 4 (Cython half). `pgSQLParser._filter_conditions_cy` renders dict values
through `jsonb_condition` first, then comparison tokens, then the FEAT-152 `ILIKE`
branch. This task adds the partial-matching operators **ahead of** the JSONB check, so a
table operator can never become implicit JSONB containment (spec AC8), and also exempts
table names inside `jsonb_condition` for any other caller of that function.
`pgSQLParser` is the only parser that supports the regex family: it sets
`supports_regex_filter = True`.

Rendering (spec §2, PostgreSQL column): `LIKE`/`ILIKE` (`NOT ` prefix when negated) with
`pg_literal(build_like_pattern(entry, operand, escaper=like_escape))`; regex family as
`~`, `~*`, `!~`, `!~*` with `pg_literal(operand)`. No `ESCAPE` clause: `pg_literal` emits
`E'...'` with doubled backslashes when needed (FEAT-152 test `code ILIKE E'a\\\\%b%'`).

The Rust parameter of the dual-path harness is skipped automatically while the shared
`_qs_parsers` extension predates the Rust twin task — exactly like
`tests/qsurl/test_pg_ilike.py:13-38`. TASK-851 rebuilds the extension and turns those
skips into failures if anything is missing. Do NOT run `make build-rust` in this task.

---

## Scope

- Import the table helpers; add `cdef str partial_match_condition(...)`.
- Exempt table names in `jsonb_condition` next to the `PG_TEXT_OPERATORS` exemption.
- Set `self.supports_regex_filter = True` in `pgSQLParser.__init__`.
- Insert the partial-matching branch at the top of the dict branch of `_filter_conditions_cy`.
- Create `tests/test_pgsql_partial_matching.py` (dual-path harness, all 20 operators).

**NOT in scope**: the Rust twin (TASK-844); the FEAT-152 `ILIKE`/`NOT ILIKE` branch and the
`field~` suffix (must stay byte-identical, spec AC9).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/parsers/pgsql.pyx` | MODIFY | helper, JSONB exemption, flag, dict-branch rendering |
| `tests/test_pgsql_partial_matching.py` | CREATE | dual-path tests (rust/cython) |

---

## Codebase Contract (Anti-Hallucination)

> **CRITICAL**: This section contains VERIFIED code references from the actual codebase
> (re-verified on `dev` at `71316ad4`, 2026-10-07). The implementing agent MUST use these exact
> imports, class names, and method signatures. **DO NOT** invent, guess, or assume any import,
> attribute, or method not listed here. If you need something not listed, VERIFY it exists first.

### Verified Imports
```python
from ..exceptions import EmptySentence, ParserError            # verified: querysource/parsers/pgsql.pyx:14 (already imported)
from ..types.validators import Entity, field_components, is_integer, is_camel_case, is_valid   # verified: pgsql.pyx:16
from .partial_matching import (PARTIAL_MATCH_OPERATORS, build_like_pattern, like_escape,
                               validate_partial_match_dict)   # created by TASK-840
from querysource.parsers import pgsql                          # verified: tests/qsurl/test_pg_ilike.py:7
from querysource.parsers.pgsql import pgSQLParser              # verified: tests/qsurl/test_pg_ilike.py:8
from querysource.models import QueryObject                     # verified: tests/qsurl/test_pg_ilike.py:6
```

### Existing Signatures to Use
```cython
# querysource/parsers/pgsql.pyx
PG_TEXT_OPERATORS = ('ILIKE', 'NOT ILIKE',)                    # line 36
cdef str pg_literal(str value)                                 # line 41
cdef tuple jsonb_condition(str col, dict value)                # line 234; `op, operand = next(iter(value.items()))` then
#    if op in PG_TEXT_OPERATORS:                                # line 270
#        # qsurl text-match operators (FEAT-152) are handled by the caller's dict
#        ...
#        return (False, None)
cdef class pgSQLParser(SQLParser):                             # line 298
    def __init__(self, *args, **kwargs):                       # line 301
        super(pgSQLParser, self).__init__(*args, **kwargs)     # line 302
        self.schema_based = True                               # line 303
    async def filter_conditions(self, sql)                     # line 305 (Rust first, except Exception → Cython)
    async def _filter_conditions_cy(self, sql)                 # line 315; dict branch:
#                if isinstance(value, dict):                   # line 359
#                    handled, cond = jsonb_condition(key, value)   # line 360
```
```python
# tests/qsurl/test_pg_ilike.py:13-57 — harness to copy: _rust_supports_ilike probe, PATHS with
# pytest.param("rust", marks=skipif(...)), _make_parser(query, filter_), _render(path, filter_),
# _where_body(sql)
```

### Does NOT Exist
- ~~`Entity.pg_literal`~~ — `pg_literal` is a module-level `cdef` in pgsql.pyx (line 41).
- ~~`PG_TEXT_OPERATORS` containing `like`/`startswith`~~ — leave that tuple untouched.
- ~~`pgSQLParser.supports_regex_filter` before TASK-842~~ — declared in `abstract.pxd` by TASK-842.

---

## Complexity Contract

> Declaration for deterministic complexity routing (FEAT-561). `targets` match the
> Files table exactly.

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "querysource/parsers/pgsql.pyx",
      "action": "MODIFY"
    },
    {
      "path": "tests/test_pgsql_partial_matching.py",
      "action": "CREATE"
    }
  ],
  "contract_symbols": [
    "sym:querysource/parsers/pgsql.pyx#pg_literal",
    "sym:querysource/parsers/pgsql.pyx#jsonb_condition",
    "sym:querysource/parsers/pgsql.pyx#pgSQLParser._filter_conditions_cy",
    "sym:querysource/parsers/pgsql.pyx#pgSQLParser.__init__",
    "sym:querysource/parsers/partial_matching.py#validate_partial_match_dict"
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
5. Set the regex flag in `__init__` — *why*: PostgreSQL is the only dialect with `~`/`~*` (spec G4).

### `querysource/parsers/pgsql.pyx` (MODIFY — import)
```cython
# occurrences: 1 (verified: grep -c 'from ..types.validators import' querysource/parsers/pgsql.pyx) — line 16
# AFTER — insert below line 16 (`from ..types.validators import Entity, field_components, is_integer, is_camel_case, is_valid`)
from .partial_matching import (
    PARTIAL_MATCH_OPERATORS, build_like_pattern, like_escape, validate_partial_match_dict,
)
```

### `querysource/parsers/pgsql.pyx` (MODIFY — helper)
```cython
# occurrences: 1 (verified: grep -c 'cdef tuple jsonb_condition(str col, dict value):' querysource/parsers/pgsql.pyx)
# BEFORE — insert above `cdef tuple jsonb_condition(str col, dict value):` (verified: pgsql.pyx:234)
cdef str partial_match_condition(str col, object entry, str operand):
    """Render one partial-matching operator for PostgreSQL (FEAT-180, spec §2).

    Args:
        col: column expression (the filter key, as the comparison branch uses it).
        entry: the PartialMatchOp returned by validate_partial_match_dict.
        operand: the raw string operand (never pre-quoted).

    Returns:
        ``col [NOT ]LIKE|ILIKE <literal>`` or ``col ~|~*|!~|!~* <literal>``.
    """
    cdef str sql_op
    if entry.kind == 'regex':
        sql_op = ('!~' if entry.negated else '~') + ('*' if entry.insensitive else '')
        return f"{col} {sql_op} {pg_literal(operand)}"
    sql_op = 'ILIKE' if entry.insensitive else 'LIKE'
    if entry.negated:
        sql_op = 'NOT ' + sql_op
    return f"{col} {sql_op} {pg_literal(build_like_pattern(entry, operand, escaper=like_escape))}"
```

### `querysource/parsers/pgsql.pyx` (MODIFY — JSONB exemption)
```cython
# occurrences: 1 (verified: grep -c '    if op in PG_TEXT_OPERATORS:' querysource/parsers/pgsql.pyx)
# BEFORE — insert above `    if op in PG_TEXT_OPERATORS:` (verified: pgsql.pyx:270)
    if op in PARTIAL_MATCH_OPERATORS:
        # FEAT-180: partial-matching operators are rendered by the caller, never as JSONB containment.
        return (False, None)
```

### `querysource/parsers/pgsql.pyx` (MODIFY — regex flag)
```cython
# occurrences: 1 (verified: grep -c '    def __init__(self, *args, **kwargs):' querysource/parsers/pgsql.pyx) — line 301, inside pgSQLParser
# occurrences of `        self.schema_based = True`: 1 (verified: grep -c, pgsql.pyx:303 inside pgSQLParser.__init__)
# AFTER — insert below `        self.schema_based = True` (verified: pgsql.pyx:303)
        self.supports_regex_filter = True
```

### `querysource/parsers/pgsql.pyx` (MODIFY — dict branch)
```cython
# occurrences: 1 (verified: grep -c '                    handled, cond = jsonb_condition(key, value)' querysource/parsers/pgsql.pyx)
# BEFORE — insert above `                    handled, cond = jsonb_condition(key, value)` (verified: pgsql.pyx:360)
                    entry = validate_partial_match_dict(
                        key, value, supports_regex=self.supports_regex_filter
                    )
                    if entry is not None:
                        where_cond.append(
                            partial_match_condition(key, entry, next(iter(value.values())))
                        )
                        continue
```
**Why**: running before `jsonb_condition` guarantees AC8 on this path; the validator
raises `ParserError` for operands that arrived through `filter_options` (AC6).

### `tests/test_pgsql_partial_matching.py` (CREATE)
```python
"""FEAT-180: PostgreSQL partial-matching operators — Rust and Cython builders agree."""
from __future__ import annotations

import pytest

from querysource.exceptions import ParserError
from querysource.models import QueryObject
from querysource.parsers import pgsql
from querysource.parsers.pgsql import pgSQLParser

SQL = "SELECT * FROM t {where_cond}"


def _rust_supports_partial_match() -> bool:
    if not pgsql.HAS_RUST:
        return False
    rendered = pgsql._rs.pgsql_filter_conditions(SQL, {"n": {"startswith": "andre"}}, {})
    return "n LIKE 'andre%'" in rendered


RUST_AVAILABLE = _rust_supports_partial_match()
PATHS = [
    pytest.param("rust", marks=pytest.mark.skipif(
        not RUST_AVAILABLE,
        reason="stale _qs_parsers: run `make build-rust && make stage-rust` (TASK-851)")),
    "cython",
]

CORPUS = [  # (filter, expected WHERE body) — spec §2 PostgreSQL column
    ({"full_name": {"startswith": "andre"}}, "full_name LIKE 'andre%'"),
    ({"n": {"istartswith": "andre"}}, "n ILIKE 'andre%'"),
    ({"n": {"not_endswith": "01"}}, "n NOT LIKE '%01'"),
    ({"n": {"icontains": "pilates"}}, "n ILIKE '%pilates%'"),
    ({"n": {"like": "an_re%"}}, "n LIKE 'an_re%'"),
    ({"n": {"regex": "^an.*e$"}}, "n ~ '^an.*e$'"),
    ({"n": {"not_iregex": "^an"}}, "n !~* '^an'"),
    # FILL IN: every remaining operator; escaping cases o'brien, 50%, a_b, a\b, {json};
    # JSONB exemption {"meta": {"contains": "abc"}} → "meta LIKE '%abc%'" — bounded by spec AC3, AC7, AC8
]


def _make_parser(filter_: dict) -> pgSQLParser:
    parser = pgSQLParser(definition=None, conditions=QueryObject(query_raw=SQL), query=SQL)
    parser.cond_definition = {}
    parser.filter = filter_
    return parser


async def _render(path: str, filter_: dict) -> str:
    if path == "rust":
        return pgsql._rs.pgsql_filter_conditions(SQL, filter_, {})
    return await _make_parser(filter_)._filter_conditions_cy(SQL)


def _where_body(sql: str) -> str | None:
    return sql.split(" WHERE ", 1)[1].strip() if " WHERE " in sql else None


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("filter_,expected", CORPUS)
async def test_pg_rendering(path, filter_, expected):
    assert _where_body(await _render(path, filter_)) == expected

# FILL IN: test_pg_regex_flag_true (pgSQLParser().supports_regex_filter is True and
# set_where({"n": {"regex": "^a"}}, None) does not raise); test_pg_cython_revalidates
# (filter {"n": {"contains": "ab"}} set directly → _filter_conditions_cy raises ParserError);
# test_pg_legacy_ilike_and_suffix_untouched ({"city": {"ILIKE": "'%san%'"}} and {"name~": "'ab'"}
# render exactly as before) — bounded by spec AC5, AC6, AC9
```

### FILL IN checklist
- [ ] Complete CORPUS and the three named tests — bounded by spec AC3, AC5–AC9.

---

## Acceptance Criteria

- [ ] All 20 operators render per spec §2 PostgreSQL column on the Cython path (rust param skipped until TASK-851).
- [ ] Operands with `'`, `%`, `_`, `\` and `{`/`}` render through `pg_literal` (E'' form where needed) and wildcards in operands never act as wildcards.
- [ ] `{"meta": {"contains": "abc"}}` never renders `@>`; existing `tests/test_pgsql_jsonb_filters.py` and `tests/qsurl/test_pg_ilike.py` stay green.
- [ ] `_filter_conditions_cy` raises `ParserError` for an invalid operand set directly on `parser.filter`.
- [ ] `make build-inplace` succeeds.

---

## Validation Commands

> File-level pytest only — no directories, no package roots.

- `pytest tests/test_pgsql_partial_matching.py -q`
- `pytest tests/test_pgsql_jsonb_filters.py -q`
- `pytest tests/qsurl/test_pg_ilike.py -q`

---

## Test Specification

See the `tests/test_pgsql_partial_matching.py` block above.

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
9. **Close the task** with `scripts/sdd/close_task.sh TASK-843 filter-with-partial-matching verified`
   — it moves this file to `sdd/tasks/completed/` and marks it `"done"` in the
   index; never move or copy the file by hand
10. **Fill in the Completion Note** below, then commit the staged SDD state

---

## Completion Note

Seat: gpt-5.6-terra · Backend: codex · Model: gpt-5.6-terra · Attempts: 1 · Duration: 394s · Tokens: n/a

**Completed by**: sdd-worker (execution e22852df)
**Date**: 2026-10-07
**Notes**: PostgreSQL Cython partial-matching builder (pgsql.pyx) + tests. Validated by hand after in-tree Cython build: 161 passed, 20 skipped (Rust-variant cases guarded on stale _qs_parsers; covered by TASK-851). Review: zero corrections.

**Deviations from spec**: none
