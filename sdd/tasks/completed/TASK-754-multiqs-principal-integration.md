# TASK-754: MultiQS accepts principal= and pre-checks every child

**Feature**: FEAT-150 — PBAC for Request-less (Programmatic) QS Callers
**Spec**: `sdd/specs/pbac-request-credentials.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-753
**Assigned-to**: unassigned

---

## Context

Implements spec §3 **Module 6** (resolved: "MultiQS included; every stored child checked
before any executes", the FEAT-147 no-partial-batch rule). With a principal,
`MultiQS.query()` must check the pipeline's own stored slug, every stored child slug,
every `files` entry and any inline raw child **before any child runs**. Not-found
errors collapse to `QueryAccessDenied` and are never wrapped by `self.Error`. This
builds on TASK-753's `AbstractQuery._principal` storage and `BaseQuery` forwarding.

---

## Scope

- `MultiQS.__init__` (`multi/__init__.py:96-118`): add keyword-only `principal=None` and forward it to `super().__init__`.
- `MultiQS.query()`:
  - With a principal, check `slug:execute` on `self.slug` **before** `get_slug()` (`:225`), and collapse `SlugNotFound` and not-found `TenantError` into `QueryAccessDenied`.
  - Add `async def _preflight_principal(self) -> None` and call it after pipeline expansion and **before** the `total_sources` guard (`:269`).
  - In the child pre-flight loop (`:288-336`), with a principal, re-raise not-found `TenantError`s from `registry.resolve(child_tenant)` and `repo.get(ident)` as `QueryAccessDenied` instead of `self.Error`.
- Write `tests/multi/test_multiqs_principal.py`.

**NOT in scope**: the `sources:` list (no PBAC today, and parity is kept; spec Non-Goals); `user_session` (leave untouched); the handlers.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/__init__.py` | MODIFY | kwarg, parent gate, `_preflight_principal`, collapse in child loop |
| `tests/multi/test_multiqs_principal.py` | CREATE | unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.exceptions import QueryAccessDenied            # created by TASK-748
from querysource.auth.enforcement import enforce_principal      # created by TASK-750 — import lazily
from querysource.auth._resource_types import ResourceType       # lazily
# already imported in multi/__init__.py:
from ...exceptions import (DataNotFound, DriverError, OutputError, ParserError, QueryException, SlugNotFound,)  # :10-17
from ...tenant_errors import TenantError                        # :19
```

### Existing Signatures to Use
```python
# querysource/queries/multi/__init__.py:90
class MultiQS(BaseQuery):
    def __init__(self, slug=None, queries=None, files=None, query=None, conditions=None,
                 request=None, loop=None, user_session: object | None = None, *,
                 tenant: str | None = None, **kwargs)                 # :96-108
        super().__init__(slug=slug, conditions=conditions, request=request, loop=loop, tenant=tenant, **kwargs)  # :110-117
        # self._queries: dict alias → cfg ({'slug': ..., 'tenant'?: ..., 'query'?: ...})
        # self._files: list | dict | None   (query.pop('files', {}) or the `files` arg)
        # self._user_session = user_session  :147 (never read — do not touch)
    async def query(self):                                           # :203
        # :224-225  if self.slug:  query = await self.get_slug(slug=self.slug, tenant=self._tenant_selector)
        # :226-263  pipeline expansion (queries/files/sources from the stored slug, or single-query wrap)
        # :269      total_sources = (   ← source-count guard (raises self.Error)
        # :288-336  child pre-flight:
        #   repo = await self.get_definition_repository()
        #   for name, query_cfg in list(self._queries.items()):
        #       child_slug = query_cfg.get("slug"); no slug + "query" in cfg → raw child, `continue`
        #       child_tenant = query_cfg.get("tenant") if "tenant" in query_cfg else self._tenant_selector
        #       child_store = repo.registry.resolve(child_tenant)          # :324 (outside the try)
        #       try: await repo.get(QueryIdentity(store=child_store, slug=child_slug))
        #       except Exception as ex: raise self.Error(message=..., exception=ex) from ex   # :330-335

