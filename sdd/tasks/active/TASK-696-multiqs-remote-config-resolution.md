# TASK-696: MultiQS Remote Config Resolution

**Feature**: FEAT-101 — MultiQuery Remote Execution
**Spec**: `sdd/specs/multiquery-remote-execution.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-695, TASK-697
**Assigned-to**: unassigned

---

## Context

> Modifies MultiQS.query() to detect `remote: true` in query config dicts, resolve the
> worker address (per-query `worker` key → central QWORKER_HOST/PORT config → error),
> build a RemoteConfig, and pass it to ThreadQuery. Also tracks which queries ran
> remotely for the `X-Remote-Queries` response header.
> Implements Spec §3 (Module 4).

---

## Scope

- Modify the query dispatch loop in `MultiQS.query()` (multi/__init__.py, lines 146-161):
  - Before creating ThreadQuery, check for `remote` key in query dict
  - If `remote: true`:
    1. Pop `remote` and `worker` keys from the query dict (so they don't get passed to QueryObject)
    2. Resolve worker address: per-query `worker` → parse "host:port" string; if absent, use
       central `QWORKER_HOST`/`QWORKER_PORT` from conf.py
    3. If neither source provides a worker address, raise `DriverError`
    4. Create `RemoteConfig(host, port, timeout)` and pass to `ThreadQuery(..., remote_config=rc)`
    5. Track the query name in a `_remote_queries` list
  - If no `remote` key (or `remote: false`): create ThreadQuery as before (no remote_config)
- Add `_remote_queries: list[str]` attribute to MultiQS, returned alongside the result
  so the handler can add the `X-Remote-Queries` header
- Write unit tests

**NOT in scope**: Config settings (TASK-697 — must be completed first), catalog/schema
updates (TASK-698), handler header changes (TASK-698), actual remote execution testing
(that's covered by TASK-694 tests)

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/__init__.py` | MODIFY | Add remote detection, config resolution, RemoteConfig passing |
| `tests/test_multiqs_remote_dispatch.py` | CREATE | Unit tests for remote config resolution |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# Current imports in multi/__init__.py (lines 1-18):
import asyncio
import logging
from typing import Optional
from aiohttp import web
from ...exceptions import SlugNotFound, QueryException, DriverError, DataNotFound, ParserError
from importlib import import_module
from ..base import BaseQuery
from .transformations import GoogleMaps
from .operators.filter import Filter
from .sources import ThreadQuery, FileSource

# New imports to add:
from .sources.executors import RemoteConfig
from ...conf import QWORKER_HOST, QWORKER_PORT, QWORKER_TIMEOUT  # added by TASK-697
```

### Existing Signatures to Use
```python
# querysource/queries/multi/__init__.py:146-161 — current dispatch loop (will be modified):
if self._queries:
    for name, query in self._queries.items():
        conditions = self._conditions.pop(name, {})
        query = {**conditions, **query}
        try:
            t = ThreadQuery(
                name, query, self._request, self._queue
            )                                                   # line 152-154
        except Exception as ex:
            raise self.Error(
                message=f"Error Starting Query {name}: {ex}",
                exception=ex
            ) from ex
        t.start()                                               # line 160
        tasks[name] = t                                         # line 161

# ThreadQuery.__init__ after TASK-695 (will accept remote_config):
def __init__(self, name: str, query: dict, request: web.Request,
             queue: asyncio.Queue, remote_config: RemoteConfig | None = None): ...

# querysource/exceptions.py:58
class DriverError(QueryException): ...
```

### Does NOT Exist
- ~~`MultiQS._remote_config`~~ — does not exist yet; this task adds remote resolution logic
- ~~`MultiQS.remote_queries`~~ — does not exist yet; this task adds `_remote_queries` list
- ~~`querysource.conf.QWORKER_HOST`~~ — does not exist until TASK-697 adds it

---

## Implementation Notes

### Pattern to Follow
```python
# Inside the self._queries loop, before creating ThreadQuery:
remote_config = None
remote_queries = []  # track names of remotely-dispatched queries

# For each query:
is_remote = query.pop("remote", False)
worker_addr = query.pop("worker", None)

if is_remote:
    if worker_addr:
        # Parse "host:port" string
        parts = worker_addr.rsplit(":", 1)
        host = parts[0]
        port = int(parts[1]) if len(parts) > 1 else QWORKER_PORT
    elif QWORKER_HOST:
        host = QWORKER_HOST
        port = QWORKER_PORT
    else:
        raise DriverError(
            f"Query {name!r} has remote=true but no worker address configured. "
            f"Set 'worker' on the query or configure QWORKER_HOST/QWORKER_PORT."
        )
    remote_config = RemoteConfig(host=host, port=port, timeout=QWORKER_TIMEOUT)
    remote_queries.append(name)

