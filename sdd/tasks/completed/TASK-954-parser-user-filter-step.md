# TASK-954: Parser step `_user_filter_sync` (Cython `.pyx` / `.pxd`)

**Feature**: FEAT-184 — PBAC-based per-user automatic filtering (`user_filtering`)
**Spec**: `sdd/specs/pbac-based-per-user-filter.spec.md`
**Status**: done
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-951, TASK-952
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 3. Enforcement happens inside the parser (resolved in
brainstorm). The step runs after the legacy `_qs_filters_sync()` and merges
the compiled predicates into `self.filter`, which every dialect already renders.
**Exclusive**: rebuilds the shared Cython extension (`make build-inplace`).

---

## Scope

- Declare `principal`, `userinfo`, `user_filter_applied` and `cpdef void _user_filter_sync(self) except *` in `abstract.pxd`.
- Initialize the three attributes in `__cinit__` from kwargs.
- Call `self._user_filter_sync()` in `_extract_options()` right after `self._qs_filters_sync()`.
- Implement `_user_filter_sync()` (lazy import of `compile_user_filter`, new-dict merge).
- Rebuild with `make build-inplace`; write `tests/test_sql_parser_user_filter.py`.

**NOT in scope**: provider/QS plumbing (TASK-955/956); any dialect `.pyx` change (AC-11).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/parsers/abstract.pxd` | MODIFY | 3 attributes + method declaration |
| `querysource/parsers/abstract.pyx` | MODIFY | kwargs init, step call, step implementation |
| `tests/test_sql_parser_user_filter.py` | CREATE | Parser tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# inside abstract.pyx, LAZY (inside the method body) — never at module level (import cycle, spec §2):
from ..auth.user_filter import compile_user_filter        # created by TASK-950/951
# tests:
from querysource.models import QueryModel, QueryObject    # verified: querysource/models.py:49 / :24
from querysource.parsers.sql import SQLParser             # verified: tests/test_sql_parser_combinations.py:20
from querysource.auth.principal import QSPrincipal        # verified: querysource/auth/principal.py:21
from querysource.exceptions import UserFilterDenied, ConfigError
```

### Existing Signatures to Use
```cython
# querysource/parsers/abstract.pyx
cdef class AbstractParser:                                         # line 36
    def __cinit__(self, *args, definition: object, conditions: object, query: str = None, **kwargs)  # line 39
        self.definition = definition if definition else None       # line 54
    cdef void _extract_options(self)                               # line 159
        self._query_filter_sync()                                  # line 173
        self._qs_filters_sync()                                    # line 174  (also at line 405 inside async _qs_filters)
        self._col_definition_sync()                                # line 175
    cdef void _program_slug_sync(self) → self.program_slug          # line 183
    cdef void _query_slug_sync(self) → self._slug                   # line 189
    cdef void _query_filter_sync(self)                             # line 318; may set self.filter = self.definition.filtering (ALIAS, line 331)
    cdef void _qs_filters_sync(self)                               # line 338
    cdef void _col_definition_sync(self):                          # line 345
# querysource/parsers/abstract.pxd
    cdef public dict filter                                        # line 17
    cdef str _slug                                                 # line 30
    cdef public dict _query_filters                                # line 41
    cdef void _qs_filters_sync(self)                               # line 81
    cdef void _col_definition_sync(self)                           # line 82
# querysource/parsers/sql.pyx: SQLParser.__init__(self, *args, **kwargs) forwards kwargs  # line 115
#   build_query() renders self.filter; list → "{key} IN (...)"                             # lines 431 / 233
# tests/test_sql_parser_combinations.py::_make_parser — builds SQLParser(definition=None, conditions=QueryObject(...), query=...)
# Makefile:156 build-inplace → python setup.py build_ext --inplace
```