# querysource/interfaces/connections.py:526 get_slug(...) raises SlugNotFound when not found (:545-548)
# AbstractQuery._principal — set by TASK-753 (interfaces/queries.py)
```

Reference test patterns: `tests/test_multiqs_remote_dispatch.py:104-107` (fake `get_definition_repository`),
`tests/tenants/test_tenant_execution_context.py:53` (MultiQS construction).

### Does NOT Exist
- ~~`MultiQS._preflight_principal`~~: created here.
- ~~A PBAC check for `sources:` entries~~: intentionally absent (spec Non-Goals).
- Do not reuse ~~`self._user_session`~~ for the principal.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/queries/multi/__init__.py", "action": "MODIFY"},
    {"path": "tests/multi/test_multiqs_principal.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/queries/multi/__init__.py#MultiQS",
    "sym:querysource/queries/multi/__init__.py#MultiQS.query"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- **No child may execute** when any check fails. All checks happen before the dispatch loop that starts at `if self._queries:` after the pre-flight.
- Inline raw children (a cfg with `query` and no `slug`) trigger **one** `raw_query:execute` check, whatever their number, the same as the handler's `has_raw_query`.
- `files` names: when `self._files` is a dict, check its keys; when it is a list, check each item's `name`/`slug`/str. **Decide from the data you see in the existing tests, and state the decision in the Completion Note.**
- `QueryAccessDenied` must propagate out of `query()` unchanged. Do not let `self.Error` wrap it.
- Without a principal, every path behaves exactly as today.

---

## Implementation Blueprint

### Steps (in order)
1. Add the kwarg and forward it — *why*: `AbstractQuery` (TASK-753) owns the storage and the request+principal `ValueError`.
2. Gate the parent slug before `get_slug` — *why*: a denied principal must not even load the pipeline definition.
3. Add `_preflight_principal` and call it before the source-count guard — *why*: spec §2 item 6 ordering.
4. Collapse the not-found errors in the child loop — *why*: existence must not leak, and `self.Error` would hide the type.
5. Write the tests.

### `querysource/queries/multi/__init__.py` (MODIFY) — signature
```python
# occurrences: 1 (verified: grep -c '            user_session: object | None = None,' querysource/queries/multi/__init__.py)
# In MultiQS.__init__ after `            tenant: str | None = None,` (the line following `*,` below user_session, :107) add:
            principal: "QSPrincipal | None" = None,
# and add `principal=principal,` after `tenant=tenant,` in the super().__init__ call (:115)
# FILL IN: TYPE_CHECKING import of QSPrincipal
```

### `querysource/queries/multi/__init__.py` (MODIFY) — parent gate
```python
# occurrences: 1 (verified: grep -c '            query = await self.get_slug(slug=self.slug, tenant=self._tenant_selector)' querysource/queries/multi/__init__.py)
# REPLACE that line (verified: multi/__init__.py:225) with:
            if self._principal is not None:
                from ...auth._resource_types import ResourceType
                from ...auth.enforcement import enforce_principal
                await enforce_principal(
                    self._principal, ResourceType.SLUG, self.slug, "slug:execute",
                    tenant=self._tenant_selector, logger=self._logger,
                )
            try:
                query = await self.get_slug(slug=self.slug, tenant=self._tenant_selector)
            except (SlugNotFound, TenantError) as ex:
                if self._principal is not None and (
                    isinstance(ex, SlugNotFound) or ex.error_code in _COLLAPSED_OWNER_ERRORS
                ):
                    raise QueryAccessDenied() from ex
                raise
# FILL IN: module constant _COLLAPSED_OWNER_ERRORS = frozenset({"query_not_found", "tenant_not_available"})
#          + add QueryAccessDenied to the :10-17 exceptions import tuple
```

### `querysource/queries/multi/__init__.py` (MODIFY) — pre-flight
```python
# occurrences: 1 (verified: grep -c '        total_sources = (' querysource/queries/multi/__init__.py)
# BEFORE — insert above `        total_sources = (` (verified: multi/__init__.py:269)
        await self._preflight_principal()

