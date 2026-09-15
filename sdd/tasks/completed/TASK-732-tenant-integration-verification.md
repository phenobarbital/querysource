# TASK-732: Verify PostgreSQL, Redis, HTTP and worker ownership end to end

**Feature**: FEAT-147 — Per-tenant Queries
**Spec**: `sdd/specs/per-tenant-queries.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-731
**Assigned-to**: unassigned

## Context

Implements §4 integration matrix; §5 release checks of the approved spec. FEAT-147 is the task/spec identity;
FEAT-176 is the historical proposal/audit identity. Preserve all five user decisions.
This task runs sequentially in the shared per-spec implementation worktree.

## Scope

- Create isolated opt-in PostgreSQL/Redis fixture infrastructure, using dedicated temporary schemas/database fixtures; never modify developer public rows. Build legacy plus two tenant shapes and an override store.
- Exercise complete CRUD/execution/cache/job lifecycle with the same slug in three stores, cross-schema SQL consumption, read-only/runtime-revoked grants, quoted names and 100 concurrent interleaved operations.
- Add catalog scale fixture with 100 schemas/10,000 definitions; assert no definition preload during discovery and report bounded catalog query count separately from scheduling.
- Cover compiled parser/custom callbacks and program-derived provider fallback without changing explicit targets. Test real worker only with supplied compatible test endpoint; unsupported-worker behavior is mandatory locally.
- Run relevant existing pagination/concurrency/PBAC/multi/scheduler/output suites and report exact commands/results. Record absent external services as unmet release gates, not successful certification.

**NOT in scope**: Unrequested live production tests, migrations or automatic worker deployment.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/tenants/test_integration.py` | CREATE | Tests must exercise the listed ownership/error branches with observable per-store outcomes. |
| `tests/tenants/conftest.py` | CREATE | Explicit service configuration protects real rows and makes absent integration infrastructure visible. |

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
import pytest
import os
import pytest_asyncio
from querysource.services import QuerySource
from querysource.queries import QS, MultiQS
```

Standard-library imports are built in. Existing repository imports resolve through
source definitions/exports below and spec §6. Imports from dependency-created
modules are **planned contracts**, not claims that those modules exist today;
verify them after the dependency lands. Preserve existing imports needed by
untouched code. Before adding any further import, verify its source and update
this packet. Datamodel BaseModel is not Pydantic BaseModel.

### Import Provenance

- `querysource.services.QuerySource` → `querysource/services.py:50`
- `querysource.queries.QS` → `querysource/queries/__init__.py:6`
- `querysource.queries.MultiQS` → `querysource/queries/__init__.py:7`

### Existing Signatures to Use

```text
tests/handlers/conftest.py:152
def seeded_query_slugs() -> list[dict]:

tests/test_queryslug_concurrency.py:79
async def test_new_pattern_is_race_free():

tests/handlers/test_querymanager_pagination.py:275
async def test_default_pagination_returns_envelope(self, test_client, fake_qs_connection, seeded_query_slugs) -> None:
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

1. Create isolated opt-in PostgreSQL/Redis fixture infrastructure, using dedicated temporary schemas/database fixtures; never modify developer public rows. Build legacy plus two tenant shapes and an override store. **Why:** Integration tests must never damage real public rows.
2. Exercise complete CRUD/execution/cache/job lifecycle with the same slug in three stores, cross-schema SQL consumption, read-only/runtime-revoked grants, quoted names and 100 concurrent interleaved operations. **Why:** Mocks cannot establish PostgreSQL schema/grant/loop isolation.
3. Add catalog scale fixture with 100 schemas/10,000 definitions; assert no definition preload during discovery and report bounded catalog query count separately from scheduling. **Why:** Discovery performance depends on metadata rather than row preload.
4. Cover compiled parser/custom callbacks and program-derived provider fallback without changing explicit targets. Test real worker only with supplied compatible test endpoint; unsupported-worker behavior is mandatory locally. **Why:** Runtime program compatibility and remote execution need real boundaries.
5. Run relevant existing pagination/concurrency/PBAC/multi/scheduler/output suites and report exact commands/results. Record absent external services as unmet release gates, not successful certification. **Why:** Skipped external checks are not release certification.

