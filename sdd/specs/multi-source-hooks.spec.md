---
type: feature
base_branch: dev
projects: [multiquery, providers, auth]
tags: [multiquery, hooks, pre-hook, post-hook, postgres, sql-guard]
---

# Feature Specification: MultiQuery Source Pre/Post-Hooks (PostgreSQL)

**Feature ID**: FEAT-157
**Date**: 2026-09-30
**Author**: Juan2coder (design by Jesus Lara)
**Status**: draft
**Target version**: 5.2.0

---

## 1. Motivation & Business Requirements

### Problem Statement
Some pipelines need to change data **right before** it is read. For example, they refresh
`wm_assembly.employee_detail_profile` (delete the last 5 months and re-insert them) and then read
it. Other pipelines need to run a SQL command **right after** a successful read, for example to
write an audit/processed mark.

Flowtask allows this with an `ExecuteSQL` step anywhere in a task, but QuerySource has no
equivalent:
- **Slugs are read-only.** They run on `PG_*` (`asyncpg_url`, `querysource/conf.py:44`).
- **Output runs too late.** MultiQuery `Output` destinations (FEAT-155/156) run at the *end* of
  the pipeline and only when data exists.

Jesus Lara's design is to attach **pre- and post-hooks to each datasource entry of a
MultiQuery**, executed as an isolated sub-process before and after that source's retrieval. This
avoids adding an extra pipeline layer.

### Goals
- `pre-hook` / `post-hook` keys on a MultiQuery `queries` entry, each a SQL string or a list of
  SQL strings:
  ```json
  "pokemon_all_fso_odoo": {
    "slug": "pokemon_all_fso_odoo_new",
    "pre-hook": "UPDATE …",
    "post-hook": "INSERT INTO audit.fetch_log …"
  }
  ```
- **Hooks are defined only in the MultiQuery source entry, never in a slug definition.**
- **Isolated execution with different credentials (Option B):**
  - Hooks run on their own connection with the full-access `DB*` credentials (`default_dsn`).
  - The retrieval keeps its own read connection.
  - There is **no shared transaction**. Each hook is atomic on its own, all of its statements in one
    transaction.
- The `pre-hook` runs before the source's retrieval. If it fails, the source is not fetched.
- The `post-hook` runs **only if the retrieval did not fail**.
- A common capability layer: a `SourceHooksMixin` that decorates both `BaseProvider` and
  `ThreadSource`.
- Hooks SQL goes through the FEAT-156 Rust guard (`sql_guard`) and executor (`guarded_sql`). No
  destructive DDL is allowed, and the check is fail-closed.
- **PostgreSQL only in this release.** Pipelines that do not declare hooks behave exactly as
  today, on every driver.
- PBAC: declaring any hook requires `datasource:use` on `pg_admin`, the same grant as FEAT-155/156.
- The MultiQuery `Query` catalog/JSON schema documents `pre-hook` and `post-hook`.

### Non-Goals (explicitly out of scope)
- Non-SQL hooks (API calls, e.g. before reading a Smartsheet). This is a follow-up.
- Hooks on non-PostgreSQL sources (BigQuery, SQL Server, MySQL, …), on `sources:` entries (`TableSource`, Airtable,
  Smartsheet, S3, SharePoint) and on `files:` entries. This is a follow-up.
- **Option A (same credentials, shared connection / transaction between pre-hook, fetch and
  post-hook).** Jesus confirmed that a pre-hook cannot open a transaction closed by the post-hook
  under Option B. Switching to A later is a separate feature.
- Hooks declared in a stored slug definition (`QueryModel`).
- Ordering guarantees *between* different sources. Sources run concurrently, so a hook orders only
  its own source's retrieval.
- Hook placeholders / variable substitution.

---

## 2. Architectural Design

### Overview

