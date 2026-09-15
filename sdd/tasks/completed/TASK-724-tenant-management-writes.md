# TASK-724: Route management mutations through selected-owner repository

**Feature**: FEAT-147 — Per-tenant Queries
**Spec**: `sdd/specs/per-tenant-queries.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-723
**Assigned-to**: unassigned

## Context

Implements M4 CRUD of the approved spec. FEAT-147 is the task/spec identity;
FEAT-176 is the historical proposal/audit identity. Preserve all five user decisions.
This task runs sequentially in the shared per-spec implementation worktree.

## Scope

- Resolve selectors on PATCH/DELETE/PUT/POST and remove top-level tenant before model validation or SQL conditions. Require path/body slug agreement and reject row moves.
- Replace all direct ORM mutations/existence checks with repository operations. Preserve POST/PUT upsert behavior and 201/202 statuses, PATCH validation handling and DELETE 202/missing conventions.
- Add internal QueryManager._sync_definition_jobs(identity) hook as an async no-op only when no scheduler is active; live scheduler hookup lands with scheduler API task. Never misrepresent committed data as rolled back.
- Return tenant_write_forbidden/store-unavailable error codes as specified without leaking SQL or inventories; preserve legacy error envelopes.

**NOT in scope**: Scheduler identities and startup; those are separate tasks. Do not create a permanent stub for the later sync hook.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/handlers/manager.py` | MODIFY | Each verb resolves ownership before its existence checks and mutations. |
| `tests/tenants/test_tenant_management_writes.py` | CREATE | Tests must exercise the listed ownership/error branches with observable per-store outcomes. |

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
import pytest
from querysource.handlers.tenant import resolve_request_store
from querysource.tenants import QueryIdentity
```

Standard-library imports are built in. Existing repository imports resolve through
source definitions/exports below and spec §6. Imports from dependency-created
modules are **planned contracts**, not claims that those modules exist today;
verify them after the dependency lands. Preserve existing imports needed by
untouched code. Before adding any further import, verify its source and update
this packet. Datamodel BaseModel is not Pydantic BaseModel.

### Import Provenance

- `querysource.handlers.tenant` → planned `querysource/handlers/tenant.py` created by TASK-723; verify after prerequisite
- `querysource.tenants` → planned `querysource/tenants.py` created by TASK-716; verify after prerequisite

Owner error dependency: `querysource.tenant_errors.TenantError(message,
error_code=...)` is created by TASK-716. Its `code` is the HTTP status and
`error_code` is the spec machine code. Preserve existing error envelopes and
legacy exception mapping; do not feed a string into QueryException.code.

### Existing Signatures to Use

```text
querysource/handlers/manager.py:253
async def patch(self):

querysource/handlers/manager.py:323
async def delete(self):

querysource/handlers/manager.py:32
class QueryManager(QueryView):
async def get(self):
async def patch(self):
async def delete(self):
async def put(self):
async def post(self):
```

### Does NOT Exist

- The new tenant registry, repository, model, handler and identity helpers do not
  exist at decomposition time except as dependency blueprints. Do not import them
  until the creating prerequisite is complete.
- No `querysource.remote` implementation exists in this checkout. The versioned
  tenant callable is an external contract, never a public fallback.
- No `tenant` parameter on the current provider checksum or compiled parser
  interface exists; use immutable execution identity and the runtime adapter.

## Implementation Notes

### Pattern to Follow

Use the exact interfaces in spec §3 and the verified existing signatures above.
Modify existing classes in place; a class wrapper in a MODIFY block locates new
methods, not permission to replace the entire class. Preserve unrelated methods,
metaclasses, inheritance, decorators and initialization behavior. CREATE blocks
are whole-file starting points; all bounded FILL IN markers must be completed.

### Key Constraints

- No per-request shared Meta, search_path, global field-policy or pooled-connection
  mutation. No owner fallback. Preserve configured legacy defaults and overrides.
- Tenant is structural definition ownership; SQL may read other permitted schemas.
- Keep strict new type hints, async I/O, existing logging, black formatting and
  existing dependencies. Do not add mandatory PBAC/membership requirements.
- Do not change fixed spec signatures. Refresh anchors after dependencies because
  earlier tasks modify shared files. If a fixed contract cannot work, report the
  concrete mismatch instead of silently changing ownership behavior.

## Implementation Blueprint

### Steps (in order)

1. Resolve selectors on PATCH/DELETE/PUT/POST and remove top-level tenant before model validation or SQL conditions. Require path/body slug agreement and reject row moves. **Why:** Routing metadata must not reach model fields or WHERE predicates.
2. Replace all direct ORM mutations/existence checks with repository operations. Preserve POST/PUT upsert behavior and 201/202 statuses, PATCH validation handling and DELETE 202/missing conventions. **Why:** Existing clients depend on verb-specific statuses and upsert behavior.
3. Add internal QueryManager._sync_definition_jobs(identity) hook as an async no-op only when no scheduler is active; live scheduler hookup lands with scheduler API task. Never misrepresent committed data as rolled back. **Why:** Scheduler synchronization occurs after database commit.
4. Return tenant_write_forbidden/store-unavailable error codes as specified without leaking SQL or inventories; preserve legacy error envelopes. **Why:** Error handling must not reveal other owners or SQL.

### `querysource/handlers/manager.py` (MODIFY)

```python
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `class QueryManager(QueryView):` (verified: querysource/handlers/manager.py:32)
async def patch(self):
    """Resolve selected store; call repository patch; preserve statuses and strip routing metadata."""
    # FILL IN: Resolve selected store; call repository patch; preserve statuses and strip routing metadata.
    raise NotImplementedError

