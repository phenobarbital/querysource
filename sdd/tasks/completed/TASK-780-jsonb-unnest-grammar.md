# TASK-780: JSONB-unnest grammar, config validation and plan detection (Cython)

**Feature**: FEAT-153 — Group & Aggregate by JSONB Array Elements (PostgreSQL)
**Spec**: `sdd/specs/group-aggregation-jsonb-columns.spec.md`
**Status**: done
**Priority**: high
**Estimated effort**: M (2-4h)
**Depends-on**: none
**Assigned-to**: unassigned

---

## Context

Spec §3 Module 1 (first third). Creates the new Cython module
`querysource/parsers/jsonb_unnest.pyx` with the **grammar** (spec §2 Data Models),
the **slug config validator** (`attributes.jsonb_unnest`), the cheap
**`is_plan_candidate`** detector, and the literal helper. TASK-782 adds rendering and
`unnest_plan`/`unnest_wrap`; TASK-783 adds element filters. TASK-785 ports all of this
to Rust and MUST reproduce the exact error messages fixed here.

---

## Scope

- Create `querysource/parsers/jsonb_unnest.pyx` with: constants, `_pg_literal`,
  `Ref` / `Expr` / `Item` value classes, `parse_ref`, `parse_expr`,
  `parse_select_item`, `parse_order_item`, `validate_config`, `is_plan_candidate`.
- Register the extension in `setup.py`.
- Unit tests `tests/test_jsonb_unnest_grammar.py`.

**NOT in scope**: rendering SQL (TASK-782), filters (TASK-783), parser wiring (TASK-784), Rust (TASK-785).

---

## Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `querysource/parsers/jsonb_unnest.pyx` | CREATE | Grammar, config validation, detection |
| `setup.py` | MODIFY | New `Extension('querysource.parsers.jsonb_unnest')` |
| `tests/test_jsonb_unnest_grammar.py` | CREATE | Grammar/config/detection unit tests |

---

## Codebase Contract (Anti-Hallucination)

### Verified Imports
```python
# none from querysource — this module is dependency-free on purpose (stdlib `re` only).
import re
```

### Existing Signatures to Use
```python
# querysource/parsers/pgsql.pyx:37 — semantics to DUPLICATE (it is cdef, not importable)
cdef str pg_literal(str value):
    cdef str escaped = value.replace("'", "''")
    if '{' in escaped or '}' in escaped or '\\' in escaped:
        escaped = escaped.replace('\\', '\\\\').replace('{', '\\x7b').replace('}', '\\x7d')
        return f"E'{escaped}'"
    return f"'{escaped}'"

# setup.py:63-69 (after commit e0c4fb3 — note extra_link_args)
    Extension(
        name='querysource.parsers.pgsql',
        sources=['querysource/parsers/pgsql.pyx'],
        extra_compile_args=COMPILE_ARGS,
        extra_link_args=LINK_ARGS,
        language="c"
    ),
```

### Does NOT Exist
- ~~`from querysource.parsers.pgsql import pg_literal`~~ — `cdef`, not importable.
- ~~`querysource.parsers.jsonb_unnest`~~ before this task.
- ~~`pydantic` models for the plan~~ — plan objects are plain classes / dicts (Rust parity).

---

## Complexity Contract

```json
{
  "schema_version": 1,
  "targets": [
    {"path": "querysource/parsers/jsonb_unnest.pyx", "action": "CREATE"},
    {"path": "setup.py", "action": "MODIFY"},
    {"path": "tests/test_jsonb_unnest_grammar.py", "action": "CREATE"}
  ],
  "contract_symbols": []
}
```

---

## Implementation Notes

