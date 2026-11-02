# TASK-904: semanticProvider

**Feature**: FEAT-175 — Querysource Semantic Layer (Ossie models compiled by Sidemantic)
**Spec**: `sdd/specs/semantic-layer.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-899, TASK-900, TASK-901, TASK-902, TASK-903
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 6 and §2 contracts S1/S3/S4/S7. The `BaseProvider` subclass loaded by
`Connection.load_provider('semantic')` (TASK-899 branch). It parses the resource and request,
resolves the datasource, enforces datasource/driver and semantic PBAC, collects governance row
filters, compiles via the `SemanticCompiler`, and runs the literal SQL like `defaultProvider.query()`.

---

## Scope

- Implement `querysource/providers/semantic.py::semanticProvider` per spec §3 M6.
- Tests `tests/providers/__init__.py`, `tests/providers/test_semantic_provider.py` (spec §4 M6 rows).

**NOT in scope**: Python API (TASK-905), catalog endpoint (TASK-906), MultiQS principal propagation (TASK-907).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/providers/semantic.py` | CREATE | provider |
| `tests/providers/__init__.py` | CREATE | package marker |
| `tests/providers/test_semantic_provider.py` | CREATE | provider tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.providers.abstract import BaseProvider     # verified: abstract.py:35
from querysource.models import QueryModel                   # verified: models.py:49
from querysource.connections import QueryConnection         # verified: tests/e2e/conftest.py:21
from querysource.exceptions import QueryException, QueryAccessDenied   # verified: exceptions.py:6,63
from querysource.utils.functions import get_hash            # verified: abstract.py:22 (`from ..utils.functions import get_hash`)
from asyncdb.exceptions import ProviderError, DriverError, NoDataFound # verified: providers/default.py:14
from querysource.semantic.models import (CompilationTarget, CompiledQuery, SemanticQuery, SemanticResource)  # TASK-897
from querysource.semantic.exceptions import SemanticError, SemanticResourceError, SemanticGovernanceError    # TASK-897
from querysource.semantic.dialects import dialect_for_driver                  # TASK-900
from querysource.semantic.filters import FilterValidator                      # TASK-900
from querysource.semantic.loader import get_loader                            # TASK-901
from querysource.semantic import governance                                   # TASK-902
from querysource.semantic.compilers import get_compiler                       # TASK-903
from querysource.conf import QS_SEMANTIC_DEFAULT_LIMIT, QS_SEMANTIC_MAX_LIMIT, QS_SEMANTIC_MAX_FILTERS  # TASK-899
```

### Existing Signatures to Use
```python
# querysource/providers/abstract.py
class BaseProvider(SourceHooksMixin, ABC):                                   # :35
    def __init__(self, slug='', query=None, qstype='', connection=None, definition=None,
                 conditions=None, request=None, **kwargs)                    # :55 — sets self._definition, _conditions
    # (deep copy, 'refresh' popped), _connection, _slug, _query = definition.query_raw, _request, _loop (pops kwargs['loop']),
    # parser only `if self.__parser__`, self._driver (pops kwargs['driver']), self.kwargs = remaining kwargs (→ 'principal')
    async def prepare_connection(self)          # :259
    async def describe_columns(self) -> list[dict]   # :273
    async def dry_run(self)                     # :298 → (self._query, None)
    def checksum(self)                          # :330 → get_hash(self._query)
    self._logger = logging.getLogger(f'QS.{name}')

# querysource/providers/default.py:117-148 — query() pattern
async with await self._connection.connection() as conn:
    result, error = await conn.query(self._query)
# error → [result, error]; result → [result, error]; empty → raise self.NotFound('DB: Empty Result')
# NoDataFound → self.NotFound ; ProviderError/DriverError → QueryException ; other → self.Error(..., code=406)

# querysource/interfaces/connections.py
async def datasource(self, name='default', session=None, app=None)   # :396 → (source, AsyncDB conn); QueryError 404
def default_connection(self, driver=..., dsn=...)                    # used by 'db' branch :279-281
# FILL IN: verify how to obtain the default pg (source-driver name, conn) on QueryConnection — read connections.py:264-300
# and the `_default_driver` / `_dsn` attributes before writing _resolve_datasource

