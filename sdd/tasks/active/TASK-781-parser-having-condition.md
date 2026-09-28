# TASK-781: `having` condition extraction (parser, QueryObject, SQL provider)

**Feature**: FEAT-153 — Group & Aggregate by JSONB Array Elements (PostgreSQL)
**Spec**: `sdd/specs/group-aggregation-jsonb-columns.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 3 (first half), AC5. `set_options` merges every leftover condition
into WHERE filters (`set_conditions`: `{**conditions, **self.filter}`), so a new
`having` condition would become `WHERE having = ...` unless it is popped in
`_extract_options` first. It must also be a parser-condition key so direct-query
mode (`sqlProvider._type == 'query'`) engages the parser when only `having` is sent.

---

## Scope

- Add `cdef public object having` + `cdef void _having_sync(self)` to `abstract.pxd`.
- Initialise `self.having = {}` in `set_attributes`; implement `_having_sync`; call it
  from `_extract_options` right after `_grouping_sync()`.
- Add `having: Optional[dict]` to `QueryObject`.
- Add `"having"` to `sqlProvider._PARSER_CONDITION_KEYS`.
- Tests `tests/test_parser_having_condition.py`.

**NOT in scope**: validating `having` contents (the planner does, TASK-782); `pgsql.pyx`.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/parsers/abstract.pxd` | MODIFY | Declare `having` + `_having_sync` |
| `querysource/parsers/abstract.pyx` | MODIFY | Init, extractor, call site |
| `querysource/models.py` | MODIFY | `QueryObject.having` |
| `querysource/providers/sql.py` | MODIFY | `"having"` parser key |
| `tests/test_parser_having_condition.py` | CREATE | Extraction tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.parsers.abstract import AbstractParser     # verified: tests/test_grouping_sync.py:21
from querysource.providers.sql import sqlProvider           # verified: querysource/providers/sql.py (class holds _PARSER_CONDITION_KEYS at :60)
```

### Existing Signatures to Use
```python
# querysource/parsers/abstract.pxd
    cdef public list grouping          # line 21
    cdef void _grouping_sync(self)     # line 72
# querysource/parsers/abstract.pyx
    cdef void set_attributes(self)     # line 69; `self.grouping = []` at line 81
    cdef void _extract_options(self)   # line 147; `self._grouping_sync()` at 156
    cdef void _grouping_sync(self)     # line 237 (pattern: try/except AttributeError around conditions.pop)
    async def set_options(self)        # line 391 (runs _extract_options, then merges leftovers into filters)
# querysource/models.py
class QueryObject(ClassDict):          # line 24
    group_by: Optional[list]           # line 33
# querysource/providers/sql.py
    _PARSER_CONDITION_KEYS = frozenset({ ... })   # lines 60-67
    @classmethod
    def _has_parser_conditions(cls, conditions: object) -> bool   # line 86
# tests/test_grouping_sync.py:24-33 — _StubParser + _make(**conditions) pattern; tests call `await parser.set_options()`
```

### Does NOT Exist
- ~~`AbstractParser.having`~~ / ~~`_having_sync`~~ — created here.
- ~~`QueryModel.having`~~ — not added; `having` is request-only (slugs use `attributes.jsonb_unnest` aliases).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/parsers/abstract.pxd", "action": "MODIFY"},
    {"path": "querysource/parsers/abstract.pyx", "action": "MODIFY"},
    {"path": "querysource/models.py", "action": "MODIFY"},
    {"path": "querysource/providers/sql.py", "action": "MODIFY"},
    {"path": "tests/test_parser_having_condition.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/parsers/abstract.pyx#AbstractParser._extract_options",
    "sym:querysource/parsers/abstract.pyx#AbstractParser._grouping_sync",
    "sym:querysource/parsers/abstract.pyx#AbstractParser.set_attributes",
    "sym:querysource/models.py#QueryObject",
    "sym:querysource/providers/sql.py#sqlProvider._has_parser_conditions"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Type is `object` (not `dict`): a non-dict `having` must survive extraction so the planner
  can reject it with a `ParserError` (spec: invalid input raises, never silently dropped).
  A `cdef void` raising here would surface outside the provider's `ParserError` wrapping.
- `None` becomes `{}`.
- Cython 3 (`Cython==3.0.11` pinned): keep the method `cdef void` like its siblings.

---

## Implementation Blueprint

### Steps (in order)
1. Edit `abstract.pxd` — *why*: Cython needs the attribute declared before `.pyx` can assign it.
2. Edit `abstract.pyx` (3 edits) — *why*: pop `having` before `set_options` merges leftovers into filters.
3. Edit `models.py` and `providers/sql.py` — *why*: typed condition + direct-query parser engagement.
4. Write the tests, `make build-inplace`, run validation.

### `querysource/parsers/abstract.pxd` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '    cdef public list grouping' querysource/parsers/abstract.pxd)
# AFTER — insert below `    cdef public list grouping` (verified: abstract.pxd:21)
    cdef public object having
# occurrences: 1 (verified: grep -c '    cdef void _grouping_sync(self)' querysource/parsers/abstract.pxd)
# AFTER — insert below `    cdef void _grouping_sync(self)` (verified: abstract.pxd:72)
    cdef void _having_sync(self)
```

