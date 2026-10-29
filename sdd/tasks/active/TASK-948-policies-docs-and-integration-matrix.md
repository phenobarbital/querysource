# TASK-948: Shipped policies, docs, changelog and the integration policy matrix

**Feature**: FEAT-182 — PBAC Coverage for the Management API, Direct Driver Route and Describe Fallback
**Spec**: `sdd/specs/querysource-management-pbac.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-943, TASK-944, TASK-945, TASK-946, TASK-947
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 5 and §4 integration test. Once the gates exist (TASK-943..947), the shipped admin policy
must grant the new actions, the docs must explain them, and one end-to-end matrix must prove the
combination.

---

## Scope

- `policies/defaults.yaml`: add `slug:view`, `slug:manage` and `driver:query` to `admin_full_access`'s actions.
- `policies/HARDENING.md`: add the new actions and a worked example, "catalogue and management for group X;
  execution open to all; `driver:query` on `driver:http` for everyone". Also note `QS_DESCRIBE_EXECUTE_FALLBACK=false`.
- `docs/DESCRIBE_API.md`: document `QS_DESCRIBE_EXECUTE_FALLBACK`.
- `docs/PBAC_PROGRAMMATIC.md` (or the PBAC doc listing actions; FILL IN: confirm the right file): the three new
  actions, `run_query`'s gates, and `QS_PBAC_STRICT_BOOT`.
- `CHANGES.rst` under **Unreleased**: a FEAT-182 section, including the behaviour-change note for PBAC-on hosts
  without a `*:*` floor (they must grant the new actions before upgrading).
- Integration matrix test against a real app with PBAC on.
- Add a test to `tests/policies/test_default_policies_load.py` asserting the 3 new actions in `admin_full_access` (same shape as the FEAT-148 test at `:112-126`).

**NOT in scope**: version bump (done by `/release`; `.bumpversion.cfg` is stale at 3.6.0 and `querysource/version.py`
says 5.2.4). Note it in the Completion Note.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `policies/defaults.yaml` | MODIFY | admin gets new actions |
| `policies/HARDENING.md` | MODIFY | new actions + example |
| `docs/DESCRIBE_API.md` | MODIFY | fallback flag |
| `docs/PBAC_PROGRAMMATIC.md` | MODIFY | actions, run_query, strict boot |
| `CHANGES.rst` | MODIFY | Unreleased entry |
| `tests/integration/test_management_pbac_matrix.py` | CREATE | role × endpoint matrix |
| `tests/policies/test_default_policies_load.py` | MODIFY | assert admin_full_access has the 3 new actions (pattern: FEAT-148 test at :112-126) |

---

## Codebase Contract (Anti-Hallucination)

### Existing Signatures to Use
```python
# policies/defaults.yaml — admin_full_access: actions list ends with
#       - "raw_query:execute"   (:26)
#       - "slug:describe"       (:27)
#       - "slug:describe_raw"   (:28)
# tests/conftest.py:102 policies_dir(tmp_path) and :109 qs_app_pbac_on — integration harness pattern
# tests/policies/test_default_policies_load.py — shipped-policy load tests
```

### Does NOT Exist
- ~~A `querysource/policies/` package~~: the shipped policies live at repo-root `policies/`.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "policies/defaults.yaml", "action": "MODIFY"},
    {"path": "policies/HARDENING.md", "action": "MODIFY"},
    {"path": "docs/DESCRIBE_API.md", "action": "MODIFY"},
    {"path": "docs/PBAC_PROGRAMMATIC.md", "action": "MODIFY"},
    {"path": "CHANGES.rst", "action": "MODIFY"},
    {"path": "tests/integration/test_management_pbac_matrix.py", "action": "CREATE"},
    {"path": "tests/policies/test_default_policies_load.py", "action": "MODIFY"}
  ],
  "contract_symbols": []
}
```

---

## Implementation Notes

- The matrix uses a `tmp_path` policy set **without** a `*:*` baseline. Roles:
  - `admin` (shipped `admin_full_access`);
  - `qsteam` (`slug:view`, `slug:manage`, `raw_query:execute`, `driver:query` on `driver:*`);
  - `plain` (`slug:execute`, `datasource:use`, `driver:use` on all; `driver:query` on `driver:http` only).
- Endpoints:
  - management: list, get, post, delete, move, store catalogue;
  - driver route: `http` and `pg`;
  - `POST /api/v2/test/queries`;
  - describe list, with the fallback flag on and off.
- `get_source` and DB I/O are patched; this asserts authorization, not query execution.
- Routes and handlers under test, by the task that gated them:
  - `querysource/handlers/manager.py` (`QueryManager`, TASK-943);
  - `querysource/handlers/query_moves.py` (`QueryMoveView`) and `querysource/handlers/query_stores.py`
    (`QueryStoreCatalog.list`), TASK-944;
  - `querysource/handlers/service.py` (`QueryService.query` / `run_query`, TASK-945);
  - `querysource/handlers/describe.py` (TASK-946).

---

## Implementation Blueprint

### Steps (in order)
1. Edit `defaults.yaml`. *Why*: shipped admins must keep full access.
2. Write the docs and the CHANGES entry.
3. Write the matrix test, parametrized as `(role, method, path, expected_status_class)`.
4. Run the full validation list.

### `policies/defaults.yaml` (MODIFY)
```yaml
# occurrences: 1 (verified: grep -c '      - "slug:describe_raw"' policies/defaults.yaml)
# AFTER — insert below `      - "slug:describe_raw"` (verified: defaults.yaml:28)
      - "slug:view"
      - "slug:manage"
      - "driver:query"
```

### `tests/integration/test_management_pbac_matrix.py` (CREATE)
```python
"""FEAT-182 TASK-948: role × endpoint PBAC matrix (real evaluator, no *:* floor)."""
import pytest

MATRIX = [
    # (role, method, path, allowed)
    # FILL IN: every row from Implementation Notes
]


@pytest.mark.asyncio
@pytest.mark.parametrize("role,method,path,allowed", MATRIX)
async def test_management_routes_policy_matrix(role, method, path, allowed):
    """allowed → not 404 (handler reached, I/O patched); denied → 404."""
    # FILL IN
```

### FILL IN checklist
- [ ] Right PBAC doc file for the actions list.
- [ ] Matrix rows, fixtures and patches.

---

## Acceptance Criteria

- [ ] Shipped admin policy covers `slug:view`, `slug:manage` and `driver:query`.
- [ ] Docs and CHANGES updated, including the upgrade note for PBAC-on hosts.
- [ ] The matrix passes; every "denied" row was mutation-checked against its gate.

---

## Validation Commands

- `pytest tests/integration/test_management_pbac_matrix.py -q`
- `pytest tests/policies/test_default_policies_load.py -q`
- `pytest tests/policies/test_authorized_policy.py -q`

---

## Test Specification

See the blueprint above.

---

## Agent Instructions

Same as TASK-943, with this task's id and files. TASK-943..947 must be `done` first.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: version bump deferred to `/release`.
