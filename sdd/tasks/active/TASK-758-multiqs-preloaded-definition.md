# TASK-758: MultiQS pre-loaded definition and child-owner helper

**Feature**: FEAT-151 — Unified single/multi dispatch on `/api/v1/{tenant}/queries/{slug}`
**Spec**: `sdd/specs/multiquery-multitenant.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-757
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 1 (second half) and design-research S5. `MultiQS` must reuse the
definition the tenant dispatcher already loaded (AC-4) and, unlike today, record
`_definition_identity` / `_definition_revision` for its own top-level slug. The child
tenant rule (explicit string, explicit `None`, or inherit) is extracted into a pure
static helper so the execution preflight and the new multi dry-run (TASK-760) share one
implementation (AC-15).

---

## Scope

- Add keyword-only `definition: LoadedDefinition | None = None` to `MultiQS.__init__`
  and forward it to `super().__init__` (the attribute itself comes from TASK-757).
- In the slug loader, when `self._preloaded_definition` is present and its
  `identity.slug == self.slug`, use `definition.runtime` instead of
  `await self.get_slug(...)`, and set `_definition_identity` / `_definition_revision`.
  When no definition is supplied, keep `get_slug()` exactly as today.
- Add `MultiQS.resolve_child_owner(query_cfg, parent_tenant, registry)` and make the
  preflight loop call it.
- Write unit tests.

**NOT in scope**: handlers (TASK-760, TASK-762); `QueryHandler._preflight_multiquery_owned` (spec §8 Q5, escalated).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/__init__.py` | MODIFY | `definition` kwarg, loader short-circuit, `resolve_child_owner` |
| `tests/tenants/test_preloaded_definition_multiqs.py` | CREATE | unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.queries import MultiQS                        # verified: querysource/queries/__init__.py:7
from querysource.models import QueryModel                      # verified: querysource/models.py:48
from querysource.tenants import LoadedDefinition, QueryIdentity, QueryStore, TenantRegistry  # verified: querysource/tenants.py:35,46,54,80
```
Inside `multi/__init__.py`, `QueryIdentity` is already imported lazily at line 207
(`from querysource.tenants import QueryIdentity`); type the helper with string
annotations and a `TYPE_CHECKING` import of `QueryStore`, `TenantRegistry`, `LoadedDefinition`.

### Existing Signatures to Use
```python
# querysource/queries/multi/__init__.py:90
class MultiQS(BaseQuery):
    def __init__(self, slug=None, queries=None, files=None, query=None, conditions=None, request=None,
                 loop=None, user_session=None, *, tenant: str | None = None, **kwargs):  # 96-109
        super().__init__(slug=slug, conditions=conditions, request=request, loop=loop, tenant=tenant, **kwargs)  # 110-117
    @staticmethod
    def _normalize_sources(raw) -> list: ...   # 174
    # query(): slug loader 216-262
    #   query = await self.get_slug(slug=self.slug, tenant=self._tenant_selector)  # 225
    #   query_raw = getattr(query, 'query_raw', None) or ''                        # 227
    # preflight loop 285-333:
    #   if "tenant" in query_cfg: child_tenant = query_cfg.get("tenant")           # 319-320
    #   else: child_tenant = self._tenant_selector                                 # 321-322
    #   child_store = repo.registry.resolve(child_tenant)                          # 324
    #   resolved_stores[name] = child_store                                        # 325

# querysource/tenants.py:402
class TenantRegistry:
    def resolve(self, tenant: str | None = None) -> QueryStore: ...

# querysource/interfaces/queries.py (after TASK-757)
    self._preloaded_definition: "LoadedDefinition | None"
    self._definition_identity: Any; self._definition_revision: str | None   # 106-107
```

### Does NOT Exist
- ~~`MultiQS.dry_run()`~~ — not added here either; the dry-run lives in `QueryHandler.test_slug` (TASK-760).
- ~~`QueryStore.tenant`~~ — the store has `schema`, not `tenant`.
- ~~`MultiQS.resolve_child_owner`~~ — created by this task.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/queries/multi/__init__.py", "action": "MODIFY"},
    {"path": "tests/tenants/test_preloaded_definition_multiqs.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/queries/multi/__init__.py#MultiQS",
    "sym:querysource/queries/multi/__init__.py#MultiQS.query",
    "sym:querysource/tenants.py#TenantRegistry.resolve",
    "sym:querysource/tenants.py#LoadedDefinition"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- The multi/single detection on `query_raw` (lines 227-262) is unchanged: only the *source* of `query` changes.
- Behavior without `definition` is byte-for-byte the current one (AC-5); `tests/test_multiqs_slug_sources_normalize.py` must stay green.
- `resolve_child_owner` is pure: no I/O, no request access.

---

## Implementation Blueprint

### Steps (in order)
1. Add and forward the `definition` keyword — *why*: the attribute from TASK-757 is only set if the keyword reaches `AbstractQuery`.
2. Replace the `get_slug` call with a guarded pre-loaded path — *why*: AC-4.
3. Add `resolve_child_owner` and call it from the preflight loop — *why*: S5, AC-15.
4. Write the tests.

### `querysource/queries/multi/__init__.py` (MODIFY) — constructor
```python
# occurrences: 1 (verified: grep -c '            tenant: str | None = None,' querysource/queries/multi/__init__.py) -> line 107
# AFTER — insert below it:
            definition: "LoadedDefinition | None" = None,
# occurrences: 1 (verified: grep -c '            tenant=tenant,' querysource/queries/multi/__init__.py) -> line 115
# AFTER — insert below it:
            definition=definition,
