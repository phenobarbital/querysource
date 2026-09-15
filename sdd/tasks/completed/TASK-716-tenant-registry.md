# TASK-716: Implement immutable store identities and catalog discovery

**Feature**: FEAT-147 — Per-tenant Queries
**Spec**: `sdd/specs/per-tenant-queries.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: none
**Assigned-to**: unassigned

## Context

Implements M1; §2 registry/owner resolution of the approved spec. FEAT-147 is the task/spec identity;
FEAT-176 is the historical proposal/audit identity. Preserve all five user decisions.
This task runs sequentially in the shared per-spec implementation worktree.

## Scope

- Create the five identity/page/envelope types and registry in tenants.py. Derive the database namespace from main DB host/port/database without secrets; physical aliases deduplicate.
- Discover base tables named queries with query_slug using information_schema, then inspect full fields/types and slug non-null uniqueness; match catalog/schema/table. Use bounded catalog reads, not per-definition reads.
- Apply None/empty/exact allowlists, system/reserved/slash exclusions, quoted/case-sensitive names and atomic publication. Keep configured legacy contract distinct. Read-only stores remain readable; skip incompatible candidates with reasons.
- Use a registry-local configuration snapshot and structured logger. Resolve None to configured default; explicit public is literal and allowlisted. Raise TenantError with spec machine code/status metadata while preserving numeric QueryException.code; do not introduce a required external error library.

**NOT in scope**: Server startup wiring, repository CRUD, schema creation, runtime refresh and new membership policies.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/tenants.py` | CREATE | Immutable identity prevents coroutine/worker routing changes; catalog eligibility must precede any definition access. |
| `tests/tenants/test_tenant_registry.py` | CREATE | Tests must exercise the listed ownership/error branches with observable per-store outcomes. |
| `querysource/tenant_errors.py` | CREATE | QueryException.code is numeric; preserve it while exposing the spec machine error code separately. |

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence, TypedDict
from asyncdb.drivers.pg import pg
from querysource.models import QueryModel
import pytest
from querysource.exceptions import QueryException
```

Standard-library imports are built in. Existing repository imports resolve through
source definitions/exports below and spec §6. Imports from dependency-created
modules are **planned contracts**, not claims that those modules exist today;
verify them after the dependency lands. Preserve existing imports needed by
untouched code. Before adding any further import, verify its source and update
this packet. Datamodel BaseModel is not Pydantic BaseModel.

### Import Provenance

- `querysource.models.QueryModel` → `querysource/models.py:48`
- `querysource.exceptions.QueryException` → `querysource/exceptions.py:6`

### Existing Signatures to Use

```text
querysource/models.py:48
class QueryModel(Model):

querysource/conf.py:353
QS_QUERIES_SCHEMA = config.get('QS_QUERIES_SCHEMA', fallback='public')

querysource/datasources/introspection.py:106
class AnsiSQLIntrospector(Introspector):
def __init__(self, count_expr: str, excluded_schemas: tuple[str, ...]) -> None:
```

Error contract verified: `querysource/exceptions.py:11` defines
`QueryException.__init__(self, message: str, code: int = 0, **kwargs)` and converts
code to int. Never pass a machine-code string to that parameter.

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

1. Create the five identity/page/envelope types and registry in tenants.py. Derive the database namespace from main DB host/port/database without secrets; physical aliases deduplicate. **Why:** Physical aliases must identify the same persisted definitions.
2. Discover base tables named queries with query_slug using information_schema, then inspect full fields/types and slug non-null uniqueness; match catalog/schema/table. Use bounded catalog reads, not per-definition reads. **Why:** Catalog reads must scale with metadata, not definition count.
3. Apply None/empty/exact allowlists, system/reserved/slash exclusions, quoted/case-sensitive names and atomic publication. Keep configured legacy contract distinct. Read-only stores remain readable; skip incompatible candidates with reasons. **Why:** A partial or broadened registry would route queries incorrectly.
4. Use a registry-local configuration snapshot and structured logger. Resolve None to configured default; explicit public is literal and allowlisted. Raise TenantError with spec machine code/status metadata while preserving numeric QueryException.code; do not introduce a required external error library. **Why:** Legacy clients must remain available without adding dependencies.

### `querysource/tenants.py` (CREATE)

```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence, TypedDict
from asyncdb.drivers.pg import pg
from querysource.models import QueryModel