### Does NOT Exist
- ~~`AbstractParser.principal` / `.userinfo` / `.user_filter_applied` / `._user_filter_sync`~~ — added here.
- ~~`AbstractParser.request`~~ — the parser never receives the request; do not add it.
- ~~module-level `from ..auth...` import in abstract.pyx~~ — forbidden (cycle).
- ~~`self.filter.update(...)` as the merge~~ — forbidden: `self.filter` may alias `definition.filtering` (spec §7 critical risk).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/parsers/abstract.pxd", "action": "MODIFY"},
    {"path": "querysource/parsers/abstract.pyx", "action": "MODIFY"},
    {"path": "tests/test_sql_parser_user_filter.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/parsers/abstract.pyx#AbstractParser",
    "sym:querysource/parsers/sql.pyx#SQLParser"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- `.pxd` and `.pyx` declarations must match exactly (`cpdef void _user_filter_sync(self) except *`) or the build fails.
- No-op (and `user_filter_applied = ()`) when `getattr(self.definition, 'user_filtering', False)` is falsy or definition is None.
- After a successful merge log at DEBUG the overwritten caller keys (`set(self.filter) & set(predicates)`).
- Let `UserFilterDenied` / `ConfigError` propagate untouched (AC-9, test `test_parser_denial_propagates`).
- Run `make build-inplace` before tests; the `.so` lands in the shared tree — that is why this task is exclusive.

---

## Implementation Blueprint

### Steps (in order)
1. Edit `abstract.pxd` — *why*: Cython needs the attributes declared before `.pyx` can assign them.
2. Init attributes in `__cinit__` — *why*: `kwargs.pop` keeps them out of `**kwargs` consumers.
3. Add the call in `_extract_options()` and the method — *why*: after `_qs_filters_sync` per spec §2.
4. `make build-inplace`, then write and run the tests.

### `querysource/parsers/abstract.pxd` (MODIFY — attributes)
```cython
# occurrences: 1 (verified: grep -c '    cdef public dict _query_filters' querysource/parsers/abstract.pxd)
# AFTER — insert below `    cdef public dict _query_filters` (verified: abstract.pxd:41)
    # FEAT-184 per-user filtering:
    cdef public object principal
    cdef public object userinfo
    cdef public tuple user_filter_applied
```

### `querysource/parsers/abstract.pxd` (MODIFY — method)
```cython
# occurrences: 1 (verified: grep -c '    cdef void _qs_filters_sync(self)' querysource/parsers/abstract.pxd)
# AFTER — insert below `    cdef void _qs_filters_sync(self)` (verified: abstract.pxd:81)
    cpdef void _user_filter_sync(self) except *
```

### `querysource/parsers/abstract.pyx` (MODIFY — `__cinit__`)
```cython
# occurrences: 1 (verified: grep -c '        self.definition = definition if definition else None' querysource/parsers/abstract.pyx)
# AFTER — insert below `        self.definition = definition if definition else None` (verified: abstract.pyx:54)
        # FEAT-184: caller identity resolved once by QS/BaseProvider.
        self.principal = kwargs.pop('principal', None)
        self.userinfo = kwargs.pop('userinfo', None)
        self.user_filter_applied = ()
```

### `querysource/parsers/abstract.pyx` (MODIFY — `_extract_options`)
```cython
# occurrences: 2 (verified: grep -c '        self._qs_filters_sync()' querysource/parsers/abstract.pyx)
# Disambiguated anchor — the occurrence inside `cdef void _extract_options(self):` (abstract.pyx:173-175):
#         self._query_filter_sync()
#         self._qs_filters_sync()
#         self._col_definition_sync()
# REPLACE those three lines with:
        self._query_filter_sync()
        self._qs_filters_sync()
        self._user_filter_sync()
        self._col_definition_sync()
```
**Why**: do NOT touch the second occurrence at line 405 (async `_qs_filters` wrapper).

### `querysource/parsers/abstract.pyx` (MODIFY — new method)
```cython
# occurrences: 1 (verified: grep -c '    cdef void _col_definition_sync(self):' querysource/parsers/abstract.pyx)
# BEFORE — insert above `    cdef void _col_definition_sync(self):` (verified: abstract.pyx:345)
    cpdef void _user_filter_sync(self) except *:
        """Merge the caller's compiled per-user predicates into ``self.filter`` (FEAT-184).

        No-op unless ``definition.user_filtering`` is truthy. Raises
        ``UserFilterDenied`` (403) or ``ConfigError`` (500) unchanged.
        """
        cdef object result
        cdef dict merged
        if self.definition is None or not getattr(self.definition, 'user_filtering', False):
            self.user_filter_applied = ()
            return
        # Lazy import: querysource.auth pulls queries.describe/models (import cycle guard).
        from ..auth.user_filter import compile_user_filter
        result = compile_user_filter(
            getattr(self.definition, 'user_filter_options', None),
            program_slug=self.program_slug,
            principal=self.principal,
            userinfo=self.userinfo,
            slug=self._slug,
            logger=self.logger,
        )
        # FILL IN: if result.predicates: log DEBUG overwritten keys (set(self.filter or {}) & set(result.predicates))
        # New dict on purpose: self.filter may alias the cached definition.filtering (spec §7).
        merged = {**(self.filter or {}), **result.predicates}
        self.filter = merged
        self.user_filter_applied = tuple(result.applied)

```

### `tests/test_sql_parser_user_filter.py` (CREATE)
```python
"""FEAT-184 TASK-954 — parser user_filter step (requires `make build-inplace`)."""
from __future__ import annotations

import pytest

from querysource.auth.principal import QSPrincipal
from querysource.exceptions import UserFilterDenied
from querysource.models import QueryModel, QueryObject
from querysource.parsers.sql import SQLParser

OPTIONS = {"rules": [{"column": "user_id", "attribute": "user_id"},
                     {"column": "store_id", "attribute": "attributes.store_id", "operator": "in"}],
           "program": {"restricted": True}}
SQL = "SELECT * FROM epson.users {where_cond}"


def _parser(definition: QueryModel, *, principal=None, userinfo=None, filter_: dict | None = None) -> SQLParser:
    parser = SQLParser(definition=definition, conditions=QueryObject(query_raw=SQL), query=SQL,
                       principal=principal, userinfo=userinfo)
    parser.cond_definition = {}
    parser.filter = filter_ if filter_ is not None else {}
    parser.program_slug = definition.program_slug
    return parser


@pytest.fixture
def definition() -> QueryModel:
    return QueryModel(query_slug="s", program_slug="epson", query_raw=SQL, filtering={"active": True},
                      user_filtering=True, user_filter_options=OPTIONS)


def test_parser_accepts_identity_kwargs(definition) -> None:
    p = QSPrincipal(user_id="7", programs=("epson",))
    parser = _parser(definition, principal=p, userinfo={"a": 1})
    assert parser.principal is p and parser.userinfo == {"a": 1}


# FILL IN: test_parser_user_filter_merges_new_dict — filter_=definition.filtering; after _user_filter_sync()
#          parser.filter is not definition.filtering and definition.filtering == {"active": True}
# FILL IN: test_parser_user_filter_overrides_caller_key — filter_={"user_id": "999"} → filter["user_id"] == "7"
# FILL IN: test_parser_renders_in_for_lists — await parser.build_query(); assert "IN (" in sql and "999" not in sql
# FILL IN: test_parser_denial_propagates — programs=("walmart",) → pytest.raises(UserFilterDenied)
# FILL IN: test_parser_noop_without_flag — user_filtering=False → filter unchanged, user_filter_applied == ()
```

### FILL IN checklist
- [ ] DEBUG log of overwritten caller keys
- [ ] remaining tests (run after `make build-inplace`)

---

## Acceptance Criteria

- [ ] Spec AC-9, AC-10, AC-11
- [ ] `make build-inplace` succeeds
- [ ] Existing parser tests still pass

---

## Validation Commands

- `pytest tests/test_sql_parser_user_filter.py -q`
- `pytest tests/test_sql_parser_combinations.py -q`

---

## Test Specification

See the test block in the Implementation Blueprint.

---

## Agent Instructions

1. Work in the feature worktree (`parrot sdd worktree ensure --slug pbac-based-per-user-filter --feature-id FEAT-184`).
2. Confirm TASK-951 and TASK-952 are done; verify the Codebase Contract.
3. Implement from the blueprint; run `make build-inplace`; complete every `# FILL IN:`.
4. Run the Validation Commands; commit only the listed files (never the generated `.c`/`.so`).
5. Close with `parrot sdd task close TASK-954 pbac-based-per-user-filter verified` and fill the Completion Note.

---

## Completion Note

**Completed by**: sdd-worker (FEAT-184)
**Date**:
**Notes**: Seat: gpt-5.6-sol (codex) · Attempts: 1 · merged by engine. Validated after in-worktree Cython rebuild: test_sql_parser_user_filter + test_sql_parser_combinations -> 112 passed.

**Deviations from spec**: none
