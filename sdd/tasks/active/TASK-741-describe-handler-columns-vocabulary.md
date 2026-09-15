# TASK-741: Describe handler — columns and vocabulary endpoints

**Feature**: FEAT-148 — Describe Query-Slug REST Endpoints
**Spec**: `sdd/specs/describe-queryslug.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-738, TASK-739, TASK-740
**Assigned-to**: unassigned

---

## Context

This task implements spec §3 **Module 7**, second half. It adds two methods to `QueryDescribe` (created in TASK-740) and registers their routes:
- **`columns`** → `GET /api/v1/queries/{slug}/columns`: typed output columns. It is the **only** describe route that touches the slug's datasource, and even then it only *prepares*, never executes.
- **`vocabulary`** → `GET /api/v1/queries/vocabulary`: the relative-date keyword vocabulary.

**Visibility for `/columns`** is identical to detail: `_load_visible` → `slug:describe OR slug:execute`, with 404 on any failure.

**Degradation.** Datasource failures degrade to `columns_source: "declared"` or `"unavailable"` with HTTP 200, never a 5xx.

---

## Scope

- Add `ColumnInfo` and `ColumnsResponse` models, and the methods `QueryDescribe.columns` and `QueryDescribe.vocabulary`, to `querysource/handlers/describe.py`.
- Register `GET /api/v1/queries/vocabulary` and `GET /api/v1/queries/{slug}/columns` in `services.py`.
- Write `tests/handlers/test_describe_columns.py` and `tests/handlers/test_describe_vocabulary.py`, and extend the route registration test.

**NOT in scope**:
- `describe_columns` provider code (TASK-738).
- The registry (TASK-739).
- Tenant routes (TASK-743).
- Fixing `handlers/service.py:408`.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/describe.py` | MODIFY | models + `columns` + `vocabulary` |
| `querysource/services.py` | MODIFY | 2 routes |
| `tests/handlers/test_describe_columns.py` | CREATE | columns tests |
| `tests/handlers/test_describe_vocabulary.py` | CREATE | vocabulary tests |
| `tests/test_route_registration.py` | MODIFY | assert 2 routes |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
import asyncio
from typing import Literal, Optional
from pydantic import BaseModel
from asyncdb.exceptions import ProviderError, DriverError            # verified: handlers/service.py:11-14 / interfaces/connections.py:13-17
from querysource.exceptions import ParserError, SlugNotFound          # verified: querysource/exceptions.py:70, :34
from querysource.queries.qs import QS                                 # verified: querysource/queries/qs.py:36
from querysource.conf import QS_DESCRIBE_COLUMNS_TIMEOUT              # added by TASK-735
from querysource.queries.describe import RESERVED_PLACEHOLDERS, extract_placeholders   # added by TASK-736
from querysource.utils.vocabulary import build_vocabulary             # added by TASK-739
from querysource.types.validators import udf_keywords, pg_constants, pg_udfs   # added by TASK-734
```

### Existing Signatures to Use
```python
# querysource/queries/qs.py
class QS(BaseQuery):                                                  # :36
    def __init__(self, slug: str = '', conditions: dict = None, request: web.Request = None, loop=None, **kwargs)  # :42-49
    def get_source(self): return self._qs                             # :123 → BaseProvider
    async def build_provider(self)                                    # :135 — loads slug (SlugNotFound), creates provider,
                                                                      #   await self._qs.prepare_connection() renders self._query
    async def close(self)                                             # :519 — disposes conn; swallows provider close errors

# querysource/providers/abstract.py
    def get_definition(self) -> Union[QueryModel, dict]               # :128
    def get_query(self)                                               # :235 — rendered query string
    async def describe_columns(self) -> list[dict]                    # added by TASK-738 (pg typed override)

# querysource/handlers/service.py:359-379 — condition merge to mirror:
#   params = self.query_parameters(request); options = await self.json_data(request) (TypeError/ValueError → {})
#   conditions = {**options, **params}
# navigator BaseHandler: async def json_data(self, request=None) → get_json → None on invalid JSON  (base.py:312-343)

# querysource/handlers/describe.py (from TASK-740)
class QueryDescribe(AbstractHandler):
    async def _principal(self, request) -> Principal            # 401 on NONE
    async def _store(self, request) -> DescribeStore
    async def _load_visible(self, request, principal, store, slug)   # 404 on any visibility failure
VOCABULARY_LINK = "/api/v1/queries/vocabulary"

