# TASK-755: End-to-end principal test with real setup_pbac + scheduler regression guard

**Feature**: FEAT-150 — PBAC for Request-less (Programmatic) QS Callers
**Spec**: `sdd/specs/pbac-request-credentials.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-753
**Assigned-to**: unassigned

---

## Context

Implements spec §4 **Integration Tests** (`test_qs_principal_end_to_end_policy_dir`) and
the `test_scheduler_jobs_unchanged` row. The unit tests in TASK-750 and TASK-753 mock
the evaluator. This task proves the whole chain with the **real** navigator-auth engine:
`setup_pbac` → runtime handle → `enforce_principal` → `QS.build_provider`. It also guards
that the scheduler keeps constructing `QS`/`MultiQS` **without** a principal (spec AC:
the scheduler is unchanged).

---

## Scope

- `tests/integration/test_qs_principal_e2e.py`:
  - Build a `web.Application()`, write a temp policy dir that allows group `sales` `slug:execute` on `slug:report_a` only, and call the real `setup_pbac(app, policy_dir=tmp)`.
  - Build `QS(slug=..., principal=...)` with the definition repository and `get_provider` faked.
  - Assert `report_a` reaches `get_provider` and `report_b` raises `QueryAccessDenied`.
  - Skip when the Rust engine is unavailable.
- `tests/scheduler/test_jobs_no_principal.py`: with `QS`/`MultiQS` patched in the scheduler job paths, assert no `principal` kwarg is ever passed.

**NOT in scope**: production code changes.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/integration/test_qs_principal_e2e.py` | CREATE | real-evaluator end-to-end |
| `tests/scheduler/test_jobs_no_principal.py` | CREATE | scheduler regression guard |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from aiohttp import web
from querysource.auth.pbac import setup_pbac, clear_pbac_runtime   # setup_pbac verified: auth/pbac.py:28; clear_* by TASK-749
from querysource.auth.principal import QSPrincipal                 # TASK-748
from querysource.exceptions import QueryAccessDenied               # TASK-748
from querysource.queries.qs import QS                              # queries/qs.py
from querysource.scheduler import jobs                             # verified: querysource/scheduler/jobs.py
```

### Existing Signatures to Use
```python
# querysource/scheduler/jobs.py — request-less constructions that must stay principal-free
QS(slug=slug, tenant=tenant)        # :87
MultiQS(slug=slug, tenant=tenant)   # :144
QS(slug=slug, tenant=tenant)        # :190
# tests/test_scheduler_jobs.py — existing pattern: @patch("querysource.queries.qs.QS") and call the job coroutine

# tests/policies/test_authorized_policy.py:17-26 — Rust engine availability gate
from navigator_auth.abac.policies.evaluator import PolicyEvaluator, _RS_PEP_AVAILABLE

# tests/tenants/test_tenant_execution_context.py:133-190 — QS with faked repository:
#   qs.get_definition_repository = <async fake returning object with .registry.resolve and async .get>
#   qs.connection.get_provider = <async fake(objquery, session=None, app=None)>
```

### Does NOT Exist
- ~~A live database in this test~~: repository and provider are faked. Only the policy engine is real.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "tests/integration/test_qs_principal_e2e.py", "action": "CREATE"},
    {"path": "tests/scheduler/test_jobs_no_principal.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/auth/pbac.py#setup_pbac"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Read the policy YAML shape from `policies/authorized.yaml` (keys `name`, `effect`, `resources`, `actions`, `subjects.groups`) and mirror it. Do not invent keys.
- Clear the runtime handle in an autouse fixture before and after each test, because the handle is process-global.
- The scheduler guard must patch the names exactly as `jobs.py` imports them. Read `jobs.py` first, and patch `querysource.queries.qs.QS` the same way `tests/test_scheduler_jobs.py` does.

---

## Implementation Blueprint

### Steps (in order)
1. Read `querysource/scheduler/jobs.py` and `policies/authorized.yaml` — *why*: the test must patch the real import sites and use a valid policy shape.
2. Write the end-to-end test with the skip gate — *why*: CI without the Rust engine must stay green (spec §4).
3. Write the scheduler guard — *why*: spec AC says the scheduler is unchanged.

### `tests/integration/test_qs_principal_e2e.py` (CREATE)
```python
"""FEAT-150 end-to-end: real setup_pbac + real Rust evaluator + QS(principal=...)."""
import pytest
from aiohttp import web

from querysource.auth.pbac import clear_pbac_runtime, setup_pbac
from querysource.auth.principal import QSPrincipal
from querysource.exceptions import QueryAccessDenied
from querysource.queries.qs import QS


def _evaluator_available() -> bool:
    try:
        from navigator_auth.abac.policies.evaluator import _RS_PEP_AVAILABLE  # noqa: F401
        return bool(_RS_PEP_AVAILABLE)
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _evaluator_available(), reason="rs_pep evaluator not available")


