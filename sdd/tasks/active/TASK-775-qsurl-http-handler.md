# TASK-775: `QSUrlService` handler and `/api/v1/services/qsurl/{path:.*}` route

**Feature**: FEAT-152 — qsurl: HTSQL-style URL query dialect (chumsky 0.13 + PyO3)
**Spec**: `sdd/specs/qsurl-parser.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-765, TASK-770, TASK-771, TASK-773, TASK-774
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 10, AC5–AC8. The HTTP surface: a dedicated route (brainstorm decision, the
legacy `/api/v2/services/queries/{slug}` is untouched) that accepts the whole qsurl string in
the path or `<slug>?q=<rest>`, enforces PBAC exactly like `QueryService.query`, resolves the
executing provider's capabilities with a lazy probe, splits the IR, executes through `QS`
with the residual plan, and answers every `QSUrlError` with HTTP 400 carrying the structured
error in `detail`.

**Task-time finding (decode once)**: aiohttp 3.14.3 already percent-decodes both
`match_info["path"]` (including `%2F`) and `request.query["q"]` exactly once
(`/qsurl/stores%7Ba%7D%3Fs%3D'C%2FA'%2527` → `stores{a}?s='C/A'%27`). The handler therefore
calls **no** `unquote()`; a second decode would turn `%27` into `'`.

---

## Scope

- Create `querysource/handlers/qsurl.py` with `QSUrlService(AbstractHandler)`: `query()` and `resolve_capabilities()`.
- Export it from `querysource/handlers/__init__.py`; register the GET route in `querysource/services.py`.
- Tests: `tests/handlers/test_qsurl_service.py` (fake-request unit tests + one aiohttp `TestServer` test for decode-once).

**NOT in scope**: `POST`/`HEAD` on the new route; `slug:format` suffix; IR-as-body endpoint; docs (TASK-778).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/qsurl.py` | CREATE | `QSUrlService` |
| `querysource/handlers/__init__.py` | MODIFY | Import + `__all__` |
| `querysource/services.py` | MODIFY | Route registration |
| `tests/handlers/test_qsurl_service.py` | CREATE | Handler tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from aiohttp import web                                                   # querysource/handlers/service.py:11
from asyncdb.exceptions import ConnectionTimeout, ProviderError          # querysource/handlers/service.py:12
from querysource.auth import ResourceType                                 # querysource/handlers/service.py:14
from querysource.conf import CSV_DEFAULT_DELIMITER, CSV_DEFAULT_QUOTING  # querysource/handlers/service.py:15
from querysource.exceptions import DriverError, ParserError, QueryException, SlugNotFound   # service.py:16-21
from querysource.outputs import DataOutput                               # service.py:24
from querysource.tenant_errors import TenantError                        # service.py:25
from querysource.tenants import QueryIdentity                            # service.py:26
from querysource.types import graph_ouputs, mime_supported               # service.py:27 (sic: graph_ouputs)
from querysource.handlers.abstract import AbstractHandler                # querysource/handlers/abstract.py:32
from querysource.queries.qs import QS                                    # querysource/handlers/abstract.py:16 (import site)
from querysource.qsurl import QSUrlError, parse                          # TASK-765
from querysource.qsurl.translate import split                            # TASK-771
```

