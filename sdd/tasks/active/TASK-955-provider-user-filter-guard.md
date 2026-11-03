# TASK-955: Provider identity forwarding and fail-closed guard

**Feature**: FEAT-184 — PBAC-based per-user automatic filtering (`user_filtering`)
**Spec**: `sdd/specs/pbac-based-per-user-filter.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-954
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 4 (provider half), AC-12, AC-17. `BaseProvider` forwards the
identity to the parser and refuses `user_filtering` definitions on paths
that never run a parser.

---

## Scope

- In `BaseProvider.__init__`: pop `principal`/`userinfo` into `_user_principal`/`_user_info`,
  pass them to the parser constructor, call `_assert_user_filter_capable()` after the parser block.
- Add `user_filtering_required()` and `_assert_user_filter_capable()` to `BaseProvider`.
- In `sqlProvider.prepare_connection()`: raise before both early returns when filtering is required.
- Write `tests/providers/test_user_filter_guard.py`.

**NOT in scope**: `QS` changes (TASK-956); other providers' `prepare_connection`.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/providers/abstract.py` | MODIFY | Identity kwargs, forwarding, guard helpers |
| `querysource/providers/sql.py` | MODIFY | Guard before early returns |
| `tests/providers/test_user_filter_guard.py` | CREATE | Unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from ..exceptions import UserFilterDenied                  # created by TASK-950; providers/abstract.py imports from ..exceptions already
from ..auth.user_filter import REASON_PARSER_REQUIRED       # created by TASK-950 — import lazily inside the helper (cycle guard)
from querysource.providers import BaseProvider              # verified: querysource/providers/__init__.py:4
from querysource.providers.sql import sqlProvider           # verified: querysource/providers/sql.py:27
```

### Existing Signatures to Use
```python
# querysource/providers/abstract.py
class BaseProvider(SourceHooksMixin, ABC):                 # line 35
    __parser__: AbstractParser = None                      # line 37
    _parser_options: dict = {}                             # line 38
    def __init__(self, slug='', query=None, qstype='', connection=None, definition=None,
                 conditions=None, request=None, **kwargs)  # lines 55-64; requires a running loop (lines 117-122)
        self._definition = definition                      # line 69
        self._slug: str = slug                             # line 73
        ## Parser Logic:                                   # line 124
        self._parser: Callable = None                      # line 125
        if self.__parser__:                                # line 126
            try: self._parser = self.__parser__(query=self._query, definition=definition,
                                                conditions=conditions, **self._parser_options)   # lines 127-133
            except Exception as err: ... raise ParserError(...) from err                         # lines 134-140
    async def prepare_connection(self)                     # line 259 → await self._parser.set_options()
    def checksum(self): return get_hash(self._query)       # line 330

# querysource/providers/sql.py
class sqlProvider(BaseProvider): __parser__ = SQLParser    # lines 27 / 43
    async def prepare_connection(self) -> Callable:        # line 138
        if not self._connection: raise QueryException(...)  # lines 141-144
        if self._type == 'query':
            use_parser = self._needs_parser(self._query) or self._has_parser_conditions(self._conditions)   # line 147
            if not use_parser: return self                 # lines 148-151
        if self.is_raw is True: return self                # lines 152-153

# querysource/providers/semantic.py:44 — semanticProvider sets self._principal = kwargs.pop("principal", None) BEFORE super().__init__
```

### Does NOT Exist
- ~~`BaseProvider._principal`~~ — must NOT be introduced (owned by semanticProvider; AC-17).
- ~~`BaseProvider._user_principal` / `_user_info` / `user_filtering_required` / `_assert_user_filter_capable`~~ — added here.
- ~~a parser-less guard in other providers' `prepare_connection`~~ — the `__init__` guard covers `__parser__ = None`.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/providers/abstract.py", "action": "MODIFY"},
    {"path": "querysource/providers/sql.py", "action": "MODIFY"},
    {"path": "tests/providers/test_user_filter_guard.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/providers/abstract.py#BaseProvider",
    "sym:querysource/providers/sql.py#sqlProvider"
  ]
}
```

---

## Implementation Blueprint

### Steps (in order)
1. Pop the identity kwargs before `## Parser Logic:` — *why*: `kwargs.pop(..., None)` is a no-op for semanticProvider, which already popped `principal`.
2. Pass them to the parser call and guard after the try/except — *why*: AC-12 parser-less refusal before any connection.
3. Add the helper methods and the `sqlProvider` guard.

### `querysource/providers/abstract.py` (MODIFY — kwargs)
```python
# occurrences: 1 (verified: grep -c '        ## Parser Logic:' querysource/providers/abstract.py)
# BEFORE — insert above `        ## Parser Logic:` (verified: providers/abstract.py:124)
        # FEAT-184: caller identity for per-user filtering. Not `_principal`
        # (owned by semanticProvider, providers/semantic.py:44).
        self._user_principal = kwargs.pop('principal', None)
        self._user_info = kwargs.pop('userinfo', None)
```

### `querysource/providers/abstract.py` (MODIFY — parser call + guard)
```python
# occurrences: 1 (verified: grep -c '                self._parser = self.__parser__(  # pylint: disable=E1102' querysource/providers/abstract.py)
# REPLACE lines 128-133 (the parser call) with:
                self._parser = self.__parser__(  # pylint: disable=E1102
                    query=self._query,
                    definition=definition,
                    conditions=conditions,
                    principal=self._user_principal,
                    userinfo=self._user_info,
                    **self._parser_options
                )
# AFTER the enclosing `raise ParserError(... ) from err` block (providers/abstract.py:137-140), dedented to method level:
        self._assert_user_filter_capable()
