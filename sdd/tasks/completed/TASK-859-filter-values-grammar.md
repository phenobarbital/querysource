# TASK-859: Filter value grammar module (`filter_values.py`)

**Feature**: FEAT-165 — JSON Dialect Filter Pre-processing Fixes
**Spec**: `sdd/specs/json-dialect-filter-fixes.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §2 A / §3 Module 1. All three FEAT-165 bugs are fixed in the request
pre-processing layer (`AbstractParser`) and in the builders; both need one
shared, dependency-light grammar: key-suffix stripping, comparison-dict
detection, the set of typed filter formats, and `[NOT] BETWEEN` parsing with
safe bound normalisation. This task creates that module, modelled on
`querysource/parsers/partial_matching.py` (FEAT-180).

---

## Scope

- Create `querysource/parsers/filter_values.py` with `KEY_SUFFIX_CHARS`,
  `COMPARISON_OPERATORS`, `TYPED_FILTER_FORMATS`, `BetweenClause`,
  `base_key()`, `is_comparison_dict()`, `parse_between()` exactly as fixed by
  the spec's Interface Skeleton.
- Write unit tests in `tests/test_filter_values.py`.

**NOT in scope**: wiring into `abstract.pyx` (TASK-861/862), builder changes
(TASK-863/864), docs (TASK-866).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/parsers/filter_values.py` | CREATE | Shared filter grammar |
| `tests/test_filter_values.py` | CREATE | Unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
from querysource.exceptions import ParserError       # verified: querysource/exceptions.py:86 (default_code = 400)
from querysource.types.validators import is_udf      # verified: querysource/types/validators.pyx:206 (cpdef bool_t is_udf(object value))
from querysource.utils.functions import to_udf       # verified: querysource/utils/functions.pyx:821 (def to_udf(str value, *args, **kwargs))
```

### Existing Signatures to Use
```python
# querysource/types/validators.pyx:206
cpdef bool_t is_udf(object value)   # True when value (already UPPER-cased by the caller) is in UDF_LIST
                                    # UDF_LIST default: CURRENT_YEAR, CURRENT_MONTH, TODAY, YESTERDAY, LAST_YEAR, FDOM, LDOM
# querysource/utils/functions.pyx:821
def to_udf(str value, *args, **kwargs)  # calls the zero-arg function named value.lower(); e.g. to_udf('FDOM') -> '2026-10-01'
                                        # NOTE: to_udf('TODAY') returns 'MM/DD/YYYY'
# querysource/parsers/partial_matching.py — style reference (pure Python, dataclass, ParserError messages)
class PartialMatchOp:                                               # line 22
def validate_partial_match_dict(key: str, value: dict, *, supports_regex: bool) -> PartialMatchOp | None:  # line 135
```

### Does NOT Exist
- ~~`querysource.parsers.filter_values`~~ — this task creates it.
- ~~`querysource.types.validators.to_udf`~~ — `to_udf` lives in `querysource.utils.functions` (validators star-imports it; import it from `utils.functions`).
- ~~a `BETWEEN` helper anywhere in `querysource/parsers/`~~ — builders only do substring checks.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/parsers/filter_values.py", "action": "CREATE"},
    {"path": "tests/test_filter_values.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/parsers/partial_matching.py#validate_partial_match_dict",
    "sym:querysource/parsers/partial_matching.py#PartialMatchOp"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Pure Python (`.py`, not `.pyx`) — no rebuild needed; importable from Cython
  modules with a normal `from .filter_values import ...`.
- Never mutate inputs.
- Error messages are stable (asserted by tests): `invalid BETWEEN clause for '<key>'`.
- Bound grammar (spec §2 B.1):
  - number `-?\d+(\.\d+)?` → rendered bare;
  - single-quoted literal `'...'` with `''` escapes → unescape, then re-quote
    (double every `'`, wrap in `'`);
  - bare token `[A-Za-z0-9_:./+-]+` → if `is_udf(token.upper())` resolve with
    `to_udf(token)` and quote the `str()` result; otherwise quote the token;
  - anything else → `ParserError`.
- Clause regex: whole string, case-insensitive, optional leading `NOT`:
  `^\s*(NOT\s+)?BETWEEN\s+(?P<lo>BOUND)\s+AND\s+(?P<hi>BOUND)\s*$` where a
  quoted bound may contain spaces (`'(?:[^']|'')*'`).