### Existing Signatures to Use
```python
# querysource/handlers/service.py — the flow to MIRROR (copy, do not call)
class QueryService(AbstractHandler):                    # line 31
    async def query(self, request):                     # line 134
        params = self.query_parameters(request)         # 164 (navigator BaseHandler.query_parameters)
        args = self.match_parameters(request)           # 165
        options = await self.json_data(request)         # 169 (TypeError/ValueError → {})
        # PBAC slug:execute (200-225):
        tenant = request.get('qs_tenant'); registry = request.app.get("qs_tenant_registry")
        #   registry → store = registry.resolve(tenant) (HTTPNotFound re-raised; other Exception → warning + HTTPNotFound)
        #            → await self._enforce_owned_slug(request, identity=QueryIdentity(store=store, slug=slug), action="slug:execute")
        #   else     → await self._enforce_pbac(request, resource_type=ResourceType.SLUG, resource_name=slug, action="slug:execute")
        # queryformat / tpl / _download / _filename / writer_options / graph options (227-289)
        # conditions = {**options, **params} (292)
        # query = await self.get_source(request, slug, conditions, driver=args, tenant=tenant, definition=request.get('qs_definition'))  (307-310)
        # await query.build_provider(): SlugNotFound → Error(400 "Slug Not Found: {slug}"), TenantError → Error(code=err.code),
        #   ParserError → Error("Error parsing Query Slug"), (ProviderError, DriverError) → Error("Connection Error"),
        #   Exception → Except("Unknown Error on Query")   (311-345)
        # datasource:use / driver:use PBAC (346-367); queryformat from query.accepts() → mime_supported (369-371)
        # output = DataOutput(request, query=query, ctype=queryformat, slug=slug, **output_args); return await output.response()  (372-380)
        # outer: web.HTTPException re-raised; (ProviderError, DriverError) → Error('Query Failed'); Exception → Except  (396-407)

# querysource/handlers/abstract.py
async def get_source(self, request, slug, conditions, **kwargs) -> QS   # line 269 — QS(slug=slug, conditions=conditions, loop=self._loop, request=request, lazy=False, **kwargs)
def Error(self, reason=None, message=None, exception=None, stacktrace=None, code=400, detail=None)   # line 133 (+detail from TASK-774) — RETURNS the exception; callers `raise self.Error(...)`
def Except(self, reason=None, message=None, exception=None, stacktrace=None, headers=None, code=500)   # line 204
def format(self, request, args: dict, ctype: str = None) -> str      # line 56
async def _enforce_pbac(self, request, resource_type, resource_name: str, action: str) -> None   # line 322
async def _enforce_owned_slug(self, request, identity: QueryIdentity, action: str) -> None      # line 440

# querysource/queries/qs.py (after TASK-773)
QS(slug, conditions=..., request=..., residual: ResidualPlan | None = None, lazy=..., tenant=..., definition=...)
async def build_provider(self)    # line 157 — sets self._qs (provider instance)
async def close(self)             # after query(); disposes the connection
# provider class attributes (TASK-770): query._qs.capabilities: frozenset[str], query._qs.residual_scan: bool

# querysource/services.py
from .handlers import (LoggingService, QueryDescribe, QueryExecutor, QueryHandler, QueryManager, QueryService, VariablesService,)   # 22-30
qs = QueryService()                                                                                   # 168
r = self.app.router.add_head('/api/v2/services/queries/{slug}', qs.get_columns)                        # 189
routes.append(r)                                                                                       # 190

# querysource/handlers/__init__.py
from .service import QueryService     # line 12
    'QueryService',                   # line 22 (in __all__)

# tests/handlers/test_queryservice_pbac_smoke.py:19-32 — handler built with __new__, MagicMock(spec=web.Request)
```

### Does NOT Exist
- ~~`querysource/handlers/qsurl.py`~~ — created here.
- ~~a slug → provider-class resolver~~ — use the `resolve_capabilities` probe (spec §3 M10 design note).
- ~~`unquote()` in the handler~~ — aiohttp already decoded once (task-time finding).
- ~~`slug:format`~~ on this route — `:` introduces pipeline operators.
- ~~`pytest-aiohttp` / the `aiohttp_client` fixture~~ — not installed; use `aiohttp.test_utils.TestServer` + `TestClient` directly.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/handlers/qsurl.py", "action": "CREATE"},
    {"path": "querysource/handlers/__init__.py", "action": "MODIFY"},
    {"path": "querysource/services.py", "action": "MODIFY"},
    {"path": "tests/handlers/test_qsurl_service.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/handlers/abstract.py#AbstractHandler",
    "sym:querysource/handlers/abstract.py#AbstractHandler.get_source",
    "sym:querysource/handlers/service.py#QueryService.query",
    "sym:querysource/queries/qs.py#QS"
  ]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Source assembly: `path = args.pop("path")`; `q = params.pop("q", None)`; with `q`, `path` must match `^[A-Za-z0-9_-]+$` else `QSUrlError("parse", "with ?q= the path must be a bare slug; q carries everything after it")` — *why*: AC5 and the brainstorm edge case.
2. `ir = parse(source)`; `slug = ir["slug"]` — *why*: the slug comes from the grammar, never from raw path splitting.
3. PBAC `slug:execute` block copied from `service.py:200-225` — *why*: AC6 parity.
4. Output negotiation copied from `service.py:227-289` (factor into `_output_args`) — *why*: unchanged negotiation (G4).
5. `caps, scan = await self.resolve_capabilities(...)`, then `conditions, plan = split(ir, caps, residual_scan=scan)`; `conditions = {**params, **options, **conditions}` — *why*: pushdown dict must exist before the executing `QS` is built (parser pops keys at construction).
6. `get_source(..., residual=plan)` → `build_provider()` (same error mapping) → datasource/driver PBAC → `DataOutput(...).response()`.
7. Wrap everything so `QSUrlError` → `raise self.Error(message=err.message, exception=err, code=400, detail=err.to_dict())`.
8. Register route; export class; tests.