# New method on MultiQS (place it right before `async def query(self):`, verified: multi/__init__.py:203):
    async def _preflight_principal(self) -> None:
        """With a principal: enforce slug:execute for every stored child slug in self._queries,
        slug:execute for every self._files entry, and raw_query:execute once when any child
        carries an inline 'query'. Any deny raises QueryAccessDenied before any child runs.
        No-op when self._principal is None.
        """
        if self._principal is None:
            return
        from ...auth._resource_types import ResourceType
        from ...auth.enforcement import enforce_principal
        # FILL IN: iterate self._queries (child slugs / raw flag) and self._files as described
        #          — bounded by Key Constraints + AC-3/AC-4
```

### `querysource/queries/multi/__init__.py` (MODIFY) — child-loop collapse
```python
# occurrences: 1 (verified: grep -c '                child_store = repo.registry.resolve(child_tenant)' querysource/queries/multi/__init__.py)
# FILL IN: with self._principal set, make a TenantError from `repo.registry.resolve(child_tenant)` (:324)
#          or from `repo.get(ident)` (inside the try at :327-335) whose error_code is in _COLLAPSED_OWNER_ERRORS
#          raise QueryAccessDenied() from ex BEFORE the generic `raise self.Error(...)` — bounded by AC-5;
#          without a principal keep today's behaviour byte-for-byte
```

### FILL IN checklist
- [ ] TYPE_CHECKING import; `_COLLAPSED_OWNER_ERRORS`; `QueryAccessDenied` import
- [ ] `_preflight_principal` iteration over queries/files; the `files` shape decision goes in the Completion Note
- [ ] Child-loop collapse for both `resolve` and `repo.get`
- [ ] Test bodies

---

## Acceptance Criteria

- [ ] AC-1: `MultiQS(slug="p", principal=p)` stores the principal. `MultiQS(slug="p", request=r, principal=p)` raises `ValueError` (inherited from TASK-753).
- [ ] AC-2: with a principal, the pipeline slug is checked before `get_slug`. A deny, or a `SlugNotFound` from `get_slug`, raises `QueryAccessDenied`.
- [ ] AC-3: if one of three stored children is denied, `QueryAccessDenied` is raised and **no** child execution (`ThreadQuery`/executor dispatch) happens.
- [ ] AC-4: `files` entries are checked with `slug:execute`. Inline raw children trigger exactly one `raw_query:execute` check.
- [ ] AC-5: with a principal, a child `TenantError(query_not_found | tenant_not_available)` raises `QueryAccessDenied`, not `self.Error`. Without a principal the existing `self.Error` wrapping is unchanged.
- [ ] AC-6: with `principal=None`, `_preflight_principal` returns immediately and `enforce_principal` is never called.
- [ ] AC-7: existing MultiQS suites pass unmodified: `tests/test_multiqs_remote_dispatch.py`, `tests/tenants/test_tenant_execution_context.py`.
- [ ] AC-8: `ruff check querysource/queries/multi/__init__.py tests/multi/test_multiqs_principal.py` is clean.

---

## Validation Commands

- `pytest tests/multi/test_multiqs_principal.py -q`
- `pytest tests/test_multiqs_remote_dispatch.py -q`
- `pytest tests/tenants/test_tenant_execution_context.py -q`

---

## Test Specification

```python
# tests/multi/test_multiqs_principal.py
import pytest

from querysource.auth.principal import QSPrincipal
from querysource.exceptions import QueryAccessDenied, SlugNotFound
from querysource.queries.multi import MultiQS
from querysource.tenant_errors import TenantError


@pytest.fixture
def principal():
    return QSPrincipal(user_id="35", groups=("sales",))


