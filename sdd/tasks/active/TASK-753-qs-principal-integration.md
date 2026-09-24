# TASK-753: QS accepts principal= and gates build_provider()

**Feature**: FEAT-150 — PBAC for Request-less (Programmatic) QS Callers
**Spec**: `sdd/specs/pbac-request-credentials.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-748, TASK-750
**Assigned-to**: unassigned

---

## Context

Implements spec §3 **Module 5**, the change that actually closes the bypass. `QS` gains
a keyword-only `principal=`, stored on `AbstractQuery`, and `build_provider()` checks it.
Slug queries check `slug:execute` **before** store resolution. Every other query type checks
`raw_query:execute` before `get_provider()`. With a principal, "not found" and "denied"
become the same `QueryAccessDenied` (resolved at spec time). Because `query()` calls
`build_provider()` before the cache lookup (`qs.py:384` vs `:395`), cached results are
gated too.

---

## Scope

- `AbstractQuery.__init__` (`interfaces/queries.py:48`): add keyword-only `principal=None`, store it as `self._principal`, and raise `ValueError` when both `request` and `principal` are given.
- `BaseQuery.__init__` (`queries/base.py:21`): accept and forward `principal=`.
- `QS.__init__` (`queries/qs.py:42`): accept and forward `principal=`.
- `QS.build_provider()`: add the gates and the not-found collapse.
- Write `tests/test_qs_principal.py`. It sits at the top level, following the existing flat QS test layout; the spec's `tests/queries/` directory does not exist.

**NOT in scope**: MultiQS (TASK-754); handlers; per-user credentials. `get_provider` keeps receiving `session=None, app=None` on the principal path.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/interfaces/queries.py` | MODIFY | `principal` kwarg + `_principal` + `ValueError` |
| `querysource/queries/base.py` | MODIFY | forward `principal` |
| `querysource/queries/qs.py` | MODIFY | kwarg + gates + collapse |
| `tests/test_qs_principal.py` | CREATE | unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.exceptions import QueryAccessDenied               # created by TASK-748
from querysource.tenant_errors import TenantError                  # verified: querysource/tenant_errors.py:15
from querysource.auth.principal import QSPrincipal                 # created by TASK-748
from querysource.auth.enforcement import enforce_principal         # created by TASK-750 — import LAZILY inside build_provider
from querysource.auth._resource_types import ResourceType          # verified: querysource/auth/_resource_types.py — lazily
```

### Existing Signatures to Use
```python
# querysource/interfaces/queries.py:48-56, :91, :104
def __init__(self, slug: str = None, conditions: dict = None, request: web.Request = None,
             loop: asyncio.AbstractEventLoop | None = None, *, tenant: str | None = None, **kwargs):
    ...
    self._request = request            # :91
    ...
    self._tenant_selector = tenant     # :104

# querysource/queries/base.py:21-40
def __init__(self, slug=None, conditions=None, request=None, loop=None, *, tenant: str | None = None, **kwargs):
    super().__init__(slug=slug, conditions=conditions, request=request, loop=loop, tenant=tenant, **kwargs)  # tenant=tenant at :39

# querysource/queries/qs.py
from ..exceptions import (DataNotFound, EmptySentence, QueryError, QueryException,)   # :23-28
def __init__(self, slug='', conditions=None, request=None, loop=None, *, tenant: str | None = None, **kwargs)  # :42-57
    # super().__init__(slug, conditions=..., request=..., loop=..., tenant=tenant, **kwargs)
    # self._type: 'slug' | 'query' | 'raw' | 'driver'   (:73, :78, :84, :90)
async def build_provider(self):   # :137
    # :167  if self._type == 'slug':  # query-based provider:
    # :168  self._logger.debug(f':: QS Slug: {self._query!s}')
    # :173  from querysource.tenants import QueryIdentity
    # :174  repo = await self.get_definition_repository()
    # :175  store = repo.registry.resolve(self._tenant_selector)
    # :176  identity = QueryIdentity(store=store, slug=self._query)
    # :177  loaded_def = await repo.get(identity)
    # :188  get_provider(objquery, session=_pbac_session, app=_pbac_app)
    # :248 elif 'query' ; :291 elif 'raw' ; :296 elif 'driver'

# querysource/tenant_errors.py:15
class TenantError(QueryException):
    def __init__(self, message: str, *, error_code: str) -> None: ...   # .error_code
# error codes: query_not_found (DefinitionRepository.get, repositories/definitions.py:166),
#              tenant_not_available (TenantRegistry.resolve, tenants.py:422), tenant_store_unavailable (NOT collapsed)

# querysource/auth/enforcement.py (TASK-750)
async def enforce_principal(principal, resource_type, resource_name: str, action: str, *,
                            tenant: str | None = None, logger=None) -> AccessDecision   # raises QueryAccessDenied
