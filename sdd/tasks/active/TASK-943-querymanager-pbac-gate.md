# TASK-943: QueryManager PBAC gate (slug:view / slug:manage)

**Feature**: FEAT-182 — PBAC Coverage for the Management API, Direct Driver Route and Describe Fallback
**Spec**: `sdd/specs/querysource-management-pbac.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §1 problem 1 and §3 Module 1. Even with `QS_PBAC_ENABLED=true`,
`QueryManager` (`/api/v1/management/queries` and `/api/v1/management/queries/{slug}`)
has no PBAC check: any authenticated user can read definitions, including
`query_raw` (the SQL text), and create, update, patch or delete any slug.
This task adds the two new actions to every `QueryManager` method.

---

## Scope

- Add `QueryManager._enforce_definition_access(slug, action)`, a thin wrapper over
  `enforce_request_pbac` with `ResourceType.SLUG`. `slug=None` means resource `"*"`.
- `get()`:
  - With a slug (single, `slug:meta`, `slug:insert`): `slug:view` on that slug.
  - Without a slug (list/pagination and route-level `:meta`): `slug:view` on `"*"`.
  - The gate runs **after** `query_slug` is parsed and **before** the `try:` at the
    store-resolution / `json_response` block.
- `post()`, `put()`, `patch()`, `delete()`: `slug:manage` on the target slug, evaluated
  right after the slug is resolved from path/body and **before** registry/repo access
  and before `_enforce_scheduler_grant`.
- Write tests.

**NOT in scope**: `QueryMoveView` and `QueryStoreCatalog` (TASK-944); `QueryService`
(TASK-945); policy YAML in `policies/` (TASK-948).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/manager.py` | MODIFY | add `_enforce_definition_access`; call it in get/post/put/patch/delete |
| `tests/handlers/test_management_pbac.py` | CREATE | gate tests with a real PolicyEvaluator |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from ..auth import ResourceType                       # manager.py:22 (already imported)
from ..auth.request_gate import enforce_request_pbac  # manager.py:24 (already imported)
```

### Existing Signatures to Use
```python
# querysource/auth/request_gate.py
async def enforce_request_pbac(request, resource_type, resource_name: str, action: str, *,
                               logger, session_resolver=None, allow_sessionless=None) -> None
#   - no app['security'] → returns (PBAC off, no-op)
#   - empty resource_name → web.HTTPNotFound
#   - deny / no session → web.HTTPNotFound
#   - default session getter: navigator_session.get_session imported in request_gate
#     (patch target in tests: "querysource.auth.request_gate.get_session")

# querysource/handlers/manager.py
class QueryManager(QueryView):                                         # :47
    async def _enforce_scheduler_grant(self, repo, identity, data) -> None  # :153 (uses enforce_request_pbac at :210 — pattern to copy)
    async def get(self):     # :247 — query_slug parsed :260-267; registry/repo :278-282; try: at :287; except Exception at :361 (would swallow HTTPNotFound!)
    async def patch(self):   # :513 — query_slug = params['slug'] at :517
    async def delete(self):  # :638 — query_slug = params['slug'] at :645
    async def put(self):     # :775 — slug in data['query_slug'] (checked :792)
    async def post(self):    # :890 — slug dict built from params['slug'] or data['query_slug'] (:894-906)
