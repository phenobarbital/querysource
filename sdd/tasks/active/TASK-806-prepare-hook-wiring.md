# TASK-806: `ThreadSource.prepare()` hook + MultiQS / multi-handler wiring

**Feature**: FEAT-159 — OneDrive Source for MultiQS
**Spec**: `sdd/specs/onedrive-multiqs-source.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-805
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 4 (G4, S3). Session-vault, Redis and asyncpg resources are
bound to the caller's event loop, while `ThreadSource.run()` creates a fresh
loop. Credentials must therefore be resolved **before** the thread starts. This
task adds a no-op `prepare(context)` hook, calls it from `MultiQS.query()` for
every `sources:` entry, lets the multi handler build the identity context, and
maps `DelegatedIdentityError` to HTTP 409.

---

## Scope

- `ThreadSource.prepare(self, context)`: a no-op async hook.
- `MultiQS.__init__`: add a keyword-only `identity_context` and store it in `self._identity_context`.
- `MultiQS.query()`: `await t.prepare(self._identity_context)` right after each source's construction (`:556`).
- `handlers/multi.py`: pass `identity_context=SourceIdentityContext.from_request(request, _user_session)`,
  and add an `except DelegatedIdentityError` branch **before** the generic
  `(QueryException, DriverError)` branch that raises `self.Error(code=409, detail={...})`.
- Write tests.

**NOT in scope**: OneDrive logic (TASK-807) and scheduler wiring (TASK-812).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/sources/base.py` | MODIFY | `prepare()` hook |
| `querysource/queries/multi/__init__.py` | MODIFY | `identity_context` param + prepare call |
| `querysource/handlers/multi.py` | MODIFY | build context; 409 mapping |
| `tests/multi/test_multiqs_prepare.py` | CREATE | unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from ....auth.identity_tokens import SourceIdentityContext         # in sources/base.py — TYPE_CHECKING only (created by TASK-805)
from ...auth.identity_tokens import SourceIdentityContext          # in queries/multi/__init__.py — TYPE_CHECKING only
from ..auth.identity_tokens import DelegatedIdentityError, SourceIdentityContext  # handlers/multi.py (TASK-805)
from ..exceptions import QueryException, DriverError               # verified: handlers/multi.py:11-18
```

### Existing Signatures to Use
```python
# querysource/queries/multi/sources/base.py
from typing import Optional                                          # :5
    def run(self) -> None:                                           # :132 (insert prepare() above it)

# querysource/queries/multi/__init__.py
    def __init__(self, slug=None, queries=None, files=None, query=None, conditions=None, request=None,
                 loop=None, user_session=None, *, tenant=None, definition=None,
                 principal: "QSPrincipal | None" = None,             # :120
                 **kwargs):
        self._user_session = user_session                            # :162
    async def query(self):                                           # :278
        t = cls(name, config, self._request, self._queue)            # :556 (inside the sources loop :541-557)

# querysource/handlers/multi.py
_user_session = request.get('user_session')                          # :483
qs = MultiQS(slug=slug, queries=_queries, files=_files, query=options, conditions=data,
             user_session=_user_session,                             # :499
             tenant=_tenant, definition=request.get('qs_definition'))  # :493-501
except SlugNotFound as snf:                                          # :527 (DataNotFound branch above at :505)
except (QueryException, DriverError) as qe:                          # :605 → code=402 (must NOT catch our error first)
# querysource/handlers/abstract.py:133
def Error(self, reason=None, message=None, exception=None, stacktrace=None, code=400, detail: dict | None = None)
```

### Does NOT Exist
- ~~`ThreadSource.prepare`~~ before this task.
- ~~`MultiQS(identity_context=...)`~~ before this task. Note `AbstractQuery.__init__` swallows `**kwargs` (`interfaces/queries.py:52-63`), so it must be an explicit keyword-only parameter.
- ~~`request=` passed by the handler to MultiQS~~: it is not (`:493-501`). Do NOT add it, because that changes BaseQuery behaviour.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/queries/multi/sources/base.py", "action": "MODIFY"},
    {"path": "querysource/queries/multi/__init__.py", "action": "MODIFY"},
    {"path": "querysource/handlers/multi.py", "action": "MODIFY"},
    {"path": "tests/multi/test_multiqs_prepare.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/queries/multi/sources/base.py#ThreadSource.run",
    "sym:querysource/queries/multi/__init__.py#MultiQS.__init__",
    "sym:querysource/queries/multi/__init__.py#MultiQS.query",
    "sym:querysource/handlers/abstract.py#AbstractHandler.Error"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- `prepare()` runs for **all** sources (the default is a no-op), so existing
  sources behave identically.
- A failing `prepare()` must abort `query()` **before any** thread starts.
  All `prepare()` calls happen in the dispatch loop, which precedes the run
  loop (`:559+`).
- The 409 payload is
  `detail={"provider": err.provider, "reason": err.reason, "link": err.link_url}`
  and `message=str(err)`.

---

## Implementation Blueprint

### Steps (in order)
1. Add the hook to `base.py` — *why*: the default no-op keeps every existing source unchanged.
2. Add the param and the prepare call to MultiQS — *why*: G4, resolve on the caller's loop.
3. Wire the handler — *why*: the request path needs session + `app["auth"]`.
4. Write the tests.

### `querysource/queries/multi/sources/base.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '    def run(self) -> None:' querysource/queries/multi/sources/base.py) — :132
# BEFORE — insert above `    def run(self) -> None:`
    async def prepare(self, context: "SourceIdentityContext | None") -> None:
        """Resolve caller-loop-bound credentials before the thread starts. Default: no-op.

        Called by MultiQS on the request/scheduler event loop, never from run().
        """
        return None