# querysource/tenants.py:54-59 — LoadedDefinition.revision; QS keeps it as self._definition_revision (FILL IN: verify name in qs.py)
```

### Does NOT Exist
- ~~`QueryModel.kind`~~ — read `kind` from the YAML.
- ~~Parameter binding in asyncdb `conn.query`~~ — SQL executed verbatim (S4).
- ~~`request['principal']`~~ — library principal arrives as `kwargs['principal']` (TASK-899); request path uses `request['user_session']` (FILL IN: verify via `querysource.auth.request_gate.get_request_session`, request_gate.py:27).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/providers/semantic.py", "action": "CREATE"},
    {"path": "tests/providers/__init__.py", "action": "CREATE"},
    {"path": "tests/providers/test_semantic_provider.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/providers/abstract.py#BaseProvider",
    "sym:querysource/providers/default.py#defaultProvider.query",
    "sym:querysource/interfaces/connections.py#Connection.datasource"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- `prepare_connection()` order is fixed (spec M6): principal → datasource → `enforce_datasource` → dialect → model → `enforce_query` → governance filters → compile. Datasource PBAC happens **before** the model is loaded (AC-21).
- Governance filters: validate with the same `FilterValidator`; an invalid governance filter → `SemanticGovernanceError` (500) logged with slug and role (AC-12). Append them to the request's filters (AND).
- `revision`: use `LoadedDefinition.revision` if reachable (kwargs/definition), else a stable fallback `get_hash(self._query)`; record which in the Completion Note.
- `checksum()` = `get_hash(f"{sql}|{model_version}|{dialect}|{connection}")` (AC-23).
- `dry_run()` returns `(self.compiled.sql, None)` (S7).
- Wrap any non-`SemanticError`/`QueryAccessDenied` exception from steps into `SemanticResourceError` only when it is a configuration problem; never raise bare `Exception`.
- Compiler call is CPU-bound sync; run it via `await self._loop.run_in_executor(None, ...)` only if it is measurably slow — default: call directly (FILL IN decision, note it).

---

## Implementation Blueprint

### Steps (in order)
1. Write `__init__` (parse resource/request, keep principal) — *why*: 400s surface early.
2. Write `prepare_connection` helpers in the fixed order.
3. Write `query/dry_run/checksum/columns/describe_columns/close`.
4. Tests with `semantic_definitions` + `fake_evaluator`.

### `querysource/providers/semantic.py` (CREATE)
```python
"""Semantic provider (FEAT-175): compile a SemanticQuery and run it on the model's datasource."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Union

from aiohttp import web
from asyncdb.exceptions import DriverError, NoDataFound, ProviderError

from ..exceptions import QueryException
from ..models import QueryModel
from ..semantic import governance
from ..semantic.exceptions import SemanticGovernanceError, SemanticRequestError
from ..semantic.models import CompilationTarget, CompiledQuery, SemanticQuery, SemanticResource
from ..utils.functions import get_hash
from .abstract import BaseProvider


class semanticProvider(BaseProvider):
    """Compile a SemanticQuery against the slug's semantic model and run the SQL on its datasource."""

    __parser__ = None

    def __init__(self, slug: str = '', query: Any = None, qstype: str = '', connection: Callable = None,
                 definition: Union[QueryModel, dict] = None, conditions: dict = None,
                 request: web.Request = None, **kwargs):
        self._principal = kwargs.pop('principal', None)
        super().__init__(slug=slug, query=query, qstype=qstype, connection=connection,
                         definition=definition, conditions=conditions, request=request, **kwargs)
        self.resource: SemanticResource = SemanticResource.from_yaml(self._query or '')
        self.request_model: SemanticQuery = SemanticQuery.from_conditions(self._conditions)
        self.compiled: CompiledQuery | None = None
        self._datasource_name: str | None = None

    async def prepare_connection(self) -> None:
        """principal → datasource → datasource PBAC → dialect → model → semantic PBAC → filters → compile."""
        principal = await self._resolve_principal()
        name, driver = await self._resolve_datasource()
        await governance.enforce_datasource(self._request, principal, name, driver, logger=self._logger)
        # FILL IN: dialect_for_driver(driver) (+warning); get_loader().resolve(slug, revision, self.resource);
        # governance.enforce_query(...); gfilters, roles = governance.governance_filters(...);
        # validate gfilters (SemanticGovernanceError on failure, log slug+role); merged request =
        # request_model.model_copy(update={"filters": [*request_model.filters, *gfilters]});
        # target = CompilationTarget(connection=self.resource.connection, driver=driver, dialect=...,
        #   user_attributes=governance.user_attributes(principal), default_limit=..., max_limit=...);
        # self.compiled = get_compiler(self.resource.semantic.compiler).compile(model, merged, target);
        # keep the ORIGINAL request in compiled.parameters["request"] and set ["roles"] = roles;
        # self._query = self.compiled.sql — bounded by AC-1, AC-11, AC-12, AC-21, AC-24