### `querysource/handlers/qsurl.py` (CREATE)
```python
"""qsurl HTTP handler: GET /api/v1/services/qsurl/{path:.*} (FEAT-152)."""
from __future__ import annotations

import re

from aiohttp import web
from asyncdb.exceptions import ProviderError

from ..auth import ResourceType
from ..exceptions import DriverError, ParserError, QueryException, SlugNotFound
from ..outputs import DataOutput
from ..qsurl import QSUrlError, parse
from ..qsurl.translate import split
from ..queries.qs import QS
from ..tenant_errors import TenantError
from ..tenants import QueryIdentity
from ..types import mime_supported
from .abstract import AbstractHandler

_BARE_SLUG = re.compile(r"^[A-Za-z0-9_-]+$")


class QSUrlService(AbstractHandler):
    """Parse a qsurl query, enforce PBAC and execute it through QS."""

    async def query(self, request: web.Request) -> web.StreamResponse:
        """Handle one qsurl read (see TASK-775 Steps 1-7).

        Raises:
            web.HTTPException: 400 (qsurl errors, with ``detail``), 404 (PBAC deny), others as QueryService.
        """
        params = self.query_parameters(request)
        args = self.match_parameters(request)
        try:
            options = await self.json_data(request) or {}
        except (TypeError, ValueError):
            options = {}
        try:
            source = self._source(args.pop("path", ""), params.pop("q", None))
            ir = parse(source)
            slug = ir["slug"]
            tenant = request.get("qs_tenant")
            await self._enforce_slug_execute(request, slug, tenant)
            queryformat, output_args = self._output_args(request, params, options)
            caps, scan = await self.resolve_capabilities(request, slug, tenant)
            pushdown, plan = split(ir, caps, residual_scan=scan)
            conditions = {**options, **params, **pushdown}
            # FILL IN: get_source(request, slug, conditions, driver=args, tenant=tenant,
            #   definition=request.get('qs_definition'), residual=plan); build_provider() with the
            #   service.py:311-345 mapping; datasource/driver PBAC (service.py:346-367); accepts() → queryformat;
            #   return await DataOutput(request, query=query, ctype=queryformat, slug=slug, **output_args).response()
        except QSUrlError as err:
            raise self.Error(message=err.message, exception=err, code=400, detail=err.to_dict()) from err
        except web.HTTPException:
            raise
        # FILL IN: (ProviderError, DriverError) → Error('Query Failed'); (QueryException, Exception) → Except — as service.py:399-407

    @staticmethod
    def _source(path: str, q: str | None) -> str:
        """Return the qsurl source; never re-decodes (aiohttp decoded once)."""
        if q is None:
            return path
        if not _BARE_SLUG.match(path):
            raise QSUrlError("parse", "with ?q= the path must be a bare slug; q carries everything after it")
        return f"{path}{q}"

    async def _enforce_slug_execute(self, request: web.Request, slug: str, tenant: str | None) -> None:
        """PBAC slug:execute, tenant-aware — verbatim logic of service.py:200-225."""
        # FILL IN: copy the registry / _enforce_owned_slug / _enforce_pbac branch

    def _output_args(self, request: web.Request, params: dict, options: dict) -> tuple[str | None, dict]:
        """queryformat + DataOutput kwargs — verbatim logic of service.py:227-289 (pops its keys from params/options)."""
        # FILL IN: copy; return (queryformat, {"filename": ..., "download": ..., "writer_options": ...})

    async def resolve_capabilities(self, request: web.Request, slug: str, tenant: str | None) -> tuple[frozenset[str], bool]:
        """Resolve (capabilities, residual_scan) of the provider that will execute ``slug`` via a lazy probe QS."""
        probe = QS(slug=slug, conditions={}, request=request, tenant=tenant,
                   definition=request.get("qs_definition"), lazy=True)
        try:
            await probe.build_provider()
            return probe._qs.capabilities, probe._qs.residual_scan  # pylint: disable=protected-access
        finally:
            # FILL IN: await probe.close() suppressing exceptions; map SlugNotFound/TenantError as in step 6
            pass
```
**Why this shape**: helpers keep `query` under the blueprint size cap and make the two verbatim copies (`_enforce_slug_execute`, `_output_args`) testable. `SlugNotFound`/`TenantError`/`ParserError` imports are used by the FILL IN mapping.

### `querysource/handlers/__init__.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -cF 'from .service import QueryService' querysource/handlers/__init__.py)
# BEFORE — insert above `from .service import QueryService` (verified: querysource/handlers/__init__.py:12), keeping alphabetical order
from .qsurl import QSUrlService

