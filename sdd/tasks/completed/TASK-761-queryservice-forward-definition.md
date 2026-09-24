# TASK-761: QueryService forwards the pre-loaded definition to QS

**Feature**: FEAT-151 — Unified single/multi dispatch on `/api/v1/{tenant}/queries/{slug}`
**Spec**: `sdd/specs/multiquery-multitenant.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-757
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 3 (service half). When the tenant dispatcher (TASK-762) routes a single
definition to `QueryService`, the definition it already loaded is on
`request['qs_definition']`. All four `QueryService` sites that build a `QS` through
`get_source(...)` must forward it so `QS.build_provider()` skips the repository read
(AC-4). Legacy v2 callers never set the key, so `request.get(...)` yields `None` and
behavior is unchanged (AC-5). `AbstractHandler.get_source` already forwards `**kwargs`
into `QS(...)` (`handlers/abstract.py:278-285`), so it needs no change.

---

## Scope

- Add `definition=request.get('qs_definition')` to the four `get_source(...)` calls in `query`, `get_columns`, `columns` and `test_slug`.
- Write unit tests proving the forward and the `None` default.

**NOT in scope**: the tenant dispatcher (TASK-762); PBAC logic in `QueryService.query` (unchanged).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/service.py` | MODIFY | forward `definition=` at four call sites |
| `tests/handlers/test_queryservice_definition_forwarding.py` | CREATE | unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.handlers.service import QueryService        # verified: querysource/handlers/__init__.py:12
```

### Existing Signatures to Use
```python
# querysource/handlers/abstract.py:270
async def get_source(self, request, slug, conditions, **kwargs) -> QS:
    # QS(slug=slug, conditions=conditions, loop=self._loop, request=request, lazy=False, **kwargs)  # 278-285

# querysource/handlers/service.py — each site is preceded by `tenant = request.get('qs_tenant')`
#   query()        line 307: if query := await self.get_source(request, slug, conditions, driver=args, tenant=tenant):
#   get_columns()  line 438: same text
#   columns()      line 580: same text
#   test_slug()    line 705: query = await self.get_source(request, slug, conditions, driver=args, tenant=tenant)

# querysource/queries/qs.py (after TASK-757)
QS.__init__(..., *, tenant=None, definition=None, **kwargs)
```

### Does NOT Exist
- ~~`request['qs_definition']` set by v2 routes~~ — only the tenant dispatcher sets it.
- ~~`QueryService.test_slug` multi support~~ — multi dry-run lives in `QueryHandler.test_slug` (TASK-760).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/handlers/service.py", "action": "MODIFY"},
    {"path": "tests/handlers/test_queryservice_definition_forwarding.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/handlers/service.py#QueryService.query",
    "sym:querysource/handlers/service.py#QueryService.get_columns",
    "sym:querysource/handlers/service.py#QueryService.columns",
    "sym:querysource/handlers/service.py#QueryService.test_slug",
    "sym:querysource/handlers/abstract.py#AbstractHandler.get_source"
  ]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Edit the three identical `if query := ...` lines — *why*: AC-4 on query/HEAD/PATCH.
2. Edit the `test_slug` line — *why*: AC-4 on the dry-run.
3. Write the tests.

### `querysource/handlers/service.py` (MODIFY)
```python
# occurrences: 3 (verified: grep -c '            if query := await self.get_source(request, slug, conditions, driver=args, tenant=tenant):' querysource/handlers/service.py)
# Disambiguated by method: query() line 307, get_columns() line 438, columns() line 580;
# each preceded by the comment "# Owner-aware execution ..." and `tenant = request.get('qs_tenant')`.
# The change is identical at all three, so replace every occurrence with:
            if query := await self.get_source(
                request, slug, conditions, driver=args, tenant=tenant,
                definition=request.get('qs_definition'),
            ):
```
```python
# occurrences: 1 (verified: grep -c '            query = await self.get_source(request, slug, conditions, driver=args, tenant=tenant)' querysource/handlers/service.py)
# REPLACE line 705 with:
            query = await self.get_source(
                request, slug, conditions, driver=args, tenant=tenant,
                definition=request.get('qs_definition'),
            )
