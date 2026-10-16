# TASK-906: Catalog endpoint and describe integration

**Feature**: FEAT-175 — Querysource Semantic Layer (Ossie models compiled by Sidemantic)
**Spec**: `sdd/specs/semantic-layer.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-899, TASK-900, TASK-901, TASK-902, TASK-903
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 9, S6. `GET /api/v1/semantic/{slug}/catalog` (+ tenant route) lists a semantic
slug's metrics/dimensions with the principal's per-field `semantic:use` grant, redacting denied
fields to `name`, `kind`, `granted=false` (AC-26). `describe_slug()` gains `derived.semantic`.

---

## Scope

- `querysource/handlers/semantic.py::SemanticCatalog(QueryDescribe).catalog`.
- Export from `querysource/handlers/__init__.py`; register both routes in `QuerySource.setup()`.
- `derived['semantic']` in `querysource/queries/describe.py::describe_slug` (no compiler import).
- Tests `tests/handlers/test_semantic_catalog.py` (all §4 M9 rows).

**NOT in scope**: execution (TASK-904).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/semantic.py` | CREATE | catalog handler |
| `querysource/handlers/__init__.py` | MODIFY | export `SemanticCatalog` |
| `querysource/services.py` | MODIFY | routes |
| `querysource/queries/describe.py` | MODIFY | `derived.semantic` |
| `tests/handlers/test_semantic_catalog.py` | CREATE | tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.handlers.describe import QueryDescribe     # verified: handlers/describe.py:70
from querysource.auth._resource_types import ResourceType   # verified (SEMANTIC from TASK-899)
from querysource.semantic.models import CatalogPayload, CatalogField, CompilationTarget, SemanticResource  # TASK-897
from querysource.semantic.exceptions import SemanticError, SemanticCompilerUnavailable  # TASK-897
from querysource.semantic.dialects import dialect_for_driver        # TASK-900
from querysource.semantic.loader import get_loader                  # TASK-901
from querysource.semantic import governance                         # TASK-902
from querysource.semantic.compilers import get_compiler             # TASK-903
```

### Existing Signatures to Use
```python
# querysource/handlers/describe.py
class QueryDescribe(AbstractHandler):                                    # :70
    async def _principal(self, request) -> Principal                    # :75 — HTTPUnauthorized when none
    async def _store(self, request) -> DescribeStore                     # :83 — legacy or {tenant} store; 404 unknown tenant
    async def _load_visible(self, request, principal, store, slug)      # :93 — 404 hidden/missing; returns the model
# querysource/handlers/abstract.py:335
    async def _enforce_pbac(self, request, resource_type, resource_name: str, action: str) -> None   # deny → HTTPNotFound
# querysource/queries/describe.py:204
def describe_slug(definition, grants, *, columns_link, vocabulary_link) -> dict   # returns at :260
# querysource/services.py:207-226 — `dh = QueryDescribe()` legacy + tenant describe routes
```

### Does NOT Exist
- ~~A management endpoint for semantic models~~ — out of scope.
- ~~`describe_slug` importing sidemantic~~ — forbidden; parse with `SemanticResource.from_yaml` only.
- ~~Revision on the describe model~~ — FILL IN: confirm what `_load_visible` returns (QueryModel vs LoadedDefinition) and how to obtain a revision for `ModelLoader.resolve`; fallback hash of `query_raw`.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/handlers/semantic.py", "action": "CREATE"},
    {"path": "querysource/handlers/__init__.py", "action": "MODIFY"},
    {"path": "querysource/services.py", "action": "MODIFY"},
    {"path": "querysource/queries/describe.py", "action": "MODIFY"},
    {"path": "tests/handlers/test_semantic_catalog.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/handlers/describe.py#QueryDescribe",
    "sym:querysource/handlers/abstract.py#AbstractHandler._enforce_pbac",
    "sym:querysource/queries/describe.py#describe_slug"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Status mapping: 401 (no principal) / 404 (missing, invisible, `provider != 'semantic'`, `semantic:execute` deny) / 501 (`SemanticCompilerUnavailable`) / 200 `CatalogPayload`.
- Dialect: resolve the resource's datasource driver; when it cannot be resolved without opening a connection, FILL IN — prefer the datasource registry metadata; never open a warehouse connection for the catalog.
- Redaction: denied field → `CatalogField(name, kind, granted=False)` with `type=None, description=None, granularities=[]`.
- `row_filters_applied`: roles matched by `governance.governance_filters`.
- `describe.py`: `derived['semantic'] = {'format', 'connection', 'definition_kind': 'inline'|'path', 'governance_roles': [...], 'catalog': '/api/v1/semantic/<slug>/catalog'}`; parse failure → `derived['semantic'] = {'error': '<message>'}` (describe must not 500).

---

## Implementation Blueprint

### Steps (in order)
1. Handler; 2. exports + routes; 3. describe extension; 4. tests (patterns: `tests/test_route_registration.py`, `tests/handlers/test_describe_tenant.py`).

### `querysource/handlers/semantic.py` (CREATE)
```python
"""Semantic catalog endpoint (FEAT-175 M9)."""
from __future__ import annotations

from aiohttp import web

from ..auth._resource_types import ResourceType
from ..semantic import governance
from ..semantic.exceptions import SemanticCompilerUnavailable, SemanticError
from ..semantic.models import CatalogField, CatalogPayload, SemanticResource
from .describe import QueryDescribe