- `parse_between` returns `None` when the stripped value does not start with
  `BETWEEN ` / `NOT BETWEEN ` (case-insensitive) — e.g. `"betweenness"`,
  `"IN BETWEEN"`. It raises only when the prefix matches but the rest is invalid.
- A `!` suffix on `key` (`base_key(key) != key` and `key.rstrip()` ends with
  `!`) negates; `NOT` + `!` together stays negated (do not double-negate).

### References in Codebase
- `querysource/parsers/partial_matching.py` — module layout, docstring style, ParserError usage.

---

## Implementation Blueprint

### Steps (in order)
1. Create `filter_values.py` from the block below — *why*: signatures are fixed by spec §3 M1 and imported by TASK-861/862/863.
2. Implement `parse_between` and the bound normaliser (FILL IN) — *why*: this is the only place bounds become SQL literals, so it must reject everything outside the grammar.
3. Write the tests in `tests/test_filter_values.py` — *why*: TASK-861/863 rely on these semantics without re-testing the grammar.

### `querysource/parsers/filter_values.py` (CREATE)
```python
"""Filter value grammar shared by the query parsers (FEAT-165).

Key suffixes, comparison dicts, typed filter formats and ``[NOT] BETWEEN``
parsing. Pure Python so both the Cython parsers and tests import it directly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..exceptions import ParserError
from ..types.validators import is_udf
from ..utils.functions import to_udf

KEY_SUFFIX_CHARS: str = '|!~#@:'
COMPARISON_OPERATORS: tuple[str, ...] = ('>=', '<=', '<>', '!=', '<', '>')
TYPED_FILTER_FORMATS: frozenset[str] = frozenset(
    {'array', 'numrange', 'int4range', 'int8range', 'tsrange', 'tstzrange', 'daterange'}
)

_BOUND = r"(?:'(?:[^']|'')*'|[A-Za-z0-9_:./+-]+)"
_BETWEEN_PREFIX = re.compile(r'^\s*(?:NOT\s+)?BETWEEN\s', re.IGNORECASE)
_BETWEEN_CLAUSE = re.compile(
    rf'^\s*(?P<not>NOT\s+)?BETWEEN\s+(?P<lo>{_BOUND})\s+AND\s+(?P<hi>{_BOUND})\s*$',
    re.IGNORECASE,
)
_NUMBER = re.compile(r'^-?\d+(?:\.\d+)?$')


@dataclass(frozen=True)
class BetweenClause:
    """A validated, normalised ``[NOT] BETWEEN`` clause."""

    negated: bool
    low: str
    high: str

    def render(self) -> str:
        """Return ``'BETWEEN <low> AND <high>'`` (``'NOT BETWEEN ...'`` when negated)."""
        prefix = 'NOT BETWEEN' if self.negated else 'BETWEEN'
        return f'{prefix} {self.low} AND {self.high}'


def base_key(key: str) -> str:
    """Return ``key`` without trailing suffix characters (``KEY_SUFFIX_CHARS``)."""
    return key.rstrip(KEY_SUFFIX_CHARS)


def is_comparison_dict(value: dict) -> bool:
    """True when ``value`` is non-empty and every key is in ``COMPARISON_OPERATORS``."""
    return bool(value) and all(k in COMPARISON_OPERATORS for k in value)


def _quote(text: str) -> str:
    """Quote ``text`` as a SQL string literal (single quotes doubled)."""
    return "'" + text.replace("'", "''") + "'"


def _normalise_bound(key: str, bound: str) -> str:
    """Return ``bound`` as a safe SQL literal (see spec §2 B.1).

    Raises:
        ParserError: the bound is outside the grammar.
    """
    # FILL IN: number → bare; quoted → unescape '' then _quote(); bare token →
    #          to_udf() when is_udf(token.upper()) else _quote(token);
    #          anything else → ParserError(f"invalid BETWEEN clause for '{key}'")
    #          — bounded by spec §2 B.1 and AC "malformed BETWEEN raises ParserError"
    raise NotImplementedError


def parse_between(key: str, value: str) -> BetweenClause | None:
    """Parse ``[NOT] BETWEEN <lo> AND <hi>`` (case-insensitive, whole string).

    A ``!`` suffix on ``key`` also negates. Returns None when ``value`` does not
    start with ``BETWEEN``/``NOT BETWEEN``.

    Raises:
        ParserError: the value starts like a BETWEEN clause but is malformed.
    """
    if not isinstance(value, str) or not _BETWEEN_PREFIX.match(value):
        return None
    # FILL IN: match _BETWEEN_CLAUSE (ParserError on no match), compute negated
    #          from the NOT group OR key ending with '!', normalise both bounds
    #          — bounded by spec §2 B.1; never double-negate
    raise NotImplementedError
```
**Why this shape**: constants and helpers are the exact names the spec's
Interface Skeleton fixes and later tasks import. The regexes are deliberately
strict (no `;`, `--`, `/*`, parentheses or spaces outside quoted bounds), so
injection markers never match and fall into the `ParserError` path instead of
the old silent drop.