# querysource/services.py — describe route block added by TASK-740:
#   r = self.app.router.add_get('/api/v1/queries/{slug}/describe', dh.describe)
#   routes.append(r)
```

### Does NOT Exist
- ~~`QS.get_definition()`~~: use `qs.get_source().get_definition()`. **Do not copy** `handlers/service.py:408`, which calls it on `QS` (a latent bug).
- ~~`QS.describe_columns()`~~: call it on the provider, `qs.get_source().describe_columns()`.
- ~~Row sampling / `querylimit=1` fallback~~: out of scope.
- ~~`ColumnInfo` / `ColumnsResponse`~~: added here.

---

## Implementation Notes

### Key Constraints (spec §2 Columns/Vocabulary, AC15/AC17)
- **`columns(request)` flow:**
  1. `principal = await self._principal(request)`; `store = await self._store(request)`; `slug = request.match_info.get("slug", "")`.
  2. `model = await self._load_visible(request, principal, store, slug)`, so visibility equals detail.
  3. Merge conditions exactly like `get_columns`: `options = await self.json_data(request)` inside `try/except (TypeError, ValueError)` → `{}`; `None` → `{}`. `params = self.query_parameters(request)`. `conditions = {**options, **params}`.
  4. `warnings: list[str] = []`, `columns: list[dict] = []`, `source = "unavailable"`.
  5. `qs = QS(slug=slug, conditions=conditions, request=request)`, then a `try:` that runs `await qs.build_provider()`.
     - `SlugNotFound` → `raise web.HTTPNotFound()` (the definition vanished between the checks).
     - `(ParserError, ProviderError, DriverError)` → `warnings.append(f"provider_unavailable: {type(err).__name__}")` and skip to the fallback.
  6. If the provider was built:
     - `provider = qs.get_source()`.
     - Unresolved check: `names, _ = extract_placeholders(str(provider.get_query() or ""))`; `unresolved = [n for n in (names or []) if n not in RESERVED_PLACEHOLDERS]`.
     - If `unresolved`: `warnings.append(f"prepare_skipped: unresolved placeholders {sorted(unresolved)}")`.
     - Else: `columns = await asyncio.wait_for(provider.describe_columns(), QS_DESCRIBE_COLUMNS_TIMEOUT)`.
       - Non-empty → `source = "prepare"`.
       - `(ParserError, ProviderError, DriverError, asyncio.TimeoutError)` → `warnings.append(f"prepare_failed: {type(err).__name__}")`.
  7. Fallback when `source != "prepare"`:
     - `definition = provider.get_definition()` if the provider was built, else `model`.
     - `attrs = (definition.get("attributes") if isinstance(definition, dict) else getattr(definition, "attributes", None)) or {}`.
     - `declared = attrs.get("columns") or []`.
     - If `declared`: `columns = [{"name": str(c), "type": None} for c in declared]`, `source = "declared"`.
  8. `finally:` `await qs.close()` inside a `try/except Exception: pass`.
  9. `return self.json_response(ColumnsResponse(slug=slug, columns=columns, columns_source=source, warnings=warnings).model_dump())`.
  - **Never** put `provider.get_query()`, `query_raw` or SQL text into `warnings`, logs above debug level, or the response (AC15).
  - Exception class names in warnings are allowed; messages are not, because they can embed SQL.
- **`vocabulary(request)`:** `await self._principal(request)` (401 on `NONE`; sessionless authz is allowed, per spec §8 default), then `return self.json_response(build_vocabulary(udf_keywords(), pg_constants(), pg_udfs()))`.

---

## Implementation Blueprint

### Steps (in order)
1. Add the imports and models at the top of `describe.py` — *why*: the response contract comes first.
2. Append the `columns` and `vocabulary` methods to `QueryDescribe` — *why*: the two endpoints of this task.
3. Register the routes after the describe-detail route — *why*: all `/api/v1/queries/*` GET routes stay together.
4. Write the tests and run `pytest tests/handlers tests/test_route_registration.py -q` — *why*: AC15–AC17.

### `querysource/handlers/describe.py` (MODIFY — imports + models)
```python
# occurrences: 1 (verified after TASK-740: grep -c "^VOCABULARY_LINK = " querysource/handlers/describe.py)
# AFTER — add to the import block (top of file):
import asyncio
from typing import Literal, Optional

from asyncdb.exceptions import DriverError, ProviderError
from pydantic import BaseModel

from ..conf import QS_DESCRIBE_COLUMNS_TIMEOUT
from ..exceptions import ParserError
from ..queries.describe import RESERVED_PLACEHOLDERS, extract_placeholders
from ..queries.qs import QS
from ..types.validators import pg_constants, pg_udfs, udf_keywords
from ..utils.vocabulary import build_vocabulary

# AFTER — insert below `VOCABULARY_LINK = "/api/v1/queries/vocabulary"`:


class ColumnInfo(BaseModel):
    """One output column; ``type`` is None when not introspectable."""

    name: str
    type: Optional[str] = None


class ColumnsResponse(BaseModel):
    """Response of GET .../queries/{slug}/columns."""

    slug: str
    columns: list[ColumnInfo]
    columns_source: Literal["prepare", "declared", "unavailable"]
    warnings: list[str] = []
```
**Why**: merge these into TASK-740's existing import block; do not duplicate the `aiohttp`/`SlugNotFound` imports. The model names come from spec §2 Data Models.

### `querysource/handlers/describe.py` (MODIFY — methods, append at end of `QueryDescribe`)
```python
    async def columns(self, request: web.Request) -> web.Response:
        """GET .../queries/{slug}/columns — 200 | 401 | 404 (prepare, never execute)."""
        principal = await self._principal(request)
        store = await self._store(request)
        slug = request.match_info.get("slug", "")
        model = await self._load_visible(request, principal, store, slug)
        try:
            options = await self.json_data(request) or {}
        except (TypeError, ValueError):
            options = {}
        conditions = {**options, **self.query_parameters(request)}
        warnings: list[str] = []
        columns: list[dict] = []
        source = "unavailable"
        provider = None
        qs = QS(slug=slug, conditions=conditions, request=request)
        try:
            # FILL IN: build_provider (SlugNotFound → HTTPNotFound; Parser/Provider/DriverError → warning);
            #          unresolved-placeholder check; wait_for(describe_columns, QS_DESCRIBE_COLUMNS_TIMEOUT);
            #          declared fallback from provider definition or `model` — bounded by AC15 (no SQL in output)
            pass
        finally:
            try:
                await qs.close()
            except Exception:  # pylint: disable=broad-except
                pass
        return self.json_response(
            ColumnsResponse(slug=slug, columns=columns, columns_source=source, warnings=warnings).model_dump()
        )

    async def vocabulary(self, request: web.Request) -> web.Response:
        """GET /api/v1/queries/vocabulary — 200 | 401."""
        await self._principal(request)
        return self.json_response(build_vocabulary(udf_keywords(), pg_constants(), pg_udfs()))
```

### `querysource/services.py` (MODIFY)
```python
# occurrences: 1 (verified after TASK-740: grep -c "add_get('/api/v1/queries/{slug}/describe', dh.describe)" querysource/services.py)
# AFTER — insert below that line's following `        routes.append(r)`:
        r = self.app.router.add_get('/api/v1/queries/vocabulary', dh.vocabulary)
        routes.append(r)
        r = self.app.router.add_get('/api/v1/queries/{slug}/columns', dh.columns)
        routes.append(r)
```

### Tests (CREATE)
```python
# tests/handlers/test_describe_columns.py
"""FEAT-148 TASK-741 — GET /api/v1/queries/{slug}/columns."""
# FILL IN (patch querysource.handlers.describe.QS with a fake exposing build_provider/get_source/close;
#   patch QueryDescribe._load_visible to return a dict definition; principal via resolve_principal patch):
#   test_columns_prepare_typed, test_columns_unresolved_placeholders_declared_fallback (describe_columns NOT awaited),
#   test_columns_datasource_down_unavailable (DriverError, no declared → 200 unavailable),
#   test_columns_timeout_declared (asyncio.TimeoutError), test_columns_slug_vanished_404,
#   test_columns_never_returns_sql (body has no 'SELECT' / query text), test_columns_conditions_merge_query_over_body,
#   test_columns_401_without_principal, test_columns_close_always_called

# tests/handlers/test_describe_vocabulary.py
"""FEAT-148 TASK-741 — GET /api/v1/queries/vocabulary."""
#   test_vocabulary_requires_principal (401), test_vocabulary_session_ok, test_vocabulary_authz_ok,
#   test_vocabulary_keywords_match_effective_udf_list

# tests/test_route_registration.py — extend TestDescribeRoutes: ("GET","/api/v1/queries/vocabulary"),
#   ("GET","/api/v1/queries/{slug}/columns"); "/api/v1/queries/vocabulary/describe" resolves to describe (slug='vocabulary')
```

### FILL IN checklist
- [ ] `columns` try-block body: bounded by AC15.
- [ ] Test bodies, including the fake `QS` and provider.

---

## Acceptance Criteria

- [ ] `pytest tests/handlers tests/test_route_registration.py -q` passes.
- [ ] AC15: `prepare` typed on pg; `declared` on prepare failure, timeout or unresolved placeholders; `unavailable` otherwise with 200; query-string conditions override body; no SQL in any response.
- [ ] AC16: `columns()` providers untouched (TASK-738); `HEAD /api/v2/services/queries/{slug}` handler untouched.
- [ ] AC17: `/vocabulary` returns `build_vocabulary(udf_keywords(), pg_constants(), pg_udfs())`, with 401 without a principal.
- [ ] `qs.close()` is always awaited.
- [ ] `ruff check querysource/handlers/describe.py querysource/services.py tests/handlers tests/test_route_registration.py` is clean.

---

## Test Specification

See the blueprint test outlines above.

---

## Agent Instructions

1. **Read the spec** (§2 Columns/Vocabulary, §3 Module 7, AC15–AC17, §7 columns risks).
2. **Check dependencies**: TASK-738, TASK-739 and TASK-740 completed.
3. **Verify the Codebase Contract**: confirm TASK-740's `describe.py` structure and route block.
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