### `tests/tenants/test_integration.py` (CREATE)

```python
"""Verify PostgreSQL, Redis, HTTP and worker ownership end to end regression contracts."""
import pytest

@pytest.mark.asyncio
async def test_http_crud_three_stores_with_override() -> None:
    """http crud three stores with override."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_postgres_redis_revision_and_concurrency() -> None:
    """postgres redis revision and concurrency."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_cross_schema_callbacks_and_grants() -> None:
    """cross schema callbacks and grants."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_scheduler_restart_and_worker_compatibility() -> None:
    """scheduler restart and worker compatibility."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_catalog_scale_no_definition_preload() -> None:
    """catalog scale no definition preload."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError
```

**Why:** Tests must exercise the listed ownership/error branches with observable per-store outcomes.

### `tests/tenants/conftest.py` (CREATE)

```python
"""Opt-in isolated tenant integration fixtures."""
import os
import pytest
import pytest_asyncio

@pytest_asyncio.fixture
async def tenant_services():
    """Provision isolated test metadata/data stores and a dedicated Redis namespace."""
    postgres_dsn = os.environ.get("QS_TEST_POSTGRES_DSN")
    redis_url = os.environ.get("QS_TEST_REDIS_URL")
    if not postgres_dsn or not redis_url:
        pytest.skip("Requires explicit isolated PostgreSQL and Redis test services")
    # FILL IN: provision isolated legacy/two-tenant/override fixtures; yield handles;
    # always clean only objects created by this fixture, including on setup failure.
    raise NotImplementedError
```

**Why:** Explicit service configuration protects real rows and makes absent integration infrastructure visible.

### FILL IN checklist

- [ ] `tests/tenants/test_integration.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `tests/tenants/conftest.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.

## Acceptance Criteria

- [ ] AC-1: Create isolated opt-in PostgreSQL/Redis fixture infrastructure, using dedicated temporary schemas/database fixtures; never modify developer public rows. Build legacy plus two tenant shapes and an override store.
- [ ] AC-2: Exercise complete CRUD/execution/cache/job lifecycle with the same slug in three stores, cross-schema SQL consumption, read-only/runtime-revoked grants, quoted names and 100 concurrent interleaved operations.
- [ ] AC-3: Add catalog scale fixture with 100 schemas/10,000 definitions; assert no definition preload during discovery and report bounded catalog query count separately from scheduling.
- [ ] AC-4: Cover compiled parser/custom callbacks and program-derived provider fallback without changing explicit targets. Test real worker only with supplied compatible test endpoint; unsupported-worker behavior is mandatory locally.
- [ ] AC-5: Run relevant existing pagination/concurrency/PBAC/multi/scheduler/output suites and report exact commands/results. Record absent external services as unmet release gates, not successful certification.
- [ ] AC-6: Focused tests pass: `source .venv/bin/activate && uv run pytest tests/tenants/test_integration.py -q`.
- [ ] New/changed Python code passes black formatting; no unrelated refactoring or dependency additions.

## Test Specification

- `test_http_crud_three_stores_with_override` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_postgres_redis_revision_and_concurrency` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_cross_schema_callbacks_and_grants` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_scheduler_restart_and_worker_compatibility` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_catalog_scale_no_definition_preload` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.

Real PostgreSQL/Redis tests must run with explicit test-service configuration. A skip records an unmet integration gate; it never certifies release readiness. Worker deployment remains external.



### Spec acceptance coverage