@pytest.fixture(autouse=True)
def _reset_runtime():
    clear_pbac_runtime()
    yield
    clear_pbac_runtime()


@pytest.fixture
def pbac_app(tmp_path):
    # FILL IN: write tmp_path/"test.yaml" allowing group "sales" slug:execute on "slug:report_a" only
    #          (mirror policies/authorized.yaml shape); call setup_pbac(web.Application(), policy_dir=str(tmp_path))
    #          and assert it returned a non-None evaluator
    raise NotImplementedError


def _fake_qs(slug: str, principal: QSPrincipal) -> tuple[QS, list]:
    """Build QS with a faked definition repository and get_provider; return (qs, provider_calls)."""
    # FILL IN: follow tests/tenants/test_tenant_execution_context.py:133-190
    raise NotImplementedError


async def test_allowed_slug_reaches_provider(pbac_app): ...
async def test_denied_slug_raises(pbac_app): ...
async def test_authz_principal_follows_flag(pbac_app, monkeypatch): ...   # flag off → denied
```

### `tests/scheduler/test_jobs_no_principal.py` (CREATE)
```python
"""FEAT-150 guard: scheduler jobs never pass principal= (trusted-service model unchanged)."""
from unittest.mock import AsyncMock, MagicMock, patch

# FILL IN: for each job coroutine in querysource/scheduler/jobs.py that builds QS/MultiQS (:87, :144, :190),
#          patch the class as tests/test_scheduler_jobs.py does, run the job, and assert
#          "principal" not in mock_cls.call_args.kwargs
```

### FILL IN checklist
- [ ] Policy YAML mirroring `policies/authorized.yaml`
- [ ] `_fake_qs` following the tenant execution-context pattern
- [ ] Scheduler guard for all three construction sites

---

## Acceptance Criteria

- [ ] AC-1: with the Rust engine present, `report_a` reaches the faked `get_provider` with `session=None, app=None`, and `report_b` raises `QueryAccessDenied`.
- [ ] AC-2: an authz principal is denied with `QS_PBAC_ALLOW_SESSIONLESS_AUTHZ` false.
- [ ] AC-3: without the Rust engine the module is skipped, not failed.
- [ ] AC-4: the scheduler guard asserts no `principal` kwarg at all three construction sites.
- [ ] AC-5: `tests/test_scheduler_jobs.py` passes unmodified.
- [ ] AC-6: `ruff check` on both new files is clean.

---

## Validation Commands

- `pytest tests/integration/test_qs_principal_e2e.py -q`
- `pytest tests/scheduler/test_jobs_no_principal.py -q`
- `pytest tests/test_scheduler_jobs.py -q`

---

## Test Specification

See the blueprint blocks: they are the test files themselves.

---

## Agent Instructions

1. Confirm TASK-753 is completed. TASK-749 and TASK-750 are transitive prerequisites.
2. Implement, then run the Validation Commands and `ruff check`.
3. Move this file to `sdd/tasks/completed/`, set the index status to `done`, and fill in the Completion Note, including whether the e2e ran or was skipped.

---

## Completion Note

The Rust `rs_pep` evaluator IS installed/available in this environment
(`navigator_auth.abac.policies.evaluator._RS_PEP_AVAILABLE` is True), so
**the end-to-end suite ran for real, it was not skipped**.

`tests/integration/test_qs_principal_e2e.py`: real `setup_pbac(app,
policy_dir=tmp)` with a policy YAML (mirroring `policies/authorized.yaml`'s
shape) granting group `sales` `slug:execute` on `slug:report_a` only; `QS`
built with a faked `get_definition_repository` (returns a fake repo/store
resolving to a `LoadedDefinition`, pattern from
`tests/tenants/test_tenant_execution_context.py:133-190`) and a faked
`connection.get_provider` recording its calls. 3 tests: `report_a` reaches
`get_provider` with `session=None, app=None` (AC-1); `report_b` raises
`QueryAccessDenied` before `get_provider` is ever called; an authz
principal (`QSPrincipal.for_authz("ip")`) with
`QS_PBAC_ALLOW_SESSIONLESS_AUTHZ` patched to `False` is denied without
`check_access` ever running (AC-2). All 3 pass against the real evaluator.

`tests/scheduler/test_jobs_no_principal.py`: patches
`querysource.queries.qs.QS` for `scheduled_query_job` and
`cache_refresh_job`, and `querysource.queries.MultiQS` for
`scheduled_multiqs_job` (exactly the import sites `tests/test_scheduler_jobs.py`
already patches), asserting `"principal" not in mock_cls.call_args.kwargs`
for all three of `jobs.py`'s construction sites (`:87`, `:144`, `:190`).
3 tests pass — the scheduler stays principal-free.

`tests/test_scheduler_jobs.py` passes unmodified (14 tests, AC-5).
`ruff check` clean on both new files (AC-6). No production code touched
(out of scope, per this task).

**Completed by**: sdd-worker (Sonnet)
**Date**: 2026-09-24