# and add under the imports:  from typing import TYPE_CHECKING
#   if TYPE_CHECKING: from ....auth.identity_tokens import SourceIdentityContext
```
**Why**: using `TYPE_CHECKING` avoids an import cycle, because `auth` is not needed at runtime here.

### `querysource/queries/multi/__init__.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '            principal: "QSPrincipal | None" = None,' …/multi/__init__.py) — :120
# AFTER — insert below it:
            identity_context: "SourceIdentityContext | None" = None,
# occurrences: 1 (verified: grep -c '        self._user_session = user_session' …) — :162
# AFTER:
        # FEAT-159: delegated-credential context resolved by sources' prepare() on this loop.
        self._identity_context = identity_context
# occurrences: 1 (verified: grep -c '                    t = cls(name, config, self._request, self._queue)' …) — :556
# AFTER:
                    await t.prepare(self._identity_context)
```
**Why**: the order within the dispatch loop guarantees that no thread starts before every `prepare()` succeeds.

### `querysource/handlers/multi.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '            user_session=_user_session,' querysource/handlers/multi.py) — :499
# AFTER:
            identity_context=SourceIdentityContext.from_request(request, _user_session),
# occurrences: 1 (verified: grep -c '        except SlugNotFound as snf:' querysource/handlers/multi.py) — :527
# BEFORE — insert above it:
        except DelegatedIdentityError as die:
            raise self.Error(
                message=str(die),
                exception=die,
                code=409,
                detail={"provider": die.provider, "reason": die.reason, "link": die.link_url},
            ) from die
# import: from ..auth.identity_tokens import DelegatedIdentityError, SourceIdentityContext  (below :23)
```
**Why**: it must precede `except (QueryException, DriverError)`, because
`DelegatedIdentityError` subclasses `QueryException`.

### FILL IN checklist
- [ ] Confirm the handler's `request` variable name at `:493` (it is `request` in the enclosing method).
- [ ] Tests: prepare ordering and 409 mapping.

---

## Acceptance Criteria

- [ ] `prepare()` is awaited for every source before any `start()`. A raising `prepare()` starts no thread.
- [ ] The handler returns 409 with `detail.link` for `DelegatedIdentityError`.
- [ ] The existing multi tests pass (`tests/multi/test_multiqs_principal.py`, `tests/test_multiqs_sources_integration.py`).
- [ ] `ruff check` on the three modified modules is clean.

## Validation Commands

- `pytest tests/multi/test_multiqs_prepare.py -q`
- `pytest tests/test_multiqs_sources_integration.py -q`
- `pytest tests/multi/test_multiqs_principal.py -q`

---

## Test Specification

```python
# tests/multi/test_multiqs_prepare.py
import asyncio
from unittest.mock import patch

import pytest

from querysource.queries.multi import MultiQS
from querysource.queries.multi.sources.base import ThreadSource


class _Rec(ThreadSource):
    events: list = []

    async def prepare(self, context):
        _Rec.events.append(("prepare", self._name, context))

    def start(self):
        _Rec.events.append(("start", self._name, None))
        super().start()

    async def fetch(self):
        return None


async def test_prepare_called_before_start():
    ...  # FILL IN: patch.dict SOURCE_REGISTRY {"_Rec": _Rec}; two entries; all prepare events precede any start


async def test_failing_prepare_starts_no_thread():
    ...  # FILL IN: prepare raises DelegatedIdentityError → query() raises; no "start" events


async def test_multi_handler_maps_delegated_error_409():
    ...  # FILL IN: handler-level test or direct call; response status 409, detail.link == "/api/v1/user/identities/link/onedrive"
```

---

## Agent Instructions

1. Work in the feature worktree (`python -m scripts.sdd.ensure_worktree --slug onedrive-multiqs-source --feature-id FEAT-159`).
2. Read the spec. Check that every `Depends-on` task is `"done"` in `sdd/tasks/index/onedrive-multiqs-source.json`.
3. Verify the Codebase Contract before writing code.
4. Set the task to `"in-progress"` in the index (with `started_at`), then implement from the blueprint and complete every `# FILL IN:`.
5. Run the Validation Commands. Commit only the listed files.
6. Close with `scripts/sdd/close_task.sh TASK-806 onedrive-multiqs-source verified`, then fill in the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*
