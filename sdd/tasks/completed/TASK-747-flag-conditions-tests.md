# TASK-747: Tests for `to_flag`, provider `refresh` and parser `refresh`/`paged`

**Feature**: FEAT-149 — Falsy Refresh — flag-condition coercion for `refresh` and `paged`
**Spec**: `sdd/specs/falsy-refresh.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: S (< 2h)
**Depends-on**: TASK-744, TASK-745, TASK-746
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 4 and §4. Locks in the truth table, the provider fix and
provider/parser parity, including the `paged` "no longer raises" regression.

---

## Scope

- Create `tests/test_flag_conditions.py` with every test listed in spec §4.
- No production code changes.

**NOT in scope**: HTTP-level tests (decided: none); tests for `externalProvider`.

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `tests/test_flag_conditions.py` | CREATE | helper truth table, provider and parser tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.types import to_flag                       # created by TASK-744
import querysource.types                                    # for __all__ check
from querysource.providers.abstract import BaseProvider     # verified: querysource/providers/abstract.py:23
from querysource.parsers.abstract import AbstractParser     # verified: tests/test_grouping_sync.py:22
```

### Existing Signatures to Use
```python
# querysource/providers/abstract.py
class BaseProvider(ABC):                                     # line 23
    __parser__: AbstractParser = None                        # line 25 (None → no parser built)
    def __init__(self, slug='', query=None, qstype='', connection=None,
                 definition=None, conditions: dict = None, request=None, **kwargs)  # line 38
        # needs a running loop (asyncio.get_running_loop) unless kwargs['loop'] (lines 89-98)
    @abstractmethod
    async def query(self): ...                               # line 240 — the only abstract method
    def refresh(self): return self._refresh                  # line 257
    # self._conditions: dict (copy of conditions, 'refresh' removed)   # line 82
    # self._logger = logging.getLogger(f'QS.{self.__name__}')          # line 50

# Parser stub pattern — tests/test_grouping_sync.py:25-33
class _StubParser(AbstractParser):
    async def build_query(self):
        return self.query_raw

def _make(**conditions) -> _StubParser:
    conditions.setdefault("query_raw", "SELECT 1")
    return _StubParser(definition=None, conditions=conditions, query="SELECT 1")
# then: await parser.set_options(); parser.refresh (public bint); parser.conditions (QueryObject)
```

