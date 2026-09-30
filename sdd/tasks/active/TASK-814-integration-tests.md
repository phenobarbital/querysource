# TASK-814: Integration tests — delegated end-to-end, scheduled run-as, append-only audit

**Feature**: FEAT-159 — OneDrive Source for MultiQS
**Spec**: `sdd/specs/onedrive-multiqs-source.spec.md`
**Status**: pending
**Priority**: medium
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-811, TASK-812, TASK-813
**Assigned-to**: unassigned

---

## Context

Spec §4 Integration Tests and §5 ACs. These are cross-module tests that prove the
pieces compose:

- HTTP MultiQS with a vault session → `prepare` → thread → mocked Graph download;
- a schedule saved by user 42 → run-as column + audit → the scheduler job
  resolves user 42's identity through a mocked IdP;
- (opt-in PG) the audit table rejects UPDATE and DELETE.

---

## Scope

- `tests/multi/test_onedrive_delegated_e2e.py`: build a MultiQS with a
  `sources: [{OneDriveSource: {auth: delegated, source: {filename: …}}}]`
  query dict and `identity_context=SourceIdentityContext(user_id=42,
  session=<fake vault session>, auth=<fake auth>)`. Patch the Graph client,
  httpx and `cached_credential`, and assert a DataFrame result.
- `tests/scheduler/test_scheduled_delegated_run_as.py`:
  - repository `patch` with `run_as_actor=42` on a mock tx conn → `_apply_run_as` set;
  - the scheduler registers the job with `run_as_user_id=42`;
  - `scheduled_multiqs_job` → `MultiQS` with `identity_context.user_id == 42`,
    and a mocked IdP `get_user_identity_credential(42, "onedrive", auto_refresh=True)`.
  - An opt-in `test_run_as_audit_append_only` is guarded by
    `QS_TEST_POSTGRES_DSN` and uses the tenant fixtures from TASK-810.

**NOT in scope**: new production code. If a test exposes a bug, fix it in the
owning module's file and note it in the Completion Note.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/multi/test_onedrive_delegated_e2e.py` | CREATE | request-path end-to-end (mocked) |
| `tests/scheduler/test_scheduled_delegated_run_as.py` | CREATE | scheduler path + opt-in PG audit |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.queries.multi import MultiQS                                  # verified: queries/multi/__init__.py:101
from querysource.auth.identity_tokens import SourceIdentityContext             # TASK-805
from querysource.queries.multi.sources import OneDriveSource, SOURCE_REGISTRY  # TASK-813
from querysource.repositories.definitions import DefinitionRepository, RUN_AS_COLUMN  # TASK-808/809
from querysource.scheduler.jobs import scheduled_multiqs_job                   # verified: jobs.py:104
```

### Existing Signatures to Use
```python
# tests/tenants/conftest.py — opt-in fixtures gated by QS_TEST_POSTGRES_DSN (provision_tenant_services / tenant_services)
# tests/test_scheduler_multi_routing.py:66-74 — _make_sched_mocked() pattern
# MultiQS(query={"sources": [...]}, identity_context=...)   # TASK-806 (sources key parsed :145-151)
# navigator_auth.identity.store.cached_credential — patch target: "navigator_auth.identity.store.cached_credential"
```

### Does NOT Exist
- ~~Real Microsoft Graph calls in tests~~: always mocked.
- ~~A default PG in CI~~: PG tests must skip without `QS_TEST_POSTGRES_DSN`.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "tests/multi/test_onedrive_delegated_e2e.py", "action": "CREATE"},
    {"path": "tests/scheduler/test_scheduled_delegated_run_as.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/queries/multi/__init__.py#MultiQS",
    "sym:querysource/scheduler/jobs.py#scheduled_multiqs_job"
  ]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Write the request-path e2e test — *why*: AC "resolved in prepare, only `StaticTokenCredential` in the thread".
2. Write the scheduler-path test — *why*: AC "scheduled run-as".
3. Write the opt-in PG append-only test — *why*: S7.

### `tests/multi/test_onedrive_delegated_e2e.py` (CREATE)
```python
"""FEAT-159 request-path integration: vault → prepare → thread → Graph (mocked)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

from querysource.auth.identity_tokens import SourceIdentityContext
from querysource.queries.multi import MultiQS


async def test_onedrive_delegated_end_to_end_mocked():
    # FILL IN: fake session {"session": {"user_id": 42}} ; patch cached_credential → non-expiring cred ;
    #          patch GraphServiceClient + httpx.AsyncClient → CSV bytes ; assert result DataFrame and that
    #          the credential passed to GraphServiceClient is a StaticTokenCredential
    ...
```

### `tests/scheduler/test_scheduled_delegated_run_as.py` (CREATE)
```python
"""FEAT-159 scheduler-path integration + opt-in PG append-only audit."""
import os

import pytest


async def test_scheduled_delegated_run_as():
    ...  # FILL IN: see Scope bullet 2


@pytest.mark.skipif(not os.environ.get("QS_TEST_POSTGRES_DSN"), reason="needs QS_TEST_POSTGRES_DSN")
async def test_run_as_audit_append_only():
    ...  # FILL IN: insert one audit row; UPDATE and DELETE raise "run-as audit is append-only"
```

### FILL IN checklist
- [ ] e2e request path
- [ ] scheduler path
- [ ] PG append-only (opt-in)

---

## Acceptance Criteria

- [ ] Both files pass locally. The PG test skips without the DSN.
- [ ] The request-path test proves `prepare()` ran before the thread, and that the thread used a `StaticTokenCredential`.

## Validation Commands

- `pytest tests/multi/test_onedrive_delegated_e2e.py -q`
- `pytest tests/scheduler/test_scheduled_delegated_run_as.py -q`

---

## Test Specification

See the blueprint blocks above. They are the test scaffold.

---

## Agent Instructions

1. Work in the feature worktree (`python -m scripts.sdd.ensure_worktree --slug onedrive-multiqs-source --feature-id FEAT-159`).
2. Read the spec. Check that every `Depends-on` task is `"done"` in `sdd/tasks/index/onedrive-multiqs-source.json`.
3. Verify the Codebase Contract before writing code.
4. Set the task to `"in-progress"` in the index (with `started_at`), then implement from the blueprint and complete every `# FILL IN:`.
5. Run the Validation Commands. Commit only the listed files.
6. Close with `scripts/sdd/close_task.sh TASK-814 onedrive-multiqs-source verified`, then fill in the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*
