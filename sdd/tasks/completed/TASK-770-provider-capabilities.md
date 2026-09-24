# TASK-770: Declare qsurl `capabilities` / `residual_scan` on providers

**Feature**: FEAT-152 — qsurl: HTSQL-style URL query dialect (chumsky 0.13 + PyO3)
**Spec**: `sdd/specs/qsurl-parser.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-765, TASK-769
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 5, AC9. The brainstorm resolved that each provider declares, next to its
`__parser__`, which qsurl capabilities it renders natively; the translator (TASK-771) pushes
those down and leaves the rest to the residual stage. `pgProvider` may only declare
`text_match` once the PostgreSQL `ILIKE` dict operator exists (TASK-769). Cassandra forbids
residual-only scans (`residual_scan = False`, cost guard).

---

## Scope

- Add `capabilities: frozenset[str]` and `residual_scan: bool` class attributes to `BaseProvider`.
- Override `capabilities` on `sqlProvider`, `pgProvider`, `cassandraProvider`; set `residual_scan = False` on `cassandraProvider`.
- Test the exact sets.

| Provider | `capabilities` | `residual_scan` |
|---|---|---|
| `BaseProvider` (default; all unaudited providers inherit) | `{select, filter, in_list, null_check}` | `True` |
| `sqlProvider` | base ∪ `{alias, sort, limit, offset}` | `True` |
| `pgProvider` | sql ∪ `{text_match}` | `True` |
| `cassandraProvider` | `{select, filter, in_list, null_check, limit}` | `False` |

**NOT in scope**: auditing other providers (they inherit the base set); any translation logic.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/providers/abstract.py` | MODIFY | Base attributes + import of capability constants |
| `querysource/providers/sql.py` | MODIFY | SQL override |
| `querysource/providers/pg.py` | MODIFY | PostgreSQL override |
| `querysource/providers/cassandra.py` | MODIFY | Cassandra override + `residual_scan = False` |
| `tests/qsurl/test_provider_capabilities.py` | CREATE | Exact-set tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.qsurl import capabilities as qsurl_caps        # created by TASK-765 (leaf module, no querysource imports)
from querysource.providers.abstract import BaseProvider         # verified: querysource/providers/abstract.py:33
from querysource.providers.sql import sqlProvider               # verified: querysource/providers/sql.py:32
from querysource.providers.pg import pgProvider                 # verified: querysource/providers/pg.py:14
from querysource.providers.cassandra import cassandraProvider   # verified: querysource/providers/cassandra.py:26
```

### Existing Signatures to Use
```python
# querysource/providers/abstract.py
class BaseProvider(ABC):            # line 33
    __parser__: AbstractParser = None   # line 35
    _parser_options: dict = {}          # line 36
    replacement: dict = {...}           # line 38-46
# querysource/providers/sql.py
class sqlProvider(BaseProvider):    # line 32
    __parser__ = SQLParser              # line 48
# querysource/providers/pg.py
class pgProvider(sqlProvider):      # line 14
    __parser__ = pgSQLParser            # line 21
# querysource/providers/cassandra.py
class cassandraProvider(BaseProvider):   # line 26
    __parser__ = CQLParser               # line 31

# querysource/qsurl/capabilities.py (TASK-765)
SELECT, ALIAS, FILTER, IN_LIST, NULL_CHECK, TEXT_MATCH, SORT, LIMIT, OFFSET: str
ALL: tuple[str, ...]; BASE: frozenset[str]
```

### Does NOT Exist
- ~~`BaseProvider.capabilities`~~ / ~~`residual_scan`~~ — created here.
- ~~a capabilities registry or decorator~~ — plain class attributes only (brainstorm decision).
- ~~`querysource.qsurl.capabilities` importing providers~~ — it must stay a leaf; providers only ever import the capability constants, nothing else from the qsurl package.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/providers/abstract.py", "action": "MODIFY"},
    {"path": "querysource/providers/sql.py", "action": "MODIFY"},
    {"path": "querysource/providers/pg.py", "action": "MODIFY"},
    {"path": "querysource/providers/cassandra.py", "action": "MODIFY"},
    {"path": "tests/qsurl/test_provider_capabilities.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/providers/abstract.py#BaseProvider",
    "sym:querysource/providers/sql.py#sqlProvider",
    "sym:querysource/providers/pg.py#pgProvider",
    "sym:querysource/providers/cassandra.py#cassandraProvider"
  ]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Add the import and base attributes in `abstract.py` — *why*: subclasses inherit the safe default.
2. Add the three overrides directly under each `__parser__` line — *why*: the spec puts capabilities next to the code that renders them.
3. Run the new test and `tests/e2e/test_qs_dry_run.py` — *why*: proves the new import introduced no cycle on the provider import path.

### `querysource/providers/abstract.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -cF '    _parser_options: dict = {}' querysource/providers/abstract.py)
# AFTER — insert below `    _parser_options: dict = {}` (verified: querysource/providers/abstract.py:36)
    #: qsurl capabilities this provider renders natively (querysource/qsurl/capabilities.py).
    capabilities: frozenset[str] = qsurl_caps.BASE
    #: False when a residual-only qsurl filter would be a full scan the store must not run.
    residual_scan: bool = True

# and add to the module imports (next to the other `from ..` imports):
from ..qsurl import capabilities as qsurl_caps
```

### `querysource/providers/sql.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -cF '    __parser__ = SQLParser' querysource/providers/sql.py)
# AFTER — insert below `    __parser__ = SQLParser` (verified: querysource/providers/sql.py:48)
    capabilities = qsurl_caps.BASE | {qsurl_caps.ALIAS, qsurl_caps.SORT, qsurl_caps.LIMIT, qsurl_caps.OFFSET}