@dataclass(frozen=True)
class QueryStore:
    """Canonical physical identity and validated persistence contract."""
    database_namespace: str
    schema: str
    table: str
    contract: Literal['legacy', 'tenant']
    columns: frozenset[str]

@dataclass(frozen=True)
class QueryIdentity:
    """A saved definition identity; aliases never replace its slug."""
    store: QueryStore
    slug: str

@dataclass(frozen=True)
class LoadedDefinition:
    """Detached runtime model plus immutable persisted-revision identity."""
    identity: QueryIdentity
    runtime: QueryModel
    revision: str

@dataclass(frozen=True)
class DefinitionPage:
    """Persisted projections and total matching the same validated filters."""
    rows: tuple[Mapping[str, Any], ...]
    total: int

class TenantOwnerEnvelope(TypedDict):
    """Versioned remote/job identity; no database secrets or connections."""
    version: Literal[1]
    database_namespace: str
    schema: str
    table: str
    contract: Literal['legacy', 'tenant']

class TenantRegistry:
    """One immutable published snapshot per QuerySource initialization."""

    async def discover(self, conn: pg, *, allowlist: Sequence[str] | None) -> None:
        """Publish compatible stores atomically; scan failure publishes nothing."""
        # FILL IN: Apply spec §2 discovery/allowlist matrix atomically; keep default store independently available.
        raise NotImplementedError

    def resolve(self, tenant: str | None=None) -> QueryStore:
        """Resolve exact selector; never fall back from an explicit name."""
        # FILL IN: Apply spec §2 discovery/allowlist matrix atomically; keep default store independently available.
        raise NotImplementedError

    def stores(self) -> tuple[QueryStore, ...]:
        """Return unique eligible physical stores, including configured default."""
        # FILL IN: Apply spec §2 discovery/allowlist matrix atomically; keep default store independently available.
        raise NotImplementedError

    def diagnostics(self) -> tuple[Mapping[str, Any], ...]:
        """Return administrative discovery reasons without secrets."""
        # FILL IN: Apply spec §2 discovery/allowlist matrix atomically; keep default store independently available.
        raise NotImplementedError

def quote_identifier(value: str) -> str:
    """Quote one validated identifier, preserving case and embedded quotes."""
    # FILL IN: Reject NUL; double embedded quotes; preserve exact identifiers per spec §2.
    raise NotImplementedError
```

**Why:** Immutable identity prevents coroutine/worker routing changes; catalog eligibility must precede any definition access.

### `tests/tenants/test_tenant_registry.py` (CREATE)

```python
"""Implement immutable store identities and catalog discovery regression contracts."""
import pytest

@pytest.mark.asyncio
async def test_allowlist_none_empty_exact_and_duplicates() -> None:
    """allowlist none empty exact and duplicates."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_discovery_tables_views_marker_shape_grants() -> None:
    """discovery tables views marker shape grants."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_quoted_names_and_default_alias_dedup() -> None:
    """quoted names and default alias dedup."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_scan_failure_has_no_partial_snapshot() -> None:
    """scan failure has no partial snapshot."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError
```

**Why:** Tests must exercise the listed ownership/error branches with observable per-store outcomes.


### `querysource/tenant_errors.py` (CREATE)

```python
"""Stable errors for explicit query ownership boundaries."""
from querysource.exceptions import QueryException

OWNERSHIP_STATUS = {
    "invalid_tenant": 400,
    "tenant_not_available": 404,
    "query_not_found": 404,
    "tenant_store_unavailable": 503,
    "tenant_write_forbidden": 403,
    "tenant_worker_unsupported": 502,
}

class TenantError(QueryException):
    """Separate a machine-readable owner error from the existing numeric code."""
    def __init__(self, message: str, *, error_code: str) -> None:
        super().__init__(message, code=OWNERSHIP_STATUS[error_code])
        self.error_code = error_code
