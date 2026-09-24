# TASK-757: QS accepts a pre-loaded definition

**Feature**: FEAT-151 — Unified single/multi dispatch on `/api/v1/{tenant}/queries/{slug}`
**Spec**: `sdd/specs/multiquery-multitenant.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 1 (first half) and design-research S3. The tenant handler will load a
stored definition once and hand it to the executor, so `QS.build_provider()` must be
able to use a supplied `LoadedDefinition` instead of reading the repository again
(spec AC-4). The keyword must be declared on the shared base (`AbstractQuery`) and
forwarded explicitly by `BaseQuery`, because anything left in `**kwargs` is silently
swallowed on its way to `Connection.__init__` (S3). `MultiQS` is handled by TASK-758.

---

## Scope

- Add keyword-only `definition: LoadedDefinition | None = None` to
  `AbstractQuery.__init__`, `BaseQuery.__init__` and `QS.__init__`, forwarding it
  explicitly down the chain.
- Store it as `self._preloaded_definition` next to `self._tenant_selector`.
- In `QS.build_provider()` slug branch, use the pre-loaded definition instead of
  `repo.get(identity)` when it is present **and** `definition.identity.slug ==
  self._query`; keep every other line (identity/revision assignment, logging,
  `objquery = loaded_def.runtime`) unchanged.
- Write unit tests.

**NOT in scope**: `MultiQS` (TASK-758), handlers (TASK-761, TASK-762), `columns_definition` (TASK-759).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/interfaces/queries.py` | MODIFY | declare and store `definition` |
| `querysource/queries/base.py` | MODIFY | forward `definition` explicitly |
| `querysource/queries/qs.py` | MODIFY | forward `definition`; short-circuit the repository read |
| `tests/tenants/test_preloaded_definition_qs.py` | CREATE | unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.queries.qs import QS                         # verified: tests/tenants/test_tenant_execution_context.py:142
from querysource.models import QueryModel                     # verified: querysource/models.py:48
from querysource.tenants import QueryIdentity, LoadedDefinition, QueryStore  # verified: querysource/tenants.py:35,46,54
```
Inside `querysource/interfaces/queries.py` use a `TYPE_CHECKING`-guarded
`from ..tenants import LoadedDefinition` and a string annotation — `tenants.py`
imports `QueryModel`, and a runtime import from the interfaces module risks a cycle.

### Existing Signatures to Use
```python
# querysource/interfaces/queries.py:41
class AbstractQuery(Connection):
    def __init__(self, slug: str = None, conditions: dict = None, request: web.Request = None,
                 loop: asyncio.AbstractEventLoop | None = None, *, tenant: str | None = None, **kwargs):  # lines 48-56
    self._tenant_selector = tenant          # line 104
    self._definition_identity: Any = None   # line 106
    self._definition_revision: str | None = None  # line 107

# querysource/queries/base.py:19
class BaseQuery(AbstractQuery):
    def __init__(self, slug=None, conditions=None, request=None, loop=None, *, tenant: str | None = None, **kwargs):  # lines 21-30
        super().__init__(slug=slug, conditions=conditions, request=request, loop=loop, tenant=tenant, **kwargs)  # lines 34-41

# querysource/queries/qs.py:35
class QS(BaseQuery):
    def __init__(self, slug: str = '', conditions: dict = None, request=None, loop=None, *, tenant: str | None = None, **kwargs):  # 41-51
        super().__init__(slug, conditions=conditions, request=request, loop=loop, tenant=tenant, **kwargs)  # 52-59
    # build_provider slug branch (line 167):
    #   repo = await self.get_definition_repository()                 # 174
    #   store = repo.registry.resolve(self._tenant_selector)          # 175
    #   identity = QueryIdentity(store=store, slug=self._query)       # 176
    #   loaded_def = await repo.get(identity)                         # 177
    #   self._definition_identity = loaded_def.identity               # 179
    #   self._definition_revision = loaded_def.revision               # 180
    #   objquery = loaded_def.runtime                                 # 186

# querysource/tenants.py:54
@dataclass(frozen=True)
class LoadedDefinition:
    identity: QueryIdentity
    runtime: QueryModel
    revision: str
