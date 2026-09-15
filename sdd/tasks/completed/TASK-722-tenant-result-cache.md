# TASK-722: Apply revision-scoped keys to every result cache boundary

**Feature**: FEAT-147 — Per-tenant Queries
**Spec**: `sdd/specs/per-tenant-queries.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2–4h)
**Depends-on**: TASK-721
**Assigned-to**: unassigned

## Context

Implements M3 result cache of the approved spec. FEAT-147 is the task/spec identity;
FEAT-176 is the historical proposal/audit identity. Preserve all five user decisions.
This task runs sequentially in the shared per-spec implementation worktree.

## Scope

- Implement AbstractQuery.result_cache_key by composing immutable loaded identity/revision with provider checksum. Wrap exactly once at read/write call sites.
- Update QS lookup/write/refresh paths plus AbstractQuery threaded writes to carry the same composed key. Preserve existing TTL/refresh options and every provider-specific checksum input.
- Do not dual-read unqualified keys, including legacy keys; document/test the intentional cold cache transition. Old in-flight writers only write their captured revision.
- Read definitions before cache access so edits, deletes and external SQL mutations cannot be served from a stale slug-only result. Physical-store aliases share keys; different stores never share identical-SQL results.

**NOT in scope**: User-specific cache redesign and source-table change invalidation.

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/interfaces/queries.py` | MODIFY | Capture identity before thread dispatch so later runtime mutations cannot change write ownership. |
| `querysource/queries/qs.py` | MODIFY | The central wrapper covers provider overrides without editing each provider checksum. |
| `tests/tenants/test_tenant_result_cache.py` | CREATE | Tests must exercise the listed ownership/error branches with observable per-store outcomes. |

## Codebase Contract (Anti-Hallucination)

### Verified Imports

```python
import pytest
from querysource.cache_identity import result_cache_key
from querysource.tenants import QueryIdentity
```

Standard-library imports are built in. Existing repository imports resolve through
source definitions/exports below and spec §6. Imports from dependency-created
modules are **planned contracts**, not claims that those modules exist today;
verify them after the dependency lands. Preserve existing imports needed by
untouched code. Before adding any further import, verify its source and update
this packet. Datamodel BaseModel is not Pydantic BaseModel.

### Import Provenance

- `querysource.cache_identity` → planned `querysource/cache_identity.py` created by TASK-718; verify after prerequisite
- `querysource.tenants` → planned `querysource/tenants.py` created by TASK-716; verify after prerequisite

### Existing Signatures to Use

```text
querysource/connections.py:98
async def in_cache(self, key: str) -> Any:

querysource/connections.py:105
async def from_cache(self, key: str) -> Any:

querysource/providers/external.py:68
def checksum(self):

querysource/interfaces/queries.py:43
class AbstractQuery(Connection):
def __init__(self, slug: str=None, conditions: dict=None, request: web.Request=None, loop: Optional[asyncio.AbstractEventLoop]=None, **kwargs):

querysource/queries/qs.py:36
class QS(BaseQuery):
def __init__(self, slug: str='', conditions: dict=None, request: web.Request=None, loop: asyncio.AbstractEventLoop=None, **kwargs):
async def build_provider(self):
async def query(self, output_format: Optional[str]=None):
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

1. Implement AbstractQuery.result_cache_key by composing immutable loaded identity/revision with provider checksum. Wrap exactly once at read/write call sites. **Why:** Double wrapping makes reads and writes use different keys.
2. Update QS lookup/write/refresh paths plus AbstractQuery threaded writes to carry the same composed key. Preserve existing TTL/refresh options and every provider-specific checksum input. **Why:** Provider hashes contain provider-specific result identity.
3. Do not dual-read unqualified keys, including legacy keys; document/test the intentional cold cache transition. Old in-flight writers only write their captured revision. **Why:** Unqualified keys cannot prove ownership.
4. Read definitions before cache access so edits, deletes and external SQL mutations cannot be served from a stale slug-only result. Physical-store aliases share keys; different stores never share identical-SQL results. **Why:** Definition reads are required to detect external edits and deletion.

### `querysource/interfaces/queries.py` (MODIFY)

```python
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `class AbstractQuery(Connection):` (verified: querysource/interfaces/queries.py:43)
from querysource.cache_identity import result_cache_key