### Grammar (spec §2, with one clarification)
```
ref    := ident | ident "[]" "." key { "." key } [ "::" cast ]      # also  ident "::" cast
expr   := "count(*)" | "count(" ["distinct" WS] ref ")"
        | ("min"|"max"|"sum"|"avg") "(" (ref | bucket) ")" | bucket | ref
bucket := ("year"|"quarter"|"month"|"week"|"day") "(" ref ")"
select := expr [ WS "as" WS ident ]
order  := (expr | ident) [ WS ("asc"|"desc") ] [ WS "nulls" WS ("first"|"last") ]
ident  := [A-Za-z_][A-Za-z0-9_]{0,62}
key    := [A-Za-z0-9_-]{1,128}
```
**Clarification vs spec:** keys do NOT allow spaces (the spec's `key` class listed a
space). A space would make `"<expr> as <alias>"` ambiguous; keys with spaces are out of
scope for v1 — record this in the Completion Note as a deviation.
Keywords/function names are case-insensitive; whitespace inside parentheses and around
`::` is tolerated.

### Exact error messages (Rust TASK-785 copies these byte-for-byte)
| Situation | `ValueError` message |
|---|---|
| bad reference | `jsonb_unnest: invalid reference '<text>'` |
| unknown cast | `jsonb_unnest: unknown cast '<cast>'` |
| bad expression | `jsonb_unnest: invalid expression '<text>'` |
| bad select item | `jsonb_unnest: invalid select item '<text>'` |
| bad order item | `jsonb_unnest: invalid order item '<text>'` |
| config not a mapping | `jsonb_unnest: invalid config: must be a mapping` |
| unknown config key | `jsonb_unnest: invalid config: unknown key '<key>'` |
| `columns` not mapping / column name not ident / column options not mapping | `jsonb_unnest: invalid config: columns must map identifiers to mappings` |
| unknown column option | `jsonb_unnest: invalid config: unknown column option '<key>'` |
| bad `empty` | `jsonb_unnest: invalid config: empty must be 'exclude' or 'include'` |
| non-bool `strict`/`safe_cast`/`prefilter` | `jsonb_unnest: invalid config: <name> must be a boolean` |
| `aliases` not mapping of ident → str | `jsonb_unnest: invalid config: aliases must map identifiers to expressions` |
| alias value not a valid `expr` | the `invalid expression` message of that value |

`<text>` is the original input string, unmodified, inside single quotes (no `repr`).

### Key Constraints
- `is_plan_candidate` runs on EVERY pg query: string scans only, never parse, never raise.
- Pure functions, no parser state, no logging (the caller logs).
- No new dependency; stdlib `re` only.

---

## Implementation Blueprint

### Steps (in order)
1. Create `jsonb_unnest.pyx` from the blocks below — *why*: the grammar is the contract every later task builds on.
2. Add the `setup.py` Extension — *why*: without it the module is never compiled.
3. Write `tests/test_jsonb_unnest_grammar.py` — *why*: fixes the messages Rust must match.
4. `make build-inplace`, then run the validation command — *why*: Cython changes are invisible until rebuilt.

