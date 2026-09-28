# TASK-789: Document JSONB-array grouping & aggregation

**Feature**: FEAT-153 — Group & Aggregate by JSONB Array Elements (PostgreSQL)
**Spec**: `sdd/specs/group-aggregation-jsonb-columns.spec.md`
**Status**: done
**Priority**: low
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-784
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 5, AC14. Slug authors and API callers need one page explaining the path
syntax, element vs row filters (and the counting pitfall), `having`, buckets, the slug
`jsonb_unnest` declaration, `safe_cast` limits, the opt-in pre-filter, the empty-array
policy, errors and non-goals. Examples must be copied from real rendered output.

---

## Scope

- Create `docs/JSONB_AGGREGATION.md`.
- Generate every SQL example by running `pgSQLParser.build_query` (Cython path) on the
  example request — do not hand-write SQL.

**NOT in scope**: README changes; qsurl docs.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `docs/JSONB_AGGREGATION.md` | CREATE | Feature documentation |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.models import QueryObject           # querysource/models.py:24 (for generating examples)
from querysource.parsers.pgsql import pgSQLParser    # querysource/providers/pg.py:12
```

### Existing Signatures to Use
```python
# tests/test_pgsql_jsonb_unnest.py (TASK-784) — `_make(query, **attrs)` + build_query pattern to render examples
# Grammar, rendering rules and messages: TASK-780 / TASK-782 / TASK-783 Implementation Notes
# Existing docs style reference: docs/QSURL.md
```

### Does NOT Exist
- ~~An existing JSONB filter doc to extend~~ — this page is new; it may briefly recap `@>`/`@>|` row filters.
- ~~`date_trunc` passthrough~~, ~~multiple arrays~~, ~~is_raw support~~, ~~SQL functions as filter values~~ — document them as NOT supported.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "docs/JSONB_AGGREGATION.md", "action": "CREATE"}
  ],
  "contract_symbols": []
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Render each example request with the parser and paste the SQL — *why*: docs must match real output.
2. Fill every section of the outline below.

### `docs/JSONB_AGGREGATION.md` (CREATE)
```markdown
# Grouping & aggregating by JSONB array elements (PostgreSQL)

FEAT-153. Available on PostgreSQL slugs rendered by the parser (not `is_raw` slugs).

## Quick example — graduates per course
<request JSON from spec §2 New Public Interfaces> → <rendered SQL> → <sample rows>

## Path syntax
`<column>[].<key>[.<key>…][::cast]`; allowed casts; keys `[A-Za-z0-9_-]`; no spaces.

## Aggregates and time buckets
count(*), count(x), count(distinct x), min/max/sum/avg; year/quarter/month/week/day; implicit casts; default aliases; `as`.

## Element filters vs row filters
Path-keyed filters (per element) vs `@>` / `@>|` (per row); the counting pitfall with a worked example.
Operators, `!` negation, lists, null; values are always literals.

## having
Keyed by alias or aggregate; operators; AND semantics.

## Ordering and paging
Aliases in ordering; LIMIT/OFFSET apply to groups.

## Slug declaration (`attributes.jsonb_unnest`)
columns / empty / safe_cast / prefilter, aliases, strict — with an example QueryModel attributes JSON.

## Empty, NULL and non-array values
exclude (default) vs include; count(*) vs count(path) under include.

## safe_cast
Regex guards; the impossible-date limitation (`2025-02-30`) on PG 12–15.

## Pre-filter (opt-in)
What it does (GIN index use), when it is safe (string-valued keys), why it is off by default.

## Errors
Table of `jsonb_unnest: ...` messages → HTTP 400.

## Not supported
is_raw slugs, other dialects, more than one array column, nested arrays, add_fields, SQL functions as filter values.
```
FILL IN: every `<...>` and section body — bounded by AC14 and the TASK-780/782/783 tables.

### FILL IN checklist
- [ ] all sections written; every SQL block produced by the parser

---

## Acceptance Criteria

- [ ] `docs/JSONB_AGGREGATION.md` covers every item listed in spec §3 M5 (AC14).
- [ ] Every SQL example is real parser output (state the command used in the Completion Note).
- [ ] `pytest tests/test_pgsql_jsonb_unnest.py -q` passes (sanity: examples come from a working build).

---

## Validation Commands

- `pytest tests/test_pgsql_jsonb_unnest.py -q`

---

## Test Specification

Documentation task — no new tests.

---

## Agent Instructions

1. Confirm TASK-784 is completed (TASK-783's filter semantics are included through it).
2. Write the page.
3. Move this file to `sdd/tasks/completed/`, set index status `"done"`, fill the Completion Note.

---

## Completion Note

**Completed by**: sdd-worker (sequential fallback, Claude)
**Date**: 2026-09-28T22:45:35+00:00
**Notes**: All SQL blocks are real Cython-path parser output generated with a throwaway script (PYTHONPATH=<worktree> python render_examples.py; env vars SITE_ROOT/DBUSER/PG_USER dummies). Sample result rows in the quick example are illustrative and marked as such. Note: non-plan raw-SQL fields (e.g. 'a;drop') remain pass-through as before, by design (spec).

**Deviations from spec**: none