class AbstractQuery:
    """Existing Connection subclass; store fresh owner and revision context."""

    def result_cache_key(self, provider_checksum: str) -> str:
        """Compose key from the loaded immutable definition and provider checksum."""
        return result_cache_key(
            self._definition_identity, self._definition_revision, provider_checksum
        )

    def save_in_cache(self, checksum: str, result: Any, loop: asyncio.AbstractEventLoop) -> None:
        """Write the already composed key; capture identity before threading."""
        # FILL IN: Preserve serialization/TTL/thread mechanics; use composed keys and immutable revision; no legacy-key fallback.
        raise NotImplementedError

    async def caching_data(self, checksum: str, result: Any) -> None:
        """Use the same composed key for TTL writes, with no second wrapping."""
        # FILL IN: Preserve serialization/TTL/thread mechanics; use composed keys and immutable revision; no legacy-key fallback.
        raise NotImplementedError
```

**Why:** Capture identity before thread dispatch so later runtime mutations cannot change write ownership.

### `querysource/queries/qs.py` (MODIFY)

```python
# occurrences: 1 (verified by exact source-line matching at task generation)
# MODIFY — attach within/after `class QS(BaseQuery):` (verified: querysource/queries/qs.py:36)
async def query(self, output_format: Optional[str]=None):
    """Use self.result_cache_key(provider checksum) once for reads/writes; preserve execution/output behavior and refresh flags."""
    # FILL IN: Use self.result_cache_key(provider checksum) once for reads/writes; preserve execution/output behavior and refresh flags.
    raise NotImplementedError
```

**Why:** The central wrapper covers provider overrides without editing each provider checksum.

### `tests/tenants/test_tenant_result_cache.py` (CREATE)

```python
"""Apply revision-scoped keys to every result cache boundary regression contracts."""
import pytest