| Spec §5 criterion | Task owners |
|---|---|
| U1 API/CRUD | TASK-723, TASK-724, TASK-726, TASK-727, TASK-732 |
| U2 legacy compatibility | TASK-716, TASK-720, TASK-723, TASK-724, TASK-726, TASK-729, TASK-732 |
| U3 persistence/runtime program | TASK-717, TASK-718, TASK-721, TASK-732 |
| U4 allowlist/current policies | TASK-716, TASK-725, TASK-727, TASK-732 |
| U5 cross-schema consumption | TASK-721, TASK-727, TASK-732 |
| Discovery readiness/diagnostics | TASK-716, TASK-720, TASK-732 |
| All definition SQL through repository | TASK-718, TASK-719, TASK-720, TASK-723, TASK-724, TASK-729, TASK-732 |
| Concurrent owner isolation | TASK-719, TASK-722, TASK-729, TASK-730, TASK-732 |
| Children/workers no fallback | TASK-727, TASK-728, TASK-732 |
| Pagination/metadata/export | TASK-718, TASK-723, TASK-732 |
| Revision/old-writer caches | TASK-718, TASK-722, TASK-730, TASK-732 |
| Events/artifacts | TASK-731, TASK-732 |
| Regression/external checks | TASK-732 |
| Catalog scale | TASK-716, TASK-732 |
| Deployment/rollback | TASK-733 |

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

**Author/date**: sdd-worker (orchestrator), 2026-09-15. Implemented directly
("attempt 3") after the merged attempt turned out to be an unfilled stub.

**Dispatch history**: attempt 1 (`mistral`, `mistral.devstral-2-123b`)
completed in 9 turns and merged cleanly (`ba6d8bf`) per the engine's fidelity
checks (file list matched, no unlisted/banned imports) — but its own
summary admitted "Tests are not yet implemented and will fail until filled
in." Both `tests/tenants/conftest.py` and `tests/tenants/test_integration.py`
were the task blueprint's literal FILL IN template, `raise NotImplementedError`
left verbatim in every function. AC-6's exact command would have errored on
every test rather than skipping as the opt-in design requires. Per protocol
this is treated as failed despite `outcome: merged` — a stub is not an
implementation — and reimplemented for real in this worktree (commit
`be2a773`; see that commit message for the full file-by-file breakdown of
`conftest.py`'s `provision_tenant_services()` and the five real
`test_integration.py` tests).

**Checks run** (AC-5 — exact commands and results):
- `source .venv/bin/activate && python -m pytest tests/tenants/test_integration.py -v`
  — **5 skipped** (correct, designed outcome: `QS_TEST_POSTGRES_DSN`/
  `QS_TEST_REDIS_URL` are not set in this sandbox — no network egress at
  all). Directly confirmed (via a standalone run with `-s`) that
  `test_scheduler_restart_and_worker_compatibility`'s mandatory-locally,
  no-external-service `RemoteExecutor` unsupported-contract rejection
  genuinely executes and passes (its warning is logged) BEFORE the rest
  of that test skips.
- `pytest tests/tenants -q` — 70 passed, 5 skipped (no regression to any
  pre-existing tenant test).
- `pytest tests/test_scheduler_core.py tests/test_scheduler_multi_routing.py
  tests/scheduler/ tests/test_scheduler_integration.py
  tests/test_scheduler_handler_integration.py tests/test_scheduler_jobs.py
  tests/tenants tests/handlers/*(excl. test_airtable_oauth.py, pre-existing
  missing aioresponses dep) tests/multi tests/test_abstract_multi.py
  tests/test_local_executor.py tests/test_multi_destinations_subpackage.py
  tests/test_multiqs_column_transforms.py
  tests/test_multiqs_destination_dispatch.py
  tests/test_multiqs_remote_dispatch.py
  tests/test_multiqs_slug_sources_normalize.py
  tests/test_multiqs_sources_integration.py tests/test_threadquery_executor.py
  tests/test_remote_executor.py tests/integration/test_multiquery_output_errors.py
  tests/unit/test_multiqs_output_raise.py tests/unit/test_handler_output_status.py
  tests/unit/test_output_error.py tests/test_table_output_postgres.py
  tests/integration/test_csv_stream_response.py -q` — **362 passed, 5
  skipped, 9 failed**. All 9 failures confirmed pre-existing and unrelated
  (each already individually documented and root-caused in TASK-727/728/
  729/730/731's own completion notes: 5 are `test_remote_executor.py`'s
  pre-existing `QClient` lazy-import patch-target mismatch; the other 4 —
  `test_frozen_dataclass`, `test_guardrail_rejects_too_many_sources`,
  `test_scheduler_not_imported_when_disabled`, `test_post_returns_405` —
  are unrelated env/config/routing quirks).
