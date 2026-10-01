# TASK-825: MultiQS wiring — PBAC gate, pop/reject, validation, `ThreadQuery(hooks=)`

**Feature**: FEAT-157 — MultiQuery Source Pre/Post-Hooks (PostgreSQL)
**Spec**: `sdd/specs/multi-source-hooks.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: L (4-8h)
**Depends-on**: TASK-823, TASK-824
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 3 and §2 "Configuration", "Validation", "PBAC". This task connects the hooks to
`MultiQS`. It adds four things:
- the `pg_admin` PBAC gate in `_preflight_principal`;
- popping hook keys from each `queries` entry **before** request conditions are merged, and
  rejecting hook keys that arrive through request conditions;
- validation 1-4 (location → dialect → same database → guard), all before any thread starts;
- handing the approved `SourceHooks` to `ThreadQuery` through a new keyword-only `hooks` parameter.

> **Cross-feature rebase required.** FEAT-155 TASK-817 (`multi-tabledelete`) and FEAT-156
> TASK-822 (`multi-executesql`) also modify `_preflight_principal` in
> `querysource/queries/multi/__init__.py`. They insert their `WRITE_DESTINATIONS` gate at the
> same spot, before `if has_raw_child:`. Before starting, rebase the feature branch on `dev` after
> FEAT-155 and FEAT-156 are merged. Then re-run every `grep -c` below and update the line numbers.
> Put the hook gate **next to** (after) their write gate. Do not merge the two gates into one
> condition.
> FEAT-156 must be merged in any case, because TASK-823's module imports `guarded_sql`.

---

## Scope

- Module level in `querysource/queries/multi/__init__.py`:
  - `PG_HOOK_DRIVERS = frozenset({"pg", "postgres", "postgresql"})`;
  - `_declares_hooks(cfg)`;
  - an import of `HOOK_KEYS`, `GuardedSQLError`, `SourceHooks`, `build_hooks`, `pop_hooks`.
- `MultiQS._hooks_target_same_database()`: `(DBHOST, DBPORT, DBNAME) == (PG_HOST, PG_PORT, PG_DATABASE)`.
  Read the values through the already-imported `conf` module and compare them as `str`.
- `MultiQS._validate_source_hooks(name, query, definition, pre, post)`: spec §2 validation steps 2-4.
- `_preflight_principal`: when a principal is set and any `queries` entry declares a hook, call
  `enforce_principal(principal, ResourceType.DATASOURCE, "pg_admin", "datasource:use", tenant=…, logger=…)`.
- Reject hook keys coming from request conditions in two places:
  - **(a)** the per-entry `conditions = self._conditions.pop(name, {})`;
  - **(b)** the single-query-slug wrap (`self._queries = {self.slug: {"slug": self.slug, **slug_conditions}}`),
    where top-level request conditions become the entry itself. Without this check, a request
    condition `pre-hook` would be accepted as a pipeline hook. That breaks spec §2, which says
    hook SQL "can come only from the pipeline definition".
- In the queries dispatch:
  - `pop_hooks(query)` from the pipeline entry **before** `query = {**conditions, **query}`;
  - validate on the merged entry after the `remote`/`worker`/`tenant` pops;
  - pass `hooks=` to `ThreadQuery`.
- Reject `pre-hook`/`post-hook` on `files:` and `sources:` entries with
  `DriverError("hooks are only supported on PostgreSQL 'queries' entries")`.
- `ThreadQuery.__init__`: add a keyword-only `hooks: Optional[SourceHooks] = None`, then call
  `self.set_hooks(hooks)`.
- Create `tests/test_multiqs_source_hooks.py`.

**NOT in scope**:
- Mixin and `_fetch_with_hooks` (TASK-824). Hook model and guard calls (TASK-823).
- Catalog docs (TASK-826).
- Hooks on `TableSource`/`sources:` or on non-PG drivers (spec Non-Goals).
- Passing a principal from the HTTP handler into MultiQS (out of scope). The HTTP path is gated in `_preflight_multiquery` instead (see the `handlers/multi.py` block).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/queries/multi/__init__.py` | MODIFY | Gate, reject/pop, validation, `hooks=` to ThreadQuery, sources/files rejection |
| `querysource/queries/multi/sources/query.py` | MODIFY | `ThreadQuery.__init__(…, *, hooks=None)` → `self.set_hooks` |
| `tests/test_multiqs_source_hooks.py` | CREATE | M3 unit tests |
| `querysource/handlers/multi.py` | MODIFY | HTTP gate: `write_access` also true when any inline `queries` entry declares a hook |
| `tests/test_multiquery_hooks_gate_http.py` | CREATE | HTTP-level hook gate tests |

