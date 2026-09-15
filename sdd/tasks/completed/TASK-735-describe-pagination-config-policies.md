# TASK-735: Pagination helpers, describe config keys and policy grants

**Feature**: FEAT-148 — Describe Query-Slug REST Endpoints
**Spec**: `sdd/specs/describe-queryslug.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

This task implements spec §3 **Module 4**: the backward-compatible groundwork that later tasks build on.
- **Consumers:** the visibility service (TASK-737) reads the config keys; the handler (TASK-740/741) uses the SQL helpers.
- **Policy grants:** the admin grants make the new `slug:describe` and `slug:describe_raw` actions usable.

**Hard constraint (spec AC19):** `build_order_by(params)` with its default arguments must produce exactly the same string as today, because `QueryManager` depends on it.

---

## Scope

- Add a `nulls_last: bool = False` parameter to `build_order_by`.
- Add `compose_where(where, extra_clause)` and `build_scan_sql(schema, table, fields, where, order_by, limit)` to `_pagination.py`.
- Add `QS_DESCRIBE_MAX_SCAN`, `QS_DESCRIBE_ADMIN_GROUPS` and `QS_DESCRIBE_COLUMNS_TIMEOUT` to `querysource/conf.py`.
- Append `slug:describe` and `slug:describe_raw` to `admin_full_access.actions` in `policies/defaults.yaml`.
- Write the unit tests, and extend the policy load test.

**NOT in scope**:
- The visibility logic (TASK-737).
- Handler code (TASK-740).
- Any change to `build_where_clause`, `build_page_sql`, `build_count_sql` or `QueryManager`.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/_pagination.py` | MODIFY | `build_order_by(..., nulls_last)`, `compose_where`, `build_scan_sql` |
| `querysource/conf.py` | MODIFY | 3 config keys |
| `policies/defaults.yaml` | MODIFY | 2 actions |
| `tests/handlers/test_pagination_describe_helpers.py` | CREATE | helper tests |
| `tests/policies/test_default_policies_load.py` | MODIFY | assert new actions |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.handlers._pagination import (          # verified: querysource/handlers/_pagination.py
    PaginationParams,             # :73
    build_order_by,               # :309
    build_where_clause,           # :251
    build_page_sql,               # :352
    FILTERABLE_COLUMNS,           # :48
    SORTABLE_COLUMNS,             # :52
)
# _pagination.py module imports: `from __future__ import annotations` (:24), logging (:26),
#   typing Any/Literal/Optional (:27), pydantic (:29), ..models.QueryModel (:31), ..types.validators.Entity (:32)
from navconfig import config      # already imported in querysource/conf.py
```

### Existing Signatures to Use
```python
# querysource/handlers/_pagination.py
def build_where_clause(params: PaginationParams, extra_filters: dict) -> str:  # :251 → "" or "WHERE ..."
def build_order_by(params: PaginationParams) -> str:                          # :309 (occurrences: 1)
    ...
    direction = "ASC" if params.sort_direction == "asc" else "DESC"           # :328
    return f'ORDER BY "{params.sort_field}" {direction}'                       # :329 (occurrences: 1)
def build_count_sql(schema: str, table: str, where: str) -> str:              # :332 (pattern to mirror)
def build_page_sql(schema, table, fields, where, order_by, limit, offset) -> str:  # :352-401 (pattern to mirror)
def _validate_bare_identifier(value: str, kind: str) -> None:                  # :404 ([A-Za-z0-9_], not leading digit)

# querysource/conf.py
QS_QUERIES_TABLE = config.get('QS_QUERIES_TABLE', fallback='queries')         # :354 (occurrences: 1)

# policies/defaults.yaml — admin_full_access.actions list ends with:
      - "raw_query:execute"                                                   # :26 (occurrences: 1)