```
```python
    # (continued, same class)
    async def _resolve_principal(self):
        # FILL IN: self._principal or governance.principal_from_session(<session from self._request>)

    async def _resolve_datasource(self) -> tuple[str, str]:
        # FILL IN: resource.connection → QueryConnection().datasource(name) → (name, source.driver), set
        # self._connection = conn; None → default pg connection ('pg' driver, name 'default') — bounded by AC-2

    async def dry_run(self) -> tuple[str, None]:
        return (self.compiled.sql, None)

    async def query(self) -> list:
        """Mirror defaultProvider.query (default.py:117-148); compiled.sql verbatim, no binding."""
        error = None
        try:
            async with await self._connection.connection() as conn:
                result, error = await conn.query(self.compiled.sql)
            if error:
                return [result, error]
            if result:
                self._result = result
                return [self._result, error]
            raise self.NotFound('Semantic: Empty Result')
        except NoDataFound as ex:
            raise self.NotFound(f'Semantic: Empty Result: {ex}') from ex
        except (ProviderError, DriverError) as ex:
            raise QueryException(f"Query Error: {ex}") from ex

    def checksum(self) -> str:
        c = self.compiled
        return get_hash(f"{c.sql}|{c.model_version}|{c.dialect}|{c.connection}")

    async def columns(self) -> list[str]:
        # FILL IN: output aliases of compiled metrics + dimensions (strip 'model.' prefix as Sidemantic aliases them)

    async def describe_columns(self) -> list[dict]:
        # FILL IN: [{'name', 'type'}] using the compiler catalog types

    async def close(self) -> None:
        self._connection = None
```
**Why**: `principal` is popped before `super().__init__` so it never lands in `self.kwargs`. `NotFound`
is inherited (used by `defaultProvider`). FILL IN: verify `self.NotFound`/`self.Error` exist on BaseProvider.

### FILL IN checklist
- [ ] `prepare_connection` body, `_resolve_principal`, `_resolve_datasource`
- [ ] revision source; executor decision
- [ ] `columns` / `describe_columns`
- [ ] Test bodies

---

## Acceptance Criteria

- [ ] `QS(slug='epson.sales', conditions={...}).dry_run()` returns `(sql, None)` with `SUM(`; `qs._qs.compiled` is a `CompiledQuery` (AC-1, AC-6, S7).
- [ ] Unknown metric → 400 listing available metrics; `limit=99999` capped with warning (AC-14, AC-15).
- [ ] Denied `datasource:use` → `QueryAccessDenied` before the loader is called (AC-21).
- [ ] Same SQL, different `model_version` → different `checksum()` (AC-23).
- [ ] Secured model, no principal (MultiQS child shape) → 403 (§4 last row).

---

## Validation Commands
- `pytest tests/providers/test_semantic_provider.py -q`

---

## Test Specification

```python
# tests/providers/test_semantic_provider.py
import pytest
from querysource.queries.qs import QS
from querysource.semantic.models import CompiledQuery

pytestmark = pytest.mark.usefixtures("sidemantic_available")
# FILL IN: the sidemantic_available / semantic_definitions fixtures live in tests/semantic/conftest.py —
# import them here (`from tests.semantic.conftest import *  # noqa`) or move to a shared plugin import.


async def test_provider_dry_run_returns_compiled_sql(semantic_definitions):
    qs = QS(slug="epson.sales", conditions={"metrics": ["revenue"], "dimensions": ["region"]})
    sql, err = await qs.dry_run()
    assert err is None and "SUM(" in sql.upper()


async def test_dry_run_tuple_contract(semantic_definitions):
    # FILL IN: build_provider; isinstance(qs._qs.compiled, CompiledQuery)


async def test_provider_rejects_unknown_metric_400(semantic_definitions): ...  # FILL IN
async def test_provider_limit_capped_with_warning(semantic_definitions): ...  # FILL IN
async def test_provider_enforces_datasource_and_driver_use(semantic_definitions, fake_evaluator): ...  # FILL IN
async def test_provider_checksum_includes_model_version(): ...  # FILL IN
async def test_multiqs_semantic_child_without_principal_is_denied_on_secured_model(definitions): ...  # FILL IN
```

---

## Agent Instructions

1. Work in the feature worktree; all dependencies done.
2. Resolve the contract FILL INs (datasource default, session, revision) by reading the cited lines first.
3. Implement; run validation; commit only listed files.
4. Close with `scripts/sdd/close_task.sh TASK-904 semantic-layer verified`.

---

## Completion Note

**Completed by**: sdd-worker (coder seat gpt-5.6-terra, codex, 1 attempt, 260s)
**Date**: 2026-10-15
**Notes**: Review fix 8b497121 repaired a test import that broke collection (coder-feedback:181db6d60b9f71b4595e0ef5). Sidemantic-dependent tests skip under the sandboxed merge run (extra not in shared venv); run separately with a scratch install: 37 passed. Review recorded as coder-review:671b7aef9a6c5f57c910f3ba. Closed via close_task.sh (finalize_task needs a durable review EvidenceRef no available tool produces).

**Deviations from spec**: none
