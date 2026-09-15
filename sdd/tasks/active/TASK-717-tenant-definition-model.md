# TASK-717: Define tenant persistence validation and runtime compatibility

**Feature**: FEAT-147 — Per-tenant Queries
**Spec**: `sdd/specs/per-tenant-queries.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-716
**Assigned-to**: unassigned

## Context

Implements M2; §2 persistence/runtime shape of the approved spec. FEAT-147 is the task/spec identity;
FEAT-176 is the historical proposal/audit identity. Preserve all five user decisions.
This task runs sequentially in the shared per-spec implementation worktree.

## Scope

- Declare all QueryModel fields except program_slug in TenantQueryDefinition; retain exact defaults, validation and JSON/array metadata. Keep program_id required with default 1 pending deployment DDL review.
- Do not subclass QueryModel to remove inherited fields and do not mutate QueryModel.Meta. Runtime adaptation in the repository will construct a fresh QueryModel with the tenant schema as program_slug.
- Reject program_slug in tenant write input before model construction, because permissive model extras must not silently accept routing/runtime fields. Verify timestamp factories and required/default behavior against the legacy model.

**NOT in scope**: Production DDL migration and repository I/O.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/tenant_models.py` | CREATE | An independent plain datamodel keeps tenant persistence separate from the unchanged legacy ORM shape. |
| `tests/tenants/test_tenant_definition_model.py` | CREATE | Tests must exercise the listed ownership/error branches with observable per-store outcomes. |

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
from __future__ import annotations
from typing import List, Optional
from datetime import datetime
from datamodel import BaseModel, Field
from querysource.models import rigth_now
import pytest
from querysource.models import QueryModel, rigth_now
```

Standard-library imports are built in. Existing repository imports resolve through
source definitions/exports below and spec §6. Imports from dependency-created
modules are **planned contracts**, not claims that those modules exist today;
verify them after the dependency lands. Preserve existing imports needed by
untouched code. Before adding any further import, verify its source and update
this packet. Datamodel BaseModel is not Pydantic BaseModel.

### Import Provenance

- `querysource.models.rigth_now` → `querysource/models.py:15`
- `querysource.models.QueryModel` → `querysource/models.py:48`

### Existing Signatures to Use

```text
querysource/models.py:48
class QueryModel(Model):

querysource/models.py:15
def rigth_now(obj) -> datetime:
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

1. Declare all QueryModel fields except program_slug in TenantQueryDefinition; retain exact defaults, validation and JSON/array metadata. Keep program_id required with default 1 pending deployment DDL review. **Why:** Tenant SQL must never request a removed program_slug column.
2. Do not subclass QueryModel to remove inherited fields and do not mutate QueryModel.Meta. Runtime adaptation in the repository will construct a fresh QueryModel with the tenant schema as program_slug. **Why:** Shared ORM metadata would leak schema selection across coroutines.
3. Reject program_slug in tenant write input before model construction, because permissive model extras must not silently accept routing/runtime fields. Verify timestamp factories and required/default behavior against the legacy model. **Why:** Validation must distinguish unsupported input from ignored extras.

### `querysource/tenant_models.py` (CREATE)

```python
from __future__ import annotations
from typing import List, Optional
from datetime import datetime
from datamodel import BaseModel, Field
from querysource.models import rigth_now

class TenantQueryDefinition(BaseModel):
    query_slug: str = Field(required=True, primary_key=True)
    description: str = Field(required=False, default=None)
    source: Optional[str] = Field(required=False)
    params: Optional[dict] = Field(required=False, db_type='jsonb', default_factory=dict)
    attributes: Optional[dict] = Field(required=False, db_type='jsonb', default_factory=dict, comment='Optional Attributes for Query')
    conditions: Optional[dict] = Field(required=False, db_type='jsonb', default_factory=dict)
    cond_definition: Optional[dict] = Field(required=False, db_type='jsonb', default_factory=dict)
    fields: List[str] = Field(required=False, db_type='array', default_factory=list)
    filtering: Optional[dict] = Field(required=False, db_type='jsonb', default_factory=dict)
    ordering: List[str] = Field(required=False, db_type='array', default_factory=list)
    grouping: List[str] = Field(required=False, db_type='array', default_factory=list)
    qry_options: Optional[dict] = Field(required=False, db_type='jsonb', default_factory=dict)
    h_filtering: bool = Field(required=False, default=False, comment='filtering based on Hierarchical rules.')
    query_raw: str = Field(required=False)
    is_raw: bool = Field(required=False, default=False)
    is_cached: bool = Field(required=False, default=True)
    provider: str = Field(required=False, default='db')
    parser: str = Field(required=False, default='SQLParser', comment='Parser to be used for parsing Query.')
    cache_timeout: int = Field(required=True, default=3600)
    cache_refresh: int = Field(required=True, default=0)
    cache_options: Optional[dict] = Field(required=False, db_type='jsonb', default_factory=dict)
    program_id: int = Field(required=True, default=1)
    dwh: bool = Field(required=True, default=False)
    dwh_driver: str = Field(required=False, default=None)
    dwh_info: Optional[dict] = Field(required=False, db_type='jsonb')
    dwh_scheduler: Optional[dict] = Field(required=False, db_type='jsonb')
    created_at: datetime = Field(required=False, default=datetime.now, db_default='now()')
    created_by: int = Field(required=False)
    updated_at: datetime = Field(required=False, default=datetime.now, encoder=rigth_now)
    updated_by: int = Field(required=False)
```

**Why:** An independent plain datamodel keeps tenant persistence separate from the unchanged legacy ORM shape.

### `tests/tenants/test_tenant_definition_model.py` (CREATE)

```python
"""Define tenant persistence validation and runtime compatibility regression contracts."""
import pytest

@pytest.mark.asyncio
async def test_tenant_columns_exclude_program_slug() -> None:
    """tenant columns exclude program slug."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_defaults_and_required_program_id() -> None:
    """defaults and required program id."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_json_array_datetime_validation() -> None:
    """json array datetime validation."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_legacy_model_meta_unchanged() -> None:
    """legacy model meta unchanged."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError
```

**Why:** Tests must exercise the listed ownership/error branches with observable per-store outcomes.

### FILL IN checklist

- [ ] `querysource/tenant_models.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `tests/tenants/test_tenant_definition_model.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.

## Acceptance Criteria

- [ ] AC-1: Declare all QueryModel fields except program_slug in TenantQueryDefinition; retain exact defaults, validation and JSON/array metadata. Keep program_id required with default 1 pending deployment DDL review.
- [ ] AC-2: Do not subclass QueryModel to remove inherited fields and do not mutate QueryModel.Meta. Runtime adaptation in the repository will construct a fresh QueryModel with the tenant schema as program_slug.
- [ ] AC-3: Reject program_slug in tenant write input before model construction, because permissive model extras must not silently accept routing/runtime fields. Verify timestamp factories and required/default behavior against the legacy model.
- [ ] AC-4: Focused tests pass: `source .venv/bin/activate && uv run pytest tests/tenants/test_tenant_definition_model.py -q`.
- [ ] New/changed Python code passes black formatting; no unrelated refactoring or dependency additions.

## Test Specification

- `test_tenant_columns_exclude_program_slug` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_defaults_and_required_program_id` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_json_array_datetime_validation` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_legacy_model_meta_unchanged` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.

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

To be completed by the implementing agent: author/date, exact checks and results,
files changed, deployment gates still unverified, and any approved spec deviations.