```

### Does NOT Exist
- ~~`QS(definition=...)`~~ / ~~`AbstractQuery._preloaded_definition`~~ — created by this task.
- ~~`LoadedDefinition.slug`~~ — use `definition.identity.slug`.
- ~~`BaseQuery.get_slug`~~ — `get_slug` is on `Connection` (`interfaces/connections.py:526`).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/interfaces/queries.py", "action": "MODIFY"},
    {"path": "querysource/queries/base.py", "action": "MODIFY"},
    {"path": "querysource/queries/qs.py", "action": "MODIFY"},
    {"path": "tests/tenants/test_preloaded_definition_qs.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/interfaces/queries.py#AbstractQuery",
    "sym:querysource/queries/base.py#BaseQuery",
    "sym:querysource/queries/qs.py#QS",
    "sym:querysource/queries/qs.py#QS.build_provider",
    "sym:querysource/tenants.py#LoadedDefinition"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Keyword-only, default `None`: every existing caller (v2, v3, scheduler, Python API) is unaffected (AC-5).
- The slug-match guard is mandatory — a nested/child query object must never reuse a parent's definition (spec §7 "Definition mismatch guard").
- Do not touch the raw-query branch; `test_raw_owner_context_without_saved_lookup` must stay green.

---

## Implementation Blueprint

### Steps (in order)
1. Add the `definition` keyword to `AbstractQuery.__init__` and store it — *why*: one attribute every executor can read.
2. Forward it explicitly in `BaseQuery.__init__` — *why*: S3, `**kwargs` would otherwise swallow it.
3. Forward it explicitly in `QS.__init__` — *why*: same chain.
4. Short-circuit `repo.get()` in `build_provider()` — *why*: AC-4, one read per request.
5. Write the tests — *why*: prove zero repository reads and identical identity/revision.

### `querysource/interfaces/queries.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '            tenant: str | None = None,' querysource/interfaces/queries.py)
# AFTER — insert below `            tenant: str | None = None,` (verified: querysource/interfaces/queries.py:55)
            definition: "LoadedDefinition | None" = None,
```
```python
# occurrences: 1 (verified: grep -c '        self._tenant_selector = tenant' querysource/interfaces/queries.py)
# AFTER — insert below `        self._tenant_selector = tenant` (verified: querysource/interfaces/queries.py:104)
        # Pre-loaded stored definition (FEAT-151): when a caller already read the
        # definition (tenant dispatcher), executors reuse it instead of re-reading.
        self._preloaded_definition: "LoadedDefinition | None" = definition
```
```python
# At module top, with the other typing imports:
from typing import TYPE_CHECKING, Any   # FILL IN: merge with the existing `from typing import Any` (line 14)
if TYPE_CHECKING:
    from ..tenants import LoadedDefinition
```
Also add a `definition` entry to the `__init__` docstring (Google style).
**Why**: the attribute is the single contract both `QS` (this task) and `MultiQS` (TASK-758) read.

### `querysource/queries/base.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '            tenant: str | None = None,' querysource/queries/base.py)
# AFTER — insert below `            tenant: str | None = None,` (verified: querysource/queries/base.py:28)
            definition: "LoadedDefinition | None" = None,
```
```python
# occurrences: 1 (verified: grep -c '            tenant=tenant,' querysource/queries/base.py)
# AFTER — insert below `            tenant=tenant,` (verified: querysource/queries/base.py:39)
            definition=definition,
```
Add the same `TYPE_CHECKING` import of `LoadedDefinition` from `..tenants`.
**Why**: S3 — explicit forwarding; never rely on `**kwargs`.

### `querysource/queries/qs.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '            tenant: str | None = None,' querysource/queries/qs.py) -> line 49
# AFTER — insert below it:
            definition: "LoadedDefinition | None" = None,
# occurrences: 1 (verified: grep -c '            tenant=tenant,' querysource/queries/qs.py) -> line 57
# AFTER — insert below it:
            definition=definition,
```
```python
# occurrences: 1 (verified: grep -c '            loaded_def = await repo.get(identity)' querysource/queries/qs.py)
# REPLACE lines 174-177 (repo / store / identity / loaded_def) with:
            preloaded = self._preloaded_definition
            if preloaded is not None and preloaded.identity.slug == self._query:
                # FEAT-151: the tenant dispatcher already read this definition.
                loaded_def = preloaded
                self._logger.debug(f"Using pre-loaded definition for slug={self._query}")
            else:
                repo = await self.get_definition_repository()
                store = repo.registry.resolve(self._tenant_selector)
                identity = QueryIdentity(store=store, slug=self._query)
                loaded_def = await repo.get(identity)
```
**Why**: lines 178-186 (identity/revision assignment, runtime model) stay untouched, so cache keys and ownership logging behave identically on both paths.

### `tests/tenants/test_preloaded_definition_qs.py` (CREATE)
```python
"""FEAT-151: QS reuses a pre-loaded LoadedDefinition instead of re-reading it."""
import pytest

from querysource.models import QueryModel
from querysource.queries.qs import QS
from querysource.tenants import LoadedDefinition, QueryIdentity, QueryStore


def _store() -> QueryStore:
    return QueryStore(database_namespace="localhost:5432/qs", schema="tenant1",
                      table="queries", contract="tenant",
                      columns=frozenset({"query_slug"}))