# + module import: from ..qsurl import capabilities as qsurl_caps
```

### `querysource/providers/pg.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -cF '    __parser__ = pgSQLParser' querysource/providers/pg.py)
# AFTER — insert below `    __parser__ = pgSQLParser` (verified: querysource/providers/pg.py:21)
    capabilities = sqlProvider.capabilities | {qsurl_caps.TEXT_MATCH}
# + module import: from ..qsurl import capabilities as qsurl_caps
# FILL IN: confirm `sqlProvider` is already imported in pg.py (it is the base class at line 14); do not add a second import
```

### `querysource/providers/cassandra.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -cF '    __parser__ = CQLParser' querysource/providers/cassandra.py)
# AFTER — insert below `    __parser__ = CQLParser` (verified: querysource/providers/cassandra.py:31)
    capabilities = frozenset({
        qsurl_caps.SELECT, qsurl_caps.FILTER, qsurl_caps.IN_LIST, qsurl_caps.NULL_CHECK, qsurl_caps.LIMIT,
    })
    residual_scan = False
# + module import: from ..qsurl import capabilities as qsurl_caps
```
**Why**: `frozenset | set` returns a `frozenset`, so overrides keep the declared type.

### `tests/qsurl/test_provider_capabilities.py` (CREATE)
```python
"""Exact qsurl capability declarations per provider (spec §3 Module 5, AC9)."""
from __future__ import annotations

import pytest

from querysource.providers.abstract import BaseProvider
from querysource.providers.cassandra import cassandraProvider
from querysource.providers.pg import pgProvider
from querysource.providers.sql import sqlProvider
from querysource.qsurl import capabilities as caps


@pytest.mark.parametrize("cls,expected,scan", [
    (BaseProvider, {"select", "filter", "in_list", "null_check"}, True),
    (sqlProvider, {"select", "filter", "in_list", "null_check", "alias", "sort", "limit", "offset"}, True),
    (pgProvider, {"select", "filter", "in_list", "null_check", "alias", "sort", "limit", "offset", "text_match"}, True),
    (cassandraProvider, {"select", "filter", "in_list", "null_check", "limit"}, False),
])
def test_capability_sets(cls, expected, scan):
    assert cls.capabilities == frozenset(expected)
    assert isinstance(cls.capabilities, frozenset)
    assert cls.residual_scan is scan
    assert caps.validate(cls.capabilities) == cls.capabilities

def test_no_provider_declares_phase1_unsupported(): ...   # FILL IN: none of the four intersects caps.UNSUPPORTED_PHASE1
```

### FILL IN checklist
- [ ] `pg.py` import check.
- [ ] `test_no_provider_declares_phase1_unsupported` body.

---

## Acceptance Criteria

- [ ] Exact sets and flags per the Scope table (spec AC9).
- [ ] `python -c "import querysource.providers.pg, querysource.providers.cassandra"` works (no import cycle).
- [ ] `ruff check querysource/providers/abstract.py querysource/providers/sql.py querysource/providers/pg.py querysource/providers/cassandra.py` clean.

---

## Validation Commands

- `pytest tests/qsurl/test_provider_capabilities.py -q`
- `pytest tests/e2e/test_qs_dry_run.py -q`

---

## Test Specification

See the `tests/qsurl/test_provider_capabilities.py` block above.

---

## Agent Instructions

When you pick up this task:

1. **Read the spec** at the path listed above for full context.
2. **Check dependencies** — verify every `Depends-on` task is `done` in `sdd/tasks/index/qsurl-parser.json`.
3. **Verify the Codebase Contract** before writing any code: re-run the `grep -c` for every MODIFY anchor; if a count changed, re-locate the anchor; if it is `0`, stop and report the drift.
4. **Update status** in `sdd/tasks/index/qsurl-parser.json` → `"in-progress"`.
5. **Implement** from the Implementation Blueprint; complete every `# FILL IN:`; never change a signature or path the blueprint fixes.
6. **Verify** every Acceptance Criterion and run the Validation Commands (`source .venv/bin/activate` first).
7. **Move this file** to `sdd/tasks/completed/TASK-770-provider-capabilities.md` and set the index entry to `"done"`.
8. **Fill in the Completion Note** below.

---

## Completion Note

**Completed by**: sdd-worker (Claude Sonnet 5, sequential fallback loop)
**Date**: 2026-09-24
**Notes**: Implemented exactly per the Implementation Blueprint: `capabilities` /
`residual_scan` class attributes added to `BaseProvider` (defaults `qsurl_caps.BASE` /
`True`) right after `_parser_options`; `sqlProvider.capabilities` override under
`__parser__ = SQLParser`; `pgProvider.capabilities = sqlProvider.capabilities |
{TEXT_MATCH}` under `__parser__ = pgSQLParser` (confirmed `sqlProvider` already imported
at `pg.py:11`, no duplicate import added); `cassandraProvider.capabilities` +
`residual_scan = False` under `__parser__ = CQLParser`. `tests/qsurl/test_provider_capabilities.py`
created with the exact-set parametrized test plus `test_no_provider_declares_phase1_unsupported`.
`pytest tests/qsurl/test_provider_capabilities.py -q` → 5 passed. `pytest
tests/e2e/test_qs_dry_run.py -q` → 23 passed (no regression). `python -c "import
querysource.providers.pg, querysource.providers.cassandra"` succeeds — no import cycle.
`ruff check` on the four modified provider files reports 8 pre-existing findings (import
sorting, `super()` call style, one `B904`) — confirmed via `git stash`/re-check to be
identical before and after this task's one-line-per-file additions, i.e. pre-existing
style debt unrelated to this task; `ruff check --select E9,F63,F7,F82` (syntax
errors/undefined names, the sdd-worker fallback-loop lint gate) is clean on every touched
file including the new test.

**Deviations from spec**: none
