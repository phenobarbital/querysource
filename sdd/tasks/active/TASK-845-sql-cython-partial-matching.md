# TASK-845: Generic SQL Cython builder: LIKE forms + Rust error fallthrough (M5, Cython half)

**Feature**: FEAT-180 — Partial-matching operators for where_cond / filter dict values
**Spec**: `sdd/specs/filter-with-partial-matching.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-840, TASK-842
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 5 (Cython half). `SQLParser` is the generic parser (MySQL via
`mysqlProvider`, SQLite and others). Its dict branch (`sql.pyx:152-163`) accepts only
comparison tokens and silently discards everything else. Its Rust fast path
(`sql.pyx:118-119`) has **no** try/except — unlike pgsql/mssql/bq — so a Rust validation
error would surface as a raw `ValueError`. This task adds the same swallow-and-fall-through
wrapper the other parsers have (spec AC10) and the LIKE rendering.

Rendering (spec §2, generic column): `col [NOT ]LIKE <lit>` or, for `i*` operators,
`LOWER(col) [NOT ]LIKE LOWER(<lit>)`; `ESCAPE '!'` is appended **only** for operators
with `entry.escape` (startswith/endswith/contains families), whose operands are escaped
with `like_escape_bang`. `<lit>` = `sql_like_literal(pattern)` (doubles `\` and `'`, safe
on MySQL). Regex operators never get here: `supports_regex_filter` is False, so the
validator raises `ParserError`.

Known limitation (document in the docstring, do not "fix"): on engines that treat `\`
literally (SQLite), a backslash typed in an operand matches two backslashes — the price of
being injection-safe on MySQL with one dialect-agnostic parser.

The Rust parameter of the dual-path harness is skipped automatically while the shared
`_qs_parsers` extension predates the Rust twin task — exactly like
`tests/qsurl/test_pg_ilike.py:13-38`. TASK-851 rebuilds the extension and turns those
skips into failures if anything is missing. Do NOT run `make build-rust` in this task.

---

## Scope

- Import the helpers and `ParserError`; add `cdef str sql_partial_match_condition(...)`.
- Wrap the Rust fast path in `try/except Exception: pass` (fall through to Cython).
- Insert the partial-matching block in the dict branch, before `op, v = next(reversed(...))`.
- Create `tests/test_sql_partial_matching.py`.

**NOT in scope**: Rust twin (TASK-846); SQL Server / BigQuery subclasses (TASK-847/849 — they override their own builders).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/parsers/sql.pyx` | MODIFY | helper, Rust try/except, dict-branch rendering |
| `tests/test_sql_partial_matching.py` | CREATE | dual-path tests (rust/cython via HAS_RUST monkeypatch) |

---

## Codebase Contract (Anti-Hallucination)

> **CRITICAL**: This section contains VERIFIED code references from the actual codebase
> (re-verified on `dev` at `71316ad4`, 2026-10-07). The implementing agent MUST use these exact
> imports, class names, and method signatures. **DO NOT** invent, guess, or assume any import,
> attribute, or method not listed here. If you need something not listed, VERIFY it exists first.

### Verified Imports
```python
from ..exceptions import EmptySentence                 # verified: querysource/parsers/sql.pyx:13 (extend to: EmptySentence, ParserError)
from ..types.validators import Entity, field_components   # verified: sql.pyx:14
from .partial_matching import (build_like_pattern, like_escape_bang, sql_like_literal,
                               validate_partial_match_dict)   # created by TASK-840
from querysource.parsers import sql as sqlmod          # module object for monkeypatching HAS_RUST in tests
from querysource.parsers.sql import SQLParser          # verified: tests/test_sql_parser_combinations.py:20
```