**Configuration.**
- `pre-hook` and `post-hook` are optional keys of a `queries:` entry.
- Each value is a `str` or `list[str]` of PostgreSQL SQL.
- They are **popped from the entry before request conditions are merged and before the entry
  reaches `ThreadQuery`/`QueryObject`**. Otherwise they would be forwarded as slug conditions
  (see `query.catalog.yaml`: "Any additional keys on a `slug` entry are passed through as
  conditions").
- Keys named `pre-hook` / `post-hook` that arrive through **request conditions** are rejected with
  `DriverError`. Hook SQL can come only from the pipeline definition, never from query-string or
  body conditions.

**Validation. All of it runs before any source thread starts; a violation raises and nothing
executes.**
1. *Location*: a `pre-hook`/`post-hook` key on a `sources:` or `files:` entry →
   `DriverError("hooks are only supported on PostgreSQL 'queries' entries")`.
2. *Dialect*: the hook must resolve to PostgreSQL. The pre/post-hook runs against the same
   database the source reads.
   - **Slug child.** The provider class comes from `self.load_provider(definition.runtime.provider)`,
     using the definition MultiQS already loaded in `child_definitions`. It must have
     `sql_hooks_dialect == "postgres"`, which is true for `pgProvider` and therefore `dbProvider`.
     A provider that is a named datasource or any other class is unsupported.
   - **Raw query child** (`{"query": …}`). It must have `driver` ∈ {`pg`, `postgres`, `postgresql`}
     and **no** `datasource` key.
   - Anything else → `DriverError("<name>: hooks are not supported for provider/driver <x>")`.
3. *Same database*: hooks run on `DB*` while `db`/`pg` sources read on `PG_*`.
   `(DBHOST, DBPORT, DBNAME)` must equal `(PG_HOST, PG_PORT, PG_DATABASE)`. If it does not, raise
   `DriverError`, because the hook would change a different database than the one read.
4. *Guard*: `guard_statements(value)` (FEAT-156) runs per hook. `GuardedSQLError` → `DriverError`
   carrying the guard message. The approved statements are stored.

**PBAC.** In `_preflight_principal`, if any `queries` entry declares a hook, call
`enforce_principal(principal, ResourceType.DATASOURCE, "pg_admin", "datasource:use", …)`.
This is the same check FEAT-155/156 perform for write destinations.

**Execution (`ThreadSource.run`).**
- `ThreadQuery` receives the validated `SourceHooks` and stores them via the mixin
  (`set_hooks`).
- `ThreadSource.run()` replaces `loop.run_until_complete(self.fetch())` with
  `loop.run_until_complete(self._fetch_with_hooks())`:
  ```
  pre-hook  (if any)  → execute_guarded(pre)   — DB* connection, own transaction
  fetch()             → retrieval, unchanged (local or remote executor)
  post-hook (if any)  → execute_guarded(post)  — only when fetch() raised nothing
  ```
- A failing pre-hook means `fetch()` is not called. A failing post-hook fails the thread. In both
  cases `self.exc` is set, and MultiQS's existing child-failure handling fails the pipeline.
- Hook status tags are logged with the source name.

**Mixin on both parents (Jesus: "a mixin that decorates both").**
- `SourceHooksMixin` (`querysource/interfaces/source_hooks.py`) holds the hooks, exposes
  `set_hooks` / `has_hooks` / `run_pre_hook` / `run_post_hook`, and declares the capability
  `sql_hooks_dialect: ClassVar[str | None] = None`.
- `BaseProvider` and `ThreadSource` both inherit it.
- `pgProvider` sets `sql_hooks_dialect = "postgres"`. Every other provider keeps `None`, so
  their behaviour does not change.
- In this release the executing side is `ThreadSource`, and the provider side only declares
  capability. A future non-MultiQuery caller can run hooks on a provider with the same API.

### Component Diagram
```
MultiQS.query()
  ├─ _preflight_principal() ── any hook? ──→ enforce_principal(DATASOURCE, "pg_admin", "datasource:use")
  ├─ queries dispatch (per entry)
  │    ├─ pop "pre-hook"/"post-hook" (reject if present in request conditions)
  │    ├─ validate: location → dialect (load_provider(...).sql_hooks_dialect / raw driver)
  │    │            → same DB (DB* vs PG_*) → guard_statements()  [FEAT-156]
  │    └─ ThreadQuery(..., hooks=SourceHooks(pre, post))
  └─ thread start → ThreadSource.run()
        └─ _fetch_with_hooks():  run_pre_hook() → fetch() → run_post_hook()
                                   └─ execute_guarded() [FEAT-156]  (DB*, own transaction)
```

### Integration Points
| Existing Component | Integration Type | Notes |
|---|---|---|
| `BaseProvider` | extends (mixin) | gains `SourceHooksMixin`; default dialect `None` |
| `pgProvider` | modifies | `sql_hooks_dialect = "postgres"` |
| `ThreadSource` | extends (mixin) + modifies `run()` | wraps `fetch()` with hooks |
| `ThreadQuery` | modifies | new keyword-only `hooks` parameter |
| `MultiQS._preflight_principal` | modifies | hook → `pg_admin` gate |
| `MultiQS.query()` queries dispatch | modifies | pop / reject / validate hooks, pass to `ThreadQuery` |
| `Connection.load_provider` | uses | provider class from a slug definition's `provider` |
| `interfaces/guarded_sql.py` (FEAT-156) | uses | `guard_statements`, `execute_guarded`, `GuardedSQLError` |
| `query.catalog.yaml` | modifies | document `pre-hook` / `post-hook` |

### Data Models
```python
@dataclass(frozen=True)
class SourceHooks:
    pre: tuple[str, ...] = ()    # guard-approved statements
    post: tuple[str, ...] = ()
```

### New Public Interfaces
```python
class SourceHooksMixin:
    sql_hooks_dialect: ClassVar[str | None] = None
    def set_hooks(self, hooks: SourceHooks | None) -> None: ...
    @property
    def has_hooks(self) -> bool: ...
    async def run_pre_hook(self) -> list[str]: ...
    async def run_post_hook(self) -> list[str]: ...
```

---

## 3. Module Breakdown

#### Delegation-eligible modules

| Module | Eligible? | Decided patterns / exact contracts | Why not (if no) |
|---|---|---|---|
| M1: `source_hooks` (mixin, dataclass, parsing) | yes | `HOOK_KEYS`, `pop_hooks`, `SourceHooks`, `SourceHooksMixin` contracts below | — |
| M2: Apply mixin to `BaseProvider` / `pgProvider` / `ThreadSource` + `run()` wrap | yes | MRO order `SourceHooksMixin` first; `_fetch_with_hooks` semantics §2 | — |
| M3: MultiQS wiring (gate, pop/reject, validation, `ThreadQuery(hooks=)`) | yes | validation order §2 1-4, error types, gate call | — |
| M4: Catalog / JSON schema | yes | two attributes in `query.catalog.yaml`, regenerate `generated/` | — |

### Module 1: Source hooks core
- **Path**: `querysource/interfaces/source_hooks.py` (new)
- **Responsibility**: The hook data model, key parsing, and the mixin that runs hooks through the FEAT-156 executor.
- **Depends on**: FEAT-156 `querysource/interfaces/guarded_sql.py`
- **Interface Skeleton**:
  ```python
  # querysource/interfaces/source_hooks.py  (new)
  from dataclasses import dataclass
  from typing import ClassVar, Optional, Tuple, Union, List
  from querysource.interfaces.guarded_sql import execute_guarded, guard_statements, GuardedSQLError  # FEAT-156

  HOOK_KEYS: Tuple[str, str] = ("pre-hook", "post-hook")

  @dataclass(frozen=True)
  class SourceHooks:
      """Guard-approved hook statements of one source."""
      pre: Tuple[str, ...] = ()
      post: Tuple[str, ...] = ()

  def pop_hooks(entry: dict) -> Tuple[Optional[Union[str, List[str]]], Optional[Union[str, List[str]]]]:
      """Remove and return (``pre-hook``, ``post-hook``) raw values from ``entry`` (mutates it)."""

  def build_hooks(pre: Optional[Union[str, List[str]]], post: Optional[Union[str, List[str]]]) -> Optional[SourceHooks]:
      """Guard both values with ``guard_statements``; return None when both are absent.

      Raises:
          GuardedSQLError: blocked/unparsable SQL or Rust extension missing.
      """

  class SourceHooksMixin:
      """Pre/post SQL hooks capability shared by BaseProvider and ThreadSource.

      Hooks execute isolated on DB* credentials (``execute_guarded``), never on the retrieval connection.
      """
      sql_hooks_dialect: ClassVar[Optional[str]] = None
      _source_hooks: Optional[SourceHooks] = None

      def set_hooks(self, hooks: Optional[SourceHooks]) -> None:
          """Attach validated hooks (None clears them)."""

      @property
      def has_hooks(self) -> bool:
          """True when a pre- or post-hook is attached."""

      async def run_pre_hook(self) -> List[str]:
          """Execute the pre-hook statements (one transaction); return status tags ([] when none)."""

      async def run_post_hook(self) -> List[str]:
          """Execute the post-hook statements (one transaction); return status tags ([] when none)."""
  ```

### Module 2: Apply the mixin
- **Path**: `querysource/providers/abstract.py`, `querysource/providers/pg.py`, `querysource/queries/multi/sources/base.py` (modify)
- **Responsibility**: Decorate both parents with `SourceHooksMixin`, declare PostgreSQL capability on `pgProvider`, and wrap `ThreadSource.fetch()` with the hooks.
- **Depends on**: M1
- **Interface Skeleton**:
  ```python
  # querysource/providers/abstract.py:34  (verified: `class BaseProvider(ABC):`)
  class BaseProvider(SourceHooksMixin, ABC): ...

  # querysource/providers/pg.py:17  (verified: `class pgProvider(sqlProvider):`, __parser__ at :24)
  class pgProvider(sqlProvider):
      __parser__ = pgSQLParser
      sql_hooks_dialect = "postgres"

  # querysource/queries/multi/sources/base.py:14  (verified: `class ThreadSource(threading.Thread, ABC):`)
  class ThreadSource(SourceHooksMixin, threading.Thread, ABC):
      async def _fetch_with_hooks(self) -> Optional[pd.DataFrame]:
          """``run_pre_hook`` → ``fetch`` → ``run_post_hook`` (post only when fetch raised nothing).

          Without hooks this is exactly ``await self.fetch()``.
          """
  # run(): `result = loop.run_until_complete(self.fetch())` (verified: base.py:147)
  #   becomes `result = loop.run_until_complete(self._fetch_with_hooks())`
  ```

### Module 3: MultiQS wiring
- **Path**: `querysource/queries/multi/__init__.py`, `querysource/queries/multi/sources/query.py` (modify)
- **Responsibility**: Implement the PBAC gate, pop and reject hook keys, run validation 1-4, and hand the hooks to `ThreadQuery`.
- **Depends on**: M1, M2
- **Interface Skeleton**:
  ```python
  # querysource/queries/multi/__init__.py
  PG_HOOK_DRIVERS: frozenset[str] = frozenset({"pg", "postgres", "postgresql"})

  def _declares_hooks(cfg: object) -> bool:
      """True when a source entry dict carries a ``pre-hook`` or ``post-hook`` key."""

  # MultiQS methods
  def _hooks_target_same_database(self) -> bool:
      """(DBHOST, DBPORT, DBNAME) == (PG_HOST, PG_PORT, PG_DATABASE)  (verified: conf.py:24-28, :38-42)."""

  def _validate_source_hooks(self, name: str, query: dict, definition: "LoadedDefinition | None",
                             pre: object, post: object) -> "SourceHooks | None":
      """Apply §2 validation 2-4 for one ``queries`` entry.

      Raises:
          DriverError: unsupported provider/driver, DB mismatch, or guard rejection.
      """
  # _preflight_principal (verified: __init__.py:239; insert before `if has_raw_child:` :272):
  #   any _declares_hooks(cfg) over self._queries → enforce_principal(DATASOURCE, "pg_admin", "datasource:use")
  # queries dispatch (verified: `query = {**conditions, **query}` :470):
  #   BEFORE the merge: reject HOOK_KEYS in `conditions`; pop_hooks(query) from the pipeline entry.
  # sources / files loops (verified: :541, :556): _declares_hooks(config) → DriverError.
  # ThreadQuery(...) call (verified: `definition=child_definitions.get(name),` :527) gains `hooks=<SourceHooks|None>`.

  # querysource/queries/multi/sources/query.py:30  (ThreadQuery.__init__, verified)
  def __init__(self, name, query, request, queue, remote_config=None, *, store=None, definition=None,
               hooks: "SourceHooks | None" = None) -> None:
      """Existing contract; additionally ``self.set_hooks(hooks)`` after ``super().__init__``."""
  ```

### Module 4: Catalog / JSON schema
- **Path**: `querysource/queries/multi/sources/query.catalog.yaml` (modify), `generated/` (regenerated)
- **Responsibility**: Document `pre-hook` / `post-hook` (type `str | list`, PostgreSQL only, needs `pg_admin` grant, guard rules, post-hook only on success).
- **Depends on**: none
- **Interface Skeleton**:
  ```yaml
  # querysource/queries/multi/sources/query.catalog.yaml — append under `attributes:` (after `tenant`)
  - name: pre-hook
    type: str
    required: false
    default: null
    description: >-
      SQL (string or list) run BEFORE this query's retrieval, isolated with write credentials …
  - name: post-hook
    type: str
    required: false
    default: null
    description: >-
      SQL (string or list) run AFTER a successful retrieval …
  ```
  Then run `generate-multiquery-docs -o generated` (pre-commit hook, `.pre-commit-config.yaml:15-17`).

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_pop_hooks_mutates_entry` (`tests/test_source_hooks.py`) | M1 | keys removed from entry, values returned |
| `test_build_hooks_none_when_absent` | M1 | no keys → None |
| `test_build_hooks_guarded` | M1 | `"DROP TABLE x"` → `GuardedSQLError`; list of scripts flattened |
| `test_mixin_runs_execute_guarded` | M1 | `run_pre_hook`/`run_post_hook` call `execute_guarded` with the stored statements; [] when none |
| `test_provider_dialects` | M2 | `pgProvider`/`dbProvider.sql_hooks_dialect == "postgres"`; `sqlserverProvider`, `bigqueryProvider` → None |
| `test_fetch_with_hooks_order` (`tests/test_thread_source_base.py`) | M2 | pre → fetch → post call order |
| `test_pre_hook_failure_skips_fetch` | M2 | pre raises → fetch not awaited, `exc` set |
| `test_post_hook_skipped_on_fetch_failure` | M2 | fetch raises → post not called |
| `test_no_hooks_unchanged` | M2 | without hooks `run()` behaves as before (existing tests stay green) |
| `test_hooks_gate` (`tests/test_multiqs_source_hooks.py`) | M3 | hook declared + principal → `enforce_principal(DATASOURCE,"pg_admin","datasource:use")`; deny before any thread starts |
| `test_hooks_rejected_from_request_conditions` | M3 | `conditions={"q1": {"pre-hook": "…"}}` → `DriverError` |
| `test_hooks_not_forwarded_as_conditions` | M3 | `ThreadQuery` receives the entry without hook keys |
| `test_hooks_unsupported_driver` | M3 | slug with sqlserver/bigquery provider, raw `driver: mysql`, raw with `datasource` → `DriverError`, no thread started |
| `test_hooks_on_sources_or_files_rejected` | M3 | hook on a `sources:`/`files:` entry → `DriverError` |
| `test_hooks_db_mismatch` | M3 | patched conf with different DBHOST vs PG_HOST → `DriverError` |
| `test_pipeline_without_hooks_untouched` | M3 | pipelines with no hook keys: no gate call, no validation, same dispatch |
| `test_catalog_lists_hooks` | M4 | Query catalog attributes include `pre-hook`, `post-hook` |

### Integration Tests
| Test | Description |
|---|---|
| `test_source_hooks_postgres_roundtrip` | (skipped without live `DB*`/`PG_*` Postgres) pre-hook `UPDATE` is visible to the read; post-hook `INSERT` into an audit table happens only on success |

### Test Data / Fixtures
```python
@pytest.fixture
def hooked_pipeline() -> dict:
    return {"queries": {"profile": {
        "query": "SELECT * FROM wm_assembly.employee_detail_profile", "driver": "pg",
        "pre-hook": "WITH deleted AS (DELETE FROM wm_assembly.employee_detail_profile WHERE … RETURNING 1) …",
        "post-hook": ["INSERT INTO audit.fetch_log(source, at) VALUES ('profile', now())"],
    }}}
```

---

## 5. Acceptance Criteria

- [ ] `pytest tests/test_source_hooks.py tests/test_thread_source_base.py tests/test_multiqs_source_hooks.py -v` passes.
- [ ] The full existing MultiQuery suite still passes, which shows pipelines without hooks are unchanged on every driver: `pytest tests/ -k "multiqs or thread_source or destination" -v`.
- [ ] `ruff check` is clean on new and modified files.
- [ ] Hooks run with `default_dsn` (`DB*`) through `execute_guarded`, never on the retrieval connection.
- [ ] Pre-hook → retrieval → post-hook order holds. A pre-hook failure skips the retrieval, and the post-hook runs only when the retrieval raised nothing.
- [ ] Every validation failure (location, dialect, DB mismatch, guard, PBAC) happens **before any source thread starts**.
- [ ] Hook keys never reach `QueryObject` conditions, and hook keys coming from request conditions are rejected.
- [ ] Only PostgreSQL `queries` entries accept hooks; all other drivers/sections raise a clear `DriverError` **only when a hook is declared**.
- [ ] `generated/Query.json` lists `pre-hook` and `post-hook`.

---

## 6. Codebase Contract

### Verified Imports
```python
from querysource.providers.abstract import BaseProvider          # verified: querysource/providers/abstract.py:34
from querysource.providers.pg import pgProvider                  # verified: querysource/providers/pg.py:17
from querysource.providers.db import dbProvider                  # verified: querysource/providers/db.py:21 (subclass of pgProvider)
from querysource.queries.multi.sources.base import ThreadSource  # verified: querysource/queries/multi/sources/base.py:14
from querysource.queries.multi.sources.query import ThreadQuery  # verified: querysource/queries/multi/sources/query.py:13
from querysource.conf import DBHOST, DBPORT, DBNAME, PG_HOST, PG_PORT, PG_DATABASE  # verified: conf.py:24,28,27,38,42,41
from querysource.exceptions import DriverError                   # verified: querysource/queries/multi/__init__.py:13 (import block)
# from FEAT-156 (must be merged first):
from querysource.interfaces.guarded_sql import GuardedSQLError, guard_statements, execute_guarded
```

### Existing Class Signatures
```python
# querysource/queries/multi/sources/base.py
class ThreadSource(threading.Thread, ABC):                       # line 14
    def __init__(self, name: str, options: dict, request: web.Request, queue: asyncio.Queue) -> None:  # line 25
    async def fetch(self) -> pd.DataFrame:                       # line 117 (abstract)
    def run(self) -> None:                                       # line 132; `result = loop.run_until_complete(self.fetch())` line 147
    #   DataNotFound/NoDataFound → self.exc (info log); other Exception → self.exc (error log)

# querysource/queries/multi/sources/query.py
class ThreadQuery(ThreadSource):                                 # line 13
    def __init__(self, name, query: dict, request, queue, remote_config=None, *, store=None, definition=None):  # line 30
    async def fetch(self) -> pd.DataFrame | None:                # line 72 — delegates to Local/RemoteExecutor

# querysource/queries/multi/__init__.py
async def _preflight_principal(self) -> None:                    # line 239
child_definitions[name] = ...                                    # lines 450-452 (LoadedDefinition per slug child)
query = {**conditions, **query}                                  # line 470 (request conditions merged into entry)
is_remote = query.pop("remote", False)                           # line 474 (precedent: pop routing keys before ThreadQuery)
ThreadQuery(..., store=resolved_stores.get(name), definition=child_definitions.get(name))  # lines 518-528
t = cls(name, config, self._request, self._queue)                # line 556 (sources loop)

# querysource/interfaces/connections.py
def load_provider(self, provider: str) -> BaseProvider:          # line 374 — MultiQS inherits via BaseQuery → AbstractQuery(Connection)

# querysource/tenants.py
class LoadedDefinition: identity; runtime: QueryModel; revision  # line 54
# querysource/models.py — QueryModel.provider: str default 'db'  # line 81
```

### Integration Points
| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `SourceHooksMixin` | `BaseProvider`, `ThreadSource` | base class | `abstract.py:34`, `base.py:14` |
| `_fetch_with_hooks` | `ThreadSource.run` | replaces `self.fetch()` call | `base.py:147` |
| hook validation | `child_definitions` / `load_provider` | method calls | `__init__.py:450-452`, `connections.py:374` |
| hook execution | `execute_guarded` | await | FEAT-156 |

### Does NOT Exist (Anti-Hallucination)
- ~~`querysource.interfaces.source_hooks`~~ — created here.
- ~~`querysource.interfaces.guarded_sql`~~ — created by FEAT-156. Merge it first.
- ~~Hook fields on `QueryModel`~~. Hooks never come from slug definitions.
- ~~A shared connection between hooks and the provider~~ — Option A, out of scope.
- ~~A write-credential tier for BigQuery/SQL Server/MySQL~~. Only PostgreSQL has `DB*` vs `PG_*` (`pg_admin.py:1-10`).
- ~~`sql_hooks_dialect` on any existing class~~ — introduced here.

### Edit Sites (Blueprint Anchors)
Verified against: `7337dbb`

| File | Action | Verbatim anchor line | Verified at | Occurrences |
|---|---|---|---|---|
| `querysource/interfaces/source_hooks.py` | CREATE | — | — | — |
| `querysource/providers/abstract.py` | MODIFY | `class BaseProvider(ABC):` | `abstract.py:34` | 1 |
| `querysource/providers/pg.py` | MODIFY | `    __parser__ = pgSQLParser` | `pg.py:24` | 1 |
| `querysource/queries/multi/sources/base.py` | MODIFY | `class ThreadSource(threading.Thread, ABC):` | `base.py:14` | 1 |
| `querysource/queries/multi/sources/base.py` | MODIFY | `            result = loop.run_until_complete(self.fetch())` | `base.py:147` | 1 |
| `querysource/queries/multi/sources/query.py` | MODIFY | `        definition: Optional[LoadedDefinition] = None,` (in `__init__`) | `query.py:39` | 1 |
| `querysource/queries/multi/__init__.py` | MODIFY | `        if has_raw_child:` | `__init__.py:272` | 1 |
| `querysource/queries/multi/__init__.py` | MODIFY | `                query = {**conditions, **query}` | `__init__.py:470` | 1 |
| `querysource/queries/multi/__init__.py` | MODIFY | `                        definition=child_definitions.get(name),` | `__init__.py:527` | 1 |
| `querysource/queries/multi/__init__.py` | MODIFY | `                    t = cls(name, config, self._request, self._queue)` | `__init__.py:556` | 1 |
| `querysource/queries/multi/sources/query.catalog.yaml` | MODIFY | `  - name: tenant` | `query.catalog.yaml` | 1 |
| `tests/test_source_hooks.py` | CREATE | — | — | — |
| `tests/test_multiqs_source_hooks.py` | CREATE | — | — | — |
| `tests/test_thread_source_base.py` | MODIFY | (append tests) | — | — |

---

## 7. Implementation Notes & Constraints

### Patterns to Follow
- Pop routing keys before `ThreadQuery`, exactly like `remote`/`worker`/`tenant` (`__init__.py:474-477`).
- Keep every validation in the dispatch phase (before `t.start()`), so failures are all-or-nothing, as the existing preflight is.
- `self.logger` for hook status tags (`"pre-hook profile: DELETE 1234"`); never log hook SQL at INFO or above with values.

### Known Risks / Gotchas
- **No cross-step atomicity (Option B).**
  - A pre-hook commits before the read. If the read fails, the pre-hook's change stays.
  - If the post-hook fails, the pipeline fails, but the data was already read.
- **Concurrency.** Sources run in parallel threads (`MULTIQS_MAX_CONCURRENT_THREADS`). A pre-hook on
  source A is **not** ordered with the retrieval of source B.
- **Same-database check.** Hooks run on `DB*`. If `DB*` and `PG_*` point at different
  hosts/databases, the hook would change a database the source doesn't read, so validation 3
  refuses it.
- **Request-condition injection.** Request conditions are merged into the entry at `__init__.py:470`.
  Hook keys must be popped from the pipeline entry **and rejected in `conditions`** before that merge.
- **Remote children** (`remote: true`). Hooks run locally in the thread, around the remote dispatch.
  They still use local `DB*`.
- **The guard is lexical** (FEAT-156). Functions or procedures called by a hook can still run DDL
  internally. The PBAC gate is the mitigation.

### External Dependencies
None new.

---

## 8. Open Questions

- [x] Transaction model — *Resolved by Jesus Lara*: Option B, different credentials, hooks isolated. A pre-hook cannot open a transaction closed by the post-hook.
- [x] Where hooks are declared — *Resolved by Jesus Lara*: in the MultiQuery source entry (`"pre-hook"`, `"post-hook"`), never in a slug definition. A mixin decorates `BaseProvider` and `ThreadSource`.
- [x] Non-SQL hooks — *Resolved by Jesus Lara*: not in this release.
- [x] Post-hook when retrieval fails — *Resolved by Jesus Lara*: it runs only when the retrieval does not fail.
- [x] Keep `ExecuteSQL` / `TableDelete` — *Resolved by Jesus Lara*: yes, as components (FEAT-155 / FEAT-156).
- [x] Drivers — *Resolved by Juan2coder*: PostgreSQL only. Other drivers are unchanged unless a hook is declared, which raises a clear error.
- [ ] Does an **empty** retrieval (`DataNotFound`, HTTP 204) count as "retrieval did not fail" for the post-hook? The spec currently treats it as not-success, so the post-hook is skipped. — *Owner: Jesus Lara*
- [ ] Hooks on `sources: TableSource` with `driver: pg` and no custom `dsn`/`credentials` (same database): add in this release or in a follow-up? — *Owner: Jesus Lara*

---

## 9. Design Research Cross-Check

> Model: — · Status: skipped (no accepted exploration document; design agreed directly with Jesus Lara in chat)

| # | Suggestion (kind) | Disposition | Reason | Landed in |
|---|---|---|---|---|

Summary: **0** confirmed · **0** rejected · **0** escalated.

---

## Worktree Strategy
- Isolation: one feature worktree `.claude/worktrees/feat-FEAT-157-multi-source-hooks`.
- Module graph: M2 → M1 (inherits `SourceHooksMixin`). M3 → M1 and M2 (uses `pop_hooks`/`build_hooks`, `ThreadQuery(hooks=)`). M4 has no edges.
- Shared files: `querysource/queries/multi/__init__.py` (M3 only); `querysource/queries/multi/sources/base.py` (M2 only).
- Exclusive resources: none (no Rust rebuild here; `sql_guard` comes built from FEAT-156).
- Cross-feature: **FEAT-156 must be merged first** (for `guarded_sql.py` and `sql_guard`). FEAT-155/156 also edit `_preflight_principal` in `querysource/queries/multi/__init__.py`, so rebase on them.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-09-30 | Juan2coder | Initial draft from Jesus Lara's hook design |