### Does NOT Exist
- ~~Python access to `parser._paged`~~ — non-public `cdef bint`; assert on "no raise", `conditions` and logs instead.
- ~~`AbstractParser(conditions=...)` without `definition=`~~ — `TypeError` (`definition` is keyword-only, required).
- ~~Existing fixtures for BaseProvider construction~~ — none; define a local `_Provider(BaseProvider)` subclass.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {
      "path": "tests/test_flag_conditions.py",
      "action": "CREATE"
    }
  ],
  "contract_symbols": [
    "sym:querysource/providers/abstract.py#BaseProvider",
    "sym:querysource/parsers/abstract.pyx#AbstractParser"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- `asyncio_mode = auto` — async tests need no decorator (existing files still use `@pytest.mark.asyncio`; either is fine).
- Provider construction must happen inside an async test (running loop required).
- Use `caplog` at `logging.WARNING` to assert warnings.
- Record TASK IDs in the module docstring like sibling tests (`"""FEAT-149 ..."""`).

---

## Implementation Blueprint

### Steps (in order)
1. Create the file with the scaffold below — *why*: fixes names so they match spec §4 exactly.
2. Complete each `# FILL IN:` — *why*: assertions are the judgement part.
3. Run the validation commands — *why*: the new file plus the existing parser suite must pass together.

### `tests/test_flag_conditions.py` (CREATE)
```python
"""FEAT-149 — flag-condition coercion for ``refresh`` and ``paged`` (TASK-747)."""
from __future__ import annotations

import logging

import pytest

import querysource.types
from querysource.parsers.abstract import AbstractParser
from querysource.providers.abstract import BaseProvider
from querysource.types import to_flag

TRUTHY = [True, 1, "", "  ", "true", "TRUE", " yes ", "on", "t", "y", "1"]
FALSY = [False, None, 0, "false", "False", "no", "off", "f", "n", "0", "null"]
UNRECOGNIZED = ["maybe", "2", 2, -1, 1.0, 0.0, b"true", bytearray(b"1"), [], {}]


class _Provider(BaseProvider):
    """Minimal concrete provider with no parser."""

    async def query(self):  # pragma: no cover - not used here
        return None


class _StubParser(AbstractParser):
    """Minimal concrete parser to exercise the option extractors."""

    async def build_query(self):  # pragma: no cover - not used here
        return self.query_raw


def _make_parser(**conditions) -> _StubParser:
    conditions.setdefault("query_raw", "SELECT 1")
    return _StubParser(definition=None, conditions=conditions, query="SELECT 1")


@pytest.mark.parametrize("value", TRUTHY)
def test_to_flag_truthy(value):
    assert to_flag(value) is True


@pytest.mark.parametrize("value", FALSY)
def test_to_flag_falsy(value):
    assert to_flag(value) is False


@pytest.mark.parametrize("value", UNRECOGNIZED)
def test_to_flag_unrecognized_raises(value):
    with pytest.raises(ValueError):
        to_flag(value)


def test_to_flag_exported():
    assert "to_flag" in querysource.types.__all__


async def test_provider_refresh_false_string():
    provider = _Provider(conditions={"refresh": "false"})
    assert provider.refresh() is False
    assert "refresh" not in provider._conditions


@pytest.mark.parametrize("value,expected", [*[(v, True) for v in TRUTHY], *[(v, False) for v in FALSY]])
async def test_provider_refresh_values(value, expected):
    # FILL IN: build _Provider(conditions={"refresh": value, "x": 1}) and assert refresh() is expected — bounded by AC "BaseProvider refresh"; note an empty conditions dict skips parsing (`if conditions:`), so always include another key
    ...


async def test_provider_refresh_unrecognized_warns(caplog):
    # FILL IN: 'maybe' → refresh() is False and a WARNING record mentioning 'refresh' — bounded by spec §5
    ...


async def test_provider_no_refresh_default():
    # FILL IN: conditions without 'refresh' → refresh() is False
    ...


@pytest.mark.parametrize("value", [*TRUTHY, *FALSY, "maybe"])
async def test_parser_refresh_parity(value):
    # FILL IN: parser = _make_parser(refresh=value); await parser.set_options();
    # assert parser.refresh == _Provider(conditions={"refresh": value, "x": 1}).refresh() — bounded by spec G2
    ...


async def test_parser_paged_unrecognized_no_raise(caplog):
    # FILL IN: _make_parser(paged="maybe", page=3); await set_options() must not raise;
    # 'page' no longer in dict(parser.conditions); a WARNING mentioning 'paged' was logged — bounded by spec G4
    ...


async def test_parser_paged_empty_is_true():
    # FILL IN: _make_parser(paged="", page=3); await set_options() must not raise — bounded by spec §2 (empty → True)
    ...
```
**Why this shape**: test names are fixed by spec §4. The `"x": 1` extra key matters because `BaseProvider.__init__` only parses conditions when the dict is truthy (`if conditions:`, abstract.py:80). Empty-list/dict and `None` values inside `TRUTHY`/`FALSY` are only valid for `to_flag` directly — double-check each parametrized provider/parser case is a value that can appear in a conditions dict.

### FILL IN checklist
- [ ] `test_provider_refresh_values` — parametrized assertion
- [ ] `test_provider_refresh_unrecognized_warns` — `caplog` assertion
- [ ] `test_provider_no_refresh_default`
- [ ] `test_parser_refresh_parity` — parser vs provider equality
- [ ] `test_parser_paged_unrecognized_no_raise` — no raise, page consumed, warning
- [ ] `test_parser_paged_empty_is_true` — no raise

---

## Acceptance Criteria

- [ ] All tests in `tests/test_flag_conditions.py` pass.
- [ ] Existing parser suites still pass.
- [ ] `ruff check tests/test_flag_conditions.py` is clean.

---

## Validation Commands

- `pytest tests/test_flag_conditions.py -q`
- `pytest tests/test_grouping_sync.py -q`

---

## Test Specification

See the blueprint above; it is the scaffold.

---

## Agent Instructions

1. Read the spec (§4, §5).
2. Confirm TASK-744/745/746 are done and the extension was rebuilt (`make build-inplace`).
3. Update the index → `"in-progress"`; implement; complete every `# FILL IN:`.
4. Run the validation commands and `ruff check tests/test_flag_conditions.py`.
5. Move this file to `sdd/tasks/completed/`, set the index to `"done"`, fill the Completion Note.

---

## Completion Note

**Completed by**: sdd-worker (Claude Sonnet 5, sequential fallback loop)
**Date**: 2026-09-24
**Notes**: Completed all `# FILL IN:` blocks per the blueprint. All 15 test
functions (parametrized to 83 cases) in `tests/test_flag_conditions.py` pass,
plus the sibling suites (`test_grouping_sync.py`,
`test_sql_parser_combinations.py`) — 188 passed total. `ruff check
tests/test_flag_conditions.py` is clean.

**Deviations from spec**: none
