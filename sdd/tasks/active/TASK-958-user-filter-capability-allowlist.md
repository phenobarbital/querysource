# TASK-958: Fail closed on every non-sqlProvider provider (capability allowlist)

**Feature**: FEAT-184 — PBAC-based per-user automatic filtering (`user_filtering`)
**Spec**: `sdd/specs/pbac-based-per-user-filter.spec.md`
**Discovered-from**: `issue:e632c01feda8`
**Status**: pending
**Priority**: critical
**Estimated effort**: S (1-2h)
**Depends-on**: TASK-955
**Assigned-to**: unassigned

---

## Context

Ledger issue `issue:e632c01feda8` (vulnerability/critical): the AC-12 guard as
scoped by TASK-955 only refuses `user_filtering` when `__parser__ is None`
(in `BaseProvider.__init__`) and before `sqlProvider.prepare_connection()`'s
two early returns. Many providers have a parser but **do not inherit from
`sqlProvider`** and override `prepare_connection()` with their own raw path
(`if self.is_raw is False: ... parser ...` else execute the raw query):
`mysqlProvider`, `sqlserverProvider`, `defaultProvider`, `cassandraProvider`,
`documentdbProvider`, `deltatblProvider`, `icebergProvider`, `influxProvider`,
`arangodbProvider`, `elasticProvider`, `rethinkProvider`, `salesforceProvider`.
Their parsers (Mongo, Elastic, Influx, …) also never run the user-filter step
TASK-954 adds to `SQLParser`. A `user_filtering=True` definition routed to any
of them executes **unfiltered** — fail-open, contradicting the spec's binding
decision "Parser-less / raw paths: fail-closed, never execute unfiltered".

Fix: invert the guard to an **allowlist**. Only providers that declare
`__user_filter_capable__ = True` may serve a `user_filtering` definition;
`sqlProvider` declares it (its subclasses `pgProvider`, `dbProvider`,
`bigqueryProvider`, `scylladbProvider` inherit it — their parsers
`pgSQLParser`, `BigQueryParser`, `CQLParser` all subclass `SQLParser`,
verified `querysource/parsers/{pgsql,bigquery,cql}.pyx`). Every other
provider raises `UserFilterDenied(reason_code=REASON_PARSER_REQUIRED)` in
`__init__`, before any connection is opened.

---

## Scope

- Add class attribute `__user_filter_capable__: bool = False` to `BaseProvider`.
- Set `__user_filter_capable__ = True` on `sqlProvider`.
- Extend `BaseProvider._assert_user_filter_capable()` (created by TASK-955):
  raise when `user_filtering_required()` and
  (`self.__parser__ is None` **or** `not type(self).__user_filter_capable__`).
- Tests in `tests/providers/test_user_filter_capability.py`.