# occurrences: 1 (verified: grep -cF "    'QueryService'," querysource/handlers/__init__.py)
# BEFORE — insert above `    'QueryService',` (verified: querysource/handlers/__init__.py:22)
    'QSUrlService',
```

### `querysource/services.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -cF "        r = self.app.router.add_head('/api/v2/services/queries/{slug}', qs.get_columns)" querysource/services.py)
# AFTER the `routes.append(r)` that follows `        r = self.app.router.add_head('/api/v2/services/queries/{slug}', qs.get_columns)` (verified: querysource/services.py:189-190)
        ## qsurl (FEAT-152): HTSQL-style URL dialect, whole query in the path or <slug>?q=<rest>
        qsurl = QSUrlService()
        r = self.app.router.add_get('/api/v1/services/qsurl/{path:.*}', qsurl.query, allow_head=False)
        routes.append(r)
# + add `QSUrlService,` to the `from .handlers import (...)` block at querysource/services.py:22-30
#   (occurrences of `from .handlers import`: 2 — lines 22 and 373; edit ONLY the module-level one at 22)
```

### `tests/handlers/test_qsurl_service.py` (CREATE)
```python
"""QSUrlService: 400 detail, PBAC ordering, ?q= form, decode-once (spec AC5-AC8)."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from querysource.handlers.qsurl import QSUrlService


def _handler() -> QSUrlService:
    h = QSUrlService.__new__(QSUrlService)
    h.logger = MagicMock(); h._loop = None; h.debug = False
    h._json = MagicMock(); h._json.dumps = json.dumps
    return h

async def test_parse_error_is_400_with_detail(): ...           # FILL IN: path "stores:order(x)" → HTTPBadRequest, body detail.kind == "parse", pointer present
async def test_pbac_deny_before_capability_probe(): ...        # FILL IN: _enforce_pbac raises HTTPNotFound → resolve_capabilities/get_source not awaited
async def test_q_form_joins_slug_and_rest(): ...               # FILL IN: path "stores", q "{a}?b=1" → parse receives "stores{a}?b=1"
async def test_q_with_nonbare_path_is_400(): ...               # FILL IN
async def test_q_not_in_conditions(): ...                      # FILL IN: capture get_source conditions; "q" absent
async def test_unsupported_capability_is_400(): ...            # FILL IN: "s?lower(a)=1" → detail.kind "unsupported"
async def test_decodes_once_via_real_router(monkeypatch): ...  # FILL IN: TestServer app with the route; patch parse to capture source;
                                                                #   GET /api/v1/services/qsurl/s%3Fa%3D'x%2527' → source == "s?a='x%27'"
```

### FILL IN checklist
- [ ] `query` execution block and outer exception mapping.
- [ ] `_enforce_slug_execute`, `_output_args` (verbatim copies).
- [ ] `resolve_capabilities` cleanup and error mapping.
- [ ] Test bodies.

---

## Acceptance Criteria

- [ ] Path form and `?q=` form reach `parse()` with the same source; `q` never reaches conditions; no double decode (AC5).
- [ ] PBAC `slug:execute` runs before the capability probe and `get_source`; datasource/driver PBAC after `build_provider()` (AC6).
- [ ] Every `QSUrlError` → 400 with `detail == err.to_dict()` in production mode (AC7); `unsupported` for functions/navigation (AC8).
- [ ] Legacy route untouched: `pytest tests/handlers/test_queryservice_pbac_smoke.py -q` green (AC18).
- [ ] `ruff check querysource/handlers/qsurl.py querysource/handlers/__init__.py querysource/services.py` clean.

---

## Validation Commands

- `pytest tests/handlers/test_qsurl_service.py -q`
- `pytest tests/handlers/test_queryservice_pbac_smoke.py -q`

---

## Test Specification

See the `tests/handlers/test_qsurl_service.py` block above.

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at the path listed above for full context.
2. **Check dependencies** — verify every `Depends-on` task is `done` in `sdd/tasks/index/qsurl-parser.json`.
3. **Verify the Codebase Contract** before writing any code: re-run the `grep -c` for every MODIFY anchor; if a count changed, re-locate the anchor; if it is `0`, stop and report the drift.
4. **Update status** in `sdd/tasks/index/qsurl-parser.json` → `"in-progress"`.
5. **Implement** from the Implementation Blueprint; complete every `# FILL IN:`; never change a signature or path the blueprint fixes.
6. **Verify** every Acceptance Criterion and run the Validation Commands (`source .venv/bin/activate` first).
7. **Move this file** to `sdd/tasks/completed/TASK-775-qsurl-http-handler.md` and set the index entry to `"done"`.
8. **Fill in the Completion Note** below.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
