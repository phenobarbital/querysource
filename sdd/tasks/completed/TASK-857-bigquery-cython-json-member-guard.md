# TASK-857: BigQuery Cython builder: guard the JSON member key before JSON_VALUE extraction

**Feature**: FEAT-162 — BigQuery Rust filter-key hardening
**Spec**: `sdd/specs/fixgroup-34032c139334.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned
**discovered_from**: issue:f5d0b4764384

---

## Context

Promoted by `/sdd-fix` from ledger issue `issue:f5d0b4764384` (vulnerability, minor,
discovered from `spec:FEAT-162`), routed back into the still-open parent FEAT-162 as spec
§9 Module 3. `BigQueryParser._filter_conditions_cy` (`querysource/parsers/bigquery.pyx:246-250`)
renders `JSON_VALUE({field_expr}, '$.{op}')` with the caller-controlled dict key `op`
unvalidated inside a single-quoted literal; `x') = "" OR TRUE OR ('` closes it. The top-level
key is already validated (FEAT-103, `bigquery.pyx:185-195`, invalid → `continue`); the Rust
twin was hardened by TASK-855 (`bq_safe_json_member`). This path matters doubly because
`BigQueryParser.filter_conditions` (`bigquery.pyx:130-137`) falls back to it on any Rust error.

Cython changes need an in-place rebuild inside the worktree before pytest sees them
(`python setup.py build_ext --inplace`, all extensions, ~3 min; artifacts are gitignored).

---

## Scope

- Add module-level `_JSON_MEMBER_PATTERN` below `COMPARISON_TOKENS`.
- In the dict branch, skip the entry (`continue`) when `op` is not a `str` matching the pattern,
  before `json_expr` is built.
- Create `tests/test_bigquery_cython_key_hardening.py` (spec §9 tests table).

**NOT in scope**: the Rust twin (done, TASK-855); the top-level key check (FEAT-103, unchanged);
raising `ParserError` for unsafe keys (spec D1 — drop semantics on both paths); `bq_quote_string`.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/parsers/bigquery.pyx` | MODIFY | module pattern + `continue` guard in the dict branch |
| `tests/test_bigquery_cython_key_hardening.py` | CREATE | Cython-path hostile-key tests + Rust/Cython agreement |

---

## Codebase Contract (Anti-Hallucination)

> VERIFIED on dev `83f4b301` (2026-10-07). Use these exact imports and signatures.

### Verified Imports
```python
import re                                                   # verified: querysource/parsers/bigquery.pyx:8 (already imported)
from querysource.models import QueryObject                  # verified: tests/test_bigquery_partial_matching.py:19
from querysource.parsers import bigquery as bqmod           # verified: tests/test_bigquery_partial_matching.py:22
from querysource.parsers.bigquery import BigQueryParser     # verified: tests/test_bigquery_partial_matching.py:23
```

### Existing Signatures to Use
```cython
# querysource/parsers/bigquery.pyx
COMPARISON_TOKENS = ('>=', '<=', '<>', '!=', '<', '>',)                      # line 32 (occurrences: 1)
cdef str bq_quote_string(object value)                                       # line 35
cdef class BigQueryParser(SQLParser):
    async def filter_conditions(self, sql)                                   # line ~125: Rust first, falls back to _filter_conditions_cy on any Exception
    async def _filter_conditions_cy(self, sql)                               # line 139
#                        # BigQuery: JSON extraction via dict key            # line 246 (occurrences: 1)
#                        json_expr = f"JSON_VALUE({field_expr}, '$.{op}')"  # line 247 (occurrences: 1)
# `op, v = next(reversed(value.items()))` precedes the COMPARISON_TOKENS check (line ~241); `continue` is legal here (inside `for key, value in self.filter.items()`)
```
```python
# tests/test_bigquery_partial_matching.py — harness to copy (module-level, NOT importable as a fixture)
SQL = "SELECT * FROM t {where_cond}"                                                                  # line 25
def _make_parser(filter_: dict, cond_definition: dict | None = None) -> BigQueryParser               # line 65-70
async def _render(path: str, filter_: dict, cond_definition: dict | None = None) -> str              # line 73-77: rust → bqmod._rs.bq_filter_conditions(...); cython → await _make_parser(...)._filter_conditions_cy(SQL)
def _where_body(sql: str) -> str | None                                                               # line 80-84
```

### Does NOT Exist
- ~~`querysource.parsers.bigquery._JSON_MEMBER_PATTERN`~~ — created by this task.
- ~~a shared `is_safe_json_member` helper in `querysource/types/validators.pyx`~~ — keep it file-local like the Rust twin.
- ~~`tests/test_bigquery_partial_matching._make_parser` as an importable fixture~~ — copy the three helper lines; test modules are not packages.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "querysource/parsers/bigquery.pyx",
      "action": "MODIFY"
    },
    {
      "path": "tests/test_bigquery_cython_key_hardening.py",
      "action": "CREATE"
    }
  ],
  "contract_symbols": [
    "sym:querysource/parsers/bigquery.pyx#BigQueryParser._filter_conditions_cy",
    "sym:querysource/parsers/bigquery.pyx#BigQueryParser.filter_conditions"
  ]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Add `_JSON_MEMBER_PATTERN` below `COMPARISON_TOKENS` — *why*: one compiled pattern, identical to the Rust `JSON_MEMBER_PATTERN` (spec D2).
