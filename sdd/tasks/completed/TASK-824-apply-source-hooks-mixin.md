# TASK-824: Apply `SourceHooksMixin` to `BaseProvider` / `pgProvider` / `ThreadSource` and wrap `fetch()`

**Feature**: FEAT-157 — MultiQuery Source Pre/Post-Hooks (PostgreSQL)
**Spec**: `sdd/specs/multi-source-hooks.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-823
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 2 and §2 "Execution (`ThreadSource.run`)". This task decorates both parents
with the TASK-823 mixin. It also declares PostgreSQL capability on `pgProvider`, which
`dbProvider` inherits. Finally, it wraps `ThreadSource.fetch()` so that a source with hooks
runs pre-hook → fetch → post-hook. Sources without hooks must behave exactly as today.

The same cross-feature precondition as TASK-823 applies: FEAT-156 must be merged into `dev`,
because importing the mixin imports `querysource.interfaces.guarded_sql`.

---

## Scope

- `BaseProvider(ABC)` becomes `BaseProvider(SourceHooksMixin, ABC)`.
- `pgProvider` gets `sql_hooks_dialect = "postgres"` directly below `__parser__ = pgSQLParser`.
- `ThreadSource(threading.Thread, ABC)` becomes `ThreadSource(SourceHooksMixin, threading.Thread, ABC)`,
  with the mixin first in the bases.
- Add `ThreadSource._fetch_with_hooks()`. `run()` calls it instead of `self.fetch()`.
  - Without hooks it is exactly `await self.fetch()`.
  - A pre-hook failure means `fetch()` is not called.
  - The post-hook runs when `fetch()` succeeded, or when it raised `DataNotFound` / `NoDataFound`.
    In the second case the same no-data exception is re-raised afterwards. If the post-hook fails
    there, its error replaces the no-data exception.
  - Any other `fetch()` error skips the post-hook.
  - Status tags are logged with the source name.
- Append the M2 tests to `tests/test_thread_source_base.py`, and create
  `tests/test_provider_hook_dialects.py`.

**NOT in scope**:
- MultiQS validation, PBAC and dispatch, and the `ThreadQuery(hooks=)` parameter (TASK-825).
- Any provider other than `pgProvider` getting a dialect.
- Running hooks from the provider side. In this release the provider only declares the capability.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/providers/abstract.py` | MODIFY | `BaseProvider` inherits `SourceHooksMixin` |
| `querysource/providers/pg.py` | MODIFY | `sql_hooks_dialect = "postgres"` |
| `querysource/queries/multi/sources/base.py` | MODIFY | Mixin first in bases; `_fetch_with_hooks`; `run()` calls it |
| `tests/test_thread_source_base.py` | MODIFY | Append hook-order / failure / empty-result tests |
| `tests/test_provider_hook_dialects.py` | CREATE | `test_provider_dialects` |

---

## Codebase Contract (Anti-Hallucination)

Re-verified against `dev` @ `f8a32ae`. There is no code drift since the spec's `7337dbb`, and every anchor below matches the spec line numbers.

### Verified Imports
```python
from querysource.interfaces.source_hooks import SourceHooks, SourceHooksMixin  # created by TASK-823
# in querysource/providers/abstract.py (relative style of that module, see :16-21):
from ..interfaces.source_hooks import SourceHooksMixin
# in querysource/queries/multi/sources/base.py (relative style, see :11):
from ....interfaces.source_hooks import SourceHooksMixin
from querysource.providers.pg import pgProvider                  # verified: querysource/providers/pg.py:17
from querysource.providers.db import dbProvider                  # verified: querysource/providers/db.py:21 (class dbProvider(pgProvider))
from querysource.providers.sqlserver import sqlserverProvider    # verified: querysource/providers/sqlserver.py:23 (BaseProvider subclass)
from querysource.providers.bigquery import bigqueryProvider      # verified: querysource/providers/bigquery.py:25 (sqlProvider subclass)
from querysource.providers.abstract import BaseProvider          # verified: querysource/providers/abstract.py:34
from querysource.queries.multi.sources.base import ThreadSource  # verified: querysource/queries/multi/sources/base.py:14
from querysource.exceptions import DataNotFound, DriverError     # verified: querysource/exceptions.py:53, :74
from asyncdb.exceptions import NoDataFound                       # verified: base.py:9
```

