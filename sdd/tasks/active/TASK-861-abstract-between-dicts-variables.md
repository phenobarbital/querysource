# TASK-861: Pre-processing — BETWEEN normalisation, whole-dict filters, unknown `@variable`

**Feature**: FEAT-165 — JSON Dialect Filter Pre-processing Fixes
**Spec**: `sdd/specs/json-dialect-filter-fixes.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-859
**Assigned-to**: unassigned

---

## Context

Spec §2 B.1, B.2, B.6 / §3 Module 2 (bugs 1, 2 and the secondary `@variable`
bug). `AbstractParser._where_element` quotes `BETWEEN` strings via
`is_valid()` and reduces every non-partial-match dict to its last item; an
unregistered `@name` silently drops the condition. This task fixes those three
behaviours at their origin. Typed-filter routing (bug 3) is TASK-862, in the
same file, after this task.

> Spec §8 Q1 (resolved by the user as "we need to do a research about this"):
> the 400 for malformed BETWEEN / unknown `@name` is implemented as the spec
> decides (Q3: `ParserError`). Mention the open research item in the PR.

---

## Scope

- `_where_element`, string values: before `is_parseable()`, call
  `parse_between(key, value)`; when it returns a clause, return
  `(base_key(key), clause.render())` without calling `is_valid()`.
- `_where_element`, dict values (after the partial-match check, which stays
  first): comparison dict → new dict keeping **every** operator with each
  operand passed through `is_valid(key, v, noquote=self.string_literal)`; any
  other dict → `dict(value)` unmodified.
- `set_where`: raise `ParserError(f"conflicting filters for '{col}'")` when two
  results share a key (e.g. `amount` and `amount!` both BETWEEN).
- `_get_function_replacement`: raise
  `ParserError(f"unknown variable '@{function}'")` when `function` is not in
  `QS_VARIABLES` (a registered function returning `None` keeps today's flow).
- Tests in `tests/test_dialect_filter_preprocessing.py` asserting on
  `parser.filter` after `set_options()`.

**NOT in scope**: `set_conditions` routing, `_typed_filter_keys`,
`_process_element` (TASK-862); builders (TASK-863/864); `_handle_keys`
(placeholder path — leave unchanged, spec §7).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/parsers/abstract.pyx` | MODIFY | `_where_element`, `set_where`, `_get_function_replacement`, imports |
| `tests/test_dialect_filter_preprocessing.py` | CREATE | full `set_options()` pre-processing tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# in abstract.pyx
from ..exceptions import EmptySentence                       # verified: abstract.pyx:17 → extend to `EmptySentence, ParserError`
from .partial_matching import validate_partial_match_dict    # verified: abstract.pyx:20
from .filter_values import base_key, is_comparison_dict, parse_between  # created by TASK-859
# in tests
from querysource.exceptions import ParserError               # verified: querysource/exceptions.py:86
from querysource.models import QueryObject                   # verified: tests/test_pgsql_partial_matching.py:9
from querysource.parsers.pgsql import pgSQLParser            # verified: tests/test_pgsql_partial_matching.py:11
from querysource.parsers import QS_VARIABLES                 # verified: querysource/parsers/__init__.py:6 (a plain dict)
```

### Existing Signatures to Use
```python
# querysource/parsers/abstract.pyx
cdef object _get_function_replacement(self, object function, str key, object val):  # line 442
    fn = QS_VARIABLES.get(function, None); if callable(fn): return fn(key, val); return None
async def _where_element(self, key, value, connection):      # line 567
    # dict branch lines 570-583: partial match (575-578) then `op, v = next(reversed(value.items()))` (581)
    # string branch: is_parseable (585-591), field_components prefix '@' (596-599), passthrough prefixes (600-601), is_valid (607)
async def set_where(self, _filter: dict, connection: object) -> object:   # line 611
    # where_cond = {key: value for key, value in results}  (line 616)