---

## Codebase Contract (Anti-Hallucination)

Re-verified against `dev` @ `f8a32ae`. There is no code drift since the spec's `7337dbb`.
Line-number corrections against the spec:
- `_preflight_principal` is defined at **:240**, not :239.
- The **files** loop is at **:536** and the **sources** loop at **:541/:544**. The spec's ":541, :556" refers to the sources loop only.

### Verified Imports
```python
# already in querysource/queries/multi/__init__.py:
from ... import conf                                             # :9  (conf.DBHOST etc. — patchable in tests)
from ...exceptions import DataNotFound, DriverError, …, QueryException  # :11-19
from .sources import FileSource, ThreadQuery                     # :24
from .sources.executors import RemoteConfig                      # :25
# local imports inside _preflight_principal (keep the lazy style):
from ...auth._resource_types import ResourceType                 # :248 (ResourceType.DATASOURCE: auth/_resource_types.py:24/:43)
from ...auth.enforcement import enforce_principal                # :249
# NEW (TASK-823):
from ...interfaces.source_hooks import HOOK_KEYS, GuardedSQLError, SourceHooks, build_hooks, pop_hooks
# in querysource/queries/multi/sources/query.py (relative style, see base.py:11 `from ....exceptions`):
from ....interfaces.source_hooks import SourceHooks
# conf names (querysource/conf.py): DBHOST :24, DBNAME :27, DBPORT :28, PG_HOST :38, PG_DATABASE :41, PG_PORT :42
#   NOTE: *PORT values are str from env or int fallback (5432) — compare as str.
```

### Existing Signatures to Use
```python
# querysource/queries/multi/__init__.py
class MultiQS(BaseQuery):                                         # :101  (BaseQuery → AbstractQuery(Connection))
    async def _preflight_principal(self) -> None:                 # :240; early `return` when self._principal is None (:246)
        has_raw_child = False                                     # :251
        for query_cfg in (self._queries or {}).values():          # :252
        if has_raw_child:                                         # :272
    async def query(self):                                        # :278
        child_definitions: dict[str, LoadedDefinition] = {}       # :287
                slug_conditions = (dict(self._conditions) if isinstance(self._conditions, dict) else {})  # :365-369
                self._queries = {self.slug: {"slug": self.slug, **slug_conditions}}  # :370-372
        await self._preflight_principal()                         # :377
                        child_definitions[name] = top_definition / await repo.get(ident)  # :450 / :452
                conditions = self._conditions.pop(name, {})       # :468
                query = {**conditions, **query}                   # :470
                is_remote = query.pop("remote", False)            # :474  (precedent: pop routing keys)
                query.pop("tenant", None)                         # :477
                    t = ThreadQuery(name, query, self._request, self._queue,
                        remote_config=remote_config, store=resolved_stores.get(name),
                        definition=child_definitions.get(name),)  # :517-528 (wrapped in try/except → self.Error)
            for name, file in self._files.items():                # :536
                for source_type, config in entry.items():         # :544
                    t = cls(name, config, self._request, self._queue)  # :556

# querysource/interfaces/connections.py
def load_provider(self, provider: str) -> BaseProvider:           # :374 — returns the CLASS; raises QueryException when
                                                                  #   querysource.providers.<provider> does not exist (named datasource)
# querysource/tenants.py:54
class LoadedDefinition: identity; runtime: QueryModel; revision
# querysource/models.py:81 — QueryModel.provider: str default 'db'

# querysource/auth/enforcement.py:134
async def enforce_principal(principal, resource_type, resource_name: str, action: str, *,
                            tenant: str | None = None, logger=None) -> AccessDecision

# querysource/queries/multi/sources/query.py
class ThreadQuery(ThreadSource):                                  # :13
    def __init__(self, name, query: dict, request, queue, remote_config=None, *,
                 store: Optional[QueryStore] = None,              # :38
                 definition: Optional[LoadedDefinition] = None,   # :39
                 ):                                               # :40
        self._definition = definition                             # :50

# TASK-823 / TASK-824
HOOK_KEYS = ("pre-hook", "post-hook"); pop_hooks(entry) -> (pre, post); build_hooks(pre, post) -> SourceHooks | None
SourceHooksMixin.set_hooks(hooks); pgProvider/dbProvider.sql_hooks_dialect == "postgres"
```