# tests/policies/test_default_policies_load.py: POLICY_DIR, test_defaults_has_admin_allow (:45) — yaml.safe_load style
```

### Does NOT Exist
- ~~`compose_where`, `build_scan_sql`~~: added by this task.
- ~~`NULLS LAST` support in `build_order_by`~~: added by this task, off by default.
- ~~`QS_DESCRIBE_MAX_SCAN`, `QS_DESCRIBE_ADMIN_GROUPS`, `QS_DESCRIBE_COLUMNS_TIMEOUT`~~: added by this task.
- ~~Bound-parameter support in `build_where_clause`~~: it does not exist, and this task must not add it. Program predicates are composed with `compose_where`.
- ~~`slug:describe` / `slug:describe_raw` in any policy~~: added by this task, to `defaults.yaml` only.

---

## Implementation Notes

### Key Constraints
- **`build_scan_sql`:**
  - Validates `schema`/`table` with `_validate_bare_identifier`.
  - Requires a **non-empty** `fields` list, all in `FILTERABLE_COLUMNS` (`ValueError` otherwise). Unlike `build_page_sql`, it has no `*` fallback, because the describe list always projects explicit columns.
  - `limit` must be an `int >= 1`.
  - Output: `SELECT "<f1>", ... FROM "<schema>"."<table>" [where] [order_by] LIMIT <limit>`, with **no OFFSET**, since pagination happens in memory after ABAC.
- **`compose_where`:**
  - `("", "")` → `""`
  - `("", c)` → `"WHERE " + c`
  - `(w, "")` → `w`
  - `(w, c)` → `f"{w} AND ({c})"`
  - A non-empty `where` that does not start with `"WHERE "` → `ValueError`.
- **`QS_DESCRIBE_ADMIN_GROUPS`** is a `list[str]`: lowercase and stripped, split on commas, empties dropped. Default `admin,superuser`.
- **`QS_DESCRIBE_COLUMNS_TIMEOUT`** is a `float` with default `5.0`. Use `float(config.get(..., fallback=5))`, because navconfig has no `getfloat` guarantee.
- **Policy grants go to admins only.** Regular users rely on the `slug:execute` fallback (brainstorm decision).

---

## Implementation Blueprint

### Steps (in order)
1. Modify `build_order_by` — *why*: the describe list needs `NULLS LAST` while `QueryManager` stays byte-identical.
2. Append `compose_where` and `build_scan_sql` after `build_count_sql` — *why*: keeps the SQL builders grouped.
3. Add the config keys after `QS_QUERIES_TABLE` — *why*: they sit next to the definitions-table settings.
4. Add the policy actions — *why*: spec AC20.
5. Write the tests, then run `pytest tests/handlers/test_pagination_describe_helpers.py tests/handlers/test_querymanager_pagination.py tests/policies -q` — *why*: AC19 and AC20.

### `querysource/handlers/_pagination.py` (MODIFY — build_order_by)
```python
# occurrences: 1 (verified: grep -c "def build_order_by(params: PaginationParams) -> str:" querysource/handlers/_pagination.py)
# REPLACE the signature line (verified :309) with:
def build_order_by(params: PaginationParams, nulls_last: bool = False) -> str:
# occurrences: 1 (verified: grep -c "    return f'ORDER BY \"{params.sort_field}\" {direction}'" ...)
# REPLACE the return line (verified :329) with:
    clause = f'ORDER BY "{params.sort_field}" {direction}'
    return f"{clause} NULLS LAST" if nulls_last else clause
```
**Why**: the default output is unchanged (AC19). Also add an `Args:` entry for `nulls_last` to the existing docstring.

### `querysource/handlers/_pagination.py` (MODIFY — new builders)
```python
# occurrences: 1 (verified: grep -c "def build_count_sql(schema: str, table: str, where: str) -> str:")
# AFTER — insert below the end of build_count_sql (its final `    return base`, verified :351)


def compose_where(where: str, extra_clause: str) -> str:
    """Combine a :func:`build_where_clause` result with an extra predicate.

    Args:
        where: ``""`` or a clause starting with ``"WHERE "``.
        extra_clause: A trusted SQL predicate (may reference ``$n`` bound params) or ``""``.

    Returns:
        ``""``, ``"WHERE <extra>"``, ``where`` or ``"<where> AND (<extra>)"``.

    Raises:
        ValueError: If ``where`` is non-empty and does not start with ``"WHERE "``.
    """
    # FILL IN: the four cases + ValueError — bounded by Implementation Notes
    raise NotImplementedError


