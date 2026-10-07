# TASK-849: BigQuery Cython builder: partial-matching before JSON_VALUE extraction (M7, Cython half)

**Feature**: FEAT-180 — Partial-matching operators for where_cond / filter dict values
**Spec**: `sdd/specs/filter-with-partial-matching.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-840, TASK-842
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 7 (Cython half). In `BigQueryParser._filter_conditions_cy` a dict value
renders comparison tokens, and **every other key** becomes JSON extraction
`JSON_VALUE(field_expr, '$.<key>') = <v>` (`bigquery.pyx:209-220`). Table names must take
precedence over that extraction (spec AC8, reserved-name rule).

Rendering (spec §2, BigQuery column): `f [NOT ]LIKE <lit>` / `LOWER(f) [NOT ]LIKE LOWER(<lit>)`,
no `ESCAPE` clause (BigQuery LIKE escapes `%`/`_` with `\` natively). The pattern is built
with `like_escape` and quoted with `bq_like_literal`, which doubles `\` — because
`bq_quote_string` (`bigquery.pyx:31-50`) does not escape backslashes, and BigQuery treats
`\` as a string-literal escape (`"\%"` is an illegal escape; `"\\%"` reaches LIKE as `\%`).
This supersedes the "yes" answer to spec §8 Q1, which assumed `bq_quote_string` preserved
the backslash; the evidence is the function body quoted in the contract.

The Rust parameter of the dual-path harness is skipped automatically while the shared
`_qs_parsers` extension predates the Rust twin task — exactly like
`tests/qsurl/test_pg_ilike.py:13-38`. TASK-851 rebuilds the extension and turns those
skips into failures if anything is missing. Do NOT run `make build-rust` in this task.

---

## Scope

- Import the helpers; add `cdef str bq_partial_match_condition(...)`.
- Insert the block before `op, v = next(reversed(value.items()))` in the dict branch.
- Create `tests/test_bigquery_partial_matching.py`.

**NOT in scope**: BigQuery regex (`REGEXP_CONTAINS`) — regex raises `ParserError` here; the Rust twin (TASK-850).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/parsers/bigquery.pyx` | MODIFY | helper + partial-matching block before JSON_VALUE fallback |
| `tests/test_bigquery_partial_matching.py` | CREATE | dual-path tests |

---

## Codebase Contract (Anti-Hallucination)

> **CRITICAL**: This section contains VERIFIED code references from the actual codebase
> (re-verified on `dev` at `71316ad4`, 2026-10-07). The implementing agent MUST use these exact
> imports, class names, and method signatures. **DO NOT** invent, guess, or assume any import,
> attribute, or method not listed here. If you need something not listed, VERIFY it exists first.

### Verified Imports
```python
from ..types.validators import Entity, field_components   # verified: querysource/parsers/bigquery.pyx:12
from ..exceptions import ParserError                     # NOT imported in bigquery.pyx today — add it (exceptions.py:86)
from .partial_matching import (bq_like_literal, build_like_pattern, like_escape,
                               validate_partial_match_dict)   # created by TASK-840
from querysource.parsers import bigquery as bqmod
from querysource.parsers.bigquery import BigQueryParser
```

### Existing Signatures to Use
```cython
# querysource/parsers/bigquery.pyx
COMPARISON_TOKENS = ('>=', '<=', '<>', '!=', '<', '>',)          # line 28
cdef str bq_quote_string(object value)                           # lines 31-50: strips a '...' pair, escapes only `"`, never `\`
cdef class BigQueryParser(SQLParser)                             # bigquery.pxd:5; __cinit__ sets _json_pattern
    async def filter_conditions(self, sql)                       # line 127 (Rust bq_filter_conditions in try/except → Cython)
    async def _filter_conditions_cy(self, sql)                   # line 139; per key computes `field_expr` (plain or JSON_VALUE)
