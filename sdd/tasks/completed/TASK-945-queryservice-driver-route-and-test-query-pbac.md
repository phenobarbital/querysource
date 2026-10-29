# TASK-945: QueryService gates — direct driver route (driver:query) and test endpoint (raw_query:execute)

**Feature**: FEAT-182 — PBAC Coverage for the Management API, Direct Driver Route and Describe Fallback
**Spec**: `sdd/specs/querysource-management-pbac.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §1 problems 2 and 4, §3 Module 2.

1. `/api/v2/queries/{driver}/{source}/{attribute}[/{var}]` reaches `QueryService.query()` with
   `slug = None`. Under PBAC the `slug:execute` check gets an empty name and returns 404 to everyone,
   admins included.
2. `POST /api/v2/test/queries` (`QueryService.run_query`) runs an arbitrary `query` and `driver`
   from the body with **no PBAC check at all**: a free-SQL hole.

---

## Scope

- In `query()`: when `slug` is `None`, evaluate `driver:query` (`ResourceType.DRIVER`) on
  `args.get('driver')` instead of `slug:execute`. Applies to both branches (tenant registry and
  legacy). When `slug` is set, behaviour is unchanged.
- In `run_query()`: before `get_source(...)`, gate `raw_query:execute` (`ResourceType.RAW_QUERY`,
  name `"raw_query"`) and then `driver:use` (`ResourceType.DRIVER`, name = the resolved `driver`).
- Write tests.

**NOT in scope**: `get_columns`/`columns`/`test_slug` (their routes always carry `{slug}`, spec rev 1.1);
`QueryExecutor`; any policy YAML.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/service.py` | MODIFY | no-slug branch in `query()`; gates in `run_query()` |
| `tests/handlers/test_queryservice_driver_rawquery_pbac.py` | CREATE | gate tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from ..auth import ResourceType   # service.py:14 (already imported)
```

### Existing Signatures to Use
```python
# querysource/handlers/service.py
class QueryService(AbstractHandler):
    async def run_query(self, request: web.Request) -> web.Response:   # :52
        # driver = options.pop('driver') / 'db' default         (:79-82)
        # conditions = {**options, **params}                    (:91)
        # self.logger.debug(f'Test Query: {query}, ...')         (:92) ← unique anchor
        # if query := await self.get_source(                     (:93)
    async def query(self, request):                                     # :134
        # args = self.match_parameters(request)                  (:165)
        # slug from args['slug'] (:173) or None (:180)
        # registry branch: identity = QueryIdentity(store=store, slug=slug) (:213) → _enforce_owned_slug(… "slug:execute")
        # legacy branch: self._enforce_pbac(request, resource_type=ResourceType.SLUG, resource_name=slug, action="slug:execute") (:220-225)

# querysource/handlers/abstract.py
async def AbstractHandler._enforce_pbac(self, request, resource_type, resource_name: str, action: str) -> None
#   no app['security'] → no-op; empty name / deny → web.HTTPNotFound

# querysource/handlers/executor.py:30 — _enforce_payload: the raw_query:execute + driver:use pattern to mirror
```

### Does NOT Exist
- ~~A `slug` path parameter on the driver routes~~ (`services.py:335-342`).
- ~~`ResourceType.DRIVER_QUERY`~~: the action is the string `"driver:query"`; the resource type is `ResourceType.DRIVER`.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/handlers/service.py", "action": "MODIFY"},
    {"path": "tests/handlers/test_queryservice_driver_rawquery_pbac.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/handlers/service.py#QueryService.query",
    "sym:querysource/handlers/service.py#QueryService.run_query",
    "sym:querysource/handlers/abstract.py#AbstractHandler._enforce_pbac",
    "sym:querysource/handlers/executor.py#QueryExecutor._enforce_payload"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Keep the existing later `datasource:use` / `driver:use` checks after provider resolution in `query()`.
- The no-slug branch must not touch the tenant registry (`registry.resolve`); a driver call has no definition owner.
- `run_query` gates go **before** `get_source`; the test asserts `get_source` is never awaited on deny.
- `query()` already exceeds the complexity budget. Add the branch as a small helper
  (`_enforce_entry(request, slug, args, registry, tenant)`) rather than nesting further. FILL IN: decide the
  helper's exact shape, bounded by "no behaviour change when slug is set".

---

## Implementation Blueprint

### Steps (in order)
1. Wrap the existing `if registry is not None: … else: …` PBAC block (`:202-225`) in `if slug: … else:` + driver gate. *Why*: the driver route must not evaluate an empty slug name.
2. Add the two gates to `run_query` before `get_source`. *Why*: close the free-SQL hole with the same actions as `QueryExecutor`.
3. Write the tests and mutation-check them.

### `querysource/handlers/service.py` (MODIFY): `query()`
```python
# occurrences: 1 (verified: grep -c "        registry = request.app.get(\"qs_tenant_registry\")" — FILL IN: re-run; quote the `tenant = request.get('qs_tenant')` line above it as context)
# REPLACE the block from `        if registry is not None:` (:202) through the legacy `_enforce_pbac(... action="slug:execute",)` (:225) with:
        if slug is None:
            # FEAT-182: direct driver route (/api/v2/queries/{driver}/...) — no
            # definition to own; decided by driver:query on the driver.
            await self._enforce_pbac(
                request,
                resource_type=ResourceType.DRIVER,
                resource_name=args.get('driver'),
                action="driver:query",
            )
        elif registry is not None:
            # FILL IN: the original registry branch (:203-218), unchanged
            pass
        else:
            # FILL IN: the original legacy branch (:220-225), unchanged
            pass
