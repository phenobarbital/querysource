# TASK-779: Freeze pre-feature pg SQL rendering (byte-identical regression fixtures)

**Feature**: FEAT-153 — Group & Aggregate by JSONB Array Elements (PostgreSQL)
**Spec**: `sdd/specs/group-aggregation-jsonb-columns.spec.md`
**Status**: done
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec goal G7 / AC1: every query that does **not** use the new syntax must render
byte-identical SQL on the Rust and Cython paths after FEAT-153. This task freezes
the CURRENT output of `pgSQLParser.build_query` for a matrix of existing query
shapes **before** the planner is wired in (TASK-784 depends on this task), so the
regression file records pre-feature truth, not post-feature output.

---

## Scope

- Create `tests/test_pgsql_jsonb_unnest_regression.py` with a case matrix (below)
  rendered through `pgSQLParser.build_query` on both code paths (`HAS_RUST` True/False).
- Capture each case's SQL with the **current** code (one-off, see Steps) and paste the
  strings as literal expected values — one per `(case_id, path)` — because Rust and
  Cython may differ in whitespace today and each must stay as it is.
- Assert equality per path.

**NOT in scope**: any change to `querysource/` code; plan-mode tests (TASK-784).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/test_pgsql_jsonb_unnest_regression.py` | CREATE | Frozen byte-identical rendering matrix |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.models import QueryObject            # verified: querysource/models.py:24
from querysource.parsers import pgsql                 # verified: tests/test_pgsql_jsonb_filters.py:20
from querysource.parsers.pgsql import pgSQLParser     # verified: querysource/providers/pg.py:12
```

### Existing Signatures to Use
```python
# tests/test_pgsql_jsonb_filters.py:34 — pattern to copy (bypasses set_options, which needs Redis)
def _make_parser(query: str, filter_: dict) -> pgSQLParser:
    parser = pgSQLParser(definition=None, conditions=QueryObject(query_raw=query), query=query)
    parser.cond_definition = {}
    parser.filter = filter_
    return parser
# tests/test_pgsql_jsonb_filters.py:178 — HAS_RUST monkeypatch parametrisation pattern

# querysource/parsers/pgsql.pyx
HAS_RUST: bool                                                           # lines 19-23
async def build_query(self, querylimit: int = None, offset: int = None)  # line 458
# public parser attributes (querysource/parsers/abstract.pxd): fields (list, :19), ordering (list, :20),
# grouping (list, :21), schema (str, :26), tablename (str, :24), filter (dict, :17), querylimit (int), _offset (int32)
```

### Does NOT Exist
- ~~`pgSQLParser.having`~~ — added by TASK-781; do NOT reference it here.
- ~~A shared golden-SQL fixture module~~ — expected strings live in this test file only.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "tests/test_pgsql_jsonb_unnest_regression.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/parsers/pgsql.pyx#pgSQLParser.build_query",
    "sym:querysource/models.py#QueryObject"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Capture expected strings from the **unmodified** base (`dev` before TASK-784). If
  TASK-784 has already landed in your worktree, STOP and report — the fixtures would be
  worthless.
- Paste strings verbatim (use `repr()` output) — no normalisation of whitespace.
- A path whose capture raises must be frozen as the exception type (`pytest.raises`).

### References in Codebase
- `tests/test_pgsql_jsonb_filters.py` — `_make_parser`, `PATHS`, monkeypatch pattern.

---

## Implementation Blueprint

### Steps (in order)
1. Write the test module skeleton below — *why*: the `CASES` builder is shared by the capture step and the test.
2. Run a one-off capture (not committed): `python - <<'EOF'` importing `CASES`/`_render` from the new module and printing `repr()` of each `(case_id, path)` output with `pgsql.HAS_RUST` forced True then False — *why*: the expected values must be the current parser's truth.
3. Paste the printed strings into `EXPECTED` — *why*: literal strings make any later drift a visible diff.
4. Run the validation command; it must pass on the untouched base.