### Existing Signatures to Use
```python
# querysource/providers/abstract.py
class BaseProvider(ABC):                                          # :34 (imports end at :21; ABC imported at :9)
    __parser__: AbstractParser = None                             # :36

# querysource/providers/pg.py
class pgProvider(sqlProvider):                                    # :17
    __parser__ = pgSQLParser                                      # :24
    capabilities = sqlProvider.capabilities | {qsurl_caps.TEXT_MATCH}  # :25

# querysource/queries/multi/sources/base.py
from ....exceptions import DataNotFound                           # :11
class ThreadSource(threading.Thread, ABC):                        # :14
    def __init__(self, name: str, options: dict, request: web.Request, queue: asyncio.Queue) -> None:  # :25
        super().__init__()                                        # :32 — MUST still reach threading.Thread.__init__
        self._name = name                                         # :35
        self.logger = logging.getLogger(__name__)                 # :43
    @abstractmethod
    async def fetch(self) -> pd.DataFrame:                        # :117
    def run(self) -> None:                                        # :132
            result = loop.run_until_complete(self.fetch())        # :147
        except (DataNotFound, NoDataFound) as ex:                 # :150 → info log, self.exc = ex
        except Exception as ex:  # noqa: BLE001                   # :155 → error log, self.exc = ex

# querysource/interfaces/source_hooks.py (TASK-823)
class SourceHooks: pre: tuple[str, ...]; post: tuple[str, ...]
class SourceHooksMixin:
    sql_hooks_dialect: ClassVar[Optional[str]] = None
    _source_hooks: Optional[SourceHooks] = None
    def set_hooks(self, hooks) -> None; has_hooks: bool (property)
    async def run_pre_hook(self) -> list[str]; async def run_post_hook(self) -> list[str]
```

### Does NOT Exist
- ~~`ThreadSource._fetch_with_hooks`~~: this task adds it.
- ~~`sql_hooks_dialect` on any provider~~ before this task.
- ~~An `__init__` on `SourceHooksMixin`~~: do not add one. MRO is `ThreadSource → SourceHooksMixin → Thread → ABC → object`,
  so `super().__init__()` at `base.py:32` still reaches `Thread.__init__`.