def _loaded(slug: str = "s1") -> LoadedDefinition:
    identity = QueryIdentity(store=_store(), slug=slug)
    runtime = QueryModel(query_slug=slug, program_slug="tenant1", provider="db", is_cached=False)
    return LoadedDefinition(identity=identity, runtime=runtime, revision="rev-1")


def _stub_provider(qs: QS) -> None:
    class _Provider:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def prepare_connection(self):
            return None

    async def _get_provider(objquery, session=None, app=None):
        return ("fake_conn", _Provider)

    qs.connection.get_provider = _get_provider


@pytest.mark.asyncio
async def test_qs_uses_preloaded_definition_and_skips_repo() -> None:
    loaded = _loaded()
    qs = QS(slug="s1", tenant="tenant1", definition=loaded)

    async def _must_not_be_called():
        raise AssertionError("repository must not be read")

    qs.get_definition_repository = _must_not_be_called
    _stub_provider(qs)
    await qs.build_provider()
    assert qs._definition_identity == loaded.identity
    assert qs._definition_revision == "rev-1"


@pytest.mark.asyncio
async def test_qs_ignores_mismatched_preloaded_definition() -> None:
    # FILL IN: definition for slug "other", QS(slug="s1"); a FakeRepo (pattern:
    # tests/tenants/test_tenant_execution_context.py:156-172) must be called exactly once.
    ...


def test_legacy_constructor_default_is_none() -> None:
    assert QS(slug="s1")._preloaded_definition is None
```

### FILL IN checklist
- [ ] `interfaces/queries.py` — merge the `TYPE_CHECKING` import with the existing `typing` import; bounded by ruff.
- [ ] `test_qs_ignores_mismatched_preloaded_definition` body — bounded by spec §7 mismatch guard.

---

## Acceptance Criteria

- [ ] `QS(slug, definition=loaded)` never calls `get_definition_repository` when the slug matches (AC-4).
- [ ] `_definition_identity` / `_definition_revision` equal the supplied definition's.
- [ ] A mismatched definition falls back to the repository path.
- [ ] Existing tenant execution tests still pass.
- [ ] `ruff check querysource/interfaces/queries.py querysource/queries/base.py querysource/queries/qs.py tests/tenants/test_preloaded_definition_qs.py`

## Validation Commands

- `pytest tests/tenants/test_preloaded_definition_qs.py -q`
- `pytest tests/tenants/test_tenant_execution_context.py -q`

---

## Agent Instructions

1. Read the spec. 2. Verify the Codebase Contract. 3. Implement from the blueprint and complete every `FILL IN`. 4. Run the validation commands and ruff. 5. Move this file to `sdd/tasks/completed/`, update the per-spec index, fill the Completion Note.

---

## Completion Note

Implemented exactly as blueprinted. `definition: LoadedDefinition | None = None`
added keyword-only to `AbstractQuery.__init__` (`interfaces/queries.py`), forwarded
explicitly through `BaseQuery.__init__` and `QS.__init__` (never via `**kwargs`,
per S3), and stored as `self._preloaded_definition`. `QS.build_provider()`'s slug
branch now uses the pre-loaded definition when `preloaded.identity.slug ==
self._query`, else falls back to the existing `repo.get(identity)` path unchanged
(identity/revision assignment, logging, `objquery = loaded_def.runtime` untouched).
`LoadedDefinition` is imported under `TYPE_CHECKING` in all three modified modules
to avoid the `tenants.py` import cycle called out in the contract; the resulting
quoted local-scope annotation triggers ruff UP037, suppressed with
`# noqa: UP037` (the codebase has no `from __future__ import annotations`, so
unquoting would raise `NameError` at every `__init__` call — verified by reasoning
about PEP 526 evaluation semantics, not by trial and error).

Tests: `tests/tenants/test_preloaded_definition_qs.py` (3/3 pass) including the
filled-in `test_qs_ignores_mismatched_preloaded_definition` (mismatched-slug
definition falls back to the repository, called exactly once, and the *repository's*
definition/revision are the ones recorded — proving the guard, not just that a read
happened). Regression: `tests/tenants/test_tenant_execution_context.py` (4/4 pass).
`ruff check` on the four changed/created files: 2 pre-existing `B904` findings in
`qs.py` (lines ~515/529, in an unrelated except block, confirmed present on
`origin/dev` before this change) — out of scope, left untouched.

Environment note: this worktree lacked the compiled Cython/Rust extensions
(`*.cpython-311*.so`, gitignored build artifacts) that `make build-inplace` /
`make build-rust` produce, so pytest's rootdir-prepend import shadowed the
site-packages editable install with the uncompiled worktree source and failed
collection. Copied the already-built `.so` files from the main checkout's
`querysource/` tree (read-only source, worktree-local destination — no shared
`.venv` mutation) rather than rebuilding, since no `.pyx`/Rust source was
touched by this task.

