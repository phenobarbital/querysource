# TASK-760: QueryHandler — forward definition, multi columns, validate-only dry-run

**Feature**: FEAT-151 — Unified single/multi dispatch on `/api/v1/{tenant}/queries/{slug}`
**Spec**: `sdd/specs/multiquery-multitenant.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-758, TASK-759
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 4, design-research S5/S6. The tenant dispatcher (TASK-762) routes stored
multi definitions here. `QueryHandler` must (1) pass the pre-loaded definition to
`MultiQS`, (2) answer HEAD/PATCH from the definition's `columns_definition`, and (3)
offer a validate-only dry-run that never executes anything. The dispatcher has
already authorized the parent and stashed `request['qs_tenant']` and
`request['qs_definition']` (a `LoadedDefinition`).

---

## Scope

- In `QueryHandler.query()`, pass `definition=request.get('qs_definition')` to `MultiQS`.
- Make `QueryHandler.columns()` definition-aware (shapes below); without a definition on the request keep today's 204 exactly (v3 callers).
- Add `QueryHandler.test_slug()` (validate-only dry-run, envelope below).
- Write unit tests.

**NOT in scope**: the tenant dispatcher (TASK-762); `_preflight_multiquery_owned` (spec §8 Q5).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/multi.py` | MODIFY | forward definition; definition-aware `columns`; new `test_slug` |
| `tests/tenants/test_multi_handler_definition.py` | CREATE | unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
Already imported in `querysource/handlers/multi.py` (lines 1-22): `time`, `web`,
`MultiQS` (`from ..queries import MultiQS`), `TenantError`, `QueryIdentity`,
`AbstractHandler`. Add only:
```python
import json                                                   # stdlib
from datetime import datetime                                 # stdlib (envelope timing, mirrors handlers/service.py:7)
```
Tests:
```python
from querysource.handlers.multi import QueryHandler           # verified: querysource/handlers/__init__.py:10
from querysource.models import QueryModel
from querysource.tenants import LoadedDefinition, QueryIdentity, QueryStore
```

### Existing Signatures to Use
```python
# querysource/handlers/multi.py:25
class QueryHandler(AbstractHandler):
    async def columns(self, request: web.Request) -> web.StreamResponse:   # 188; raises self.no_content(headers={'Content-Type': 'application/json', 'X-Message': 'No Columns available'})
    async def query(self, request: web.Request) -> web.StreamResponse:     # 196
    #   qs = MultiQS(slug=slug, queries=_queries, files=_files, query=options,
    #                conditions=data, user_session=_user_session, tenant=_tenant,)   # 335-343 ; `tenant=_tenant,` at 342

# querysource/handlers/abstract.py
async def _enforce_owned_slug(self, request, identity: QueryIdentity, action: str) -> None:  # 463; no-op when request.app.get("security") is None; raises web.HTTPNotFound on deny
# navigator BaseView (inherited): no_content(headers=None, content_type='application/json');
#   json_response(response=None, reason=None, headers=None, status=200, ...); error(response=None, exception=None, status=400, ...)
#   query_parameters(request) -> dict ; match_parameters(request) -> dict

# querysource/handlers/service.py (shapes to mirror, do not import)
#   get_columns 204 headers: {"X-Columns": f"{columns!r}", "X-Slug": str(slug)} + 'X-Message': "No Columns found" when empty  # 474-484
#   test_slug envelope: {"slug", "works", "error", "generated", "execution"} + "conditions","query" unless ignore_query  # 723-733
#   ignore_query popped from params  # 659-663

# querysource/queries/multi/__init__.py (after TASK-758)
MultiQS._normalize_sources(raw) -> list                                      # 174
MultiQS.resolve_child_owner(query_cfg, parent_tenant, registry) -> tuple[str | None, QueryStore]  # new in TASK-758
# querysource/repositories/definitions.py:161
DefinitionRepository.get(identity) -> LoadedDefinition  # TenantError(error_code=...) when missing/unavailable
# app keys: request.app["qs_tenant_registry"] (TenantRegistry), request.app["qs_definition_repository"] (DefinitionRepository)
```