```

### `querysource/providers/abstract.py` (MODIFY — helpers)
```python
# Add as methods of BaseProvider, directly above `    async def prepare_connection(self):` (verified: providers/abstract.py:259, occurrences: 1)
    def user_filtering_required(self) -> bool:
        """True when the definition declares ``user_filtering`` (FEAT-184)."""
        definition = self._definition
        if definition is None:
            return False
        if isinstance(definition, dict):
            return bool(definition.get('user_filtering', False))
        return bool(getattr(definition, 'user_filtering', False))

    def _assert_user_filter_capable(self) -> None:
        """Refuse a ``user_filtering`` definition on a provider without a parser."""
        if self._parser is None and self.user_filtering_required():
            from ..auth.user_filter import REASON_PARSER_REQUIRED  # lazy: import-cycle guard
            self._logger.warning(
                "user_filter denied slug=%s reason=%s provider=%s",
                self._slug, REASON_PARSER_REQUIRED, self.__name__,
            )
            raise UserFilterDenied(reason_code=REASON_PARSER_REQUIRED, slug=self._slug)
```
**Why**: add `UserFilterDenied` to the existing `from ..exceptions import ...` line of `providers/abstract.py` (verified: providers/abstract.py:16 `from ..exceptions import DataNotFound, ParserError, QueryException, RawQueryPlaceholderError`).

### `querysource/providers/sql.py` (MODIFY)
```python
# occurrences: 1 each (verified: grep -c '            use_parser = self._needs_parser(self._query) or self._has_parser_conditions(self._conditions)' and
#                       grep -c '        if self.is_raw is True:' querysource/providers/sql.py)
# REPLACE lines 146-153 with:
        if self._type == 'query':
            use_parser = self._needs_parser(self._query) or self._has_parser_conditions(self._conditions)
            if not use_parser:
                # FEAT-184: never run a user_filtering slug unparsed.
                self._refuse_unparsed_user_filtering()
                # Keep SQL untouched to avoid formatting collisions with
                # literals that contain braces (e.g. Postgres array/json syntax).
                return self
        if self.is_raw is True:
            self._refuse_unparsed_user_filtering()
            return self
# and add the method to sqlProvider:
    def _refuse_unparsed_user_filtering(self) -> None:
        """Raise ``UserFilterDenied(parser_required)`` when filtering is required but parsing is skipped."""
        # FILL IN: if self.user_filtering_required(): log WARNING like BaseProvider._assert_user_filter_capable and
        #          raise UserFilterDenied(reason_code=REASON_PARSER_REQUIRED, slug=self._slug) — AC-12
```
**Why**: add `UserFilterDenied` to `from ..exceptions import DataNotFound, ParserError, QueryException` (sql.py:15).

### `tests/providers/test_user_filter_guard.py` (CREATE)
```python
"""FEAT-184 TASK-955 — provider forwarding and fail-closed guard."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from querysource.auth.principal import QSPrincipal
from querysource.exceptions import UserFilterDenied
from querysource.models import QueryModel
from querysource.providers.sql import sqlProvider

OPTIONS = {"rules": [{"column": "user_id", "attribute": "user_id"}]}


def _definition(**overrides) -> QueryModel:
    data = {"query_slug": "s", "program_slug": "epson", "query_raw": "SELECT * FROM t {where_cond}",
            "user_filtering": True, "user_filter_options": OPTIONS}
    data.update(overrides)
    return QueryModel(**data)


async def test_provider_forwards_identity_to_parser() -> None:
    p = QSPrincipal(user_id="7")
    prov = sqlProvider(slug="s", qstype="slug", definition=_definition(), conditions={},
                       principal=p, userinfo={"x": 1})
    assert prov._parser.principal is p and prov._parser.userinfo == {"x": 1}


async def test_sql_provider_raw_guard() -> None:
    prov = sqlProvider(slug="s", qstype="slug", definition=_definition(is_raw=True), conditions={},
                       connection=MagicMock())
    prov.is_raw = True
    with pytest.raises(UserFilterDenied) as exc:
        await prov.prepare_connection()
    assert exc.value.reason_code == "parser_required"


# FILL IN: test_provider_parserless_guard — subclass sqlProvider with __parser__ = None → UserFilterDenied in __init__
# FILL IN: test_sql_provider_query_without_placeholders_guard — qstype="query", query "SELECT 1", flag on → UserFilterDenied
# FILL IN: test_provider_no_flag_unchanged — user_filtering=False, is_raw → prepare_connection returns provider
# FILL IN: test_checksum_differs_per_user — two parsers with different principals, after set-up + build_query the
#          rendered queries differ (AC-14); patch Redis-dependent set_options parts if needed
```

### FILL IN checklist
- [ ] `UserFilterDenied` import in both provider modules
- [ ] `sqlProvider._refuse_unparsed_user_filtering`
- [ ] remaining tests

---

## Acceptance Criteria

- [ ] Spec AC-12, AC-14, AC-17
- [ ] Semantic provider tests unchanged
- [ ] `ruff check querysource/providers/abstract.py querysource/providers/sql.py tests/providers/test_user_filter_guard.py` clean

---

## Validation Commands

- `pytest tests/providers/test_user_filter_guard.py -q`
- `pytest tests/providers/test_semantic_provider.py -q`

---

## Test Specification

See the test block in the Implementation Blueprint.

---

## Agent Instructions

1. Work in the feature worktree (`parrot sdd worktree ensure --slug pbac-based-per-user-filter --feature-id FEAT-184`).
2. Confirm TASK-954 is done (the parser must accept `principal`/`userinfo`).
3. Verify the Codebase Contract; implement from the blueprint; complete every `# FILL IN:`.
4. Run the Validation Commands; commit only the listed files.
5. Close with `parrot sdd task close TASK-955 pbac-based-per-user-filter verified` and fill the Completion Note.

---

## Completion Note

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: none