- Additional PBAC/concurrency sweep for AC-5's own "pagination/concurrency/
  PBAC" mandate: `pytest tests/test_queryslug_concurrency.py
  tests/auth/test_pbac_bootstrap.py tests/datasources/test_datasource_view_pbac.py
  tests/integration/test_pbac_credentials.py tests/integration/test_pbac_enforcement.py
  tests/integration/test_pbac_listing.py tests/services/test_querysource_setup_pbac.py -q`
  — **41 passed, 4 xfailed, 2 failed**.
  - **New finding, reported per AC-5 rather than fixed** (outside this
    task's declared file scope — fixing it would require editing
    `tests/conftest.py`, not `tests/tenants/conftest.py`, and is not
    declared by this task): `tests/integration/test_pbac_enforcement.py::
    TestPbacOff::test_pbac_off_baseline_no_pbac_check` and
    `::test_pbac_off_no_session_needed` both fail with a raw
    `asyncdb.exceptions.exceptions.DriverError: ... Temporary failure in
    name resolution` — the `qs_app_pbac_off` fixture (`tests/conftest.py`)
    never publishes `app["qs_tenant_registry"]`/`app["qs_definition_
    repository"]`, and `QS.build_provider()` (TASK-721) unconditionally
    calls `get_definition_repository()` — which always goes through the
    global `QuerySource()` singleton's own `initialize_tenants()`, with no
    app-level bypass — for ANY slug-based dispatch, tenant feature
    configured or not. Verified this is not a NEW-this-task regression:
    the identical fixture is byte-for-byte unchanged on `dev` (`git show
    dev:tests/conftest.py`), and this sandbox has zero network egress for
    ANY DB connection attempt (confirmed repeatedly across TASK-716
    through TASK-731's own reviews) — the pre-FEAT-147 code path
    (`QueryModel.get()` directly) would have hit the identical "no DB
    reachable" wall in this same sandbox, just via a different error
    message. This is exactly the class of finding AC-5 exists to surface
    ("Record absent external services as unmet release gates, not
    successful certification") rather than paper over with an
    out-of-scope test-infrastructure edit. **Recommend a follow-up task**
    to either mock `get_definition_repository()` in `qs_app_pbac_off`/
    `qs_app_pbac_on` (matching the established pattern from TASK-723's
    pagination-fixture fix) or give `Connection.get_definition_repository()`
    an app-level bypass for tests that never wire up the tenant feature.

**Spec deviations**: none. **Deployment gates unverified** (AC-1/AC-2/AC-3/
AC-4, all opt-in and correctly skipped in this sandbox — genuinely unmet
release gates, not certified by this run):
- Real isolated PostgreSQL + Redis integration (`QS_TEST_POSTGRES_DSN` /
  `QS_TEST_REDIS_URL`) — no network egress in this sandbox.
- Full 100-schema / 10,000-definition catalog scale
  (`QS_TEST_CATALOG_SCALE=1`, additionally opt-in on top of the above).
- Real compatible qworker endpoint (`QS_TEST_WORKER_ENDPOINT`) — this
  repository intentionally does not ship a worker implementation (see
  TASK-728's contract doc), so this gate can only ever be certified against
  an external deployment.
- 100-concurrent-interleaved-operations, cross-schema SQL consumption, and
  read-only/runtime-revoked grants (AC-2) are implemented as real code
  paths in `test_postgres_redis_revision_and_concurrency` /
  `test_cross_schema_callbacks_and_grants` but, like everything above,
  unexercised without real services.