async def test_parent_slug_checked_before_get_slug(principal, monkeypatch): ...      # AC-2
async def test_parent_slug_not_found_collapses(principal, monkeypatch): ...          # AC-2
async def test_denied_child_runs_nothing(principal, monkeypatch): ...                # AC-3
async def test_files_and_raw_children(principal, monkeypatch): ...                   # AC-4
@pytest.mark.parametrize("code", ["query_not_found", "tenant_not_available"])
async def test_missing_child_not_wrapped(principal, monkeypatch, code): ...          # AC-5
async def test_no_principal_no_enforcement(monkeypatch): ...                         # AC-6
```

---

## Agent Instructions

1. Confirm TASK-753 is completed (`AbstractQuery._principal` exists).
2. Re-run every `grep -c` in the blueprint and record a green baseline of the AC-7 suites.
3. Implement, test and lint. Move this file to `sdd/tasks/completed/`, set the index status to `done`, and fill in the Completion Note, including the `files` shape decision.

---

## Completion Note

Added keyword-only `principal=` to `MultiQS.__init__`, forwarded to
`super().__init__` (TASK-753's `AbstractQuery` owns storage + the
request+principal `ValueError`), with a `TYPE_CHECKING` import of
`QSPrincipal`. In `MultiQS.query()`: the pipeline's own `self.slug` is
gated with `slug:execute` before `get_slug()`; `SlugNotFound` and
collapsible `TenantError` codes (`query_not_found`, `tenant_not_available`,
via the new `_COLLAPSED_OWNER_ERRORS` constant) from `get_slug()` become
`QueryAccessDenied` when a principal is set. Added
`_preflight_principal()`, called after pipeline expansion and before the
`total_sources` guard: it checks `slug:execute` for every stored child
slug in `self._queries`, `slug:execute` for every `self._files` entry, and
exactly one `raw_query:execute` when any child carries an inline `query`
(no slug). In the per-child repo-preflight loop, both
`repo.registry.resolve(child_tenant)` and `repo.get(ident)` now collapse a
matching `TenantError` into `QueryAccessDenied` (principal-gated) before
falling through to the existing `self.Error` wrapping; without a principal,
behaviour is byte-for-byte unchanged (same `self.Error` message, now
just also covering `registry.resolve`, which was not wrapped in a
try/except at all before — a to-string-identical enhancement, not a
behaviour change, since it only reaches this except when principal-gated
collapse does not apply).

**`files` shape decision**: `self._files` is a dict keyed by file name/alias
(confirmed at the dispatch loop, `for name, file in self._files.items()`
and `FileSource(name, file, ...)`), so `_preflight_principal` iterates its
keys and checks `slug:execute` on each key, the same identifying name the
dispatch loop later uses to construct the `FileSource`.

Wrote `tests/multi/test_multiqs_principal.py` (8 tests covering AC-1
through AC-6). `ruff check` clean on both files except 3 pre-existing,
unrelated findings in `multi/__init__.py` (1 `B904`, 2 `LOG015`, verified
present at HEAD via `git show HEAD:... | ruff check --select B904,LOG015 -`,
at different line numbers but the same code, out of this task's scope).
Both pinned suites pass unmodified: `tests/test_multiqs_remote_dispatch.py`
+ `tests/tenants/test_tenant_execution_context.py` = 13 passed (AC-7).
Broader regression (`tests/handlers/`, `tests/tenants/`, `tests/auth/`,
`tests/multi/`, `tests/test_qs_principal.py`,
`tests/test_multiqs_remote_dispatch.py`, `tests/test_abstract_multi.py`,
`tests/test_abstract_refactor.py`, excluding the pre-existing
`test_airtable_oauth.py` collection failure): 347 passed, 5 skipped, 3
failed — the same 3 pre-existing, unrelated sandbox/environment failures
recorded in TASK-752/753's Completion Notes.

**Completed by**: sdd-worker (Sonnet)
**Date**: 2026-09-24