- ~~`bigqueryProvider.sql_hooks_dialect = …`~~ and similar on sqlserver / mysql: they must stay `None`.
- ~~A `self.logger` guarantee on the mixin~~: logging happens in `_fetch_with_hooks`, which has `self.logger` (`base.py:43`).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/providers/abstract.py", "action": "MODIFY"},
    {"path": "querysource/providers/pg.py", "action": "MODIFY"},
    {"path": "querysource/queries/multi/sources/base.py", "action": "MODIFY"},
    {"path": "tests/test_thread_source_base.py", "action": "MODIFY"},
    {"path": "tests/test_provider_hook_dialects.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/providers/abstract.py#BaseProvider",
    "sym:querysource/providers/pg.py#pgProvider",
    "sym:querysource/providers/db.py#dbProvider",
    "sym:querysource/providers/sqlserver.py#sqlserverProvider",
    "sym:querysource/providers/bigquery.py#bigqueryProvider",
    "sym:querysource/queries/multi/sources/base.py#ThreadSource",
    "sym:querysource/queries/multi/sources/base.py#ThreadSource.fetch",
    "sym:querysource/queries/multi/sources/base.py#ThreadSource.run"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- **MRO**: put the mixin **first** in both class statements. `BaseProvider(SourceHooksMixin, ABC)` and
  `ThreadSource(SourceHooksMixin, threading.Thread, ABC)` both produce a consistent C3 linearization.
  Check it with `ThreadSource.__mro__` and `pgProvider.__mro__` after the change.
- Without hooks, `_fetch_with_hooks` must be exactly `return await self.fetch()`, so every existing
  `tests/test_thread_source_base.py` test stays green unchanged.
- `run()` keeps its `except (DataNotFound, NoDataFound)` / `except Exception` branches unchanged. The
  re-raised no-data exception reaches the first branch (204 path), and a post-hook error reaches the second.
- For non-`ThreadQuery` sources, the post-hook runs **before** `run()` puts the result on the queue,
  because it runs inside `_fetch_with_hooks`. `ThreadQuery.fetch()` queues inside its executor. In both
  cases a post-hook failure sets `self.exc`, and MultiQS fails the pipeline.
- Log tags at INFO as `"pre-hook %s: %s"` with the source name and `", ".join(tags)`. Never log the SQL.

---

## Implementation Blueprint

### Steps (in order)
1. Edit `providers/abstract.py`: add the import and the base. *Why*: the capability layer lives on every provider (spec §2).
2. Edit `providers/pg.py`: set the dialect. *Why*: this makes `pgProvider` / `dbProvider` the only hook-capable providers.
3. Edit `sources/base.py`: add the import and the base, add `_fetch_with_hooks`, and swap the `run()` call. *Why*: the hooks wrap the retrieval (spec §2 Execution).
4. Append the tests, create the dialect test, and run the Validation Commands.

### `querysource/providers/abstract.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'from ..utils.functions import get_hash' querysource/providers/abstract.py) — :21
# AFTER — insert below `from ..utils.functions import get_hash`:
from ..interfaces.source_hooks import SourceHooksMixin

# occurrences: 1 (verified: grep -c 'class BaseProvider(ABC):' querysource/providers/abstract.py) — :34
# REPLACE `class BaseProvider(ABC):` with:
class BaseProvider(SourceHooksMixin, ABC):
```
**Why**: spec §2 says "BaseProvider and ThreadSource both inherit it". The default dialect `None` leaves every provider unchanged.

### `querysource/providers/pg.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '    __parser__ = pgSQLParser' querysource/providers/pg.py) — :24
# AFTER — insert below `    __parser__ = pgSQLParser`:
    #: FEAT-157: sources on this provider (and dbProvider) may declare pre/post-hooks.
    sql_hooks_dialect = "postgres"
```
**Why**: `dbProvider(pgProvider)` inherits it. TASK-825 validates slug children against it.

### `querysource/queries/multi/sources/base.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'from ....exceptions import DataNotFound' querysource/queries/multi/sources/base.py) — :11
# AFTER — insert below `from ....exceptions import DataNotFound`:
from ....interfaces.source_hooks import SourceHooksMixin

# occurrences: 1 (verified: grep -c 'class ThreadSource(threading.Thread, ABC):' querysource/queries/multi/sources/base.py) — :14
# REPLACE with:
class ThreadSource(SourceHooksMixin, threading.Thread, ABC):

# occurrences: 1 (verified: grep -c '    def run(self) -> None:' querysource/queries/multi/sources/base.py) — :132
# BEFORE — insert above `    def run(self) -> None:`:
    async def _fetch_with_hooks(self) -> Optional[pd.DataFrame]:
        """Run ``run_pre_hook`` → ``fetch`` → ``run_post_hook``.

        The post-hook runs when ``fetch`` raised nothing or raised
        ``DataNotFound``/``NoDataFound``; in the latter case the same no-data
        exception is re-raised after the post-hook (a post-hook error replaces
        it). Any other ``fetch`` error skips the post-hook. A pre-hook error
        propagates and ``fetch`` is never called.

        Without hooks this is exactly ``await self.fetch()``.

        Returns:
            Whatever ``fetch()`` returned.
        """
        if not self.has_hooks:
            return await self.fetch()
        tags = await self.run_pre_hook()
        if tags:
            self.logger.info("pre-hook %s: %s", self._name, ", ".join(tags))
        try:
            result = await self.fetch()
        except (DataNotFound, NoDataFound):
            # FILL IN: run + log the post-hook, then bare `raise` to re-raise the
            #   no-data exception — bounded by: a post-hook exception propagates
            #   unchanged (replaces the no-data one); no broad except here.
            raise
        # FILL IN: run + log the post-hook on success — bounded by: same log format
        #   ("post-hook %s: %s"), then return result.
        return result