def build_scan_sql(
    schema: str,
    table: str,
    fields: list[str],
    where: str,
    order_by: str,
    limit: int,
) -> str:
    """Return an un-paged ``SELECT`` bounded by ``LIMIT`` (no OFFSET).

    Used by the describe list, which paginates in memory after the ABAC filter.

    Raises:
        ValueError: On non-bare identifiers, empty/unknown ``fields`` or ``limit < 1``.
    """
    _validate_bare_identifier(schema, "schema")
    _validate_bare_identifier(table, "table")
    # FILL IN: validate limit/fields; build SELECT like build_page_sql but LIMIT only — bounded by Implementation Notes
    raise NotImplementedError
```
**Why**: the describe list needs every pre-filtered row, capped at `QS_DESCRIBE_MAX_SCAN + 1`, so it can apply ABAC before slicing (spec AC8/AC9). `_validate_bare_identifier` is defined later in the module, which is fine because it is resolved at call time.

### `querysource/conf.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c "QS_QUERIES_TABLE = config.get('QS_QUERIES_TABLE', fallback='queries')" querysource/conf.py)
# AFTER — insert below `QS_QUERIES_TABLE = config.get('QS_QUERIES_TABLE', fallback='queries')` (verified :354)

## Describe API (FEAT-148):
# Max pre-filtered rows the describe list scans before ABAC + in-memory paging.
QS_DESCRIBE_MAX_SCAN = config.getint('QS_DESCRIBE_MAX_SCAN', fallback=10000)
# Session groups that may see admin-only describe fields (dwh_info, cache_options, ...).
QS_DESCRIBE_ADMIN_GROUPS = [
    g.strip().lower()
    for g in str(config.get('QS_DESCRIBE_ADMIN_GROUPS', fallback='admin,superuser')).split(',')
    if g.strip()
]
# Seconds allowed for GET /api/v1/queries/{slug}/columns to prepare the statement.
QS_DESCRIBE_COLUMNS_TIMEOUT = float(config.get('QS_DESCRIBE_COLUMNS_TIMEOUT', fallback=5))
```
**Why**: the defaults are the brainstorm decisions (10000 rows; `admin,superuser`) and the spec's 5 s prepare budget.

### `policies/defaults.yaml` (MODIFY)
```yaml
# occurrences: 1 (verified: grep -c '      - "raw_query:execute"' policies/defaults.yaml)
# AFTER — insert below `      - "raw_query:execute"` (verified :26)
      - "slug:describe"
      - "slug:describe_raw"
```
**Why**: this implements the brainstorm decision "admins/superuser only". `resources: "slug:*"` already covers these slug actions.

### `tests/handlers/test_pagination_describe_helpers.py` (CREATE)
```python
"""FEAT-148 TASK-735 — describe pagination helpers."""
import pytest

from querysource.handlers._pagination import (
    PaginationParams, build_order_by, compose_where, build_scan_sql,
)


def test_build_order_by_default_unchanged():
    params = PaginationParams.from_query_string({})
    assert build_order_by(params) == 'ORDER BY "updated_at" DESC'


def test_build_order_by_nulls_last():
    # FILL IN: nulls_last=True appends " NULLS LAST"
    ...


@pytest.mark.parametrize("where,extra,expected", [
    # FILL IN: the four compose_where cases
])
def test_compose_where(where, extra, expected):
    assert compose_where(where, extra) == expected


def test_compose_where_rejects_bad_where():
    # FILL IN
    ...


def test_build_scan_sql_shape_and_validation():
    # FILL IN: exact SQL string for public.queries; ValueError for bad schema, unknown field, empty fields, limit 0
    ...
```

### `tests/policies/test_default_policies_load.py` (MODIFY)
```python
# APPEND at end of file
def test_defaults_admin_policy_grants_describe_actions():
    """FEAT-148: admin_full_access grants slug:describe and slug:describe_raw."""
    # FILL IN: load defaults.yaml like test_defaults_has_admin_allow (:45); assert both actions present
    ...