### Existing Signatures to Use
```cython
# querysource/parsers/sql.pyx
HAS_RUST (module global, try-import of _qs_parsers as _rs)          # lines 17-22
COMPARISON_TOKENS = ('>=', '<=', '<>', '!=', '<', '>',)             # line 25
cdef class SQLParser(AbstractParser)                                 # line 85
    async def filter_conditions(self, sql)                           # line 113
#        if HAS_RUST and self.filter:                                # line 118
#            return _rs.filter_conditions(sql, dict(self.filter), dict(self.cond_definition))   # line 119
#   dict branch:
#                if isinstance(value, dict):                         # line 152
#                    if not value:
#                        continue
#                    op, v = next(reversed(value.items()))  # never popitem(): the filter dict is the caller's   # line 155
```
```python
# Monkeypatching a Cython module global works: tests/e2e/test_qsurl_dry_run.py does
# monkeypatch.setattr(pgsql, "HAS_RUST", False)
```

### Does NOT Exist
- ~~`SQLParser._filter_conditions_cy`~~ — the generic Cython fallback is inline in `filter_conditions`; force it in tests by monkeypatching `HAS_RUST` to False.
- ~~`Entity.quoteString` for LIKE patterns~~ — strips quote pairs and maps `null`/`true`; use `sql_like_literal`.
- ~~`ESCAPE '\'`~~ on generic SQL — syntax error on MySQL; the escape char is `!`.

---

## Complexity Contract