```

### Does NOT Exist
- ~~`QueryManager._enforce_pbac`~~: `QueryManager` is not an `AbstractHandler`. Use `enforce_request_pbac`.
- ~~`ResourceType.SLUG_VIEW` / an `ActionType` for `slug:view` / `slug:manage`~~: actions are plain strings.
- ~~per-item filtering of the list~~: the list is all-or-nothing on `"*"` (spec §2: SQL pagination).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/handlers/manager.py", "action": "MODIFY"},
    {"path": "tests/handlers/test_management_pbac.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/handlers/manager.py#QueryManager",
    "sym:querysource/handlers/manager.py#QueryManager.get",
    "sym:querysource/handlers/manager.py#QueryManager.post",
    "sym:querysource/handlers/manager.py#QueryManager.put",
    "sym:querysource/handlers/manager.py#QueryManager.patch",
    "sym:querysource/handlers/manager.py#QueryManager.delete",
    "sym:querysource/auth/request_gate.py#enforce_request_pbac"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Deny is a 404 from `enforce_request_pbac`; never convert it to 403.
- `get()`'s `except Exception` (`:361`) turns any exception into `self.error(...)` (500). The gate must
  run **outside** that `try:`. Otherwise a denial becomes a 500. A test pins this.
- PBAC off (no `app['security']`): zero behaviour change, no extra DB reads.
- Keep `_enforce_scheduler_grant` as is. It is an additional, narrower check.

### References in Codebase
- `querysource/handlers/manager.py:210` — how `enforce_request_pbac` is called from `QueryManager`.
- `tests/conftest.py:102` (`policies_dir`) and `tests/integration/test_pbac_listing.py` — real `setup_pbac` app pattern.
- `tests/handlers/conftest.py:120` (`_build_app`) — app with `QueryManager` routes and a fake `qs_connection`.

---

## Implementation Blueprint

### Steps (in order)
1. Add `_enforce_definition_access` to `QueryManager`, right after `_enforce_scheduler_grant`. *Why*: one call site per method, same 404 semantics as every QS gate.
2. In `get()`, insert the gate after the `query_slug` parse (`:267`) and before the store-resolution comment block (`:269`). *Why*: it must be outside the `try:` at `:287` and before any store or DB work.
3. In `patch`/`delete`/`put`/`post`, insert `await self._enforce_definition_access(<slug>, "slug:manage")` immediately after the slug is validated (after each method's "slug missing" early return). *Why*: deny before the repository is touched.
4. Write the tests; mutation-check each denial test (remove the call → test goes red).

### `querysource/handlers/manager.py` (MODIFY): helper
```python
# occurrences: 1 (verified: grep -c '    async def _sync_definition_jobs(self, identity: QueryIdentity) -> bool:' querysource/handlers/manager.py)
# BEFORE — insert above `    async def _sync_definition_jobs(self, identity: QueryIdentity) -> bool:` (verified: manager.py:218)
    async def _enforce_definition_access(self, slug: str | None, action: str) -> None:
        """Gate a management call with ``slug:view`` / ``slug:manage`` (FEAT-182).

        ``slug=None`` (list, route-level ``:meta``) is evaluated on ``"*"``.
        No-op when PBAC is off; raises ``web.HTTPNotFound`` on deny.
        """
        await enforce_request_pbac(
            self.request,
            ResourceType.SLUG,
            slug or "*",
            action,
            logger=self.logger,
        )

```
**Why**: same call shape as `_enforce_scheduler_grant` (`:210`); `"*"` keeps `enforce_request_pbac`'s empty-name guard from denying the list for everyone.

### `querysource/handlers/manager.py` (MODIFY): `get()`
```python
# occurrences: `        registry = self.request.app.get('qs_tenant_registry')` = 5 (one per method) → NOT usable alone.
# Unique context (occurrences: 1, verified: grep -c "        # (AC-2/AC-4: get, pagination, :meta and :insert all route through" manager.py → :270):
#   the `# Resolve store once before …` comment block at :269-277 is followed by the registry line at :278.
# BEFORE — insert above the comment line `        # Resolve store once before any field/filter/:meta/:insert decision`
#   that is immediately followed by `        # (AC-2/AC-4: get, pagination, :meta and :insert all route through` (get() only, :269)
        # FEAT-182: gate before the try/except below, whose catch-all would turn
        # a PBAC 404 into a 500.
        await self._enforce_definition_access(query_slug, "slug:view")
```

### `querysource/handlers/manager.py` (MODIFY): write methods
```python
# FILL IN: for each of patch (:513), delete (:638), put (:775), post (:890) insert
#   await self._enforce_definition_access(<slug>, "slug:manage")
# right after that method's "slug missing" early-return and before its
# `registry = …` / `repo = …` lines.
#   patch/delete: <slug> = query_slug
#   put: <slug> = data['query_slug']
#   post: <slug> = slug["query_slug"]
# Bounded by: AC-2 (repository untouched on deny). Quote 2–3 lines of context per site;
# the `repo = self.request.app.get('qs_definition_repository')` line occurs in several methods.
```

### `tests/handlers/test_management_pbac.py` (CREATE)
```python
"""FEAT-182 TASK-943: QueryManager PBAC gate (slug:view / slug:manage)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from querysource.auth import setup_pbac
from querysource.handlers.manager import QueryManager

_PATCH_SESSION = "querysource.auth.request_gate.get_session"

_POLICIES = """
version: "1.0"
defaults:
  effect: deny