# occurrences: 1 (verified: grep -c '            result = loop.run_until_complete(self.fetch())' querysource/queries/multi/sources/base.py) — :147
# REPLACE with:
            result = loop.run_until_complete(self._fetch_with_hooks())
```
**Why**: this implements spec §2 Execution exactly. Pre-hook and post-hook failures surface through
the existing `run()` `except Exception` branch, which sets `self.exc`. Keep `run()`'s two `except`
branches untouched.

### `tests/test_thread_source_base.py` (MODIFY)
```python
# APPEND at end of file (after `test_exc_is_none_on_success`). New imports go at the top of the file:
#   from unittest.mock import AsyncMock, patch
#   from querysource.exceptions import DataNotFound, DriverError
#   from querysource.interfaces import source_hooks
#   from querysource.interfaces.source_hooks import SourceHooks


class _RecordingSource(ThreadSource):
    """Records fetch() into a shared event list; outcome configurable."""

    def __init__(self, *args, events: list, fetch_exc: Exception | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._events = events
        self._fetch_exc = fetch_exc

    async def fetch(self) -> pd.DataFrame:
        self._events.append("fetch")
        if self._fetch_exc is not None:
            raise self._fetch_exc
        return pd.DataFrame({"a": [1]})


_HOOKS = SourceHooks(pre=("UPDATE t SET a = 1",), post=("INSERT INTO l VALUES (1)",))


def _run(source) -> None:
    source.start()
    source.join()


class TestThreadSourceHooks:
    def test_fetch_with_hooks_order(self):
        events: list = []

        async def _exec(statements, **_kw):
            events.append("pre" if statements == list(_HOOKS.pre) else "post")
            return ["OK 1"]

        src = _RecordingSource("s", {}, None, asyncio.Queue(), events=events)
        src.set_hooks(_HOOKS)
        with patch.object(source_hooks, "execute_guarded", AsyncMock(side_effect=_exec)):
            _run(src)
        assert src.exc is None
        assert events == ["pre", "fetch", "post"]

    def test_pre_hook_failure_skips_fetch(self):
        # FILL IN: execute_guarded raises GuardedSQLError("boom", category="infra") →
        #   events == [] (fetch never ran), src.exc is that error, queue empty.
        pass

    def test_post_hook_skipped_on_fetch_failure(self):
        # FILL IN: fetch_exc=DriverError("x") → execute_guarded awaited once (pre only), src.exc is DriverError.
        pass

    def test_post_hook_runs_on_empty_result(self):
        # FILL IN: fetch_exc=DataNotFound("empty") → events == ["pre", "fetch", "post"],
        #   isinstance(src.exc, DataNotFound).
        pass

    def test_post_hook_error_on_empty_result(self):
        # FILL IN: fetch_exc=DataNotFound("empty"); post call raises GuardedSQLError →
        #   src.exc is the GuardedSQLError (not DataNotFound).
        pass

    def test_no_hooks_unchanged(self):
        events: list = []
        src = _RecordingSource("s", {}, None, asyncio.Queue(), events=events)
        with patch.object(source_hooks, "execute_guarded", AsyncMock()) as ex:
            _run(src)
        ex.assert_not_awaited()
        assert src.exc is None and events == ["fetch"]

    def test_mixin_mro(self):
        from querysource.interfaces.source_hooks import SourceHooksMixin
        import threading
        mro = ThreadSource.__mro__
        assert mro.index(SourceHooksMixin) < mro.index(threading.Thread)
```

### `tests/test_provider_hook_dialects.py` (CREATE)
```python
"""FEAT-157 — only PostgreSQL providers declare the SQL hooks dialect (TASK-824)."""
from querysource.interfaces.source_hooks import SourceHooksMixin
from querysource.providers.abstract import BaseProvider
from querysource.providers.db import dbProvider
from querysource.providers.pg import pgProvider
from querysource.providers.sqlserver import sqlserverProvider


def test_provider_dialects():
    assert issubclass(BaseProvider, SourceHooksMixin)
    assert BaseProvider.sql_hooks_dialect is None
    assert pgProvider.sql_hooks_dialect == "postgres"
    assert dbProvider.sql_hooks_dialect == "postgres"
    assert sqlserverProvider.sql_hooks_dialect is None
    # FILL IN: bigqueryProvider.sql_hooks_dialect is None — bounded by: import it inside the
    #   test; if its optional google deps are missing use pytest.importorskip("google.cloud.bigquery").
```

### FILL IN checklist
- [ ] `base.py::ThreadSource._fetch_with_hooks`: post-hook on success and on no-data (then re-raise), bounded by spec §2 and AC-3.
- [ ] `tests/test_thread_source_base.py`: finish the four stubbed tests.
- [ ] `tests/test_provider_hook_dialects.py`: add the bigquery assertion.

---

## Acceptance Criteria

- [ ] `BaseProvider` and `ThreadSource` inherit `SourceHooksMixin`, with the mixin first in the bases. `ThreadSource(...)` still constructs and starts as a thread.
- [ ] `pgProvider.sql_hooks_dialect == dbProvider.sql_hooks_dialect == "postgres"`. `sqlserverProvider` and `bigqueryProvider` stay `None`.
- [ ] The order is pre-hook → retrieval → post-hook. A pre-hook failure skips `fetch()`. The post-hook runs on success and on `DataNotFound`/`NoDataFound`, which is re-raised so `exc` stays no-data. It is skipped on any other error. A post-hook error on an empty result becomes `exc`.
- [ ] Without hooks, `run()` behaves exactly as before, and every pre-existing test in `tests/test_thread_source_base.py` passes unchanged.
- [ ] `ruff check` is clean on the three modified modules and the two test files.

## Validation Commands

- `pytest tests/test_thread_source_base.py -q`
- `pytest tests/test_provider_hook_dialects.py -q`
- `pytest tests/test_multiqs_sources_integration.py -q`
- `pytest tests/test_multiqs_destination_dispatch.py -q`

---

## Test Specification

See the two test blocks above (spec §4 M2 rows: `test_provider_dialects`, `test_fetch_with_hooks_order`,
`test_pre_hook_failure_skips_fetch`, `test_post_hook_skipped_on_fetch_failure`,
`test_post_hook_runs_on_empty_result`, `test_post_hook_error_on_empty_result`, `test_no_hooks_unchanged`).

---

## Agent Instructions

1. **Precondition**: FEAT-156 is merged into `dev` (`querysource/interfaces/guarded_sql.py` exists). If it is not, STOP.
2. Work in the feature worktree (`python -m scripts.sdd.ensure_worktree --slug multi-source-hooks --feature-id FEAT-157`).
3. Read the spec. Check that TASK-823 is `"done"` in `sdd/tasks/index/multi-source-hooks.json`.
4. Verify the Codebase Contract, re-running each `grep -c` anchor, before writing code.
5. Set the task to `"in-progress"` in the index (with `started_at`), then implement from the blueprint and complete every `# FILL IN:`.
6. Run the Validation Commands. Commit only the listed files.
7. Close with `scripts/sdd/close_task.sh TASK-824 multi-source-hooks verified`, then fill in the Completion Note.

---

## Completion Note

**Completed by**: sdd-worker (sequential fallback, Claude)
**Date**: 2026-09-30
**Notes**: Mixin on BaseProvider/ThreadSource (first in bases), pgProvider dialect, ThreadSource._fetch_with_hooks + run() swap, tests. 17 + 1 + 8 + (sources integration 8 pass, 1 fail) tests. tests/test_multiqs_sources_integration.py::test_guardrail_rejects_too_many_sources fails identically without my change (pre-existing, SlugNotFound needs DB).

**Deviations from spec**: none (abstract.py import placed in ruff-sorted position)