> Declaration for deterministic complexity routing (FEAT-561). `targets` match the
> Files table exactly.

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "querysource/parsers/sql.pyx",
      "action": "MODIFY"
    },
    {
      "path": "tests/test_sql_partial_matching.py",
      "action": "CREATE"
    }
  ],
  "contract_symbols": [
    "sym:querysource/parsers/sql.pyx#SQLParser.filter_conditions",
    "sym:querysource/parsers/partial_matching.py#validate_partial_match_dict",
    "sym:querysource/parsers/partial_matching.py#sql_like_literal"
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
5. Wrap the Rust call — *why*: spec AC10, a Rust validation error must end as `ParserError` from the Cython path.

### `querysource/parsers/sql.pyx` (MODIFY — imports)
```cython
# occurrences: 1 (verified: grep -c 'from ..exceptions import EmptySentence' querysource/parsers/sql.pyx) — line 13
# REPLACE line 13 with:
from ..exceptions import EmptySentence, ParserError
# occurrences: 1 (verified: grep -c 'from ..types.validators import Entity, field_components' querysource/parsers/sql.pyx) — line 14
# AFTER — insert below line 14:
from .partial_matching import (
    build_like_pattern, like_escape_bang, sql_like_literal, validate_partial_match_dict,
)
```

### `querysource/parsers/sql.pyx` (MODIFY — helper, insert before `cdef class SQLParser(AbstractParser):`)
```cython
# occurrences: 1 (verified: grep -c 'cdef class SQLParser(AbstractParser):' querysource/parsers/sql.pyx) — line 85
cdef str sql_partial_match_condition(str col, object entry, str operand):
    """Render one partial-matching operator for generic SQL (FEAT-180, spec §2).

    Uses ``ESCAPE '!'`` for escaped operators and ``LOWER()`` folding for ``i*``.
    A backslash in an operand is doubled (MySQL-safe); on engines that read it
    literally it matches two backslashes.

    Raises:
        ParserError: for regex operators (PostgreSQL only).
    """
    cdef str like
    cdef str lit
    cdef str esc
    if entry.kind == 'regex':
        raise ParserError(f"{entry.name} on '{col}': regex operators are not supported by this query parser")
    like = 'NOT LIKE' if entry.negated else 'LIKE'
    lit = sql_like_literal(build_like_pattern(entry, operand, escaper=like_escape_bang))
    esc = " ESCAPE '!'" if entry.escape else ""
    if entry.insensitive:
        return f"LOWER({col}) {like} LOWER({lit}){esc}"
    return f"{col} {like} {lit}{esc}"
```

### `querysource/parsers/sql.pyx` (MODIFY — Rust fallthrough)
```cython
# occurrences: 1 (verified: grep -c '            return _rs.filter_conditions(sql, dict(self.filter), dict(self.cond_definition))' querysource/parsers/sql.pyx) — line 119
# REPLACE line 119 with:
            try:
                return _rs.filter_conditions(sql, dict(self.filter), dict(self.cond_definition))
            except Exception:
                pass  # fall through to the Cython implementation (raises ParserError on invalid operands)
```

### `querysource/parsers/sql.pyx` (MODIFY — dict branch)
```cython
# occurrences: 1 (verified: grep -c "op, v = next(reversed(value.items()))  # never popitem(): the filter dict is the caller's" querysource/parsers/sql.pyx) — line 155
# BEFORE — insert above line 155:
                    entry = validate_partial_match_dict(
                        key, value, supports_regex=self.supports_regex_filter
                    )
                    if entry is not None:
                        where_cond.append(
                            sql_partial_match_condition(key, entry, next(iter(value.values())))
                        )
                        continue
```

### `tests/test_sql_partial_matching.py` (CREATE)
```python
"""FEAT-180: generic SQL partial-matching operators (Rust and Cython paths)."""
import pytest

from querysource.exceptions import ParserError
from querysource.models import QueryObject
from querysource.parsers import sql as sqlmod
from querysource.parsers.sql import SQLParser

SQL = "SELECT * FROM t {where_cond}"
CORPUS = [
    ({"n": {"startswith": "andre"}}, "n LIKE 'andre%' ESCAPE '!'"),
    ({"n": {"istartswith": "andre"}}, "LOWER(n) LIKE LOWER('andre%') ESCAPE '!'"),
    ({"n": {"contains": "50%"}}, "n LIKE '%50!%%' ESCAPE '!'"),
    ({"n": {"like": "an_re%"}}, "n LIKE 'an_re%'"),
    ({"n": {"not_ilike": "a%"}}, "LOWER(n) NOT LIKE LOWER('a%')"),
    # FILL IN: all 16 LIKE-family operators; o'brien, a\b, a!b, a[b operands — bounded by spec AC3, AC7
]


def _rust_supports() -> bool:
    if not sqlmod.HAS_RUST:
        return False
    return "n LIKE 'andre%' ESCAPE '!'" in sqlmod._rs.filter_conditions(SQL, {"n": {"startswith": "andre"}}, {})


RUST_AVAILABLE = _rust_supports()
PATHS = [pytest.param("rust", marks=pytest.mark.skipif(not RUST_AVAILABLE, reason="stale _qs_parsers (TASK-851)")), "cython"]


async def _render(path, filter_, monkeypatch):
    if path == "cython":
        monkeypatch.setattr(sqlmod, "HAS_RUST", False)
    parser = SQLParser(definition=None, conditions=QueryObject(query_raw=SQL), query=SQL)
    parser.cond_definition = {}
    parser.filter = filter_
    return await parser.filter_conditions(SQL)


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("filter_,expected", CORPUS)
async def test_sql_rendering(path, filter_, expected, monkeypatch):
    sql = await _render(path, filter_, monkeypatch)
    assert sql.split(" WHERE ", 1)[1].strip() == expected

# FILL IN: test_sql_regex_raises (cython path raises ParserError); test_sql_rust_error_falls_through
# (with HAS_RUST True and an invalid operand, filter_conditions raises ParserError, never ValueError)
# — bounded by spec AC5, AC10
```

### FILL IN checklist
- [ ] CORPUS completion and the two named tests — bounded by spec AC3, AC5, AC7, AC10.

---

## Acceptance Criteria

- [ ] The 16 LIKE-family operators render per spec §2 generic column on the Cython path; regex operators raise `ParserError`.
- [ ] `%`, `_`, `!`, `[` in startswith/endswith/contains operands are `!`-escaped with `ESCAPE '!'`; `'` and `\` are doubled.
- [ ] With `HAS_RUST` True, an invalid operand raises `ParserError` from `filter_conditions` (Rust error swallowed).
- [ ] `tests/test_sql_parser_combinations.py` stays green; `make build-inplace` succeeds.

---

## Validation Commands

> File-level pytest only — no directories, no package roots.

- `pytest tests/test_sql_partial_matching.py -q`
- `pytest tests/test_sql_parser_combinations.py -q`

---

## Test Specification

See the `tests/test_sql_partial_matching.py` block above.

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
9. **Close the task** with `scripts/sdd/close_task.sh TASK-845 filter-with-partial-matching verified`
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