### `tests/test_pgsql_jsonb_unnest_regression.py` (CREATE)
```python
"""FEAT-153 G7 regression: queries without JSONB-unnest syntax render byte-identical SQL.

Expected strings were captured from the pre-FEAT-153 parser (TASK-779) on both
code paths; any drift after the planner is wired in (TASK-784) is a regression.
"""
from __future__ import annotations

from typing import Any

import pytest

from querysource.models import QueryObject
from querysource.parsers import pgsql
from querysource.parsers.pgsql import pgSQLParser

WHERE_SQL = "SELECT * FROM t {where_cond}"
TABLE_SQL = "SELECT {fields} FROM {schema}.{table} {filter} {grouping} {offset} {limit}"

# case_id -> (query_raw, attribute overrides, build_query kwargs)
CASES: dict[str, tuple[str, dict[str, Any], dict[str, Any]]] = {
    "scalar_filter": (WHERE_SQL, {"filter": {"status": "'active'"}}, {"querylimit": 10}),
    "group_and_count": (
        WHERE_SQL,
        {"fields": ["store_id", "count(*)"], "grouping": ["store_id"], "ordering": ["store_id DESC"]},
        {},
    ),
    "jsonb_contains": (WHERE_SQL, {"filter": {"attrs": {"@>": {"status": "active"}}}}, {"querylimit": 10}),
    "jsonb_any_of": (
        WHERE_SQL,
        {"filter": {"graduation_details": {"@>|": [[{"course": "Pilates Studio"}], [{"course": "Pilates Mat"}]]}}},
        {},
    ),
    "jsonb_path_text": (WHERE_SQL, {"filter": {"attrs": {"->>": {"status": "active"}}}}, {}),
    "array_cast_field": (WHERE_SQL, {"fields": ["tags::text[]", "id"]}, {}),
    "table_template": (
        TABLE_SQL,
        {"fields": ["a", "b"], "schema": "public", "tablename": "students", "filter": {"licensee": "'Asia'"}},
        {"querylimit": 5, "offset": 10},
    ),
    "ordering_only": (WHERE_SQL, {"ordering": ["created_at DESC"]}, {}),
    # FILL IN: add 2-4 more shapes you find in tests/test_pgsql_jsonb_filters.py or
    # tests/test_rust_parsers.py (e.g. list IN filter, negated key `status!`) — bounded by AC1:
    # only shapes that exist today, no new syntax.
}


def _make(case_id: str) -> tuple[pgSQLParser, dict[str, Any]]:
    """Build a parser for ``case_id`` bypassing ``set_options`` (needs Redis)."""
    query, attrs, kwargs = CASES[case_id]
    parser = pgSQLParser(definition=None, conditions=QueryObject(query_raw=query), query=query)
    parser.cond_definition = {}
    for name, value in attrs.items():
        setattr(parser, name, value)
    return parser, kwargs


async def _render(case_id: str) -> str:
    """Render ``case_id`` through ``build_query`` on the currently selected path."""
    parser, kwargs = _make(case_id)
    return await parser.build_query(**kwargs)


EXPECTED: dict[tuple[str, str], str] = {
    # FILL IN: paste the captured repr() strings, one entry per (case_id, "rust"|"cython")
    # — bounded by Step 2: values come from the unmodified parser, never hand-written.
}


@pytest.mark.parametrize("use_rust", [
    pytest.param(True, marks=pytest.mark.skipif(not pgsql.HAS_RUST, reason="qs_parsers not built")),
    False,
])
@pytest.mark.parametrize("case_id", sorted(CASES))
async def test_non_plan_queries_byte_identical(case_id: str, use_rust: bool, monkeypatch) -> None:
    """Every pre-existing query shape renders exactly the frozen SQL."""
    monkeypatch.setattr(pgsql, "HAS_RUST", use_rust)
    path = "rust" if use_rust else "cython"
    assert await _render(case_id) == EXPECTED[(case_id, path)]
```
**Why this shape**: the per-path `EXPECTED` map encodes G7 exactly ("unchanged on both paths")
without assuming Rust and Cython agree today. Attributes are set directly because
`set_options` needs Redis. Do not add plan-mode cases here.

### FILL IN checklist
- [ ] `CASES` — 2–4 extra existing shapes; bounded by AC1 (no new syntax)
- [ ] `EXPECTED` — captured literal strings for every `(case_id, path)`; bounded by Step 2

---

## Acceptance Criteria

- [ ] `EXPECTED` holds a captured string for every case on both paths (Rust entries may be skipped only when `_qs_parsers` is not built on the capturing machine — then note it in the Completion Note).
- [ ] Tests pass on the unmodified base.
- [ ] `ruff check tests/test_pgsql_jsonb_unnest_regression.py` is clean.

---

## Validation Commands

- `pytest tests/test_pgsql_jsonb_unnest_regression.py -q`

---

## Test Specification

The module above is the test. Minimum: 10 case shapes × 2 paths.

---

## Agent Instructions

1. Read the spec §5 AC1 and §7.
2. Verify TASK-784 has NOT landed in this worktree (`grep -c "unnest" querysource/parsers/pgsql.pyx` must be `0`).
3. Implement from the blueprint; complete every `FILL IN`.
4. Move this file to `sdd/tasks/completed/`, set the index status to `"done"`, fill the Completion Note.

---

## Completion Note

**Completed by**: sdd-worker (sequential fallback, Claude)
**Date**: 2026-09-28T22:29:07+00:00
**Notes**: 12 cases x 2 paths captured from unmodified parser (Rust HAS_RUST true, Cython). Rust/Cython differ today for comparison_dict (frozen as-is). Env note: tests need SITE_ROOT=<worktree> + dummy DBUSER/PG_USER env and locally built ext (python setup.py build_ext --inplace).

**Deviations from spec**: none