t = ThreadQuery(name, query, self._request, self._queue, remote_config=remote_config)
```

### Key Constraints
- **Pop `remote` and `worker` before passing to ThreadQuery**: These are execution
  directives, not query parameters. They must NOT reach QueryObject or the database driver.
- **Worker address parsing**: The `worker` value is a "host:port" string. Use `rsplit(":", 1)`
  to handle IPv6 addresses or hostnames with colons (though rare).
- **Central config fallback**: `QWORKER_HOST` from conf.py. If it's None (not configured)
  AND no per-query `worker` key, raise `DriverError` immediately — don't let it fail
  later in QClient with an opaque error.
- **`_remote_queries` tracking**: Store the list of remote query names as an instance
  attribute so the handler can access it for the `X-Remote-Queries` header.

### References in Codebase
- `querysource/queries/multi/__init__.py:146-161` — dispatch loop to modify
- `querysource/queries/multi/__init__.py:59-106` — MultiQS.__init__()
- `querysource/conf.py` — where QWORKER_* settings will live (TASK-697)

---

## Acceptance Criteria

- [ ] `remote` and `worker` keys are popped from query dict before ThreadQuery creation
- [ ] `remote: true` with `worker: "host:port"` resolves to RemoteConfig correctly
- [ ] `remote: true` without `worker` falls back to QWORKER_HOST/QWORKER_PORT
- [ ] `remote: true` with no worker and no QWORKER_HOST raises DriverError
- [ ] `remote: false` or absent `remote` key creates ThreadQuery without remote_config
- [ ] `_remote_queries` list tracks names of remotely-dispatched queries
- [ ] Existing tests pass unchanged (backward compatibility)
- [ ] Tests pass: `pytest tests/test_multiqs_remote_dispatch.py -v`
- [ ] No linting errors: `ruff check querysource/queries/multi/__init__.py`

---

## Test Specification

```python
# tests/test_multiqs_remote_dispatch.py
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from querysource.queries.multi.sources.executors import RemoteConfig
from querysource.exceptions import DriverError


class TestRemoteConfigResolution:
    def test_parse_worker_host_port(self):
        """worker='host:8888' parses correctly."""
        query = {"slug": "test", "remote": True, "worker": "qworker1:8888"}
        is_remote = query.pop("remote", False)
        worker_addr = query.pop("worker", None)
        assert is_remote is True
        parts = worker_addr.rsplit(":", 1)
        assert parts[0] == "qworker1"
        assert int(parts[1]) == 8888
        assert "remote" not in query
        assert "worker" not in query

    def test_no_remote_key_is_local(self):
        """Query without 'remote' key stays local."""
        query = {"slug": "test", "store_id": 42}
        is_remote = query.pop("remote", False)
        assert is_remote is False

    def test_remote_true_no_worker_no_config_raises(self):
        """remote=true with no worker and no central config raises DriverError."""
        with patch("querysource.queries.multi.QWORKER_HOST", None):
            # This tests the error path — actual integration tested in MultiQS
            pass  # Full test requires MultiQS instantiation

    def test_strips_remote_keys_from_query(self):
        """remote and worker keys are removed before ThreadQuery gets the dict."""
        query = {"slug": "test", "remote": True, "worker": "host:8888", "store_id": 42}
        query.pop("remote", None)
        query.pop("worker", None)
        assert query == {"slug": "test", "store_id": 42}
```

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at `sdd/specs/multiquery-remote-execution.spec.md` for full context
2. **Check dependencies** — verify TASK-695 and TASK-697 are in `sdd/tasks/completed/`
3. **Verify the Codebase Contract** — read `multi/__init__.py` lines 146-161 for current dispatch
4. **Update status** in `sdd/tasks/index/multiquery-remote-execution.json` → `"in-progress"`
5. **Implement** following the scope, codebase contract, and notes above
6. **Verify** all acceptance criteria are met
7. **Move this file** to `sdd/tasks/completed/TASK-696-multiqs-remote-config-resolution.md`
8. **Update index** → `"done"`
9. **Fill in the Completion Note** below

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**: <session or agent ID>
**Date**: YYYY-MM-DD
**Notes**: What was implemented, any deviations from scope, issues encountered.

**Deviations from spec**: none | describe if any
