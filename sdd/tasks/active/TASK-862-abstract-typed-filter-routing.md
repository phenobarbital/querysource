# TASK-862: Pre-processing — typed `cond_definition` keys in an explicit filter stay filters

**Feature**: FEAT-165 — JSON Dialect Filter Pre-processing Fixes
**Spec**: `sdd/specs/json-dialect-filter-fixes.spec.md`
**Status**: pending
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: TASK-859, TASK-861
**Assigned-to**: unassigned

---

## Context

Spec §2 B.3, B.4, B.5 / §3 Module 2 (bug 3). `set_conditions` routes every key
declared in `cond_definition` to the placeholder dict, so the PostgreSQL typed
renderings (`array`, ranges, date-list `BETWEEN`) are unreachable: the
condition vanishes when the template has no `{name}`, and an `array` key with a
string crashes in `_process_element` (only `TypeError` is caught).

Spec §8 Q2 was answered "become typed filters" for **flat** keys — that is a
*later* feature. This task changes only keys that come from the explicit filter
(`filter` / `where_cond` / the slug's `filtering`).

---

## Scope

- Declare and initialise `self._typed_filter_keys` (a `set`).
- `set_conditions`: an explicit-filter key whose `base_key()` is declared in
  `cond_definition` and has **no** `{<base>}` placeholder in `self.query_raw`
  bypasses `_process_element`, goes straight to the returned filter, and its
  base key is added to `_typed_filter_keys`. All other routing is unchanged.
- `_where_element`: for a key whose base is in `_typed_filter_keys`, return the
  value **raw** when its type is in `TYPED_FILTER_FORMATS`, or when the type is
  `date`/`datetime` and the value is a two-item list.
- `_process_element`: catch `ValueError` exactly like the existing `TypeError`.
- Tests in `tests/test_dialect_typed_filters.py` (Cython path only — the `|`
  suffix and Rust quoting arrive with TASK-863/864; the parity file is TASK-865).

**NOT in scope**: builder changes; flat-key routing (later feature, spec §8 Q2).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/parsers/abstract.pyx` | MODIFY | `set_attributes`, `set_conditions`, `_where_element`, `_process_element` |
| `querysource/parsers/abstract.pxd` | MODIFY | declare `_typed_filter_keys` |
| `tests/test_dialect_typed_filters.py` | CREATE | tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# abstract.pyx — after TASK-861 the import line reads:
from .filter_values import base_key, is_comparison_dict, parse_between   # TASK-861
# extend it to also import TYPED_FILTER_FORMATS (created by TASK-859)
```

### Existing Signatures to Use
```python
# querysource/parsers/abstract.pyx (line numbers at dev@83136229; TASK-861 shifts them — re-grep)
cdef void set_attributes(self):                    # line 69; last line `self.supports_partial_match = False` (98)
async def _process_element(self, name, value, connection):   # line 506; `except TypeError as exc:` at 524
async def set_conditions(self, conditions: dict, connection) -> dict:  # line 544
    elements = self._merge_conditions_and_filters(conditions)            # line 546 — {**conditions, **self.filter}
    # results loop: `if key in self.cond_definition:` (557) → self._conditions[key] = value
self.query_raw   # the slug template string (set in define_conditions)
self.filter      # explicit filter dict (where_cond / filter / definition.filtering), set by _query_filter_sync
# querysource/parsers/abstract.pxd
    cdef bint _distinct                            # line 37 — insert the new declaration after it
```

### Does NOT Exist
- ~~`AbstractParser._typed_filter_keys`~~ — this task creates it.
- ~~a helper that lists a template's placeholders~~ — use the literal test `'{' + base + '}' in (self.query_raw or '')`.

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/parsers/abstract.pyx", "action": "MODIFY"},
    {"path": "querysource/parsers/abstract.pxd", "action": "MODIFY"},
    {"path": "tests/test_dialect_typed_filters.py", "action": "CREATE"}
  ],
  "contract_symbols": [
    "sym:querysource/parsers/abstract.pyx#AbstractParser.set_conditions",
    "sym:querysource/parsers/abstract.pyx#AbstractParser._process_element",
    "sym:querysource/parsers/abstract.pyx#AbstractParser._where_element",
    "sym:querysource/parsers/abstract.pyx#AbstractParser.set_attributes"
  ]
}
```

---

## Implementation Notes

### Key Constraints
- Backwards compatibility (AC): a filter key that **is** a template placeholder
  is still consumed as a placeholder; flat keys keep today's routing.
- Raw typed values are safe only because every PostgreSQL typed branch escapes
  or validates (`pgsql.pyx` array/range branches; Rust after TASK-864).
- Rebuild: `python setup.py build_ext --inplace --build-temp /tmp/qs-build`; exclusive task.

---

## Implementation Blueprint

### Steps (in order)
1. Declare `cdef public set _typed_filter_keys` in `abstract.pxd` and initialise it in `set_attributes` — *why*: `set_conditions` writes it, `_where_element` reads it.
2. Route explicit typed keys in `set_conditions` before scheduling `_process_element` — *why*: `_process_element` would type-convert (and possibly crash on) them.
3. Return typed values raw from `_where_element` — *why*: builders quote typed values themselves; pre-quoting double-quotes them.
4. Catch `ValueError` in `_process_element` — *why*: a bad typed placeholder value must not crash the request (spec §2 B.5).

### `querysource/parsers/abstract.pxd` (MODIFY)
```python
# occurrences: 1 (verified: grep -c 'cdef bint _distinct' abstract.pxd)
# AFTER `    cdef bint _distinct` (verified: abstract.pxd:37)
    cdef public set _typed_filter_keys