#                if isinstance(value, dict):                     # line 209
#                    if not value:
#                        continue
#                    op, v = next(reversed(value.items()))  # never popitem(): the filter dict is the caller's   # line 212
#                    if op in COMPARISON_TOKENS:                 # line 213
#                    else:  JSON_VALUE({field_expr}, '$.{op}') = ...   # lines 216-220
```

### Does NOT Exist
- ~~`bq_quote_string` escaping backslashes~~ — it does not (see body); use `bq_like_literal`.
- ~~`ESCAPE` clause in BigQuery LIKE~~ — not supported; `\` is the native escape.

---

## Complexity Contract

> Declaration for deterministic complexity routing (FEAT-561). `targets` match the
> Files table exactly.

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "querysource/parsers/bigquery.pyx",
      "action": "MODIFY"
    },
    {
      "path": "tests/test_bigquery_partial_matching.py",
      "action": "CREATE"
    }
  ],
  "contract_symbols": [
    "sym:querysource/parsers/bigquery.pyx#BigQueryParser._filter_conditions_cy",
    "sym:querysource/parsers/bigquery.pyx#bq_quote_string",
    "sym:querysource/parsers/partial_matching.py#bq_like_literal"
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

### `querysource/parsers/bigquery.pyx` (MODIFY — imports)
```cython
# occurrences: 1 (verified: grep -c 'from ..types.validators import' querysource/parsers/bigquery.pyx) — line 12
# AFTER — insert below line 12:
from ..exceptions import ParserError
from .partial_matching import (
    bq_like_literal, build_like_pattern, like_escape, validate_partial_match_dict,
)
```

### `querysource/parsers/bigquery.pyx` (MODIFY — helper, insert before `cdef class BigQueryParser(SQLParser):`)
```cython
# occurrences: 1 (verified: grep -c 'cdef class BigQueryParser(SQLParser):' querysource/parsers/bigquery.pyx) — line 54
cdef str bq_partial_match_condition(str field_expr, object entry, str operand):
    """Render one partial-matching operator for BigQuery (FEAT-180, spec §2).

    Raises:
        ParserError: for regex operators (PostgreSQL only).
    """
    cdef str like
    cdef str lit
    if entry.kind == 'regex':
        raise ParserError(f"{entry.name} on '{field_expr}': regex operators are not supported by this query parser")
    like = 'NOT LIKE' if entry.negated else 'LIKE'
    lit = bq_like_literal(build_like_pattern(entry, operand, escaper=like_escape))
    if entry.insensitive:
        return f"LOWER({field_expr}) {like} LOWER({lit})"
    return f"{field_expr} {like} {lit}"
```

### `querysource/parsers/bigquery.pyx` (MODIFY — dict branch)
```cython
# occurrences: 1 (verified: grep -c "op, v = next(reversed(value.items()))  # never popitem(): the filter dict is the caller's" querysource/parsers/bigquery.pyx) — line 212
# BEFORE — insert above line 212:
                    entry = validate_partial_match_dict(
                        key, value, supports_regex=self.supports_regex_filter
                    )
                    if entry is not None:
                        where_cond.append(
                            bq_partial_match_condition(field_expr, entry, next(iter(value.values())))
                        )
                        continue
```

### `tests/test_bigquery_partial_matching.py` (CREATE)
```python
"""FEAT-180: BigQuery partial-matching operators (Rust and Cython paths)."""
import pytest

from querysource.exceptions import ParserError
from querysource.models import QueryObject
from querysource.parsers import bigquery as bqmod
from querysource.parsers.bigquery import BigQueryParser

SQL = "SELECT * FROM t {where_cond}"
CORPUS = [
    ({"n": {"startswith": "andre"}}, 'n LIKE "andre%"'),
    ({"n": {"icontains": "pil"}}, 'LOWER(n) LIKE LOWER("%pil%")'),
    ({"n": {"contains": "50%"}}, 'n LIKE "%50\\\\%%"'),     # BigQuery literal "%50\\%%" → LIKE sees %50\%%
    ({"n": {"contains": 'say "hi"'}}, 'n LIKE "%say \\"hi\\"%"'),
    # FILL IN: all 16 LIKE-family operators; JSON field_expr case; precedence case
    # {"meta": {"contains": "abc"}} is LIKE while {"meta": {"other": "x"}} stays JSON_VALUE — bounded by spec AC3, AC7, AC8
]
# FILL IN: rust probe via bqmod._rs.bq_filter_conditions, PATHS, _render via
# parser._filter_conditions_cy for "cython"; test_bq_rendering; test_bq_regex_raises — bounded by spec AC3, AC5
```

### FILL IN checklist
- [ ] Test file completion — bounded by spec AC3, AC5, AC7, AC8.

---

## Acceptance Criteria

- [ ] The 16 LIKE-family operators render per spec §2 BigQuery column, on plain and `JSON_VALUE` field expressions; regex raises `ParserError`.
- [ ] Backslashes are doubled and `"` escaped inside the literal (spec §8 Q1 evidence-based resolution).
- [ ] Table names win over `JSON_VALUE` extraction; any other dict key still renders `JSON_VALUE(f, '$.<key>') = ...`.
- [ ] `make build-inplace` succeeds.

---

## Validation Commands

> File-level pytest only — no directories, no package roots.

- `pytest tests/test_bigquery_partial_matching.py -q`

---

## Test Specification

See the `tests/test_bigquery_partial_matching.py` block above.

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
9. **Close the task** with `scripts/sdd/close_task.sh TASK-849 filter-with-partial-matching verified`
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