policies:
  - name: editors_manage
    effect: allow
    resources: ["slug:*"]
    actions: ["slug:view", "slug:manage"]
    subjects:
      groups: ["editors"]
    priority: 20
  - name: viewers_view
    effect: allow
    resources: ["slug:*"]
    actions: ["slug:view"]
    subjects:
      groups: ["viewers"]
    priority: 20
"""


@pytest.fixture
async def client(tmp_path):
    (tmp_path / "defaults.yaml").write_text(_POLICIES)
    app = web.Application()
    setup_pbac(app, policy_dir=str(tmp_path))
    # FILL IN: wire a fake qs_connection like tests/handlers/conftest.py::_build_app
    # (app["qs_connection"]) so allowed requests reach the handler.
    app.router.add_view("/api/v1/management/queries/{slug}", QueryManager)
    app.router.add_view(r"/api/v1/management/queries{meta:\:?.*}", QueryManager)
    c = TestClient(TestServer(app))
    await c.start_server()
    yield c
    await c.close()


def _session(groups):
    # FILL IN: return the session object request_gate reads userinfo from
    # (AUTH_SESSION_OBJECT key) — copy the shape used by tests/integration/test_pbac_enforcement.py.
    raise NotImplementedError


@pytest.mark.asyncio
async def test_manager_get_single_denied_404(client):
    """No slug:view → 404 and the repository/pool is never read."""
    # FILL IN


@pytest.mark.asyncio
async def test_manager_list_requires_view_star(client):
    """List and :meta need slug:view on '*'; viewers get the normal paginated response."""
    # FILL IN


@pytest.mark.asyncio
async def test_manager_gate_not_swallowed(client):
    """Denied GET is 404, never the 500 produced by get()'s catch-all."""
    # FILL IN


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_manager_write_methods_require_manage(client, method):
    """viewers (no slug:manage) → 404 on every write method; nothing written."""
    # FILL IN


@pytest.mark.asyncio
async def test_management_pbac_off_unchanged():
    """No setup_pbac (no app['security']) → identical behaviour to before FEAT-182."""
    # FILL IN: reuse tests/handlers/conftest.py test_client fixture path
```

### FILL IN checklist
- [ ] `manager.py` write methods: four insertion points with unique context; bounded by AC-2.
- [ ] Test fake pool wiring copied from `tests/handlers/conftest.py`; bounded by "no live Postgres".
- [ ] Session shape copied from existing PBAC integration tests; bounded by the R6-style rule: no hand-set request attributes.
- [ ] Every denial test mutation-checked (remove the gate → red).

---

## Acceptance Criteria

- [ ] With PBAC on, a user without `slug:view` gets 404 on single GET, list and `:meta`; with it, responses are unchanged.
- [ ] With PBAC on, a user without `slug:manage` gets 404 on POST/PUT/PATCH/DELETE and the repository is untouched.
- [ ] A denial is a 404, not a 500.
- [ ] With PBAC off, existing `tests/handlers/test_querymanager_pagination.py` still passes unchanged.
- [ ] `ruff check querysource/handlers/manager.py` clean.

---

## Validation Commands

- `pytest tests/handlers/test_management_pbac.py -q`
- `pytest tests/handlers/test_querymanager_pagination.py -q`
- `pytest tests/handlers/test_manager_run_as.py -q`

---

## Test Specification

See the `tests/handlers/test_management_pbac.py` blueprint above. Use the real
`PolicyEvaluator` through `setup_pbac(app, policy_dir=tmp_path)`. Never mock
`enforce_request_pbac` itself.

---

## Agent Instructions

1. Work in the feature worktree: `python -m scripts.sdd.ensure_worktree --slug querysource-management-pbac --feature-id FEAT-182`.
2. Read the spec; verify the Codebase Contract line numbers (`grep -n`) before editing.
3. Mark `"in-progress"` in `sdd/tasks/index/querysource-management-pbac.json` and commit only that file.
4. Implement from the blueprint; complete every `FILL IN`.
5. Run the Validation Commands.
6. Commit only the listed files.
7. Run `scripts/sdd/close_task.sh TASK-943 querysource-management-pbac verified`, then fill in the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none
