# TASK-742: Describe API documentation, changelog and version 4.6.0

**Feature**: FEAT-148 — Describe Query-Slug REST Endpoints
**Spec**: `sdd/specs/describe-queryslug.spec.md`
**Status**: pending
**Priority**: low
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-741
**Assigned-to**: unassigned

---

## Context

This task implements spec §3 **Module 9**. It documents the new public surface and the one behaviour change:
- **The behaviour change:** relative-date keywords now resolve on raw-query slugs (TASK-734).
- **The version:** it bumps to `4.6.0`, a minor bump because of the new public endpoints (spec AC22).

It runs after the non-tenant endpoints (TASK-741). When TASK-743 (tenant routes) lands later, that task appends its own changelog bullet.

---

## Scope

- Add a `CHANGES.rst` entry under `Unreleased`: endpoints, principal/redaction rules, config keys, policy actions, the keyword-resolution behaviour change and keyword formats.
- Create `docs/DESCRIBE_API.md`, following the style of the existing `docs/QSSCHEDULER.md`.
- Bump `querysource/version.py` to `4.6.0`.

**NOT in scope**:
- Any code change.
- `querysource/_version.py` (setuptools_scm output; never edit or commit it).
- Tenant route docs (added by TASK-743).
- Running `/release`.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `CHANGES.rst` | MODIFY | new section at top of `Unreleased` |
| `docs/DESCRIBE_API.md` | CREATE | endpoint reference |
| `querysource/version.py` | MODIFY | `4.5.16` → `4.6.0` |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.version import __version__   # verified: querysource/version.py:9
```

### Existing Signatures to Use
```text
# CHANGES.rst — top of file (verified :1-5):
Unreleased
==========

Row-oriented outputs — DataFrame results and swallowed errors
-------------------------------------------------------------
# Section style: title line + '-' underline of equal length, RST double-backtick literals, '-' bullets.

# querysource/version.py:9 (occurrences: 1)
__version__ = '4.5.16'

# docs/QSSCHEDULER.md — existing Markdown reference doc to mirror in tone/structure.
```

**Facts to document** (all from the spec and completed tasks):

Endpoints:
- `GET /api/v1/queries/describe` (+HEAD)
- `GET /api/v1/queries/{slug}/describe`
- `GET /api/v1/queries/{slug}/columns`
- `GET /api/v1/queries/vocabulary`

Access control:
- Principal rules: no principal → 401; programs pre-filter with `default`; superuser and sessionless-authz unfiltered; no programs → nothing.
- ABAC: list `slug:list|slug:execute`; detail/columns `slug:describe|slug:execute` → 404.
- `query_raw` needs `slug:describe_raw`. Admin fields need superuser or `QS_DESCRIBE_ADMIN_GROUPS`.
- With PBAC disabled, `query_raw` is visible to principals (spec §7).

Configuration and policies:
- Config: `QS_DESCRIBE_MAX_SCAN=10000` (`X-Truncated`), `QS_DESCRIBE_ADMIN_GROUPS=admin,superuser`, `QS_DESCRIBE_COLUMNS_TIMEOUT=5`.
- Policy: `policies/defaults.yaml` `admin_full_access` gains `slug:describe`, `slug:describe_raw`.

Behaviour change and keywords:
- Keywords `TODAY`, `YESTERDAY`, `FDOM`, `LDOM`, `CURRENT_YEAR`, `CURRENT_MONTH`, `LAST_YEAR` now resolve on raw-query providers (pg/sql/mysql/sqlserver/cassandra/default) for untyped and `date`/`datetime`/`timestamp` conditions.
- Formats: `TODAY` is `MM/DD/YYYY`; the others are `YYYY-MM-DD`; the year/month keywords are integers.
- `UDF_LIST`, `PG_CONSTANTS` and `PG_UDF` env overrides now accept comma-separated values.

### Does NOT Exist
- ~~A docs page for handlers~~: none exists. `docs/DESCRIBE_API.md` is new.
- ~~OpenAPI/Swagger generation~~: absent. Document by hand.

---

## Implementation Notes

### Key Constraints
- Do not promise tenant routes in these docs. TASK-743 adds them when FEAT-147 lands.
- Keep the changelog factual and short. Use RST literals (double backticks) in `CHANGES.rst` and Markdown in `docs/`.
- Include one request/response example per endpoint in `docs/DESCRIBE_API.md`. Take the JSON shapes from spec §2.

---

## Implementation Blueprint

### Steps (in order)
1. Insert the `CHANGES.rst` section — *why*: release notes are consumed by `/release`.
2. Create `docs/DESCRIBE_API.md` — *why*: consumer reference for ai-parrot and the frontend.
3. Bump the version — *why*: AC22.

### `CHANGES.rst` (MODIFY)
```rst
.. occurrences: 1 (verified: grep -cx "Unreleased" CHANGES.rst)
.. AFTER — insert below the `==========` underline of `Unreleased` (verified :2), before the existing first section:

Describe API for query slugs (FEAT-148)
---------------------------------------

New read-only endpoints for discovering stored query slugs without executing them:
``GET /api/v1/queries/describe`` (paginated list), ``GET /api/v1/queries/{slug}/describe``
(definition + typed variables), ``GET /api/v1/queries/{slug}/columns`` (prepared, never
executed) and ``GET /api/v1/queries/vocabulary`` (relative-date keywords).

- FILL IN: access rules bullet (401 / program pre-filter / ABAC actions / 404)
- FILL IN: redaction bullet (``slug:describe_raw``, ``QS_DESCRIBE_ADMIN_GROUPS``)
- FILL IN: configuration bullet (``QS_DESCRIBE_MAX_SCAN``, ``QS_DESCRIBE_ADMIN_GROUPS``, ``QS_DESCRIBE_COLUMNS_TIMEOUT``)
- FILL IN: policy bullet (``slug:describe``, ``slug:describe_raw`` granted to admins in ``policies/defaults.yaml``)
- FILL IN: behaviour change bullet — keywords now resolve on raw-query providers; formats; env override fix

```

### `docs/DESCRIBE_API.md` (CREATE)
```markdown
# Describe API (FEAT-148)

Read-only discovery of stored query slugs. None of these endpoints execute a query.

## Access rules
<!-- FILL IN: principal table, ABAC actions, 401/404 semantics, redaction, PBAC-disabled note -->

## GET /api/v1/queries/describe
<!-- FILL IN: params (page, page_size, sort, search|q, filters), headers (X-Total-Count…, X-Truncated), example -->

## GET /api/v1/queries/{slug}/describe
<!-- FILL IN: payload sections, derived.variables fields, redacted, example -->

## GET /api/v1/queries/{slug}/columns
<!-- FILL IN: columns_source values, query-string conditions, warnings, example -->

## GET /api/v1/queries/vocabulary
<!-- FILL IN: keywords (with formats), constants, pg_functions, functions (invocable: false), example -->

## Configuration
<!-- FILL IN: the three QS_DESCRIBE_* keys with defaults -->
```

### `querysource/version.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c "__version__ = '4.5.16'" querysource/version.py)
# REPLACE `__version__ = '4.5.16'` (verified :9) with:
__version__ = '4.6.0'
```
**Why**: new public endpoints make this a minor bump (spec AC22). If `/release` has bumped the version on `dev` in the meantime, keep the higher of the two, and record that in the Completion Note.

### FILL IN checklist
- [ ] 5 changelog bullets, bounded by the "Facts to document" list.
- [ ] 6 doc sections, bounded by spec §2.

---

## Acceptance Criteria

- [ ] AC22: `CHANGES.rst` documents endpoints, config keys, policy actions and the keyword behaviour change; `python -c "from querysource.version import __version__; print(__version__)"` prints `4.6.0`.
- [ ] `docs/DESCRIBE_API.md` exists, covers all 4 endpoints, and contains no tenant routes.
- [ ] `querysource/_version.py` is not modified.

---

## Test Specification

No automated tests. Verify with `git diff --stat` that only the 3 listed files changed.

---

## Agent Instructions

1. **Read the spec** (§2, §5 AC22, §7 risks to document).
2. **Check dependencies**: TASK-741 completed.
3. **Verify the Codebase Contract**: the changelog head and the version line.
4. **Update status** → `"in-progress"`.
5. **Implement** from the blueprint.
6. **Verify** the acceptance criteria.
7. **Move this file** to `sdd/tasks/completed/`.
8. **Update the index** → `"done"`.
9. **Fill in the Completion Note**.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**:

**Deviations from spec**: none | describe if any