### `querysource/parsers/abstract.pyx` (MODIFY)
```python
# occurrences: 2 (verified: grep -c '        self.grouping = \[\]' querysource/parsers/abstract.pyx) -> disambiguate:
# in set_attributes, the block reads:
#         self.ordering = []
#         self.grouping = []
#         self.program_slug = None
# AFTER `        self.grouping = []` in THAT block (abstract.pyx:81), insert:
        self.having = {}

# occurrences: 2 (verified: grep -c '        self._grouping_sync()' querysource/parsers/abstract.pyx) -> disambiguate:
# in _extract_options (abstract.pyx:155-157):
#         self._offset_pagination_sync()
#         self._grouping_sync()
#         self._ordering_sync()
# AFTER `        self._grouping_sync()` in THAT block (line 156), insert:
        self._having_sync()

# occurrences: 1 — the method starts at `    cdef void _grouping_sync(self):` (abstract.pyx:237);
# insert this new method right after the END of _grouping_sync (before `    cdef void _ordering_sync(self):`, abstract.pyx:259)
    cdef void _having_sync(self):
        """Pop the ``having`` condition so it never becomes a WHERE filter (FEAT-153).

        The value is kept as-is (validated later by the JSONB-unnest planner, which
        raises ``ParserError`` for non-mapping values); ``None`` becomes ``{}``.
        """
        try:
            self.having = self.conditions.pop('having', {})
        except (KeyError, AttributeError):
            self.having = {}
        if self.having is None:
            self.having = {}
```

### `querysource/models.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '    group_by: Optional\[list\]' querysource/models.py)
# AFTER — insert below `    group_by: Optional[list]` (verified: models.py:33)
    having: Optional[dict]
```

### `querysource/providers/sql.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '"group_by", "grouping", "order_by", "ordering",' querysource/providers/sql.py)
# REPLACE the line `        "group_by", "grouping", "order_by", "ordering",` (verified: providers/sql.py:63) with:
        "group_by", "grouping", "having", "order_by", "ordering",
```

### `tests/test_parser_having_condition.py` (CREATE)
```python
"""FEAT-153: the ``having`` condition is extracted and never leaks into WHERE filters."""
from __future__ import annotations

import pytest

from querysource.parsers.abstract import AbstractParser
from querysource.providers.sql import sqlProvider


class _StubParser(AbstractParser):
    """Minimal concrete parser to exercise the option extractors."""

    async def build_query(self):  # pragma: no cover - not used here
        return self.query_raw


def _make(**conditions) -> _StubParser:
    conditions.setdefault("query_raw", "SELECT 1")
    return _StubParser(definition=None, conditions=conditions, query="SELECT 1")


@pytest.mark.asyncio
async def test_having_is_extracted_not_filtered():
    parser = _make(having={"graduates": {">": 5}}, group_by=["course"])
    await parser.set_options()
    assert parser.having == {"graduates": {">": 5}}
    assert "having" not in parser.filter


def test_having_is_a_parser_condition_key():
    assert sqlProvider._has_parser_conditions({"having": {"total": {">": 1}}})

# FILL IN: default {} when absent; None -> {}; non-dict value kept verbatim (e.g. "x");
#   bounded by Implementation Notes (planner validates, extractor never raises).
```

### FILL IN checklist
- [ ] tests — absent / None / non-dict cases

---

## Acceptance Criteria

- [ ] `having` never appears in `parser.filter` after `set_options()`.
- [ ] `pytest tests/test_grouping_sync.py -q` still passes.
- [ ] `ruff check querysource/models.py querysource/providers/sql.py tests/test_parser_having_condition.py` is clean.

---

## Validation Commands

- `pytest tests/test_parser_having_condition.py -q`
- `pytest tests/test_grouping_sync.py -q`

---

## Test Specification

See the test block above. Note: like `tests/test_grouping_sync.py`, `set_options()` needs a
reachable Redis (`_parser_conditions`); run where that suite already runs.

---

## Agent Instructions

1. Read spec §3 Module 3 and §7 (having leaking into WHERE).
2. Implement; `make build-inplace` (exclusive resource).
3. Move this file to `sdd/tasks/completed/`, set index status `"done"`, fill the Completion Note.

---

## Completion Note

*(Agent fills this in when done)*

**Completed by**:
**Date**:
**Notes**:

**Deviations from spec**: `having` declared `object` (spec skeleton said `dict`) so non-mapping input reaches the planner and raises `ParserError`.