```

**Why:** QueryException.code is numeric; preserve it while exposing the spec machine error code separately.

### FILL IN checklist

- [ ] `querysource/tenant_errors.py` — implement bounded branch/wiring and validate the declared contract.
- [ ] `querysource/tenants.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `tests/tenants/test_tenant_registry.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.

## Acceptance Criteria

- [ ] AC-1: Create the five identity/page/envelope types and registry in tenants.py. Derive the database namespace from main DB host/port/database without secrets; physical aliases deduplicate.
- [ ] AC-2: Discover base tables named queries with query_slug using information_schema, then inspect full fields/types and slug non-null uniqueness; match catalog/schema/table. Use bounded catalog reads, not per-definition reads.
- [ ] AC-3: Apply None/empty/exact allowlists, system/reserved/slash exclusions, quoted/case-sensitive names and atomic publication. Keep configured legacy contract distinct. Read-only stores remain readable; skip incompatible candidates with reasons.
- [ ] AC-4: Use a registry-local configuration snapshot and structured logger. Resolve None to configured default; explicit public is literal and allowlisted. Raise TenantError with spec machine code/status metadata while preserving numeric QueryException.code; do not introduce a required external error library.
- [ ] AC-5: Focused tests pass: `source .venv/bin/activate && uv run pytest tests/tenants/test_tenant_registry.py -q`.
- [ ] New/changed Python code passes black formatting; no unrelated refactoring or dependency additions.

## Test Specification

- `test_allowlist_none_empty_exact_and_duplicates` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_discovery_tables_views_marker_shape_grants` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_quoted_names_and_default_alias_dedup` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_scan_failure_has_no_partial_snapshot` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.

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

Implemented by seat `glm` (backend: nova, model: zai.glm-4.7-flash), merged
into the feature branch, then hardened by the orchestrator after the merged
code failed its own focused test run:

- `NameError: TypedDict is not defined` — missing import, fixed.
- `TenantRegistry.discover()` used `async with conn.cursor() as cur:` with a
  zero-arg `cursor()` call — not a real method of `asyncdb.drivers.pg.pg`
  (its `cursor(sentence, params)` is a coroutine, not directly usable as an
  async context manager without `await`). Replaced with the established
  `result, error = await conn.query(sentence)` contract already used by
  `querysource/datasources/introspection.py::AnsiSQLIntrospector._run`
  (explicitly referenced in this task's Codebase Contract), wrapped in
  try/except so a scan failure leaves no partial snapshot (state is already
  reset at the top of `discover()`).
- Updated the task's own `MockConn` fixtures in
  `tests/tenants/test_tenant_registry.py` to the same `query()` contract.
  Fixed a fixture that asserted `diagnostics() > 0` while providing no
  excludable schema (added a `management` reserved-schema row), and fixed
  a `quote_identifier()` expected literal that was missing its closing
  quote (`'"tenant""1""'` → `'"tenant""1"""'`, verified against the actual
  double-embedded-quote + outer-wrap logic in `_quote_identifier`).
- Addressed ruff findings on the touched files (UP006/UP035/I001/F401/
  BLE001/TRY002); `except Exception` is intentional and kept with a
  `# noqa: BLE001` justification matching the existing convention in
  `introspection.py`.

Checks run (this worktree, `.venv` from the primary checkout, Cython
extensions rebuilt in-worktree via `make build-inplace` — required because
`.so` build artifacts are gitignored and a fresh worktree has none):

- `pytest tests/tenants/test_tenant_registry.py -q` → 4 passed.
- `ruff check querysource/tenants.py querysource/tenant_errors.py
  tests/tenants/test_tenant_registry.py` → clean.

Files changed (beyond the original merge): `querysource/tenants.py`,
`tests/tenants/test_tenant_registry.py`. `querysource/tenant_errors.py`
required no changes.

Deployment gates still unverified: `black --check` could not run in this
environment (`/home/jesuslara/.local/bin/black` is not executable here);
not part of this task's own toolchain failure, left unverified rather than
guessed at. No live PostgreSQL integration test was run — `discover()` was
only exercised against the mock `query()` contract; the real
`asyncdb.drivers.pg.pg.query()` signature/return shape was verified by
reading `asyncdb`'s installed source, not by a live DB call.

No spec deviations from the approved Implementation Blueprint — all five
identity/page/envelope types, the registry, and `quote_identifier` match
the blueprint's fixed interfaces.
