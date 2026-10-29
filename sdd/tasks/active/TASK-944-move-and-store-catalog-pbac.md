# TASK-944: PBAC gate for slug move and the query-store catalogue

**Feature**: FEAT-182 — PBAC Coverage for the Management API, Direct Driver Route and Describe Fallback
**Spec**: `sdd/specs/querysource-management-pbac.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-943
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 1 (last two bullets). `QueryMoveView` (`POST /api/v1/management/queries/{slug}/move`)
and `QueryStoreCatalog.list` (`GET /api/v1/management/query-stores`) belong to the
same management boundary as `QueryManager`, but have no PBAC check.

---

## Scope

- `QueryMoveView.post`: call `self._enforce_definition_access(slug, "slug:manage")` (inherited from
  `QueryManager`, added by TASK-943) right after `slug = self.request.match_info.get("slug")`,
  **before** the `try:`. *Why before*: the `try:` maps exceptions to tenant error responses.
- `QueryStoreCatalog.list`: call `enforce_request_pbac(request, ResourceType.SLUG, "*", "slug:view", logger=…)`
  before the registry lookup.
- Write tests.

**NOT in scope**: `QueryManager` methods (TASK-943).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/query_moves.py` | MODIFY | `slug:manage` gate in `post()` |
| `querysource/handlers/query_stores.py` | MODIFY | `slug:view` on `*` in `list()` |
| `tests/handlers/test_management_moves_stores_pbac.py` | CREATE | gate tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.handlers.manager import QueryManager            # query_moves.py:17 (already imported)
from querysource.auth import ResourceType                         # verified: querysource/handlers/manager.py:22
from querysource.auth.request_gate import enforce_request_pbac    # verified: querysource/handlers/manager.py:24
from navconfig.logging import logging                             # query_stores.py:12 (already imported)
```

### Existing Signatures to Use
```python
# querysource/handlers/query_moves.py
class QueryMoveView(QueryManager):                                # :32
    async def post(self) -> web.Response:                         # :72
        slug = self.request.match_info.get("slug")                # :74
        try:                                                      # :75

# querysource/handlers/query_stores.py
class QueryStoreCatalog:                                          # :67
    async def list(self, request: web.Request) -> web.Response:   # :70
        registry = request.app.get("qs_tenant_registry")          # :72

# from TASK-943 (querysource/handlers/manager.py)
async def QueryManager._enforce_definition_access(self, slug: str | None, action: str) -> None
```

### Does NOT Exist
- ~~`QueryStoreCatalog.logger`~~: it is a plain class with no logger attribute. Use the existing module-level `logger = logging.getLogger("QS.QueryStoreCatalog")` (`query_stores.py:17`).
- ~~`QueryStoreCatalog._enforce_definition_access`~~: not a `QueryManager`. Call `enforce_request_pbac` directly.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/handlers/query_moves.py", "action": "MODIFY"},
    {"path": "querysource/handlers/query_stores.py", "action": "MODIFY"},
    {"path": "tests/handlers/test_management_moves_stores_pbac.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/handlers/query_moves.py#QueryMoveView.post",
    "sym:querysource/handlers/query_stores.py#QueryStoreCatalog.list",
    "sym:querysource/handlers/manager.py#QueryManager",
    "sym:querysource/auth/request_gate.py#enforce_request_pbac"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- The move is denied **before** `_move_body()`, so a denied caller learns nothing about body validation.
- PBAC off → no change.

---

## Implementation Blueprint

### Steps (in order)
1. Insert the move gate after `:74`. *Why*: deny before body parsing and the move transaction.
2. Add the imports and gate to `query_stores.py`. *Why*: the catalogue reveals the tenant/store layout.
3. Write the tests and mutation-check them.

### `querysource/handlers/query_moves.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '        slug = self.request.match_info.get("slug")' querysource/handlers/query_moves.py)
# AFTER — insert below `        slug = self.request.match_info.get("slug")` (verified: query_moves.py:74)
        # FEAT-182: same slug:manage gate as the CRUD write methods.
        await self._enforce_definition_access(slug, "slug:manage")
```

### `querysource/handlers/query_stores.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '        registry = request.app.get("qs_tenant_registry")' querysource/handlers/query_stores.py)
# BEFORE — insert above `        registry = request.app.get("qs_tenant_registry")` (verified: query_stores.py:72)
        # FEAT-182: the catalogue is part of the management boundary.
        await enforce_request_pbac(
            request, ResourceType.SLUG, "*", "slug:view", logger=logger,
        )
# Add the two imports (Verified Imports). `logger` is the existing module logger (query_stores.py:17).
```

### `tests/handlers/test_management_moves_stores_pbac.py` (CREATE)
```python
"""FEAT-182 TASK-944: move + store-catalogue PBAC gates."""
from __future__ import annotations

import pytest

_PATCH_SESSION = "querysource.auth.request_gate.get_session"


@pytest.mark.asyncio
async def test_move_requires_manage():
    """Viewer (slug:view only) → 404 on POST …/{slug}/move; no store resolved."""
    # FILL IN: real setup_pbac app (see TASK-943 test fixture), route
    # app.router.add_route('POST', r'/api/v1/management/queries/{slug}/move', QueryMoveView)


@pytest.mark.asyncio
async def test_store_catalog_requires_view():
    """No slug:view on '*' → 404; with it → 200/503 as before."""
    # FILL IN
```

### FILL IN checklist
- [ ] Test bodies; mutation-check both.

---

## Acceptance Criteria

- [ ] Move denied (404) without `slug:manage`, before body validation.
- [ ] Catalogue denied (404) without `slug:view` on `*`.
- [ ] PBAC off: unchanged.

---

## Validation Commands

- `pytest tests/handlers/test_management_moves_stores_pbac.py -q`

---

## Test Specification

See the blueprint above. Use a real evaluator via `setup_pbac` with a `tmp_path` policy file.

---

## Agent Instructions

Same as TASK-943, with this task's id and files. TASK-943 must be `done` first.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none
