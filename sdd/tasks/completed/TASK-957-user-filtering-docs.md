# TASK-957: Documentation — user guide, describe API, programmatic callers, changelog

**Feature**: FEAT-184 — PBAC-based per-user automatic filtering (`user_filtering`)
**Spec**: `sdd/specs/pbac-based-per-user-filter.spec.md`
**Status**: done
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-953, TASK-956
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 5, AC-19. Documents the behaviour implemented by TASK-950…956.

---

## Scope

- Create `docs/USER_FILTERING.md` with the sections listed in spec §3 Module 5.
- Update both payload examples in `docs/DESCRIBE_API.md` (`user_filtering`, `h_filtering` alias, admin-only `user_filter_options`).
- Append a "Per-user filtering (FEAT-184)" section to `docs/PBAC_PROGRAMMATIC.md`.
- Add an entry under `Unreleased` in `CHANGES.rst`.

**NOT in scope**: code changes.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `docs/USER_FILTERING.md` | CREATE | User guide |
| `docs/DESCRIBE_API.md` | MODIFY | Payload examples |
| `docs/PBAC_PROGRAMMATIC.md` | MODIFY | Library-caller section |
| `CHANGES.rst` | MODIFY | Changelog entry |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# none — documentation task
```

### Existing Signatures to Use
```text
docs/DESCRIBE_API.md:89 and :115 — `  "h_filtering": false,` (two payload examples)
CHANGES.rst:1 — `Unreleased` heading, sections underlined with '-' (see FEAT-182 entry at lines 4-5)
docs/sql/user_filtering.sql — migration created by TASK-952
Reason codes (TASK-950): no_principal, authz_principal, missing_attribute, program_restricted, parser_required
```

### Does NOT Exist
- ~~`QS(userinfo=...)`~~ — not a QS kwarg; library callers supply only `principal=` (open question Q6).
- ~~`QUERYSOURCE_FILTERS` removal~~ — legacy registry stays; do not document it as removed.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "docs/USER_FILTERING.md", "action": "CREATE"},
    {"path": "docs/DESCRIBE_API.md", "action": "MODIFY"},
    {"path": "docs/PBAC_PROGRAMMATIC.md", "action": "MODIFY"},
    {"path": "CHANGES.rst", "action": "MODIFY"}
  ],
  "contract_symbols": []
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Write the user guide — *why*: slug authors need the syntax table and failure modes.
2. Update describe examples, programmatic section and changelog.

### `docs/USER_FILTERING.md` (CREATE)
```markdown
# Per-user automatic filtering (`user_filtering`, FEAT-184)

## Overview
## Enabling a slug
## Rule syntax
<!-- FILL IN: copy the JSON example and the field table from spec §2 Data Models -->
## Identity resolution
<!-- FILL IN: QSPrincipal claims first, then session userinfo; library callers pass QS(principal=...) -->
## Program restriction
<!-- FILL IN: program_slug only, 'default' allowed, tenant stores derive program_slug from schema -->
## Bypass
## Errors and reason codes
<!-- FILL IN: 403 generic body; reason codes in logs only; 500 for invalid options; no session → 403 even with QS_PBAC_ENABLED=false -->
## Caching
## Migration
<!-- FILL IN: run docs/sql/user_filtering.sql on every store; ADD-only; un-migrated stores read user_filtering=False -->
## Deprecation of `h_filtering`
## Limitations
<!-- FILL IN: multi-query follow-up; parser-less providers and raw slugs refuse to run; QS_FILTERS plugins run after the step -->
```

### `docs/DESCRIBE_API.md` (MODIFY)
```markdown
# occurrences: 2 (verified: grep -c '  "h_filtering": false,' docs/DESCRIBE_API.md)
# FILL IN: disambiguate — update BOTH examples (lines 89 and 115): add `"user_filtering": false,` above each
#          `"h_filtering": false,`, note the alias is deprecated, and add a sentence that `user_filter_options`
#          is admin-only (listed in `redacted` otherwise)
```

### `docs/PBAC_PROGRAMMATIC.md` (MODIFY)
```markdown
<!-- APPEND at end of file -->
## Per-user filtering (FEAT-184)

Slugs with `user_filtering` enabled need a caller identity. HTTP callers get it from the
session; library callers must pass `QS(slug=..., principal=QSPrincipal(...))`. Without a
principal the call fails with 403 (`UserFilterDenied`), even when `QS_PBAC_ENABLED` is off.
Rules that reference non-claim attributes (for example `attributes.store_id`) are only
satisfiable through an HTTP session. See `docs/USER_FILTERING.md`.
```

### `CHANGES.rst` (MODIFY)
```rst
# occurrences: 1 (verified: grep -c '^Unreleased$' CHANGES.rst)
# AFTER — insert below the `==========` underline of `Unreleased` (CHANGES.rst:2), before the FEAT-182 section:

Per-user automatic filtering (FEAT-184)
---------------------------------------

- New definition columns ``user_filtering`` and ``user_filter_options``. Run
  ``docs/sql/user_filtering.sql`` on every query store (ADD-only).
- Enforced in the parser; denials answer **403**; invalid options answer 500.
- ``h_filtering`` is deprecated: still accepted, never consulted, reported in
  describe as an alias of ``user_filtering``. Removed in 5.3.0.

```

### FILL IN checklist
- [ ] every `FILL IN` section of the user guide
- [ ] both describe examples

---

## Acceptance Criteria

- [ ] Spec AC-19
- [ ] Every reason code and the migration path are documented

---

## Validation Commands

- `pytest tests/handlers/test_describe_detail.py -q`

---

## Test Specification

No new tests; the describe tests (TASK-953 and test_describe_detail.py) guard the documented payload shape.

---

## Agent Instructions

1. Work in the feature worktree (`parrot sdd worktree ensure --slug pbac-based-per-user-filter --feature-id FEAT-184`).
2. Confirm TASK-953 and TASK-956 are done; write the docs from the blueprint.
3. Commit only the listed files.
4. Close with `parrot sdd task close TASK-957 pbac-based-per-user-filter verified` and fill the Completion Note.

---

## Completion Note

**Completed by**: sdd-worker (coder seat kimi, nova:moonshotai.kimi-k2.5, attempt 3)
**Date**: 2026-11-03
**Notes**: Docs delivered (USER_FILTERING.md, DESCRIBE_API.md, PBAC_PROGRAMMATIC.md, CHANGES.rst). tests/handlers/test_describe_detail.py: 7 passed. Engine task-tier validation unavailable (attempt has no execution_id); merged manually.

**Deviations from spec**: none