@pytest.mark.asyncio
async def test_identical_sql_owner_key_isolation() -> None:
    """identical sql owner key isolation."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_revision_changed_by_external_edit() -> None:
    """revision changed by external edit."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_delayed_old_writer_after_edit_delete() -> None:
    """delayed old writer after edit delete."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError

@pytest.mark.asyncio
async def test_ttl_refresh_and_no_double_wrapping() -> None:
    """ttl refresh and no double wrapping."""
    # FILL IN: Construct explicit fixtures and assert task AC outcomes; do not substitute a smoke-only assertion.
    raise NotImplementedError
```

**Why:** Tests must exercise the listed ownership/error branches with observable per-store outcomes.

### FILL IN checklist

- [ ] `querysource/interfaces/queries.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `querysource/queries/qs.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.
- [ ] `tests/tenants/test_tenant_result_cache.py` — complete the bounded branches/wiring/tests described above, verify dependency anchors, and retain the fixed spec contract.

## Acceptance Criteria

- [ ] AC-1: Implement AbstractQuery.result_cache_key by composing immutable loaded identity/revision with provider checksum. Wrap exactly once at read/write call sites.
- [ ] AC-2: Update QS lookup/write/refresh paths plus AbstractQuery threaded writes to carry the same composed key. Preserve existing TTL/refresh options and every provider-specific checksum input.
- [ ] AC-3: Do not dual-read unqualified keys, including legacy keys; document/test the intentional cold cache transition. Old in-flight writers only write their captured revision.
- [ ] AC-4: Read definitions before cache access so edits, deletes and external SQL mutations cannot be served from a stale slug-only result. Physical-store aliases share keys; different stores never share identical-SQL results.
- [ ] AC-5: Focused tests pass: `source .venv/bin/activate && uv run pytest tests/tenants/test_tenant_result_cache.py -q`.
- [ ] New/changed Python code passes black formatting; no unrelated refactoring or dependency additions.

## Test Specification

- `test_identical_sql_owner_key_isolation` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_revision_changed_by_external_edit` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_delayed_old_writer_after_edit_delete` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.
- `test_ttl_refresh_and_no_double_wrapping` — implement the explicit scenario in the test blueprint; assert selected-store outcomes and failure behavior.

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

Author/date: sdd-worker (orchestrated via parrot-sdd-coder, native haiku
seat), 2026-09-15.

Implemented by the native `haiku` coder (no MCP dispatch; ran directly as
a Task subagent in its own prepared sub-worktree). The implementation was
reviewed carefully given TASK-718/TASK-721's merged-but-broken history,
and found genuinely correct on this pass:

- `AbstractQuery.result_cache_key(provider_checksum)` composes the key via
  the real `querysource.cache_identity.result_cache_key` (module-level
  function, imported at the top of the file) using
  `self._definition_identity`/`self._definition_revision`. Verified there
  is no accidental self-recursion despite the instance method and the
  imported function sharing the exact name `result_cache_key` — Python
  resolves the bare call inside the method body against the module
  global, not the class's own method (confirmed with a standalone
  runtime check constructing a `BaseQuery`, setting identity/revision,
  and calling `result_cache_key()` — returned a `qs:r2:...` key, no
  `RecursionError`).
- `save_cache()`/`save_in_cache()`/`caching_data()`/`cache_saved()` all
  carry the *already-composed* key through — `save_cache()` defensively
  detects an already-`qs:r2:`-prefixed checksum and reuses it verbatim
  rather than re-wrapping (AC-1/AC-3 "wrap exactly once").
- `QS.query()` computes `cache_key = self.result_cache_key(checksum)`
  once and threads that same value through `in_cache`/`from_cache`/
  `save_cache` — no dual-read of an unqualified/legacy key (AC-3).
  `build_provider()` (TASK-721) already reads the definition and captures
  `_definition_identity`/`_definition_revision` before any cache access,
  satisfying AC-4's "read definitions before cache access" ordering.
- `AbstractQuery.__init__` now pre-declares
  `_definition_identity`/`_definition_revision` as `None` — a reasonable,
  intentional design choice (owner/revision context slots exist from
  construction, not only after a slug lookup) that broke one assumption
  in TASK-721's own test (`not hasattr(qs, "_definition_identity")` for a
  raw query, previously true only because the attribute was never set at
  all for that path). Fixed that assertion to check the new, correct
  `None` default instead — a genuine cross-task regression, not a defect
  in this task's own scope, but left unfixed the suite would be red.

Checks run (this worktree, `.venv` from the primary checkout):

- `pytest tests/tenants/ -q` → 29 passed (all of TASK-716–722's suites
  together, run for regression; catches the TASK-721 assumption break
  described above).
- `ruff check` — fixed unused imports
  (`AsyncMock`/`MagicMock`/`patch`/`ProviderError`/`AbstractQuery`/`QS`)
  and import ordering in the coder's own
  `tests/tenants/test_tenant_result_cache.py` via `ruff --fix`; no other
  findings in that file. `querysource/interfaces/queries.py`/
  `querysource/queries/qs.py` findings are all pre-existing
  (`RUF013`/`TRY401`/`BLE001`/`SIM102`) on lines this task did not modify
  — left as-is, matching the same convention observed on every prior
  task in this feature.

Files changed (beyond the original merge):
`tests/tenants/test_tenant_execution_context.py` (TASK-721's suite,
one-line compat fix), `tests/tenants/test_tenant_result_cache.py` (lint
only, no logic change).

Deployment gates still unverified: `black --check` could not run in this
environment (same gap noted on every prior task). No live Redis/
PostgreSQL was used; the four tests exercise `result_cache_key`/
`definition_revision` and the cache-key composition logic directly, not a
real cache round-trip.

No spec deviations: user-specific cache redesign and source-table change
invalidation are explicitly out of scope for this task and were not
touched.