```
**Why**: a request-scoped key keeps the tenant handler decoupled from `QueryService` internals, exactly like `qs_tenant`.

### `tests/handlers/test_queryservice_definition_forwarding.py` (CREATE)
```python
"""FEAT-151: QueryService forwards request['qs_definition'] to get_source/QS."""
import pytest

from querysource.handlers.service import QueryService

# FILL IN: build a QueryService instance without a live app (pattern:
#   tests/handlers/test_queryservice_pbac_smoke.py), stub json_data/query_parameters/
#   match_parameters/_enforce_* as needed, and monkeypatch `get_source` to capture its
#   kwargs and raise a sentinel exception so the method stops right after the call.
# FILL IN tests:
#   test_query_forwards_definition        (request storage has qs_definition=<obj>)
#   test_get_columns_and_columns_forward_definition
#   test_test_slug_forwards_definition
#   test_legacy_request_forwards_none     (no key -> definition=None)
```

### FILL IN checklist
- [ ] test harness and four tests — bounded by AC-4/AC-5.

---

## Acceptance Criteria

- [ ] All four sites forward `definition=request.get('qs_definition')`.
- [ ] A request without the key forwards `None` (AC-5).
- [ ] Existing QueryService PBAC tests stay green.
- [ ] `ruff check querysource/handlers/service.py tests/handlers/test_queryservice_definition_forwarding.py`

## Validation Commands

- `pytest tests/handlers/test_queryservice_definition_forwarding.py -q`
- `pytest tests/handlers/test_queryservice_pbac_smoke.py -q`

---

## Agent Instructions

1. Read the spec. 2. Confirm TASK-757 is done. 3. Verify the Codebase Contract. 4. Implement from the blueprint and complete every `FILL IN`. 5. Run the validation commands and ruff. 6. Move this file to `sdd/tasks/completed/`, update the index, fill the Completion Note.

---

## Completion Note

Implemented exactly as blueprinted: `definition=request.get('qs_definition')`
added to all four `get_source(...)` call sites in `QueryService` — the three
identical `if query := await self.get_source(...)` occurrences (`query()`,
`get_columns()`, `columns()`) via a single `replace_all` edit, and the
`test_slug()` unconditional-assignment site separately. No change needed to
`AbstractHandler.get_source`, which already forwards `**kwargs` into `QS(...)`.

Tests: `tests/handlers/test_queryservice_definition_forwarding.py` (4/4 pass) —
`test_query_forwards_definition`, `test_get_columns_and_columns_forward_definition`,
`test_test_slug_forwards_definition`, `test_legacy_request_forwards_none`. Built
on the `_make_handler`/`_make_request` pattern from
`tests/handlers/test_queryservice_pbac_smoke.py` (copied locally, not
imported, per convention): `get_source` stubbed to return `None` (falsy) for
`query`/`get_columns`/`columns`, whose branches return/raise via `self.Error`
without touching `query`/DB state; `test_slug` instead stubs `get_source` to
raise a local `_StopSentinel`, and the test asserts only on
`get_source.call_args.kwargs['definition']` under a broad `pytest.raises`,
since `test_slug`'s `finally: await query.close()` turns an early
`get_source` failure into an `UnboundLocalError` — the mock still records the
call before raising, which is all this test needs to prove forwarding.

Regression: `tests/handlers/test_queryservice_pbac_smoke.py` (3/3 pass); full
`tests/handlers` suite excluding the pre-existing, unrelated
`test_airtable_oauth.py` collection failure (`ModuleNotFoundError:
aioresponses`, confirmed present before this change, not a dependency this
task touches) — 129 passed.

`ruff check --select E9,F63,F7,F82`: clean. Pre-existing `B904`/`DTZ005`
findings elsewhere in `service.py` (confirmed unrelated to the four edited
call sites) left untouched, per the Fallback Loop's lint scope.