```

### `querysource/parsers/abstract.pyx` (MODIFY)
```python
# occurrences: 1 (verified: grep -c '        self.supports_partial_match = False' abstract.pyx)
# AFTER line 98
        self._typed_filter_keys = set()
```
```python
# occurrences: 1 (verified: grep -c '                except TypeError as exc:' abstract.pyx)
# REPLACE that line (verified: abstract.pyx:524)
                except (TypeError, ValueError) as exc:
```
```python
# occurrences: 1 (verified: grep -c '        elements = self._merge_conditions_and_filters(conditions)' abstract.pyx)
# REPLACE the loop that schedules _process_element (abstract.pyx:550-551) with:
        explicit = self.filter if isinstance(self.filter, dict) else {}
        template = self.query_raw or ''
        for name, val in elements.items():
            base = base_key(name)
            if (
                name in explicit
                and base in self.cond_definition
                and '{' + base + '}' not in template
            ):
                # FEAT-165: a typed column filtered explicitly stays a WHERE filter.
                _filter[name] = val
                self._typed_filter_keys.add(base)
                continue
            tasks.append(self._process_element(name, val, connection))
```
```python
# _where_element — insert as the FIRST statement of the method body
        base = base_key(key) if isinstance(key, str) else key
        if base in self._typed_filter_keys:
            # FILL IN: return (key, value) unchanged when
            #          self.cond_definition.get(base) in TYPED_FILTER_FORMATS, or when it is
            #          'date'/'datetime' and value is a 2-item list; otherwise fall through
            #          — bounded by spec §2 B.4
            pass
```
**Why**: the routing decision is made once, where keys are classified, and
recorded so `_where_element` can skip `is_valid()` quoting for exactly those
keys. The `'{base}' in template` test is the spec's definition of "is a
placeholder" (§2 B.3).

### `tests/test_dialect_typed_filters.py` (CREATE)
```python
"""FEAT-165: typed cond_definition columns filtered explicitly (Cython path)."""
import pytest

from querysource.parsers import pgsql
from querysource.parsers.pgsql import pgSQLParser

SQL = "SELECT * FROM public.t {where_cond}"


async def render(conditions: dict, monkeypatch, query: str = SQL) -> str:
    """Full pipeline on the Cython path."""
    monkeypatch.setattr(pgsql, "HAS_RUST", False)
    parser = pgSQLParser(definition=None, conditions=dict(conditions), query=query)
    await parser.set_options()
    return await parser.build_query()


async def test_typed_array_scalar(monkeypatch):
    sql = await render({"filter": {"tags": "vip"}, "cond_definition": {"tags": "array"}}, monkeypatch)
    assert "'vip'::character varying = ANY(tags)" in sql


# FILL IN: array list (<@); numrange / int4range / tstzrange / daterange;
#          date list → BETWEEN and 'd!' → NOT BETWEEN;
#          key that IS a template placeholder (query "... WHERE c >= {since} {and_cond}") still substituted;
#          flat (non-filter) declared key keeps placeholder routing;
#          flat {"tags": "vip"} typed array → no exception (needs TASK-860 or the ValueError catch)
#          — bounded by spec §4 typed rows; the `tags|` overlap case belongs to TASK-865
```

### FILL IN checklist
- [ ] `_where_element` raw-typed branch — bounded by spec §2 B.4
- [ ] remaining tests — bounded by spec §4

---

## Acceptance Criteria

- [ ] `pytest tests/test_dialect_typed_filters.py -v` passes after an in-place rebuild
- [ ] `pytest tests/test_dialect_filter_preprocessing.py -q` still passes
- [ ] No request raises `ValueError` from typed placeholder validation
- [ ] Filter keys that are template placeholders and flat keys behave exactly as before

## Validation Commands

- `pytest tests/test_dialect_typed_filters.py -q`
- `pytest tests/e2e/test_qs_dry_run.py -q`
