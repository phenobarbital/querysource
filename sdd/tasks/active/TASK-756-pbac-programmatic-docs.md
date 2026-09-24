# TASK-756: Document programmatic PBAC (QSPrincipal) for integrators and operators

**Feature**: FEAT-150 — PBAC for Request-less (Programmatic) QS Callers
**Spec**: `sdd/specs/pbac-request-credentials.spec.md`
**Status**: pending
**Priority**: low
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-748, TASK-753, TASK-754
**Assigned-to**: unassigned

---

## Context

Implements spec §3 **Module 7**. Integrators such as ai-parrot need one page explaining
how to run a slug on behalf of a user, what happens in each PBAC situation, and what is
unchanged (credentials, tenant routing, the scheduler). Operators need to know that
request-derived policy conditions do not exist on this path.

---

## Scope

- Create `docs/PBAC_PROGRAMMATIC.md` covering:
  - `QSPrincipal` (user form and `for_authz`).
  - `QS`/`MultiQS(..., principal=...)`.
  - The decision table (spec §2 "User-facing behaviour").
  - The existence collapse.
  - `tenant=` routing vs. `principal.tenant_id` (logs only).
  - Trusted-service credentials.
  - The `QS_PBAC_ALLOW_SESSIONLESS_AUTHZ` gate for the authz form.
  - The detached evaluator (no decision cache).
  - The navigator-auth `EvalContext.from_userinfo` feature detection.
  - The fact that request-derived policy conditions cannot match.
- Add a short "Programmatic callers and PBAC" subsection to `docs/PER_TENANT_QUERIES.md` linking to it.

**NOT in scope**: code changes; ai-parrot docs.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `docs/PBAC_PROGRAMMATIC.md` | CREATE | integrator/operator guide |
| `docs/PER_TENANT_QUERIES.md` | MODIFY | link subsection |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.auth import QSPrincipal, get_pbac_runtime   # exported by TASK-750
from querysource.exceptions import QueryAccessDenied         # TASK-748
from querysource.queries.qs import QS
from querysource.queries.multi import MultiQS
```

### Existing Signatures to Use
- The spec §2 decision table is the source of truth. Copy its rows and do not re-derive them.
- Document only names that exist after TASK-748 to TASK-754: `QSPrincipal(user_id, username, groups, roles, programs, superuser, tenant_id, channel)`, `QSPrincipal.for_authz(backend, *, tenant_id=None, channel="library")`, `QS(..., tenant=..., principal=...)`, `MultiQS(..., tenant=..., principal=...)`, `QueryAccessDenied`.
- Config keys: `QS_PBAC_ENABLED`, `QS_PBAC_ALLOW_SESSIONLESS_AUTHZ` (`querysource/conf.py:441,453`).

### Does NOT Exist
- ~~`QSPrincipal.org_id` / `client_id`~~: do not document them.
- ~~Per-user credentials on the principal path~~: say explicitly that they do not apply.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "docs/PBAC_PROGRAMMATIC.md", "action": "CREATE"},
    {"path": "docs/PER_TENANT_QUERIES.md", "action": "MODIFY"}
  ],
  "contract_symbols": []
}
```

---

## Implementation Notes

### Key Constraints
- Every code sample must run against the merged implementation. Verify each import before writing it.
- Keep the ai-parrot mapping as one short example (`PermissionContext` → `QSPrincipal`) marked as the integrator's responsibility.

---

## Implementation Blueprint

### Steps (in order)
1. Re-read spec §2 and the merged code of TASK-753/754 — *why*: the doc describes behaviour, and it must match the code, not the plan.
2. Write `docs/PBAC_PROGRAMMATIC.md` using the outline below — *why*: the section order mirrors the questions an integrator asks.
3. Add the link subsection to `docs/PER_TENANT_QUERIES.md`.

### `docs/PBAC_PROGRAMMATIC.md` (CREATE)
```markdown
# Programmatic PBAC: running queries on behalf of a user

## When you need this
## QSPrincipal
### User principal
### Sessionless-authz principal (`QSPrincipal.for_authz`)
## Running QS and MultiQS as a principal
## What happens (decision table)
## Missing vs. denied
## Tenants: `tenant=` routes, `principal.tenant_id` is logged
## Credentials stay trusted-service
## Operator notes
<!-- FILL IN: detached evaluator/no decision cache; request-derived conditions cannot match;
     one warning when QS_PBAC_ENABLED but no runtime; EvalContext.from_userinfo feature detection -->
## Example: mapping an ai-parrot PermissionContext
```

### `docs/PER_TENANT_QUERIES.md` (MODIFY)
```markdown
<!-- occurrences: 1 (verified: grep -c '^## Discovery and allowlist' docs/PER_TENANT_QUERIES.md) -->
<!-- BEFORE — insert above `## Discovery and allowlist` (verified: docs/PER_TENANT_QUERIES.md:140), i.e. at the end of "## Python and HTTP API" -->
### Programmatic callers and PBAC

Library callers that run a slug on behalf of a user pass `principal=QSPrincipal(...)`;
`tenant=` still selects the store. See [PBAC_PROGRAMMATIC.md](PBAC_PROGRAMMATIC.md).
```

### FILL IN checklist
- [ ] Every section body in `PBAC_PROGRAMMATIC.md`, verified against the merged code

---

## Acceptance Criteria

- [ ] AC-1: `docs/PBAC_PROGRAMMATIC.md` exists with every outline section filled in, and its decision table matches spec §2.
- [ ] AC-2: every code sample's imports resolve: `python -c "from querysource.auth import QSPrincipal, get_pbac_runtime; from querysource.exceptions import QueryAccessDenied"`.
- [ ] AC-3: `docs/PER_TENANT_QUERIES.md` links to the new page from the "Python and HTTP API" section.

---

## Validation Commands

- `pytest tests/auth/test_principal.py -q`

---

## Test Specification

No new tests. The validation command re-checks that the documented public type still behaves as documented.

---

## Agent Instructions

1. Confirm TASK-753 and TASK-754 are completed.
2. Write the docs and run AC-2's import check.
3. Move this file to `sdd/tasks/completed/`, set the index status to `done`, and fill in the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