```

### FILL IN checklist
- [ ] `compose_where`: the 4 cases and the `ValueError`.
- [ ] `build_scan_sql`: validation and SQL assembly.
- [ ] Test bodies and parametrize cases.

---

## Acceptance Criteria

- [ ] `build_order_by(params)` output is unchanged; `pytest tests/handlers/test_querymanager_pagination.py -q` passes (spec AC19).
- [ ] `pytest tests/handlers/test_pagination_describe_helpers.py tests/policies -q` passes (AC20).
- [ ] `from querysource.conf import QS_DESCRIBE_MAX_SCAN, QS_DESCRIBE_ADMIN_GROUPS, QS_DESCRIBE_COLUMNS_TIMEOUT` works, with defaults `10000`, `['admin', 'superuser']` and `5.0`.
- [ ] `ruff check querysource/handlers/_pagination.py querysource/conf.py tests/handlers/test_pagination_describe_helpers.py tests/policies/test_default_policies_load.py` is clean.

---

## Test Specification

See the blueprint test blocks above.

---

## Agent Instructions

1. **Read the spec** (§3 Module 4, §5 AC8–AC10, AC19, AC20).
2. **Check dependencies**: none. This task is parallel-safe with TASK-734 and TASK-736.
3. **Verify the Codebase Contract** anchor counts.
4. **Update status** → `"in-progress"` in `sdd/tasks/index/describe-queryslug.json`.
5. **Implement** from the blueprint.
6. **Verify** the acceptance criteria.
7. **Move this file** to `sdd/tasks/completed/`.
8. **Update the index** → `"done"`.
9. **Fill in the Completion Note**.

---

## Completion Note

**Completed by**: sdd-worker (Claude Sonnet 5) — implemented directly after the
parrot-sdd-coder dispatch failed at the infra level for this task (`SubWorktreeMergeError:
git worktree add failed`, both attempts, zero code produced).
**Date**: 2026-09-15
**Notes**:
- Implemented per blueprint: `build_order_by(params, nulls_last=False)` — default output
  byte-identical (AC19), `NULLS LAST` appended only when requested. `compose_where` — the
  4 cases plus `ValueError` on a non-empty `where` not starting with `"WHERE "`.
  `build_scan_sql` — validates schema/table via `_validate_bare_identifier`, requires
  non-empty `fields` all in `FILTERABLE_COLUMNS`, `limit >= 1`, no `OFFSET`.
- `querysource/conf.py`: added `QS_DESCRIBE_MAX_SCAN` (int, default 10000),
  `QS_DESCRIBE_ADMIN_GROUPS` (list[str], default `['admin', 'superuser']`),
  `QS_DESCRIBE_COLUMNS_TIMEOUT` (float, default 5.0) — all verified importable with the
  documented defaults.
- `policies/defaults.yaml`: appended `slug:describe` and `slug:describe_raw` to
  `admin_full_access.actions`.
- `pytest tests/handlers/test_pagination_describe_helpers.py
  tests/handlers/test_querymanager_pagination.py tests/policies -q` → 62 passed, 1 xfailed
  (pre-existing xfail, unrelated to this task).
- `ruff check querysource/handlers/_pagination.py querysource/conf.py
  tests/handlers/test_pagination_describe_helpers.py tests/policies/test_default_policies_load.py`:
  the new test file is fully clean; the 3 modified pre-existing files carry the same 13
  violations as on `dev` before this task (verified by diffing counts with a scoped
  `git stash`) — this task's diff introduces zero new lint issues.
- Only the 5 listed files touched; no scope creep.

**Deviations from spec**: none

Seat: sdd-worker (native, Claude Sonnet 5) · Backend: n/a (in-worktree fallback after
2 failed nova-seat attempts each errored before producing any code: attempt 1 seat
glm/nova/zai.glm-4.7-flash, attempt 2 seat qwen/nova/qwen.qwen3-coder-480b-a35b-instruct —
both `SubWorktreeMergeError: git worktree add failed`, duration ~0.03s each, no tokens
consumed) · Attempts: 1 (this implementation) · Duration: n/a (interactive) · Tokens: n/a