### `querysource/parsers/jsonb_unnest.pyx` (CREATE) — part 1: constants, literal, value classes
```python
# cython: language_level=3, embedsignature=True
# Copyright (C) 2018-present Jesus Lara
#
# file: jsonb_unnest.pyx
"""JSONB array unnest planner for pgSQLParser (FEAT-153).

Cython fallback of the ``_qs_parsers`` Rust fast path (``pgsql_unnest_plan`` /
``pgsql_unnest_wrap``). Both implementations must produce identical output and
identical ``ValueError`` messages.
"""
import re

ALLOWED_CASTS = (
    'text', 'int', 'integer', 'bigint', 'numeric', 'float',
    'date', 'timestamp', 'timestamptz', 'boolean',
)
AGGREGATES = ('count', 'min', 'max', 'sum', 'avg')
BUCKETS = ('year', 'quarter', 'month', 'week', 'day')
ARRAY_ALIAS = '_qs_e0'
SOURCE_ALIAS = '_qs_src'
CONFIG_KEYS = ('columns', 'aliases', 'strict', 'safe_cast')
COLUMN_KEYS = ('empty', 'safe_cast', 'prefilter')
EMPTY_POLICIES = ('exclude', 'include')

_IDENT = r'[A-Za-z_][A-Za-z0-9_]{0,62}'
_KEY = r'[A-Za-z0-9_-]{1,128}'
IDENT_RE = re.compile(rf'^{_IDENT}$')
REF_RE = re.compile(
    rf'^(?P<col>{_IDENT})(?:\[\]\.(?P<keys>{_KEY}(?:\.{_KEY})*))?(?:\s*::\s*(?P<cast>[A-Za-z]+))?$'
)


cdef str _pg_literal(str value):
    """Quote ``value`` as a PostgreSQL literal (same semantics as pgsql.pyx ``pg_literal``)."""
    cdef str escaped = value.replace("'", "''")
    if '{' in escaped or '}' in escaped or '\\' in escaped:
        escaped = escaped.replace('\\', '\\\\').replace('{', '\\x7b').replace('}', '\\x7d')
        return f"E'{escaped}'"
    return f"'{escaped}'"


def pg_literal(value: str) -> str:
    """Python-visible wrapper of ``_pg_literal`` (used by tests and sibling tasks)."""
    return _pg_literal(value)


class Ref:
    """A row column or an array-element path: ``col`` or ``col[].k1.k2`` with optional cast."""

    __slots__ = ('column', 'keys', 'cast')

    def __init__(self, column: str, keys: tuple, cast: str | None) -> None:
        self.column = column
        self.keys = keys
        self.cast = cast

    @property
    def is_path(self) -> bool:
        """True when the reference points inside a JSONB array element."""
        return bool(self.keys)


class Expr:
    """A parsed expression: kind is 'ref', 'count_star', 'agg' or 'bucket'."""

    __slots__ = ('kind', 'func', 'distinct', 'arg')

    def __init__(self, kind: str, func: str | None = None, distinct: bool = False, arg=None) -> None:
        self.kind = kind
        self.func = func
        self.distinct = distinct
        self.arg = arg  # Ref for 'ref'/'bucket'; Ref or Expr(bucket) for 'agg'; None for 'count_star'

    @property
    def is_aggregate(self) -> bool:
        """True for count(*) and agg(...) expressions."""
        return self.kind in ('count_star', 'agg')


class Item:
    """A select/order entry: an expression or a bare name, plus alias / direction / nulls."""

    __slots__ = ('expr', 'name', 'alias', 'direction', 'nulls', 'text')

    def __init__(self, text: str, expr=None, name=None, alias=None, direction=None, nulls=None) -> None:
        self.text = text
        self.expr = expr
        self.name = name
        self.alias = alias
        self.direction = direction
        self.nulls = nulls
```
**Why**: plain `class` objects (not `cdef class`) keep the module importable/testable from
Python and trivially mirrored in Rust structs. `pg_literal` is duplicated because the
pgsql.pyx one is `cdef`.