```

### `querysource/queries/multi/__init__.py` (MODIFY) — slug loader
```python
# occurrences: 1 (verified: grep -c '            query = await self.get_slug(slug=self.slug, tenant=self._tenant_selector)' querysource/queries/multi/__init__.py)
# REPLACE line 225 with:
            preloaded = self._preloaded_definition
            if preloaded is not None and preloaded.identity.slug == self.slug:
                # FEAT-151: reuse the definition the tenant dispatcher loaded.
                query = preloaded.runtime
                self._definition_identity = preloaded.identity
                self._definition_revision = preloaded.revision
            else:
                query = await self.get_slug(slug=self.slug, tenant=self._tenant_selector)
```
**Why**: `get_slug()` returns only the runtime model, so the repository path cannot record identity/revision without a wider change; the spec only requires it when a definition is supplied.

### `querysource/queries/multi/__init__.py` (MODIFY) — child owner helper
```python
# Place directly after `_normalize_sources` (verified: querysource/queries/multi/__init__.py:174)
    @staticmethod
    def resolve_child_owner(
        query_cfg: dict,
        parent_tenant: "str | None",
        registry: "TenantRegistry",
    ) -> "tuple[str | None, QueryStore]":
        """Resolve a saved child's owner selector and store.

        Args:
            query_cfg: The child's config from the ``queries`` mapping.
            parent_tenant: The parent MultiQS tenant selector.
            registry: Tenant registry used to resolve the store.

        Returns:
            ``(tenant_selector, store)``: an explicit ``tenant`` key wins, including an
            explicit ``None`` (legacy store); a missing key inherits ``parent_tenant``.
        """
        if "tenant" in query_cfg:
            child_tenant = query_cfg.get("tenant")
        else:
            child_tenant = parent_tenant
        return child_tenant, registry.resolve(child_tenant)
```
```python
# occurrences: 1 (verified: grep -c '                if "tenant" in query_cfg:' querysource/queries/multi/__init__.py)
# REPLACE lines 319-324 (the if/else and `child_store = repo.registry.resolve(child_tenant)`) with:
                _child_tenant, child_store = self.resolve_child_owner(
                    query_cfg, self._tenant_selector, repo.registry
                )
```
Keep the explanatory comment block above it (lines 313-318).

### `tests/tenants/test_preloaded_definition_multiqs.py` (CREATE)
```python
"""FEAT-151: MultiQS reuses a pre-loaded definition; child owner rule helper."""
import pytest

from querysource.models import QueryModel
from querysource.queries import MultiQS
from querysource.tenants import LoadedDefinition, QueryIdentity, QueryStore

MULTI_RAW = '{"queries": {"a": {"slug": "child_a"}}}'


def _store(schema: str = "tenant1") -> QueryStore:
    return QueryStore(database_namespace="localhost:5432/qs", schema=schema, table="queries",
                      contract="tenant", columns=frozenset({"query_slug"}))


class _FakeRegistry:
    def resolve(self, tenant):
        return _store(tenant or "public")


def test_resolve_child_owner_rules() -> None:
    reg = _FakeRegistry()
    assert MultiQS.resolve_child_owner({"slug": "c"}, "tenant1", reg)[0] == "tenant1"
    assert MultiQS.resolve_child_owner({"slug": "c", "tenant": "t2"}, "tenant1", reg)[0] == "t2"
    assert MultiQS.resolve_child_owner({"slug": "c", "tenant": None}, "tenant1", reg)[0] is None


@pytest.mark.asyncio
async def test_multiqs_uses_preloaded_definition_and_records_identity() -> None:
    identity = QueryIdentity(store=_store(), slug="parent")
    runtime = QueryModel(query_slug="parent", program_slug="tenant1", provider="multi", query_raw=MULTI_RAW)
    loaded = LoadedDefinition(identity=identity, runtime=runtime, revision="rev-9")
    qs = MultiQS(slug="parent", tenant="tenant1", definition=loaded)

    async def _no_get_slug(*args, **kwargs):
        raise AssertionError("get_slug must not be called")

    qs.get_slug = _no_get_slug
    # FILL IN: stop query() right after the slug loader (e.g. patch
    # get_definition_repository to raise a sentinel exception that the test
    # catches), then assert qs._queries == {"a": {"slug": "child_a"}},
    # qs._definition_identity == identity and qs._definition_revision == "rev-9".
    # Bounded by: no datasource or DB access in a unit test.


def test_multiqs_without_definition_unchanged() -> None:
    assert MultiQS(slug="parent")._preloaded_definition is None
```

### FILL IN checklist
- [ ] `test_multiqs_uses_preloaded_definition_and_records_identity` — how to halt `query()` after the loader; bounded by "no DB/datasource in unit tests".
- [ ] `TYPE_CHECKING` imports for the string annotations; bounded by ruff and import-cycle safety.

---

## Acceptance Criteria

- [ ] `MultiQS(slug, definition=loaded)` never calls `get_slug` for the top-level slug and records identity/revision (AC-4).
- [ ] `resolve_child_owner` implements inherit / explicit / explicit-null and is used by the preflight loop (AC-15).
- [ ] Without `definition`, behavior is unchanged (AC-5).
- [ ] `ruff check querysource/queries/multi/__init__.py tests/tenants/test_preloaded_definition_multiqs.py`

## Validation Commands

- `pytest tests/tenants/test_preloaded_definition_multiqs.py -q`
- `pytest tests/tenants/test_tenant_child_execution.py -q`
- `pytest tests/test_multiqs_slug_sources_normalize.py -q`

---

## Agent Instructions

1. Read the spec. 2. Confirm TASK-757 is done (`AbstractQuery._preloaded_definition` exists). 3. Verify the Codebase Contract. 4. Implement from the blueprint and complete every `FILL IN`. 5. Run the validation commands and ruff. 6. Move this file to `sdd/tasks/completed/`, update the index, fill the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*