### Does NOT Exist
- ~~`PG_HOOK_DRIVERS`, `_declares_hooks`, `MultiQS._validate_source_hooks`, `MultiQS._hooks_target_same_database`~~: this task adds them.
- ~~`ThreadQuery(hooks=…)`~~ before this task.
- ~~`WRITE_DESTINATIONS`~~ on `dev` @ `f8a32ae`: it arrives with FEAT-155. This task does not depend on it.
- ~~A principal passed by `handlers/multi.py`~~: `MultiQS(...)` at `handlers/multi.py:493` passes none, so the gate only fires for principal-based callers. This task does not change that; it gates HTTP callers in the handler instead.
- ~~`load_provider` returning an instance~~: it returns the provider **class**.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/queries/multi/__init__.py", "action": "MODIFY"},
    {"path": "querysource/queries/multi/sources/query.py", "action": "MODIFY"},
    {"path": "tests/test_multiqs_source_hooks.py", "action": "CREATE"},
    {"path": "querysource/handlers/multi.py", "action": "MODIFY"},
    {"path": "tests/test_multiquery_hooks_gate_http.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/queries/multi/__init__.py#MultiQS",
    "sym:querysource/queries/multi/__init__.py#MultiQS._preflight_principal",
    "sym:querysource/queries/multi/__init__.py#MultiQS.query",
    "sym:querysource/queries/multi/sources/query.py#ThreadQuery.__init__",
    "sym:querysource/interfaces/connections.py#Connection.load_provider",
    "sym:querysource/auth/enforcement.py#enforce_principal",
    "sym:querysource/tenants.py#LoadedDefinition"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- **Only when a hook is declared.** Pipelines without hook keys must see no gate call, no
  `load_provider`, no DB comparison and no guard call (AC-9, `test_pipeline_without_hooks_untouched`).
- **Order of errors**, following spec §2:
  1. location (sources/files);
  2. dialect (slug child: `load_provider(definition.runtime.provider or "db").sql_hooks_dialect == "postgres"`,
     where a `QueryException` from `load_provider` means unsupported; raw child: `driver` lower-cased
     ∈ `PG_HOOK_DRIVERS` **and** no `datasource` key);
  3. same database;
  4. guard (`GuardedSQLError` → `DriverError(f"{name}: {err}")` with `from err`).
- Validate the **merged** entry, after `{**conditions, **query}`, so a `datasource` injected through
  request conditions is also caught.
- Error text for dialect: `DriverError(f"{name}: hooks are not supported for provider/driver {x}")`.
- `_validate_source_hooks` runs **outside** the `try:` that wraps `ThreadQuery(...)`. Otherwise its
  `DriverError` would be re-wrapped by `self.Error(...)` into a generic error.
- Keep the lazy `ResourceType`/`enforce_principal` imports. Tests monkeypatch `enforcement.enforce_principal`
  (see `tests/multi/test_multiqs_principal.py`).

### Known Risks
- The HTTP handler never passes `principal=`, so `_preflight_principal` protects principal callers
  (the scheduler) only. HTTP callers are gated in `QueryHandler._preflight_multiquery` through the
  `write_access` flag, which FEAT-155 TASK-817 introduced and this task extends to hooks.
- A slug whose `provider` name is also a registered datasource, such as a datasource literally named
  `pg`, resolves through `get_provider` to that datasource. `load_provider` still says `pgProvider`.
  That case is out of scope. Mention it in the Completion Note if tests surface it.

---

## Implementation Blueprint

### Steps (in order)
1. Rebase on `dev` after FEAT-155/156, then re-run every `grep -c` below. *Why*: those features edit `_preflight_principal`.
2. Add the import and the module-level helpers. *Why*: `_declares_hooks` is used by both the gate and the dispatch.
3. Add the two `MultiQS` methods. *Why*: they keep the dispatch loop readable and make validation unit-testable.
4. Add the gate. *Why*: PBAC first, before any repository or database work (spec §2 PBAC).
5. Add the conditions rejections (a) and (b), the pop, the validation and `hooks=`. *Why*: spec §2 Configuration and Validation.
6. Add the sources/files rejection. *Why*: spec §2 validation step 1.
7. Add the `ThreadQuery` parameter. *Why*: the thread stores the hooks through the mixin.
8. Write the tests and run the Validation Commands.

### `querysource/queries/multi/__init__.py` (MODIFY) — imports + module helpers
```python
# occurrences: 1 (verified: grep -c 'from .sources.executors import RemoteConfig' querysource/queries/multi/__init__.py) — :25
# AFTER — insert below `from .sources.executors import RemoteConfig`:
from ...interfaces.source_hooks import (
    HOOK_KEYS,
    GuardedSQLError,
    SourceHooks,
    build_hooks,
    pop_hooks,
)

# occurrences: 1 (verified: grep -c 'def classify_output_error(exc: BaseException) -> str | None:' querysource/queries/multi/__init__.py) — :52
# BEFORE — insert above `def classify_output_error(exc: BaseException) -> str | None:`:
# FEAT-157: raw ``queries`` drivers whose sources may declare pre/post-hooks.
PG_HOOK_DRIVERS: frozenset[str] = frozenset({"pg", "postgres", "postgresql"})
_HOOKS_LOCATION_ERROR = "hooks are only supported on PostgreSQL 'queries' entries"


def _declares_hooks(cfg: object) -> bool:
    """True when a source entry dict carries a ``pre-hook`` or ``post-hook`` key."""
    return isinstance(cfg, dict) and any(key in cfg for key in HOOK_KEYS)


```

### `querysource/queries/multi/__init__.py` (MODIFY) — MultiQS methods
```python
# occurrences: 1 (verified: grep -c '    async def _preflight_principal(self) -> None:' querysource/queries/multi/__init__.py) — :240
# BEFORE — insert above `    async def _preflight_principal(self) -> None:` (line :239 is the
#   `return child_tenant, …` of resolve_child_owner; keep one blank line between methods):
    def _hooks_target_same_database(self) -> bool:
        """True when hooks (``DB*``) and ``db``/``pg`` reads (``PG_*``) hit the same database."""
        hook_target = (str(conf.DBHOST), str(conf.DBPORT), str(conf.DBNAME))
        read_target = (str(conf.PG_HOST), str(conf.PG_PORT), str(conf.PG_DATABASE))
        return hook_target == read_target

    def _validate_source_hooks(
        self,
        name: str,
        query: dict,
        definition: "LoadedDefinition | None",
        pre: object,
        post: object,
    ) -> "SourceHooks | None":
        """Apply spec §2 validation 2-4 for one ``queries`` entry.

        Args:
            name: The entry alias (DataFrame name).
            query: The merged entry (hook keys already popped).
            definition: The preloaded slug definition (``None`` for raw children).
            pre: Raw ``pre-hook`` value, or ``None``.
            post: Raw ``post-hook`` value, or ``None``.

        Returns:
            ``None`` when no hook is declared, else the guard-approved hooks.

        Raises:
            DriverError: unsupported provider/driver, DB mismatch, or guard rejection.
        """
        if pre is None and post is None:
            return None
        # FILL IN: dialect check — bounded by: slug child ("slug" in query) → definition must be
        #   non-None; provider = getattr(definition.runtime, "provider", None) or "db";
        #   cls = self.load_provider(provider) (QueryException → unsupported);
        #   getattr(cls, "sql_hooks_dialect", None) == "postgres". Raw child → str(query.get("driver") or "").lower()
        #   in PG_HOOK_DRIVERS and "datasource" not in query. Else
        #   DriverError(f"{name}: hooks are not supported for provider/driver {x}").
        if not self._hooks_target_same_database():
            raise DriverError(
                f"{name}: hooks run on DB* credentials but DB* and PG_* point at "
                "different databases; refusing to run them"
            )
        try:
            return build_hooks(pre, post)
        except GuardedSQLError as err:
            raise DriverError(f"{name}: {err}") from err

```

### `querysource/queries/multi/__init__.py` (MODIFY) — PBAC gate
```python
# occurrences: 1 (verified: grep -c '        if has_raw_child:' querysource/queries/multi/__init__.py) — :272
#   (after the FEAT-155/156 rebase: insert AFTER their WRITE_DESTINATIONS gate, still before this line)
# BEFORE — insert above `        if has_raw_child:`:
        # FEAT-157: pre/post-hooks run with full-access DB* credentials.
        if any(_declares_hooks(cfg) for cfg in (self._queries or {}).values()):
            await enforce_principal(
                self._principal, ResourceType.DATASOURCE, "pg_admin", "datasource:use",
                tenant=self._tenant_selector, logger=self._logger,
            )

```

### `querysource/queries/multi/__init__.py` (MODIFY) — request-condition rejection (b), single-slug wrap
```python
# occurrences: 1 (verified: grep -c '                self._queries = {' querysource/queries/multi/__init__.py) — :370
#   context (unique):
#                   else {}
#               )
#               self._queries = {
#                   self.slug: {"slug": self.slug, **slug_conditions}
# BEFORE — insert above `                self._queries = {`:
                if _declares_hooks(slug_conditions):
                    raise DriverError(
                        "'pre-hook'/'post-hook' cannot be passed as request conditions"
                    )
```

### `querysource/queries/multi/__init__.py` (MODIFY) — dispatch: reject (a), pop, validate, `hooks=`
```python
# occurrences: 1 (verified: grep -c '                # those conditions be applied to the query' querysource/queries/multi/__init__.py) — :469
# BEFORE — insert above `                # those conditions be applied to the query`
#   (i.e. between `conditions = self._conditions.pop(name, {})` :468 and the merge :470):
                # FEAT-157: hook SQL only from the pipeline entry, never from request conditions;
                # popped BEFORE the merge so it never reaches QueryObject as a condition.
                if _declares_hooks(conditions):
                    raise DriverError(
                        f"{name}: 'pre-hook'/'post-hook' cannot be passed as request conditions"
                    )
                pre_hook, post_hook = pop_hooks(query)

# occurrences: 1 (verified: grep -c '                query.pop("tenant", None)' querysource/queries/multi/__init__.py) — :477
# AFTER — insert below `                query.pop("tenant", None)`:
                hooks = self._validate_source_hooks(
                    name, query, child_definitions.get(name), pre_hook, post_hook
                )

# occurrences: 1 (verified: grep -c '                        definition=child_definitions.get(name),' querysource/queries/multi/__init__.py) — :527
# AFTER — insert below `                        definition=child_definitions.get(name),`:
                        hooks=hooks,
```

### `querysource/queries/multi/__init__.py` (MODIFY) — files / sources rejection
```python
# occurrences: 1 (verified: grep -c '            for name, file in self._files.items():' querysource/queries/multi/__init__.py) — :536
# AFTER — insert below `            for name, file in self._files.items():`:
                if _declares_hooks(file):
                    raise DriverError(f"{name}: {_HOOKS_LOCATION_ERROR}")

# occurrences: 1 (verified: grep -c '                for source_type, config in entry.items():' querysource/queries/multi/__init__.py) — :544
# AFTER — insert below `                for source_type, config in entry.items():`:
                    if _declares_hooks(config):
                        raise DriverError(f"{source_type}: {_HOOKS_LOCATION_ERROR}")
```
**Why**: this is spec §2 validation step 1. All of it happens in the dispatch phase, before the
`t.start()` loop at :566+, so nothing starts on a violation.

### `querysource/queries/multi/sources/query.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'from .base import ThreadSource' querysource/queries/multi/sources/query.py) — :9
# BEFORE — insert above `from .base import ThreadSource`:
from ....interfaces.source_hooks import SourceHooks

# occurrences: 1 (verified: grep -c '        definition: Optional\[LoadedDefinition\] = None,' querysource/queries/multi/sources/query.py) — :39
# AFTER — insert below `        definition: Optional[LoadedDefinition] = None,`:
        hooks: Optional[SourceHooks] = None,

# occurrences: 1 (verified: grep -c '        self._definition = definition' querysource/queries/multi/sources/query.py) — :50
# AFTER — insert below `        self._definition = definition`:
        # FEAT-157: MultiQS-validated pre/post-hooks, run around fetch() by ThreadSource.run().
        self.set_hooks(hooks)
```

### `tests/test_multiqs_source_hooks.py` (CREATE)
```python
"""FEAT-157 — MultiQS source pre/post-hook wiring (TASK-825)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import querysource.auth.enforcement as enforcement
import querysource.queries.multi as multiqs_module
from querysource.auth.principal import QSPrincipal
from querysource.exceptions import DriverError, QueryAccessDenied
from querysource.interfaces import source_hooks
from querysource.queries.multi import MultiQS


class _Stop(Exception):
    """Raised by the fake ThreadQuery so query() stops right after dispatch."""


@pytest.fixture
def same_db(monkeypatch):
    for db_key, pg_key in (("DBHOST", "PG_HOST"), ("DBPORT", "PG_PORT"), ("DBNAME", "PG_DATABASE")):
        monkeypatch.setattr(multiqs_module.conf, pg_key, getattr(multiqs_module.conf, db_key))


@pytest.fixture
def guard_ok(monkeypatch):
    monkeypatch.setattr(source_hooks, "guard_statements", lambda sql: [sql] if isinstance(sql, str) else list(sql))


@pytest.fixture
def captured(monkeypatch):
    calls: dict = {}

    def _fake_thread_query(name, query, request, queue, **kwargs):
        calls[name] = {"query": dict(query), **kwargs}
        raise _Stop()

    monkeypatch.setattr(multiqs_module, "ThreadQuery", _fake_thread_query)
    return calls


def _repo(provider: str = "db"):
    definition = SimpleNamespace(identity=None, runtime=SimpleNamespace(provider=provider), revision="r")
    repo = SimpleNamespace(registry=SimpleNamespace(resolve=lambda tenant: None), get=AsyncMock(return_value=definition))
    return AsyncMock(return_value=repo)


async def test_hooks_not_forwarded_as_conditions(same_db, guard_ok, captured):
    mqs = MultiQS(queries={"q1": {"query": "SELECT 1", "driver": "pg", "pre-hook": "UPDATE t SET a = 1"}})
    mqs.get_definition_repository = _repo()
    with pytest.raises(Exception):  # _Stop is re-wrapped by self.Error(...)
        await mqs.query()
    assert "pre-hook" not in captured["q1"]["query"]
    assert captured["q1"]["hooks"].pre == ("UPDATE t SET a = 1",)


async def test_hooks_gate(same_db, guard_ok, captured, monkeypatch):
    # FILL IN: principal=QSPrincipal(user_id="35", groups=("sales",)); (a) allow mock → an await with
    #   args[1:4] == (ResourceType.DATASOURCE, "pg_admin", "datasource:use"); (b) deny mock
    #   (AsyncMock(side_effect=QueryAccessDenied())) → QueryAccessDenied and captured == {} (no dispatch).
    pass


async def test_hooks_rejected_from_request_conditions(same_db, guard_ok, captured):
    # FILL IN: MultiQS(queries={"q1": {"query": "SELECT 1", "driver": "pg"}},
    #   conditions={"q1": {"pre-hook": "UPDATE t SET a = 1"}}) → DriverError, captured == {};
    #   and single-slug path: MultiQS(slug="s", conditions={"pre-hook": "…"}), get_slug stubbed to
    #   SimpleNamespace(query_raw="") → DriverError.
    pass


async def test_hooks_unsupported_driver(same_db, guard_ok, captured):
    # FILL IN: parametrize — slug child with provider "sqlserver"/"bigquery" (stub mqs.load_provider to
    #   return SimpleNamespace(sql_hooks_dialect=None)); raw driver "mysql"; raw {"driver": "pg",
    #   "datasource": "x"} → DriverError match "not supported", captured == {}.
    pass


async def test_hooks_on_sources_or_files_rejected(same_db, guard_ok):
    # FILL IN: files={"f": {"path": "/tmp/x.csv", "post-hook": "…"}} and
    #   query={"sources": [{"AirtableSource": {"pre-hook": "…"}}]} → DriverError(_HOOKS_LOCATION_ERROR).
    pass


async def test_hooks_db_mismatch(guard_ok, captured, monkeypatch):
    # FILL IN: monkeypatch conf.PG_HOST = "other-host" (DBHOST differs) → DriverError, captured == {}.
    pass


async def test_pipeline_without_hooks_untouched(captured, monkeypatch):
    # FILL IN: guard_statements / load_provider / enforce_principal mocks must not be called; the entry
    #   reaches ThreadQuery unchanged and hooks=None is passed.
    pass
```

### `querysource/handlers/multi.py` (MODIFY) — HTTP gate for hooks
```python
# PREREQUISITE: FEAT-155 TASK-817 (merged) added `write_access=` to `_preflight_multiquery` and computes it
# at the first `await self._preflight_multiquery(` call (handlers/multi.py ~:458) from the inline Output.
# occurrences: expected 1 after FEAT-155 merge (verify: grep -c 'and _output_step_names(options.get("Output")) & WRITE_DESTINATIONS' querysource/handlers/multi.py)
# 1) extend the import added by TASK-817:
from ..queries.multi import WRITE_DESTINATIONS, _declares_hooks, _output_step_names
# 2) extend the write_access expression so hooks also require the pg_admin grant:
            write_access=bool(
                not slug and isinstance(options, dict)
                and (
                    _output_step_names(options.get("Output")) & WRITE_DESTINATIONS
                    or any(_declares_hooks(cfg) for cfg in (_queries or {}).values())
                )
            ),
```
**Why**: HTTP requests never pass `principal=` to MultiQS (`handlers/multi.py:493`), so the `_preflight_principal` gate never fires for API callers. Without this, any caller allowed to run a multi could execute hook SQL with `DB*` credentials. Stored multi slugs are out of this check: their hooks come from the stored definition, and request data cannot add hook keys, because this task rejects hook keys in request conditions.

### `tests/test_multiquery_hooks_gate_http.py` (CREATE)
```python
"""FEAT-157 / TASK-825: HTTP-level gate for source hooks."""
from querysource.handlers.multi import QueryHandler


async def test_inline_hook_requires_pg_admin():
    # FILL IN: drive QueryHandler's query path (or the call-site helper) with an inline payload
    #          {"queries": {"q": {"query": "SELECT 1", "driver": "pg", "pre-hook": "UPDATE t SET a=1"}}};
    #          mock _preflight_multiquery and assert it was awaited with write_access=True.
    #          Pattern: tests/handlers/test_multiquery_pbac_smoke.py (_make_handler, :16)
    ...


async def test_no_hooks_no_write_access():
    # FILL IN: same payload without hook keys and without write Output -> write_access=False
    ...
```

### FILL IN checklist
- [ ] `handlers/multi.py`: re-verify the TASK-817 anchor after rebase; keep the Output clause unchanged.
- [ ] `__init__.py::MultiQS._validate_source_hooks`: the dialect branch, bounded by spec §2 validation step 2 and the error text above.
- [ ] Re-anchor the gate after the FEAT-155/156 rebase, bounded by: after their write gate, before `if has_raw_child:`.
- [ ] `tests/test_multiqs_source_hooks.py`: finish the six stubbed tests.

---

## Acceptance Criteria

- [ ] When a principal is set, declaring any hook calls `enforce_principal(principal, DATASOURCE, "pg_admin", "datasource:use")`. A deny raises `QueryAccessDenied` before any repository work or dispatch.
- [ ] Hook keys never reach `ThreadQuery`'s `query` dict, and so never reach `QueryObject` conditions. `ThreadQuery` receives `hooks=SourceHooks(...)`, or `None`.
- [ ] Hook keys in request conditions raise `DriverError`. This covers both the per-entry conditions and the single-query-slug wrap.
- [ ] Hooks on non-PG slug providers, on raw non-PG drivers, on a raw child with a `datasource`, or on `files:`/`sources:` entries raise `DriverError` before any thread starts.
- [ ] A `DB*` vs `PG_*` mismatch raises `DriverError`. A guard rejection becomes a `DriverError` carrying the guard message.
- [ ] Pipelines without hook keys dispatch exactly as before: no gate, no `load_provider`, no guard.
- [ ] HTTP callers: an inline pipeline declaring any hook calls `_preflight_multiquery(..., write_access=True)`, which denies (404) without `datasource:use` on `pg_admin` before anything runs.
- [ ] Existing regressions pass: `tests/test_multiqs_remote_dispatch.py`, `tests/multi/test_multiqs_principal.py`, `tests/test_multiqs_sources_integration.py`.
- [ ] `ruff check` is clean on both modified modules and the new test file.

## Validation Commands

- `pytest tests/test_multiqs_source_hooks.py -q`
- `pytest tests/test_multiqs_remote_dispatch.py -q`
- `pytest tests/multi/test_multiqs_principal.py -q`
- `pytest tests/test_multiqs_sources_integration.py -q`
- `pytest tests/test_multiqs_destination_dispatch.py -q`
- `pytest tests/test_thread_source_base.py -q`
- `pytest tests/test_multiquery_hooks_gate_http.py -q`
- `pytest tests/handlers/test_multiquery_pbac_smoke.py -q`

---

## Test Specification

See the `tests/test_multiqs_source_hooks.py` block above (spec §4 M3 rows: `test_hooks_gate`,
`test_hooks_rejected_from_request_conditions`, `test_hooks_not_forwarded_as_conditions`,
`test_hooks_unsupported_driver`, `test_hooks_on_sources_or_files_rejected`, `test_hooks_db_mismatch`,
`test_pipeline_without_hooks_untouched`).

---

## Agent Instructions

1. **Precondition**: FEAT-156 is merged into `dev`. If FEAT-155 is merged too, rebase the feature branch on `dev` first and re-verify every anchor.
2. Work in the feature worktree (`python -m scripts.sdd.ensure_worktree --slug multi-source-hooks --feature-id FEAT-157`).
3. Read the spec. Check that TASK-823 and TASK-824 are `"done"` in `sdd/tasks/index/multi-source-hooks.json`.
4. Verify the Codebase Contract before writing code.
5. Set the task to `"in-progress"` in the index (with `started_at`), then implement from the blueprint and complete every `# FILL IN:`.
6. Run the Validation Commands. Commit only the listed files.
7. Close with `scripts/sdd/close_task.sh TASK-825 multi-source-hooks verified`, then fill in the Completion Note.

---

## Completion Note

**Completed by**: sdd-worker (sequential fallback, Claude)
**Date**: 2026-09-30
**Notes**: Wired hooks into MultiQS: PG_HOOK_DRIVERS/_declares_hooks, _hooks_target_same_database, _validate_source_hooks (dialect -> same DB -> guard), pg_admin gate next to the FEAT-155/156 write gate (separate condition), request-condition rejection in both places, pop before merge, files/sources rejection, ThreadQuery(hooks=), handler write_access extended. 11 + 2 new tests pass; regression suites green except pre-existing test_guardrail_rejects_too_many_sources.

**Deviations from spec**: none