```

### `querysource/handlers/service.py` (MODIFY): `run_query()`
```python
# occurrences: 1 (verified: grep -c "Test Query: {query}" querysource/handlers/service.py)
# AFTER — insert below `        self.logger.debug(f'Test Query: {query}, format: {queryformat}, conditions: {conditions}')` (verified: service.py:92)
        # FEAT-182: free SQL — same gates as QueryExecutor._enforce_payload.
        await self._enforce_pbac(
            request,
            resource_type=ResourceType.RAW_QUERY,
            resource_name="raw_query",
            action="raw_query:execute",
        )
        await self._enforce_pbac(
            request,
            resource_type=ResourceType.DRIVER,
            resource_name=driver,
            action="driver:use",
        )
```

### `tests/handlers/test_queryservice_driver_rawquery_pbac.py` (CREATE)
```python
"""FEAT-182 TASK-945: driver:query on the direct driver route; raw_query:execute on /api/v2/test/queries."""
from __future__ import annotations

import pytest

_PATCH_SESSION = "querysource.handlers.abstract.get_session"


@pytest.mark.asyncio
async def test_driver_route_evaluates_driver_query():
    """driver:query on driver:http allowed → reaches get_source; pg denied → 404."""
    # FILL IN: real setup_pbac app with tmp_path policies (see tests/conftest.py::qs_app_pbac_on),
    # route GET /api/v2/queries/{driver}/{source}/{attribute} → QueryService().query; patch get_source.


@pytest.mark.asyncio
async def test_driver_route_admin_not_blocked():
    """Regression: an admin is no longer 404'd by the empty slug name."""
    # FILL IN


@pytest.mark.asyncio
async def test_slug_route_unchanged():
    """With a slug, the check is still slug:execute (existing policies decide)."""
    # FILL IN


@pytest.mark.asyncio
async def test_run_query_requires_raw_query():
    """POST /api/v2/test/queries without raw_query:execute → 404; get_source never awaited."""
    # FILL IN
```

### FILL IN checklist
- [ ] Preserve the registry and legacy branches verbatim inside the new `elif`/`else`.
- [ ] Helper extraction if the edit grows `query()`; bounded by the "no behaviour change" constraint.
- [ ] Test bodies; mutation-check every denial test.

---

## Acceptance Criteria

- [ ] `/api/v2/queries/{driver}/…` is decided by `driver:query` on the driver; an admin is allowed; `http` can be allowed while `pg` is denied.
- [ ] `POST /api/v2/test/queries` → 404 without `raw_query:execute`, before `get_source`.
- [ ] Slug routes are unchanged (existing PBAC smoke tests pass).
- [ ] PBAC off: unchanged.

---

## Validation Commands

- `pytest tests/handlers/test_queryservice_driver_rawquery_pbac.py -q`
- `pytest tests/handlers/test_queryservice_pbac_smoke.py -q`
- `pytest tests/integration/test_pbac_enforcement.py -q`

---

## Test Specification

See the blueprint above. Use a real evaluator via `setup_pbac(app, policy_dir=tmp_path)`.

---

## Agent Instructions

Same as TASK-943, with this task's id and files.

---

## Completion Note

**Completed by**: sdd-worker (sequential, Sonnet)
**Date**: 2026-10-29
**Notes**: `query()`: new first branch `if slug is None` → `driver:query` on `ResourceType.DRIVER`/`args.get('driver')` (registry never touched); registry/legacy branches kept verbatim as `elif`/`else`, later datasource:use/driver:use checks untouched. `run_query()`: `raw_query:execute` then `driver:use` (driver default 'db') before `get_source`. Tests: real setup_pbac/YAML; only get_session (abstract) and get_source patched. Mutation-checked: driver branch off → 2 red; raw_query gate off → red (needed a driver:use-only user in the matrix); driver:use gate off → red. Existing smoke tests pass. Pre-existing: `tests/integration/test_pbac_enforcement.py::TestPbacOff` 2 tests fail on dev too (need live Postgres) — unrelated.
**Contract drift**: none (anchors matched; helper extraction not needed).
**Deviations from spec**: none