```

Reference test pattern: `tests/tenants/test_tenant_execution_context.py:133-190`. It builds a real `QS`,
then replaces `qs.get_definition_repository` and `qs.connection.get_provider` with fakes.

### Does NOT Exist
- ~~`AbstractQuery._principal`~~, ~~`QS(principal=...)`~~: created here.
- ~~`tests/queries/`~~: the directory does not exist. Use `tests/test_qs_principal.py`.
- Do not pass the principal to ~~`get_provider(session=...)`~~. Credentials stay trusted-service (spec Goal).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/interfaces/queries.py", "action": "MODIFY"},
    {"path": "querysource/queries/base.py", "action": "MODIFY"},
    {"path": "querysource/queries/qs.py", "action": "MODIFY"},
    {"path": "tests/test_qs_principal.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/interfaces/queries.py#AbstractQuery",
    "sym:querysource/queries/base.py#BaseQuery",
    "sym:querysource/queries/qs.py#QS",
    "sym:querysource/queries/qs.py#QS.build_provider",
    "sym:querysource/tenant_errors.py#TenantError"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Import `enforce_principal` / `ResourceType` **inside** the `if self._principal is not None:` blocks. The no-principal path must not import the enforcement module at all (spec AC: principal=None never calls the core).
- Collapse only `error_code in {"query_not_found", "tenant_not_available"}`, and only when a principal is set. Everything else re-raises unchanged.
- Use a `TYPE_CHECKING` import for the `QSPrincipal` annotation in `interfaces/queries.py` and `qs.py`, to avoid import cycles.
- Pass `tenant=self._tenant_selector` and `logger=self._logger` to `enforce_principal`. The tenant is used for logs only.

---

## Implementation Blueprint

### Steps (in order)
1. Add `principal` to `AbstractQuery.__init__` with the `ValueError` rule — *why*: the base stores it once for both QS and MultiQS (TASK-754 relies on `self._principal`).
2. Forward it through `BaseQuery` and `QS` — *why*: they re-declare the signature explicitly.
3. Insert the non-slug gate above the `if self._type == 'slug':` line, and the slug gate plus collapse inside the slug branch — *why*: spec §2 item 5 (check before any store or DB access).
4. Write the tests.

### `querysource/interfaces/queries.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '            tenant: str | None = None,' querysource/interfaces/queries.py)
# AFTER — insert below `            tenant: str | None = None,` in AbstractQuery.__init__ (verified: interfaces/queries.py:55)
            principal: "QSPrincipal | None" = None,

# occurrences: 1 (verified: grep -c '        self._tenant_selector = tenant' querysource/interfaces/queries.py)
# AFTER — insert below `        self._tenant_selector = tenant` (verified: interfaces/queries.py:104)
        # FEAT-150: identity for programmatic (request-less) PBAC enforcement.
        if request is not None and principal is not None:
            raise ValueError(
                "QS Error: pass either request= or principal=, not both (ambiguous identity)."
            )
        self._principal = principal
# FILL IN: add `from typing import TYPE_CHECKING` handling + `if TYPE_CHECKING: from ..auth.principal import QSPrincipal`
#          next to the existing `from typing import Any` (verified: interfaces/queries.py:14)
```

### `querysource/queries/base.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '            tenant=tenant,' querysource/queries/base.py)
# Add `principal: "QSPrincipal | None" = None,` after `tenant: str | None = None,` in the signature,
# and AFTER `            tenant=tenant,` (verified: queries/base.py:39) in the super().__init__ call:
            principal=principal,
# FILL IN: TYPE_CHECKING import of QSPrincipal, as in interfaces/queries.py
```

### `querysource/queries/qs.py` (MODIFY) — signature
```python
# occurrences: 1 (verified: grep -c '            tenant: str | None = None,' querysource/queries/qs.py)
# AFTER `            tenant: str | None = None,` (verified: qs.py:49) add:
            principal: "QSPrincipal | None" = None,
# and pass `principal=principal,` in the super().__init__(...) call right after `tenant=tenant,` (qs.py:57)
```

### `querysource/queries/qs.py` (MODIFY) — gates in build_provider
```python
# occurrences: 1 (verified: grep -c "        if self._type == 'slug':  # query-based provider:" querysource/queries/qs.py)
# BEFORE — insert above `        if self._type == 'slug':  # query-based provider:` (verified: qs.py:167)
        if self._principal is not None and self._type != 'slug':
            from ..auth._resource_types import ResourceType
            from ..auth.enforcement import enforce_principal
            await enforce_principal(
                self._principal, ResourceType.RAW_QUERY, "raw_query", "raw_query:execute",
                tenant=self._tenant_selector, logger=self._logger,
            )

# occurrences: 1 (verified: grep -c '            store = repo.registry.resolve(self._tenant_selector)' querysource/queries/qs.py)
# Inside the slug branch: BEFORE `            repo = await self.get_definition_repository()` (qs.py:174) insert
#   the slug gate (enforce_principal(..., ResourceType.SLUG, self._query, "slug:execute", ...)),
# then REPLACE lines :175-177 (resolve / identity / repo.get) with:
            try:
                store = repo.registry.resolve(self._tenant_selector)
                identity = QueryIdentity(store=store, slug=self._query)
                loaded_def = await repo.get(identity)
            except TenantError as ex:
                if self._principal is not None and ex.error_code in _COLLAPSED_OWNER_ERRORS:
                    raise QueryAccessDenied() from ex
                raise