### `tests/test_filter_values.py` (CREATE)
```python
"""FEAT-165: filter value grammar."""
import pytest

from querysource.exceptions import ParserError
from querysource.parsers.filter_values import (
    BetweenClause, base_key, is_comparison_dict, parse_between,
)


def test_parse_between_numbers():
    assert parse_between('amount', 'BETWEEN 100 AND 500').render() == 'BETWEEN 100 AND 500'


# FILL IN: test_parse_between_quoted_and_bare_dates, test_parse_between_keyword_bound
#          (FDOM/LDOM resolve to quoted YYYY-MM-DD), test_parse_between_negated (NOT and '!'),
#          test_parse_between_rejects (parametrize: "BETWEEN 1 AND 2; DROP", "BETWEEN 1 -- AND 2",
#          "BETWEEN /*x*/ 1 AND 2", "BETWEEN 1 AND 2 UNION SELECT 1", "BETWEEN 1 AND 2 AND 3",
#          "BETWEEN AND 2"), test_parse_between_not_a_clause ("betweenness", "IN BETWEEN", 5),
#          test_base_key, test_is_comparison_dict (mixed/empty → False), embedded quote
#          "BETWEEN 'O''Brien' AND 'Z'" → "'O''Brien'" — bounded by spec §4 M1 rows
```

### FILL IN checklist
- [ ] `filter_values.py::_normalise_bound` — bound grammar; bounded by spec §2 B.1
- [ ] `filter_values.py::parse_between` — match/negation/normalisation; bounded by spec §2 B.1
- [ ] `tests/test_filter_values.py` — the cases listed in the stub; bounded by spec §4

---

## Acceptance Criteria

- [ ] `from querysource.parsers.filter_values import base_key, is_comparison_dict, parse_between, TYPED_FILTER_FORMATS, COMPARISON_OPERATORS, BetweenClause` works
- [ ] All tests pass: `pytest tests/test_filter_values.py -v`
- [ ] `ruff check querysource/parsers/filter_values.py tests/test_filter_values.py` clean
- [ ] Every rejected input raises `ParserError` with message `invalid BETWEEN clause for '<key>'`

## Validation Commands

- `pytest tests/test_filter_values.py -q`

---

## Test Specification

See the `tests/test_filter_values.py` block above.

## Completion Note

Seat: gpt-5.6-terra · Backend: codex · Model: gpt-5.6-terra · Attempts: 1 · Duration: 248s · Tokens: n/a.
Created `querysource/parsers/filter_values.py` + tests; 17 passed, ruff clean. No review fixes.