**NOT in scope**: making other providers' parsers support user filtering (a
provider opts in later by implementing the step and flipping the flag);
`QS` changes; `semanticProvider` behaviour (AC-17 — it is parser-less and
already refused by TASK-955's check; do not touch `_principal`).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/providers/abstract.py` | MODIFY | `__user_filter_capable__` attr + allowlist check in `_assert_user_filter_capable()` |
| `querysource/providers/sql.py` | MODIFY | `__user_filter_capable__ = True` |
| `tests/providers/test_user_filter_capability.py` | CREATE | Unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from ..exceptions import UserFilterDenied                 # created by TASK-950
from ..auth.user_filter import REASON_PARSER_REQUIRED      # created by TASK-950 — import lazily (cycle guard), as TASK-955 does
from querysource.providers.sql import sqlProvider          # querysource/providers/sql.py:27
from querysource.providers.mysql import mysqlProvider      # querysource/providers/mysql.py:25 (BaseProvider subclass, SQLParser)
from querysource.providers.documentdb import documentdbProvider  # querysource/providers/documentdb.py:32
from querysource.providers.pg import pgProvider            # querysource/providers/pg.py:17 (sqlProvider subclass)
```

### Existing Signatures to Use
```python
# querysource/providers/abstract.py
class BaseProvider(SourceHooksMixin, ABC):                 # line 35
    __parser__: AbstractParser = None                      # line 37 (occurrences: 1)
    def user_filtering_required(self) -> bool              # created by TASK-955
    def _assert_user_filter_capable(self) -> None          # created by TASK-955, called at end of __init__ parser block
# querysource/providers/sql.py
class sqlProvider(BaseProvider):                           # line 27
    __parser__ = SQLParser                                 # line 43 (occurrences: 1)
```

### Does NOT Exist
- ~~`BaseProvider.supports_user_filtering`~~ / ~~`USER_FILTER_PROVIDERS` registry~~ — use the class attribute only.
- ~~a user-filter step in `MongoParser`, `ElasticParser`, `InfluxParser`, `msSQLParser`-specific code~~ — not added by FEAT-184.

---

## Implementation Blueprint

### `querysource/providers/abstract.py`
```python
# AFTER — insert below `__parser__: AbstractParser = None` (verified: abstract.py:37; occurrences: 1)
    #: Only providers whose parser applies the FEAT-184 user-filter step may
    #: serve a ``user_filtering`` definition. Default False = fail closed.
    __user_filter_capable__: bool = False
```
```python
# MODIFY — inside _assert_user_filter_capable() (created by TASK-955)
        if not self.user_filtering_required():
            return
        if self.__parser__ is None or not type(self).__user_filter_capable__:
            from ..auth.user_filter import REASON_PARSER_REQUIRED
            raise UserFilterDenied(reason_code=REASON_PARSER_REQUIRED)
```
**Why**: an allowlist cannot fail open when a new provider is added; a
denylist (checking `__parser__ is None`) misses every provider with a
non-filtering parser or its own raw `prepare_connection` path.

### `querysource/providers/sql.py`
```python
# AFTER — insert below `    __parser__ = SQLParser` (verified: sql.py:43; occurrences: 1)
    __user_filter_capable__ = True
```
**Why**: `sqlProvider.prepare_connection()` carries the raw-path guard
(TASK-955) and its parsers are all `SQLParser` subclasses.

### `tests/providers/test_user_filter_capability.py`
- FILL IN: parametrize over `mysqlProvider`, `documentdbProvider` (and a
  `sqlserverProvider` / `cassandraProvider` if constructible without a live
  connection) → constructing with a `user_filtering=True` definition raises
  `UserFilterDenied`, with the `parser_required` reason — bounded by AC-12.
- FILL IN: `pgProvider` / `sqlProvider` with the same definition does NOT raise.
- FILL IN: any provider with `user_filtering=False` does not raise (no regression).
- Reuse the definition/loop fixtures pattern of `tests/providers/test_user_filter_guard.py` (TASK-955).

### Steps (in order)
1. Confirm TASK-955 is merged (helpers exist) — because this edits its helper.
2. Apply the abstract.py and sql.py blocks.
3. Write the tests; run Validation Commands; commit only listed files.

### FILL IN checklist
- [ ] Test parametrization and fixtures
- [ ] Reason-code assertion form matching `UserFilterDenied`'s API from TASK-950

---

## Acceptance Criteria
- [ ] A `user_filtering=True` definition on any provider not in the `sqlProvider` hierarchy raises `UserFilterDenied(parser_required)` in `__init__`.
- [ ] `sqlProvider` and its subclasses still serve `user_filtering` definitions (TASK-955 raw-path guard unchanged).
- [ ] `semanticProvider._principal` untouched (AC-17).

## Validation Commands
- `pytest tests/providers/test_user_filter_capability.py -q`
- `pytest tests/providers/test_user_filter_guard.py -q`

## Complexity Contract
```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/providers/abstract.py", "action": "MODIFY"},
    {"path": "querysource/providers/sql.py", "action": "MODIFY"},
    {"path": "tests/providers/test_user_filter_capability.py", "action": "CREATE"}
  ],
  "contract_symbols": ["sym:querysource/providers/abstract.py#BaseProvider", "sym:querysource/providers/sql.py#sqlProvider"]
}
```

## Completion Note
_(filled by the implementer)_