# FILL IN: add module constant `_COLLAPSED_OWNER_ERRORS = frozenset({"query_not_found", "tenant_not_available"})`
#          and imports `QueryAccessDenied` (into the :23-28 exceptions tuple) + `from ..tenant_errors import TenantError`
#          — bounded by AC-4/AC-5
```
**Why**: The slug gate runs before `get_definition_repository()`, so a denied principal never opens the definition store (AC-3). The non-slug gate covers `query`, `raw` and `driver` in one place, the same action `handlers/executor.py` enforces.

### FILL IN checklist
- [ ] `TYPE_CHECKING` imports in `interfaces/queries.py`, `base.py`, `qs.py`
- [ ] `_COLLAPSED_OWNER_ERRORS` constant + `QueryAccessDenied`/`TenantError` imports in `qs.py`
- [ ] Slug gate call placed before `repo = await self.get_definition_repository()`
- [ ] Test bodies (Test Specification)

---

## Acceptance Criteria

- [ ] AC-1: `QS(slug="x", request=<req>, principal=<p>)` raises `ValueError`. `QS(slug="x")` still works, and `qs._principal is None`.
- [ ] AC-2: with `principal=None`, `enforce_principal` is never imported or called (patch `querysource.auth.enforcement.enforce_principal` and assert it was not awaited).
- [ ] AC-3: a denied principal raises `QueryAccessDenied` from `build_provider()`/`query()`, and `get_definition_repository`, `registry.resolve`, `repo.get`, `connection.get_provider` and the cache are **not** called.
- [ ] AC-4: with a principal, `TenantError(error_code="query_not_found")` and `TenantError(error_code="tenant_not_available")` become `QueryAccessDenied`.
- [ ] AC-5: `TenantError(error_code="tenant_store_unavailable")` propagates unchanged, and without a principal `query_not_found` propagates unchanged.
- [ ] AC-6: `QS(raw_query=..., principal=p)`, `QS(query=..., driver=..., principal=p)` and `QS(driver=..., principal=p)` check `raw_query:execute` on `"raw_query"`.
- [ ] AC-7: `principal.tenant_id="a"` with `tenant="b"` → `registry.resolve` is called with `"b"`.
- [ ] AC-8: an allowed principal reaches `get_provider(objquery, session=None, app=None)`.
- [ ] AC-9: `pytest tests/tenants/test_tenant_execution_context.py -q` passes unmodified.
- [ ] AC-10: `ruff check` is clean on the three modified modules and the new test.

---

## Validation Commands

- `pytest tests/test_qs_principal.py -q`
- `pytest tests/tenants/test_tenant_execution_context.py -q`

---

## Test Specification

```python
# tests/test_qs_principal.py
import pytest
from unittest.mock import AsyncMock, MagicMock

from querysource.auth.principal import QSPrincipal
from querysource.exceptions import QueryAccessDenied
from querysource.queries.qs import QS
from querysource.tenant_errors import TenantError


@pytest.fixture
def principal():
    return QSPrincipal(user_id="35", username="jdoe", groups=("sales",), tenant_id="client_a")


@pytest.fixture
def deny(monkeypatch):
    """Patch enforce_principal to raise QueryAccessDenied."""
    ...


@pytest.fixture
def allow(monkeypatch):
    """Patch enforce_principal to return an allowed decision and record calls."""
    ...


def test_request_and_principal_is_value_error(principal): ...          # AC-1
async def test_no_principal_never_calls_core(monkeypatch): ...         # AC-2
async def test_denied_before_store_resolution(principal, deny): ...    # AC-3
@pytest.mark.parametrize("code", ["query_not_found", "tenant_not_available"])
async def test_not_found_collapses(principal, allow, code): ...        # AC-4
async def test_store_unavailable_not_collapsed(principal, allow): ...  # AC-5
@pytest.mark.parametrize("kwargs", [{"raw_query": "SELECT 1"}, {"query": "SELECT 1", "driver": "pg"}, {"driver": "pg"}])
async def test_non_slug_checks_raw_query(principal, allow, kwargs): ...  # AC-6
async def test_tenant_selector_not_principal_tenant(principal, allow): ...  # AC-7
async def test_allowed_uses_trusted_credentials(principal, allow): ...      # AC-8
```

---

## Agent Instructions

1. Confirm TASK-748 and TASK-750 are completed.
2. Re-run every `grep -c` in the blueprint. Stop and report any count that differs.
3. Implement, then run the Validation Commands and `ruff check`.
4. Move this file to `sdd/tasks/completed/`, set the index status to `done`, and fill in the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