async def delete(self):
    """Resolve selected store; call repository delete; preserve statuses and strip routing metadata."""
    # FILL IN: Resolve selected store; call repository delete; preserve statuses and strip routing metadata.
    raise NotImplementedError

async def put(self):
    """Resolve selected store; call repository upsert; preserve statuses and strip routing metadata."""
    # FILL IN: Resolve selected store; call repository upsert; preserve statuses and strip routing metadata.
    raise NotImplementedError

async def post(self):
    """Resolve selected store; call repository upsert; preserve statuses and strip routing metadata."""
    # FILL IN: Resolve selected store; call repository upsert; preserve statuses and strip routing metadata.
    raise NotImplementedError
```

**Why:** Each verb resolves ownership before its existence checks and mutations.

### `tests/tenants/test_tenant_management_writes.py` (CREATE)

```python
"""Route management mutations through selected-owner repository regression contracts."""
import pytest

@pytest.mark.asyncio
async def test_all_crud_methods_target_selected_store() -> None:
    """all crud methods target selected store."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_null_and_conflicting_write_selectors() -> None:
    """null and conflicting write selectors."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_patch_immutable_slug_and_program_rejection() -> None:
    """patch immutable slug and program rejection."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_legacy_upsert_and_delete_statuses() -> None:
    """legacy upsert and delete statuses."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError
```

**Why:** Tests must exercise the listed ownership/error branches with observable per-store outcomes.

### FILL IN checklist

- [ ] `querysource/handlers/manager.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `tests/tenants/test_tenant_management_writes.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.

## Acceptance Criteria

- [ ] AC-1: Resolve selectors on PATCH/DELETE/PUT/POST and remove top-level tenant before model validation or SQL conditions. Require path/body slug agreement and reject row moves.
- [ ] AC-2: Replace all direct ORM mutations/existence checks with repository operations. Preserve POST/PUT upsert behavior and 201/202 statuses, PATCH validation handling and DELETE 202/missing conventions.
- [ ] AC-3: Add internal QueryManager._sync_definition_jobs(identity) hook as an async no-op only when no scheduler is active; live scheduler hookup lands with scheduler API task. Never misrepresent committed data as rolled back.
- [ ] AC-4: Return tenant_write_forbidden/store-unavailable error codes as specified without leaking SQL or inventories; preserve legacy error envelopes.
- [ ] AC-5: Focused tests pass: `source .venv/bin/activate && uv run pytest tests/tenants/test_tenant_management_writes.py -q`.
- [ ] New/changed Python code passes black formatting; no unrelated refactoring or dependency additions.

## Test Specification

- `test_all_crud_methods_target_selected_store` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_null_and_conflicting_write_selectors` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_patch_immutable_slug_and_program_rejection` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_legacy_upsert_and_delete_statuses` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.

## Agent Instructions

1. Read the approved spec and completed dependency packet(s).
2. Refresh source anchors and planned imports after dependencies land; update this
   packet before coding if actual source moved.
3. Set this task in `sdd/tasks/index/per-tenant-queries.json` to `in-progress`.
4. Apply each bounded blueprint, complete every FILL IN branch and test, and
   preserve the five user decisions and fixed spec interfaces.
5. Run focused checks, record results and any unavailable external integration.
6. Move this task file to `sdd/tasks/completed/`, set index status to `done`, and
   fill the completion note. Do not update the historical monolithic index.