async def set_options(self)   # public entry: extracts options then set_conditions + set_where
# pgSQLParser._where_element (pgsql.pyx:597) handles '[].' path keys then calls super() — unchanged
```

### Does NOT Exist
- ~~`AbstractParser._typed_filter_keys`~~ — added by TASK-862, not here.
- ~~a `ParserError` import in abstract.pyx today~~ — only `EmptySentence` is imported (line 17).
- ~~an existing end-to-end filter test~~ — current filter tests set `parser.filter` directly and skip `set_options()`.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/parsers/abstract.pyx", "action": "MODIFY"},
    {"path": "tests/test_dialect_filter_preprocessing.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/parsers/abstract.pyx#AbstractParser._where_element",
    "sym:querysource/parsers/abstract.pyx#AbstractParser.set_where",
    "sym:querysource/parsers/abstract.pyx#AbstractParser._get_function_replacement",
    "sym:querysource/parsers/pgsql.pyx#pgSQLParser._where_element"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Never mutate the caller's dict (comment at abstract.pyx:579-580 explains why).
- Ordering inside `_where_element` (spec §7): dict branch → partial match first;
  string branch → BETWEEN check **before** `is_parseable()`; a value starting
  with `@` is never a BETWEEN clause (`parse_between` returns None for it).
- `_get_function_replacement` is `cdef object` — raising a Python exception from
  it propagates (it is not `noexcept`); keep the signature.
- Rebuild: `python setup.py build_ext --inplace --build-temp /tmp/qs-build`
  (never `make build`). Exclusive task.
- Through Rust, `filter_conditions` still receives the new shapes; multi-operator
  rendering arrives with TASK-863/864, so assert on `parser.filter` here, and on
  SQL only for BETWEEN (the existing builders already render the canonical form).

---

## Implementation Blueprint

### Steps (in order)
1. Extend the imports — *why*: `ParserError` and the TASK-859 helpers are needed below.
2. Make `_get_function_replacement` raise for unregistered names — *why*: an unknown `@name` must be a 400, not a silent drop (spec §2 B.6).
3. Replace the dict reduction in `_where_element` — *why*: bug 2's root cause (spec §2 B.2).
4. Add the BETWEEN branch before `is_parseable` — *why*: bug 1's root cause (spec §2 B.1).
5. Add the collision check in `set_where` — *why*: `amount` + `amount!` would otherwise overwrite silently.
6. Rebuild and write the tests.

### `querysource/parsers/abstract.pyx` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'from ..exceptions import EmptySentence' abstract.pyx)
# REPLACE line 17
from ..exceptions import EmptySentence, ParserError
# occurrences: 1 (verified: grep -c 'from .partial_matching import validate_partial_match_dict' abstract.pyx)
# AFTER line 20
from .filter_values import base_key, is_comparison_dict, parse_between
```
```python
# occurrences: 1 (verified: grep -c 'cdef object _get_function_replacement(self, object function, str key, object val):' abstract.pyx)
# REPLACE the body (verified: abstract.pyx:442-446)
    cdef object _get_function_replacement(self, object function, str key, object val):
        fn = QS_VARIABLES.get(function, None)
        if fn is None:
            raise ParserError(f"unknown variable '@{function}'")
        if callable(fn):
            return fn(key, val)
        return None
```
```python
# occurrences: 1 (verified: grep -c '            op, v = next(reversed(value.items()))' abstract.pyx — the 12-space indented one at line 581)
# REPLACE lines 579-583 (comment + reduction) with:
            # Never mutate the caller's dict (a linked dashboard may re-send it).
            if is_comparison_dict(value):
                return key, {
                    op: is_valid(key, v, noquote=self.string_literal)
                    for op, v in value.items()
                }
            # JSONB payloads / operators, qsurl ILIKE and mixed dicts reach the
            # builders unmodified: they own validation and quoting (FEAT-165).
            return key, dict(value)
```
```python
# occurrences: 1 (verified: grep -c '            parser = is_parseable(value)' abstract.pyx)
# BEFORE `if isinstance(value, str):` at line 585, insert:
        if isinstance(value, str):
            clause = parse_between(key, value)
            if clause is not None:
                return base_key(key), clause.render()
```
```python
# occurrences: 1 (verified: grep -c '        where_cond = {key: value for key, value in results}' abstract.pyx)
# REPLACE line 616
        where_cond = {}
        for key, value in results:
            if key in where_cond:
                raise ParserError(f"conflicting filters for '{key}'")
            where_cond[key] = value
```
**Why**: each block maps one spec decision (B.6, B.2, B.1, collision rule) onto
the exact line that causes the bug; nothing else in the method changes.

### `tests/test_dialect_filter_preprocessing.py` (CREATE)
```python
"""FEAT-165: request pre-processing through set_options() (not builder-only)."""
import pytest

from querysource.exceptions import ParserError
from querysource.parsers import QS_VARIABLES
from querysource.parsers.pgsql import pgSQLParser

SQL = "SELECT * FROM public.t {where_cond}"


async def preprocess(conditions: dict, query: str = SQL) -> pgSQLParser:
    """Run the real pre-processing pipeline and return the parser."""
    parser = pgSQLParser(definition=None, conditions=dict(conditions), query=query)
    await parser.set_options()
    return parser


async def test_between_numeric_is_normalised():
    parser = await preprocess({"filter": {"amount": "BETWEEN 100 AND 500"}})
    assert parser.filter == {"amount": "BETWEEN 100 AND 500"}
    assert "(amount BETWEEN 100 AND 500)" in await parser.build_query()


# FILL IN: dates (quoted + bare) → quoted bounds; 'amount!' → key 'amount', 'NOT BETWEEN';
#          malformed → ParserError("invalid BETWEEN clause"); 'amount' + 'amount!' → ParserError("conflicting filters");
#          {"note": "IN BETWEEN"} stays a quoted equality value;
#          implicit containment dict kept whole and unquoted ({"status": "active", "tier": "gold"});
#          comparison dict keeps both ops ({">": 1, "<": 9} → both keys present);
#          explicit {"@>": {...}} and {"@>|": [...]} unchanged; partial match unchanged;
#          unknown '@nope' → ParserError("unknown variable '@nope'");
#          registered variable (monkeypatch.setitem(QS_VARIABLES, 'qs_test_var', lambda k, v: '2025-01-01')) resolves;
#          caller's filter dict unchanged after set_options()
#          — bounded by spec §4 rows for M2 (BETWEEN, dicts, variables)
```

### FILL IN checklist
- [ ] test cases listed in the stub — bounded by spec §4 / §5

---

## Acceptance Criteria

- [ ] `pytest tests/test_dialect_filter_preprocessing.py -v` passes after an in-place rebuild
- [ ] Existing suites unchanged: `pytest tests/test_pgsql_partial_matching.py tests/test_sql_partial_matching.py tests/test_partial_matching_prevalidation.py tests/e2e/test_qs_dry_run.py -q`
- [ ] `ruff check tests/test_dialect_filter_preprocessing.py` clean
- [ ] The caller's filter dict is never mutated

## Validation Commands

- `pytest tests/test_dialect_filter_preprocessing.py -q`
- `pytest tests/test_partial_matching_prevalidation.py -q`
- `pytest tests/test_pgsql_partial_matching.py -q`
- `pytest tests/e2e/test_qs_dry_run.py -q`