### Does NOT Exist
- ~~`MultiQS.dry_run()`~~ — do not call it; the dry-run is implemented here.
- ~~`QueryHandler.get_columns`~~ — HEAD and PATCH both go through `columns()`.
- ~~`QueryStore.tenant`~~ — report the store as `f"{store.schema}.{store.table}"`.
- ~~`attributes['columns']`~~ as the multi source — use `runtime.columns_definition` only.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/handlers/multi.py", "action": "MODIFY"},
    {"path": "tests/tenants/test_multi_handler_definition.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/handlers/multi.py#QueryHandler",
    "sym:querysource/handlers/multi.py#QueryHandler.columns",
    "sym:querysource/handlers/multi.py#QueryHandler.query",
    "sym:querysource/handlers/abstract.py#AbstractHandler._enforce_owned_slug",
    "sym:querysource/repositories/definitions.py#DefinitionRepository.get"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- The dry-run constructs no `MultiQS`, no `ThreadQuery`, opens no datasource connection, runs no `EXPLAIN` (AC-8, S5).
- A non-multi `query_raw` is a warning, not an error (decision: keep the MultiQS fallback).
- Without `request['qs_definition']`, `columns()` keeps its current 204 exactly (v3 parity, AC-5).

---

## Implementation Blueprint

### Steps (in order)
1. Forward the definition to `MultiQS` — *why*: AC-4.
2. Rewrite `columns()` — *why*: AC-6.
3. Add `test_slug()` — *why*: AC-8.
4. Write the tests.

### `querysource/handlers/multi.py` (MODIFY) — forward
```python
# occurrences: 1 (verified: grep -c '            tenant=_tenant,' querysource/handlers/multi.py)
# AFTER — insert below `            tenant=_tenant,` (verified: querysource/handlers/multi.py:342)
            definition=request.get('qs_definition'),
```

### `querysource/handlers/multi.py` (MODIFY) — columns
```python
# occurrences: 1 (verified: grep -c '    async def columns(self, request: web.Request) -> web.StreamResponse:' querysource/handlers/multi.py)
# REPLACE the whole method at line 188 with:
    async def columns(self, request: web.Request) -> web.StreamResponse:
        """Column inspection for a stored multi definition.

        Uses ``request['qs_definition'].runtime.columns_definition`` when the tenant
        dispatcher stashed a definition. HEAD answers 204 with ``X-Columns``/``X-Slug``
        (plus ``X-Message: No Columns found`` when empty). PATCH answers 200 with the
        list, or the legacy 204 ``No Columns available`` when empty. Without a
        definition the legacy 204 is returned unchanged (v3 callers).
        """
        definition = request.get('qs_definition')
        columns = list(getattr(definition.runtime, 'columns_definition', None) or []) if definition else []
        if definition is not None and request.method == 'HEAD':
            headers = {
                'Content-Type': 'application/json',
                'X-Columns': f"{columns!r}",
                'X-Slug': str(definition.identity.slug),
            }
            if not columns:
                headers['X-Message'] = 'No Columns found'
            return self.no_content(headers=headers)
        if columns:
            return self.json_response(columns, status=200)
        raise self.no_content(
            headers={
                'Content-Type': 'application/json',
                'X-Message': 'No Columns available',
            }
        )
```
**Why**: the legacy branch keeps `raise self.no_content(...)` exactly as today so existing v3 behavior and tests do not move.

### `querysource/handlers/multi.py` (MODIFY) — test_slug (new, after `columns`)
```python
    async def test_slug(self, request: web.Request) -> web.StreamResponse:
        """Validate a stored multi definition without executing it (FEAT-151).

        Requires ``request['qs_definition']`` (set by TenantQueryHandler). Resolves each
        saved child's owner with ``MultiQS.resolve_child_owner``, checks existence with
        the definition repository and ownership with ``_enforce_owned_slug``, and returns
        the single dry-run envelope extended with ``kind``, ``children``, ``files``,
        ``sources`` and ``warnings``. Never builds executors, opens datasource
        connections, or runs EXPLAIN.
        """
        started = datetime.now()
        definition = request.get('qs_definition')
        if definition is None:
            return self.error(response={'message': 'No stored definition to test.'}, status=400)
        params = self.query_parameters(request) or {}
        ignore_query = bool(params.pop('ignore_query', False))
        tenant = request.get('qs_tenant')
        registry = request.app.get('qs_tenant_registry')
        repo = request.app.get('qs_definition_repository')
        warnings: list = []
        payload: dict = {}
        # FILL IN: parse definition.runtime.query_raw with json.loads; on failure or when
        #   the result is not a dict containing 'queries'|'files'|'sources', append the
        #   warning "query_raw is not a multi-query payload; MultiQS will fall back to
        #   single-query mode" and keep payload = {} — bounded by §8 fallback decision.
        children: list = []
        for alias, cfg in (payload.get('queries') or {}).items():
            # FILL IN: build one child entry {alias, slug, kind, tenant, store, exists,
            #   allowed, error}. Raw child ('query' key, no 'slug'): kind='raw', other
            #   fields None. Saved child: (child_tenant, store) =
            #   MultiQS.resolve_child_owner(cfg, tenant, registry); exists via
            #   await repo.get(QueryIdentity(store=store, slug=cfg['slug'])) —
            #   TenantError -> exists=False, error=err.error_code; allowed=None when
            #   request.app.get('security') is None, else _enforce_owned_slug(...,
            #   action='slug:execute') with web.HTTPNotFound -> False — bounded by AC-8.
            pass
        works = all(c['exists'] is not False and c['allowed'] is not False for c in children)
        resultset = {
            'slug': definition.identity.slug,
            'kind': 'multi',
            'works': works,
            'error': None,
            'generated': (datetime.now() - started).total_seconds(),
            'execution': None,
            'tenant': tenant,
            'store': f"{definition.identity.store.schema}.{definition.identity.store.table}",
            'children': children,
            'files': sorted((payload.get('files') or {}).keys()),
            'sources': [],  # FILL IN: source type names from MultiQS._normalize_sources(payload.get('sources', []))
            'warnings': warnings,
        }
        if not ignore_query:
            resultset['conditions'] = params
            resultset['query'] = payload
        # FILL IN: queryformat txt/plain/raw -> text/plain of json.dumps(payload, indent=2),
        #   mirroring handlers/service.py:734-738; default -> json below.
        return self.json_response(resultset, status=200)
```
**Why**: keys match the single envelope (`service.py:723-733`) plus the multi additions fixed by spec §2; no execution path is reachable.

### `tests/tenants/test_multi_handler_definition.py` (CREATE)
```python
"""FEAT-151: QueryHandler definition-aware columns and validate-only dry-run."""
import json
import pytest
from aiohttp import web

from querysource.handlers.multi import QueryHandler
from querysource.models import QueryModel
from querysource.tenants import LoadedDefinition, QueryIdentity, QueryStore

MULTI_RAW = json.dumps({"queries": {"a": {"slug": "child_a"}, "b": {"slug": "child_b", "tenant": None},
                                    "r": {"query": "SELECT 1"}}, "files": {}})


def _loaded(query_raw: str = MULTI_RAW, columns: list | None = None) -> LoadedDefinition:
    store = QueryStore(database_namespace="localhost:5432/qs", schema="tenant1", table="queries",
                       contract="tenant", columns=frozenset({"query_slug"}))
    runtime = QueryModel(query_slug="parent", program_slug="tenant1", provider="multi",
                         query_raw=query_raw, columns_definition=columns or [])
    return LoadedDefinition(identity=QueryIdentity(store=store, slug="parent"), runtime=runtime, revision="r1")

# FILL IN: request factory — reuse the MagicMock pattern of
#   tests/tenants/test_tenant_http_routes.py:24-41 (storage dict behind request.get);
#   pre-populate storage with qs_definition / qs_tenant; app dict with a fake registry
#   and a fake repository whose get() returns or raises TenantError("...", error_code="query_not_found").

# FILL IN tests:
#   test_columns_head_multi_headers          (204, X-Columns, X-Slug, X-Message when empty)
#   test_columns_patch_multi_list_or_204     (200 list | 204 'No Columns available')
#   test_columns_without_definition_legacy_204
#   test_multi_dry_run_reports_children      (inherit/explicit-null/raw; exists/allowed; works)
#   test_multi_dry_run_non_multi_payload_warns
#   test_multi_dry_run_requires_definition   (400)
#   test_query_forwards_definition_to_multiqs (monkeypatch querysource.handlers.multi.MultiQS to capture kwargs)
```

### FILL IN checklist
- [ ] `test_slug` payload parsing + warning — bounded by the fallback decision (spec §8).
- [ ] per-child entry — bounded by AC-8 and the §2 envelope.
- [ ] `sources` listing and text formats — bounded by spec §2 envelope.
- [ ] all seven tests.

---

## Acceptance Criteria

- [ ] `MultiQS` receives `definition=` from the request (AC-4).
- [ ] HEAD/PATCH shapes match AC-6; without a definition the legacy 204 is unchanged.
- [ ] Dry-run matches AC-8; no executor or datasource access.
- [ ] `ruff check querysource/handlers/multi.py tests/tenants/test_multi_handler_definition.py`

## Validation Commands

- `pytest tests/tenants/test_multi_handler_definition.py -q`
- `pytest tests/handlers/test_multiquery_pbac_smoke.py -q`
- `pytest tests/unit/test_handler_output_status.py -q`

---

## Agent Instructions

1. Read the spec. 2. Confirm TASK-758 and TASK-759 are done. 3. Verify the Codebase Contract. 4. Implement from the blueprint and complete every `FILL IN`. 5. Run the validation commands and ruff. 6. Move this file to `sdd/tasks/completed/`, update the index, fill the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*