### `querysource/parsers/jsonb_unnest.pyx` — part 2: parsers
```python
def parse_ref(text: str) -> Ref:
    """Parse ``col``, ``col::cast`` or ``col[].k1.k2[::cast]``.

    Raises:
        ValueError: ``jsonb_unnest: invalid reference '<text>'`` or ``unknown cast``.
    """
    match = REF_RE.match(text.strip())
    if match is None:
        raise ValueError(f"jsonb_unnest: invalid reference '{text}'")
    cast = match.group('cast')
    if cast is not None:
        cast = cast.lower()
        if cast not in ALLOWED_CASTS:
            raise ValueError(f"jsonb_unnest: unknown cast '{cast}'")
    keys = tuple(match.group('keys').split('.')) if match.group('keys') else ()
    return Ref(match.group('col'), keys, cast)


def parse_expr(text: str) -> Expr:
    """Parse an expression per the grammar (count(*), agg(ref|bucket), bucket(ref), ref).

    Raises:
        ValueError: ``jsonb_unnest: invalid expression '<text>'`` (or a nested reference error).
    """
    # FILL IN: implement with an outer regex `^(?P<func>[A-Za-z]+)\s*\(\s*(?P<body>.*)\s*\)$`:
    #   - no match -> parse_ref(text) wrapped as Expr('ref', arg=ref)
    #   - func.lower() == 'count' and body == '*' -> Expr('count_star', func='count')
    #   - func in AGGREGATES: optional leading 'distinct' + WS ONLY for count; body is a bucket
    #     (min/max/sum/avg only) or a ref -> Expr('agg', func, distinct, arg)
    #   - func in BUCKETS: body must be a ref -> Expr('bucket', func, arg=ref)
    #   - anything else (unknown func, nested aggregate, distinct on non-count, empty body)
    #     -> ValueError(f"jsonb_unnest: invalid expression '{text}'")
    #   bounded by: spec §2 grammar; messages table above; keep a reference error from
    #   parse_ref when the only problem is the reference (e.g. unknown cast).
    raise NotImplementedError


def parse_select_item(text: str) -> Item:
    """Parse ``<expr> [as <ident>]`` (``as`` case-insensitive).

    Raises:
        ValueError: ``jsonb_unnest: invalid select item '<text>'`` for a bad alias;
            expression errors propagate unchanged.
    """
    # FILL IN: split on the LAST case-insensitive r'\s+as\s+' outside parentheses;
    #   alias must match IDENT_RE else invalid select item; bounded by grammar `select`.
    raise NotImplementedError


def parse_order_item(text: str) -> Item:
    """Parse ``(<expr> | <ident>) [asc|desc] [nulls first|last]``.

    A bare identifier is returned as ``Item(name=...)`` with ``expr`` also parsed as a
    ref, because the caller decides whether it names a select alias (TASK-782).

    Raises:
        ValueError: ``jsonb_unnest: invalid order item '<text>'``.
    """
    # FILL IN: strip trailing `nulls first|last` then `asc|desc` tokens (case-insensitive),
    #   uppercase them into direction/nulls; the remainder is parsed with parse_expr; any
    #   leftover token -> invalid order item; bounded by grammar `order`.
    raise NotImplementedError
```
**Why**: the FILL INs are pure parsing logic bounded by the grammar/message table; signatures
and messages are fixed because TASK-782/785 depend on them.

### `querysource/parsers/jsonb_unnest.pyx` — part 3: config + detection
```python
def validate_config(config) -> dict:
    """Validate and normalise ``attributes['jsonb_unnest']``.

    Returns:
        ``{'columns': {col: {'empty': str, 'safe_cast': bool|None, 'prefilter': bool}},
        'aliases': {name: str}, 'strict': bool, 'safe_cast': bool}``. ``columns`` is ``None``
        when not declared (any grammar-valid array column allowed). A column's ``safe_cast``
        stays ``None`` when unset (inherit the top-level value).

    Raises:
        ValueError: messages from the table in Implementation Notes.
    """
    # FILL IN: None / {} -> defaults (columns None, aliases {}, strict False, safe_cast False);
    #   validate every key/type per the messages table; each alias value must parse with
    #   parse_expr (let its ValueError propagate); bools must be real bool (not int).
    raise NotImplementedError


def is_plan_candidate(fields, grouping, ordering, filter, having, config) -> bool:
    """Cheap activation check; never parses, never raises.

    True when any string in fields/grouping/ordering or any filter key contains ``'[].'``,
    when ``having`` is truthy, or when ``config`` is a dict whose ``aliases`` dict contains
    a bare name used in fields/grouping, the first token of an ordering entry, a filter key
    (trailing ``'!'`` stripped) or a having key.
    """
    # FILL IN: implement exactly the rule above; non-str entries are skipped; non-dict filter
    #   / config are treated as empty — bounded by spec §2 Overview activation + Key Constraints.
    raise NotImplementedError
```
**Why**: `columns=None` vs `{}` distinguishes "not declared" from "declared empty" (the latter
allows no array at all). `safe_cast` per column stays `None` so TASK-782 can apply
"column value, else top-level".

### `setup.py` (MODIFY)
```python
# occurrences: 1 (verified: grep -c "name='querysource.parsers.pgsql'," setup.py)
# AFTER — insert a sibling block after the `),` that closes the Extension whose
# anchor line is `        name='querysource.parsers.pgsql',` (verified: setup.py:64)
    Extension(
        name='querysource.parsers.jsonb_unnest',
        sources=['querysource/parsers/jsonb_unnest.pyx'],
        extra_compile_args=COMPILE_ARGS,
        extra_link_args=LINK_ARGS,
        language="c"
    ),
```
**Why**: same flags as its sibling (including the strip link args from commit `e0c4fb3`).

