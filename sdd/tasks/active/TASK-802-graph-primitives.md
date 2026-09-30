# TASK-802: Graph primitives — scoped kiota patch + StaticTokenCredential

**Feature**: FEAT-159 — OneDrive Source for MultiQS
**Spec**: `sdd/specs/onedrive-multiqs-source.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 1, part one. The Graph SDK accepts only a `TokenCredential` or
`AsyncTokenCredential`, not a bare access-token string (design research S3).
Separately, today's `SharepointSource` patches `platform.version`
process-wide and **never restores it** (`sharepoint.py:208-210`, S9). This task
creates `querysource/queries/multi/sources/graph.py` with the two primitives
that TASK-803's `GraphDriveSource` builds on.

---

## Scope

- Create `graph.py` with `kiota_platform_version_patch()`: a context manager,
  reference-counted under a module `threading.Lock`. It installs the
  `.strip()` wrapper on the first entry and restores the original in `finally`
  on the last exit.
- Add `StaticTokenCredential`, an `AsyncTokenCredential` adapter over an
  already-resolved delegated access token.
- Write unit tests for both.

**NOT in scope**: `GraphDriveSource` (TASK-803); any change to `sharepoint.py` (TASK-804).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/sources/graph.py` | CREATE | primitives module |
| `tests/multi/sources/test_graph_primitives.py` | CREATE | unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from azure.core.credentials import AccessToken   # verified import (azure-core, transitive of azure-identity)
import platform, threading                        # stdlib
from contextlib import contextmanager             # stdlib
```

### Existing Signatures to Use
```python
# querysource/queries/multi/sources/sharepoint.py:205-210 — the patch being replaced (reference only; do NOT edit here)
import platform as _platform
_orig_version = _platform.version
_platform.version = lambda: _orig_version().strip()

# msgraph: GraphServiceClient(credentials: TokenCredential | AsyncTokenCredential | None = None, scopes=None, request_adapter=None)
# azure.core.credentials.AccessToken(token: str, expires_on: int)   # NamedTuple, expires_on = epoch seconds
```

### Does NOT Exist
- ~~`querysource.queries.multi.sources.graph`~~: this task creates it.
- ~~Any existing token-adapter class in querysource~~: none. Do not import one.
- ~~`platform.version` restoration anywhere in the repo~~: none today.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/queries/multi/sources/graph.py", "action": "CREATE"},
    {"path": "tests/multi/sources/test_graph_primitives.py", "action": "CREATE"}
  ],
  "contract_symbols": ["sym:querysource/queries/multi/sources/sharepoint.py#SharepointSource.fetch"]
}
```

---

## Implementation Notes

### Key Constraints
- The patch is **process-global** and source threads run concurrently, so
  nesting and concurrency must be safe. Keep a module-level counter and
  original reference under one `threading.Lock`.
- `StaticTokenCredential.close()` is a no-op. TASK-803 must never treat it as
  an owned Azure credential.
- An unknown expiry falls back to `now + 300` seconds: kiota needs an expiry,
  and 300 s is below Graph's shortest refresh leeway.

---

## Implementation Blueprint

### Steps (in order)
1. Create `graph.py` with the block below — *why*: TASK-803 and TASK-804 import these exact names.
2. Complete the FILL INs — *why*: the reference-count logic is the S9 guarantee.
3. Write the tests in the Test Specification — *why*: AC "patch always restored".