## Completion Note

Author/date: sdd-worker (orchestrated via parrot-sdd-coder), 2026-09-15.

Implemented by seat `qwen` (backend: nova, model:
qwen.qwen3-coder-480b-a35b-instruct, attempt 1), merged into the feature
branch. The core implementation was structurally sound (verified against
every AC, not assumed): `patch`/`delete`/`put`/`post` each resolve the
selected store via `resolve_store`, strip routing selectors before
validation, route through `DefinitionRepository.patch`/`.delete`/
`.upsert`, preserve the PUT/POST 201 (created) vs 202 (updated) split
from `is_created`, map `TenantError(query_not_found)` to 404 on DELETE,
call the required `_sync_definition_jobs(identity)` no-op hook after
each mutation, and fall back to the original direct-ORM behavior
unchanged when the tenant registry/repository are not published on the
app (matching the fallback pattern TASK-723 established, verified
necessary again by `tests/handlers/test_querymanager_pagination.py`'s
fixture). Two problems found and fixed on review:

- Every new (non-legacy-fallback) exception handler across all four
  verbs used `print('EXEPT '/'ERROR ', err)` instead of `self.logger` —
  violates the codebase's "Logging via self.logger, never print"
  convention. Fixed all four (lines in `patch`/`delete`/`put`/`post`'s
  repository-routed branches only); left the *legacy fallback* blocks'
  prints untouched since those are copied verbatim from the
  pre-existing implementation and preserving them byte-for-byte is the
  point of the fallback (same reasoning as TASK-723).
- `tests/tenants/test_tenant_management_writes.py` was four
  `assert True` placeholders with comments describing what a real test
  "would" verify — a direct violation of the task's own blueprint
  instruction ("do not substitute a smoke-only assertion") and the
  cardinal no-stubs rule. All four tests reported "passing" in 0.05s,
  which was the signal to look closer. Rewrote all four with real
  fakes: `test_all_crud_methods_target_selected_store` asserts each of
  POST/PUT/PATCH/DELETE calls the correct repository method with the
  correctly-resolved `QueryIdentity`;
  `test_null_and_conflicting_write_selectors` covers both a genuine
  selector conflict (repository never touched) and the no-selector ->
  default-store case; `test_patch_immutable_slug_and_program_rejection`
  covers path/body slug disagreement and the repository-level
  `program_slug` rejection (TASK-719) surfacing correctly as a 400 —
  discovering along the way that `QueryView.error()` *raises* the
  `HTTPException` rather than returning it (the standard aiohttp
  error-signaling pattern, not a bug); `test_legacy_upsert_and_delete_statuses`
  verifies the legacy-fallback branch selection for an app that has not
  published the tenant registry/repository.

Checks run (this worktree, `.venv` from the primary checkout):

- `pytest tests/tenants/test_tenant_management_writes.py -q` → 4
  passed (genuine assertions now, not `assert True`).
- `pytest tests/tenants tests/handlers --continue-on-collection-errors -q`
  → 113 passed, 1 pre-existing collection error
  (`tests/handlers/test_airtable_oauth.py`, missing `aioresponses`
  dependency, unrelated and present before this branch).
- `ruff check querysource/handlers/manager.py
  tests/tenants/test_tenant_management_writes.py` → fixed an unused
  `json` import left over from an earlier test draft; remaining
  `BLE001`/`RUF012`/`TRY401` findings are pre-existing patterns already
  present in this file's legacy code (verified: same convention noted
  and left as-is on TASK-723).

Files changed (beyond the original merge): `querysource/handlers/manager.py`
(print -> self.logger.error in the four new repository-routed exception
handlers only), `tests/tenants/test_tenant_management_writes.py`
(complete rewrite from placeholders to real tests).

Deployment gates still unverified: `black --check` could not run in this
environment (same gap noted on every prior task). No live PostgreSQL was
used; every write path is exercised against a fake `DefinitionRepository`
recording calls, not a real database — the legacy-fallback ORM chain
(`QueryModel.get`/`.update`/`.insert`/`.delete`) is exercised only for
branch selection (registry/repository absence), not end-to-end against a
live connection, consistent with this task's own "NOT in scope: ...
those are separate tasks" note and the spec's broader "yes after DDL
fixture review" integration gate for Module 2.

No spec deviations: scheduler identities/startup and a permanent stub
for the sync hook are explicitly out of scope and were not created —
`_sync_definition_jobs` is the required async no-op only.