class SemanticCatalog(QueryDescribe):
    """Read-only catalog for semantic slugs."""

    async def catalog(self, request: web.Request) -> web.Response:
        """200 CatalogPayload | 401 | 404 | 501."""
        slug = request.match_info["slug"]
        principal = await self._principal(request)
        store = await self._store(request)
        model = await self._load_visible(request, principal, store, slug)
        if getattr(model, "provider", None) != "semantic":
            raise web.HTTPNotFound()
        await self._enforce_pbac(request, ResourceType.SEMANTIC, slug, "semantic:execute")
        # FILL IN: resource = SemanticResource.from_yaml(model.query_raw) (SemanticError → 404 + log);
        # model_ref = get_loader().resolve(...); target (dialect via dialect_for_driver); fields =
        # get_compiler(...).catalog(model_ref, target) (SemanticCompilerUnavailable → web.HTTPNotImplemented);
        # per field governance.check(request, qs_principal, f"{slug}:{kind}:{short}", "semantic:use") →
        # granted / redact; payload = CatalogPayload(...); return self.json_response(payload.model_dump())
        # FILL IN: verify json response helper name on AbstractHandler/BaseHandler (else web.json_response)
```
**Why**: `QueryDescribe` gives tenant-aware `_store`/`_load_visible` (S6). The visibility `Principal`
differs from `QSPrincipal`: FILL IN — build the `QSPrincipal` for `governance.check` via
`governance.principal_from_session(await self._get_user_session(request))` (abstract.py:313).

### `querysource/handlers/__init__.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'from .scheduler import SchedulerJobsView' querysource/handlers/__init__.py)
# AFTER — insert below `from .scheduler import SchedulerJobsView` (verified: handlers/__init__.py:12; alphabetical)
from .semantic import SemanticCatalog
```
```python
# occurrences: 1 (verified: grep -c "    'SchedulerJobsView'," querysource/handlers/__init__.py)
# AFTER — insert below `    'SchedulerJobsView',` (verified: handlers/__init__.py:25)
    'SemanticCatalog',
```

### `querysource/services.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '^    QueryService,$' querysource/services.py)
# AFTER — insert below `    QueryService,` inside `from .handlers import (` (verified: services.py:29)
    SemanticCatalog,
```
```python
# occurrences: 1 (verified: grep -c "add_get('/api/v1/{tenant}/queries/{slug}/columns', dh.columns)" querysource/services.py)
# AFTER — insert below that line AND its following `        routes.append(r)` (verified: services.py:225-226)

        ### Semantic catalog (FEAT-175): metrics/dimensions with per-field grants.
        sc = SemanticCatalog()
        r = self.app.router.add_get('/api/v1/semantic/{slug}/catalog', sc.catalog)
        routes.append(r)
        r = self.app.router.add_get('/api/v1/{tenant}/semantic/{slug}/catalog', sc.catalog)
        routes.append(r)
```

### `querysource/queries/describe.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '    return {\*\*payload, "derived": derived, "redacted": redacted}' querysource/queries/describe.py)
# BEFORE — insert above `    return {**payload, "derived": derived, "redacted": redacted}` (verified: describe.py:260)
    if base.get("provider") == "semantic":
        derived["semantic"] = _semantic_summary(base)
```
```python
# NEW module-level helper (place above `def describe_slug`, describe.py:204)
def _semantic_summary(base: dict[str, Any]) -> dict[str, Any]:
    """Summarize a semantic resource without importing any compiler (FEAT-175)."""
    from ..semantic.exceptions import SemanticError
    from ..semantic.models import SemanticResource
    # FILL IN: parse base['query_raw']; build the dict from Key Constraints; SemanticError → {'error': str(e)}
```

### FILL IN checklist
- [ ] Handler body, json helper, QSPrincipal derivation, revision source, dialect without connection
- [ ] `_semantic_summary`
- [ ] Test bodies

---

## Acceptance Criteria

- [ ] Both catalog routes registered (AC-16).
- [ ] No session → 401; non-semantic slug → 404; semantic slug → 200 with `granted` per field (AC-16).
- [ ] Denied field serialized with only `name`, `kind`, `granted=false` (AC-26).
- [ ] Tenant route resolves the tenant store (AC-26).
- [ ] `describe_slug()` adds `derived.semantic` for `provider='semantic'` (AC-16).

---

## Validation Commands
- `pytest tests/handlers/test_semantic_catalog.py -q`
- `pytest tests/test_route_registration.py -q`
- `pytest tests/handlers/test_describe_tenant.py -q`

---

## Test Specification

```python
# tests/handlers/test_semantic_catalog.py
# FILL IN following tests/test_route_registration.py and tests/handlers/test_describe_tenant.py patterns:
def test_catalog_route_registered(): ...
async def test_catalog_401_404_200(): ...
async def test_catalog_redacts_denied_fields(): ...
async def test_catalog_tenant_route_uses_tenant_store(): ...
def test_describe_semantic_derived(): ...
```

---

## Agent Instructions

1. Work in the feature worktree; dependencies done.
2. Implement; validate; commit only listed files.
3. Close with `scripts/sdd/close_task.sh TASK-906 semantic-layer verified`.

---

## Completion Note

**Completed by**: sdd-worker (coder seat gpt-5.6-luna, codex, 1 attempt, 71s)
**Date**: 2026-10-15
**Notes**: Merge-tier tests green, no fixes. Review recorded as coder-review:df1f9e50912500ec4753eea8. Closed via close_task.sh (finalize_task needs a durable review EvidenceRef no available tool produces).

**Deviations from spec**: none