### `tests/test_jsonb_unnest_grammar.py` (CREATE)
```python
"""FEAT-153 grammar, config validation and plan detection (Cython module)."""
from __future__ import annotations

import pytest

from querysource.parsers import jsonb_unnest as ju


@pytest.mark.parametrize("text,column,keys,cast", [
    ("student_uid", "student_uid", (), None),
    ("graduation_details[].course", "graduation_details", ("course",), None),
    ("graduation_details[].meta.level", "graduation_details", ("meta", "level"), None),
    ("graduation_details[].course_date::date", "graduation_details", ("course_date",), "date"),
    ("graduation_details[].course_date :: DATE", "graduation_details", ("course_date",), "date"),
    ("created_at::timestamptz", "created_at", (), "timestamptz"),
])
def test_parse_ref(text, column, keys, cast):
    ref = ju.parse_ref(text)
    assert (ref.column, ref.keys, ref.cast) == (column, keys, cast)


@pytest.mark.parametrize("text", [
    "a;drop", "a[].", "a[]", "a[].k k", "a[].k'", "a[]..k", "1col", "a[].b[].c", "a.b", "",
])
def test_parse_ref_rejects(text):
    with pytest.raises(ValueError, match=r"^jsonb_unnest: invalid reference '"):
        ju.parse_ref(text)


def test_unknown_cast():
    with pytest.raises(ValueError, match=r"^jsonb_unnest: unknown cast 'regclass'$"):
        ju.parse_ref("a[].k::regclass")

# FILL IN: parse_expr cases (count(*), COUNT(DISTINCT x), sum(a[].n), min(year(a[].d::date)),
#   month(a[].d), rejects: count(distinct *), sum(distinct x), max(count(*)), foo(x), year(sum(x)));
#   parse_select_item (alias, case-insensitive AS, bad alias 'as 1x'); parse_order_item
#   ('graduates DESC', 'a[].k asc nulls last', rejects 'x DESC DESC'); validate_config every
#   message row; is_plan_candidate true/false matrix incl. 'tags::text[]' -> False,
#   {'attrs': {'@>': ...}} -> False, having {} -> False, alias name in ordering 'course DESC' -> True.
#   bounded by: Implementation Notes messages table (exact strings).
```

### FILL IN checklist
- [ ] `parse_expr` — grammar `expr`/`bucket`; messages table
- [ ] `parse_select_item` — last ` as ` split outside parens; ident alias
- [ ] `parse_order_item` — direction/nulls tokens; uppercase
- [ ] `validate_config` — defaults + every message row
- [ ] `is_plan_candidate` — activation rule; never raises
- [ ] tests — the listed cases, exact messages

---

## Acceptance Criteria

- [ ] `from querysource.parsers.jsonb_unnest import parse_expr, validate_config, is_plan_candidate` works after `make build-inplace`.
- [ ] Every row of the messages table is asserted with an exact-match regex.
- [ ] `is_plan_candidate` never raises (fuzz a few non-str / None inputs).
- [ ] `ruff check tests/test_jsonb_unnest_grammar.py` is clean.

---

## Validation Commands

- `pytest tests/test_jsonb_unnest_grammar.py -q`

---

## Test Specification

See the test block above (to be completed per its FILL IN).

---

## Agent Instructions

1. Read spec §2 (Data Models) and §7.
2. Implement from the blueprint; complete every `FILL IN`.
3. Rebuild: `make build-inplace` (exclusive resource — no other build may run concurrently).
4. Move this file to `sdd/tasks/completed/`, set the index status to `"done"`, fill the Completion Note.

---

## Completion Note

**Completed by**: sdd-worker (sequential fallback, Claude)
**Date**: 2026-09-28T22:30:27+00:00
**Notes**: Implemented grammar, validate_config, is_plan_candidate; 59 tests. Deviation (per task): keys exclude spaces.

**Deviations from spec**: keys exclude spaces (see Implementation Notes).