### `querysource/queries/multi/sources/graph.py` (CREATE)
```python
"""Shared Microsoft Graph helpers for MultiQS drive sources (FEAT-159)."""
from __future__ import annotations

import platform
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator, Optional

from azure.core.credentials import AccessToken  # verified: azure-core

_PATCH_LOCK = threading.Lock()
_PATCH_DEPTH: int = 0
_ORIGINAL_VERSION = None


@contextmanager
def kiota_platform_version_patch() -> Iterator[None]:
    """Strip platform.version()'s trailing space for the kiota User-Agent.

    kiota builds its User-Agent from ``platform.version()``, which carries a
    trailing space on Linux that httpx rejects. Reference-counted under a lock:
    the first entry installs the wrapper, the last exit restores the original
    in ``finally`` — never leaked, never restored early under concurrency.
    """
    global _PATCH_DEPTH, _ORIGINAL_VERSION  # noqa: PLW0603
    # FILL IN: under _PATCH_LOCK, on depth 0 save platform.version to _ORIGINAL_VERSION and install
    #          `lambda: _ORIGINAL_VERSION().strip()`; increment depth — bounded by AC "patch always restored"
    try:
        yield
    finally:
        # FILL IN: under _PATCH_LOCK decrement depth; when it reaches 0 restore platform.version and
        #          reset _ORIGINAL_VERSION to None — bounded by S9 (restore in finally, last exit only)
        pass


class StaticTokenCredential:
    """AsyncTokenCredential adapter over an already-resolved delegated access token."""

    def __init__(self, access_token: str, expires_at: Optional[datetime]) -> None:
        self._access_token = access_token
        self._expires_at = expires_at

    async def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        """Return the token; unknown expiry → now + 300 seconds (epoch)."""
        # FILL IN: expires_on = int(self._expires_at.timestamp()) if set else int(time.time()) + 300
        raise NotImplementedError

    async def close(self) -> None:
        """No-op — nothing to release; never treated as an owned Azure credential."""

    async def __aenter__(self) -> "StaticTokenCredential":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()
```
**Why this shape**: the names and signatures are fixed by spec §3 M1.
`StaticTokenCredential` only needs `get_token`/`close` plus the async context
protocol for msgraph's `AsyncTokenCredential` duck typing. Do not add a
`get_token_info` unless tests prove kiota calls it.

### FILL IN checklist
- [ ] `kiota_platform_version_patch` enter: install under the lock, count depth.
- [ ] `kiota_platform_version_patch` exit: restore on the last exit only.
- [ ] `StaticTokenCredential.get_token`: epoch expiry with the 300 s fallback.

---

## Acceptance Criteria

- [ ] `from querysource.queries.multi.sources.graph import kiota_platform_version_patch, StaticTokenCredential` works.
- [ ] Nested and concurrent (threads) entries leave `platform.version` identical to the original afterwards, including when the body raises.
- [ ] `get_token()` returns `AccessToken` with an int epoch `expires_on`.
- [ ] `ruff check querysource/queries/multi/sources/graph.py` is clean.

## Validation Commands

- `pytest tests/multi/sources/test_graph_primitives.py -q`

---

## Test Specification

```python
# tests/multi/sources/test_graph_primitives.py
import platform
import threading
from datetime import datetime, timezone, timedelta

import pytest

from querysource.queries.multi.sources.graph import (
    StaticTokenCredential, kiota_platform_version_patch,
)


def test_kiota_patch_restores_and_nests():
    original = platform.version
    with kiota_platform_version_patch():
        with kiota_platform_version_patch():
            assert platform.version() == original().strip()
        assert platform.version is not original
    assert platform.version is original


def test_kiota_patch_restores_on_exception():
    original = platform.version
    with pytest.raises(RuntimeError):
        with kiota_platform_version_patch():
            raise RuntimeError("boom")
    assert platform.version is original


def test_kiota_patch_concurrent_threads():
    ...  # FILL IN: 8 threads entering/leaving with a barrier; original restored at the end


async def test_static_token_credential_get_token():
    exp = datetime.now(timezone.utc) + timedelta(minutes=10)
    cred = StaticTokenCredential("tok", exp)
    tok = await cred.get_token("https://graph.microsoft.com/.default")
    assert tok.token == "tok" and tok.expires_on == int(exp.timestamp())


async def test_static_token_credential_unknown_expiry_and_close():
    ...  # FILL IN: expires_on within now+300±5; `await cred.close()` returns None
```

---

## Agent Instructions

1. Work in the feature worktree (`python -m scripts.sdd.ensure_worktree --slug onedrive-multiqs-source --feature-id FEAT-159`).
2. Read the spec. Check that every `Depends-on` task is `"done"` in `sdd/tasks/index/onedrive-multiqs-source.json`.
3. Verify the Codebase Contract before writing code.
4. Set the task to `"in-progress"` in the index (with `started_at`), then implement from the blueprint and complete every `# FILL IN:`.
5. Run the Validation Commands. Commit only the listed files.
6. Close with `scripts/sdd/close_task.sh TASK-802 onedrive-multiqs-source verified`, then fill in the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*