2. Guard the dict branch with `continue` — *why*: drop semantics on both paths (spec D1); `continue` is what FEAT-103 does for the top-level key.
3. Create the test file, rebuild in place, run the Validation Commands.

### `querysource/parsers/bigquery.pyx` (MODIFY — pattern)
```cython
# occurrences: 1 (verified: grep -c "COMPARISON_TOKENS = ('>=', '<=', '<>', '!=', '<', '>',)" querysource/parsers/bigquery.pyx) — line 32
# AFTER — insert below that line:

# SECURITY (FEAT-162 / TASK-857): dotted JSON member path allowed inside JSON_VALUE(f, '$.<member>');
# identical to JSON_MEMBER_PATTERN in rust/src/bigquery_parser.rs.
_JSON_MEMBER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
```

### `querysource/parsers/bigquery.pyx` (MODIFY — guard)
```cython
# occurrences: 1 (verified: grep -c '                        # BigQuery: JSON extraction via dict key' querysource/parsers/bigquery.pyx) — line 246
# AFTER — insert below that comment line (before `json_expr = ...`):
                        # SECURITY (FEAT-162 / TASK-857): the member key lands inside a string
                        # literal — skip the entry unless it is a dotted identifier path.
                        if not (isinstance(op, str) and _JSON_MEMBER_PATTERN.match(op)):
                            continue
```

### `tests/test_bigquery_cython_key_hardening.py` (CREATE)
```python
"""FEAT-162 / TASK-857: hostile dict member keys never reach the SQL rendered by the Cython BigQuery builder."""
import pytest

from querysource.models import QueryObject
from querysource.parsers import bigquery as bqmod
from querysource.parsers.bigquery import BigQueryParser

SQL = "SELECT * FROM t {where_cond}"
HOSTILE_MEMBER = "x') = \"\" OR TRUE OR ('"
SAFE_JSON = "JSON_VALUE(meta, '$.region') = \"us\""


def _make_parser(filter_: dict) -> BigQueryParser:
    """Build a parser wired directly to the Cython filtering path."""
    parser = BigQueryParser(definition=None, conditions=QueryObject(query_raw=SQL), query=SQL)
    parser.cond_definition = {}
    parser.filter = filter_
    return parser


async def _cython(filter_: dict) -> str:
    return await _make_parser(filter_)._filter_conditions_cy(SQL)

# FILL IN: the four tests of spec §9 — bounded by AC7/AC8; the agreement test is skipif-guarded
# by a Rust probe (`"OR TRUE" not in bqmod._rs.bq_filter_conditions(...)`), reason "stale _qs_parsers (TASK-855)"
```

### FILL IN checklist
- [ ] four tests per spec §9 — bounded by AC7/AC8.

---

## Acceptance Criteria

- [ ] AC7. `_filter_conditions_cy` never renders `JSON_VALUE(..., '$.<op>')` for an `op` that is not a dotted identifier path; the entry is skipped, siblings still render.
- [ ] AC8. `pytest tests/test_bigquery_cython_key_hardening.py tests/test_bigquery_partial_matching.py -q` passes after `python setup.py build_ext --inplace`.
- [ ] Safe keys render byte-for-byte as before (FEAT-180 corpus green).

---

## Validation Commands

> File-level pytest only — no directories, no package roots.

- `pytest tests/test_bigquery_cython_key_hardening.py -q`
- `pytest tests/test_bigquery_partial_matching.py -q`

---

## Test Specification

```python
async def test_cython_rejects_hostile_dict_member() -> None:
    rendered = await _cython({"other": {HOSTILE_MEMBER: "v"}, "meta": {"region": "us"}})
    assert "OR TRUE" not in rendered
    assert "JSON_VALUE(other" not in rendered
    assert SAFE_JSON in rendered
```

---

## Agent Instructions

1. **Work in the feature worktree** — `python -m scripts.sdd.ensure_worktree --slug fixgroup-34032c139334 --feature-id FEAT-162` (existing branch `feat-FEAT-162-fixgroup-34032c139334`, PR #651)
2. **Read the spec** §9 Amendment
3. **Check dependencies** — none
4. **Verify the Codebase Contract** — re-run `grep -c` for both MODIFY anchors; `0` means STOP
5. **Update status** in `sdd/tasks/index/fixgroup-34032c139334.json` → `"in-progress"` and commit only that index file
6. **Implement**, rebuild in place, **verify** the Validation Commands
7. **Commit the code** — only the two files above
8. **Close the task** with `scripts/sdd/close_task.sh TASK-857 fixgroup-34032c139334 verified`
9. **Fill in the Completion Note** below, then commit the staged SDD state

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: agent:sdd-fix (Claude Code session 5141c3af)
**Date**: 2026-10-07
**Notes**: Implemented in commit ac4cd9c1. `_JSON_MEMBER_PATTERN` added below `COMPARISON_TOKENS`;
the dict branch of `_filter_conditions_cy` skips the entry (`continue`) unless `op` is a `str`
matching the pattern. Validation in the worktree after `python setup.py build_ext --inplace`:
`pytest tests/test_bigquery_cython_key_hardening.py tests/test_bigquery_partial_matching.py
tests/test_bigquery_key_hardening.py -q` → 72 passed, 0 skipped (Rust/Cython agreement test ran
against the TASK-855 extension).

**Deviations from spec**: spec §9 lists `{"meta": {1: "v"}}` as "renders nothing"; in practice the
builder declares the key as `cdef str`, so a non-string member raises `TypeError` before the guard
(pre-existing, fail-closed). The test asserts that raise instead.
