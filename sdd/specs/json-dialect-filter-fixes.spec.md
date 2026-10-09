---
type: feature
base_branch: dev
projects: [parsers, rust-parsers]
tags: [filter, between, jsonb, cond-definition, json-dialect]
---

# Feature Specification: JSON Dialect Filter Pre-processing Fixes

**Feature ID**: FEAT-165
**Date**: 2026-10-09
**Author**: Jesus Lara
**Status**: draft
**Target version**: 5.3.0

---

## 1. Motivation & Business Requirements

### Problem Statement

While verifying `docs/QUERYSOURCE_DIALECT.md` against a fresh build of
`pgSQLParser` (2026-10-09, `dev` @ `83136229`), three documented parts of
the QuerySource JSON dialect turned out to be broken. All three are caused by
the **request pre-processing layer** (`AbstractParser.set_conditions` /
`_where_element` in `querysource/parsers/abstract.pyx`), which runs *before*
the dialect builders. The builders (Cython and Rust) already handle these
shapes correctly when given unprocessed input, which is why the existing
builder-level tests pass: they assign `parser.filter` directly and never go
through `set_options()`.

1. **`BETWEEN` string filters render invalid SQL.**
   `{"amount": "BETWEEN 100 AND 500"}` →
   `(amount 'BETWEEN 100 AND 500')`;
   `{"created_at": "BETWEEN '2025-01-01' AND '2025-12-31'"}` →
   `(created_at 'BETWEEN ''2025-01-01'' AND ''2025-12-31')`.
   `_where_element` passes the value to `is_valid()`, which quotes the whole
   string before the builder's `BETWEEN` branch sees it.

2. **Dict filter values are reduced to their last item.**
   `_where_element` does `op, v = next(reversed(value.items()))` and returns
   `{op: is_valid(v)}`. Consequences:
   - implicit JSONB containment `{"attributes": {"status": "active", "tier": "gold"}}`
     renders `attributes @> '{"tier":"''gold''"}'::jsonb` — the `status` key
     is lost and SQL quotes are embedded in the JSON string;
   - a multi-operator comparison `{"x": {">": 1, "<": 9}}` renders only
     `x < '9'` (and the Rust builder, if it ever received both, would keep the
     *first* one — `entries[0]` — while Cython keeps the *last*).
   Explicit JSONB operators (`{"@>": {...}}`) work only because their operand
   is a dict/list, which `is_valid()` returns unchanged.

3. **Typed-column filters are unreachable.** `pgsql.pyx` (and
   `rust/src/pgsql_parser.rs`) render columns typed in `cond_definition` as
   `array` (`= ANY`, `<@`, `&&` with the `|` suffix), `numrange`,
   `int4range`/`int8range`, `tsrange`/`tstzrange`, `daterange`, and a
   two-item list on a `date`/`datetime` column as `BETWEEN`. None of this is
   reachable: `set_conditions` routes **every** key declared in
   `cond_definition` to the placeholder dict (`self._conditions`), so
   - when the template has no `{name}` placeholder the condition silently
     disappears;
   - an `array`-typed key with a string value raises an uncaught
     `ValueError` (`is_valid(T='array')` → `is_array('vip')` is `True` for a
     `str` → `to_unquoted` → `int('vip')`) and the request fails;
   - builders look the type up with the *suffixed* key
     (`self.cond_definition[key]` with `key == "tags|"`), so the `|` overlap
     suffix could never select the `array` branch anyway;
   - the Rust `array` / `tsrange` / `daterange` branches emit the value
     unquoted (`vip::character varying = ANY(tags)`), relying on a
     pre-quoted value, while the Cython branches quote a raw value.

Secondary: an unregistered `@variable` (`{"visit_date": "@yesterday"}` when
`yesterday` is not in `QUERYSOURCE_VARIABLES`) makes the filter **silently
disappear**: `_get_function_replacement` returns `None` and the key is moved
to `self._conditions`.

### Goals
- `BETWEEN` / `NOT BETWEEN` string filters render valid, safely quoted SQL on
  PostgreSQL, generic SQL, SQL Server and BigQuery (Cython and Rust paths).
- Dict filter values reach the builders intact: implicit JSONB containment
  keeps every key with correctly serialised JSON values; comparison dicts keep
  every operator and render them AND-ed (PostgreSQL, generic SQL, BigQuery).
- Typed-column filters declared in `cond_definition` are reachable from an
  explicit `filter` / `where_cond` entry, render correctly on both PostgreSQL
  paths, and never crash the request.
- An unregistered `@variable` is reported as a `ParserError` (HTTP 400)
  instead of silently dropping the condition.
- Rust and Cython builders render byte-identical SQL for every case above.
- Regression tests exercise the **full** pre-processing pipeline
  (`set_options()` → `build_query()`), not only the builders.
- `docs/QUERYSOURCE_DIALECT.md` known-issue notes are replaced by the fixed
  behaviour.

### Non-Goals (explicitly out of scope)
- Rendering numeric comparison values bare (`qty > 0` instead of `qty > '0'`):
  today's quoted-literal behaviour is intentional and documented.
- Comparison-dict support for SQL Server (`sqlserver.pyx` / `mssql_parser.rs`
  have no comparison-dict branch today), CQL and SOQL (keep their current
  single-operator behaviour).
- Non-SQL parsers (Mongo, Elastic, Arango, Rethink, Influx): they only receive
  the normalised `BETWEEN` string; their own `BETWEEN` regex handling is not
  changed.
- Changing the routing of **flat** (non-`filter`) request keys: a flat key
  declared in `cond_definition` stays a placeholder value, exactly as today.
- JSONB array aggregation (`jsonb_unnest`, FEAT-153) — its path filters are
  handled by `pgSQLParser._where_element` and are not affected.
- qsurl (`querysource/qsurl/`) — it builds filters that already go through
  this layer and simply benefits from the fixes.

---

## 2. Architectural Design

### Overview

All three bugs are fixed where they originate — the pre-processing layer —
with a small, pure-Python helper module holding the shared grammar, plus
targeted builder changes to keep Cython and Rust in lockstep.

**A. Shared helper (`querysource/parsers/filter_values.py`).** A pure Python
module (same style as `partial_matching.py`) that owns:
- `base_key(key)` — strip key suffix characters (`|!~#@:`) to get the column
  name used for `cond_definition` lookups;
- `parse_between(key, value)` — recognise `[NOT] BETWEEN <lo> AND <hi>`
  (case-insensitive, whole string) and return a normalised clause with each
  bound rendered as a safe SQL literal, or raise `ParserError` for a malformed
  clause; returns `None` for any string that is not a `BETWEEN` clause;
- `is_comparison_dict(value)` — every key is a comparison token;
- `TYPED_FILTER_FORMATS` — the `cond_definition` types the PostgreSQL builder
  renders specially (`array`, `numrange`, `int4range`, `int8range`,
  `tsrange`, `tstzrange`, `daterange`).

**B. Pre-processing (`AbstractParser`).**
1. `_where_element`, string values: when `parse_between()` recognises the
   value, return `(base_key(key), clause.render())` — the canonical string
   `"BETWEEN <lo> AND <hi>"` or `"NOT BETWEEN <lo> AND <hi>"` (a `!` key
   suffix, or a leading `NOT`, selects `NOT BETWEEN`). The value never reaches
   `is_valid()`. If both `col` and `col!` produce a key collision, raise
   `ParserError`.
   Bound grammar: a number (`-?\d+(\.\d+)?`) renders bare; a single-quoted
   literal (with `''` escapes) is unescaped and re-quoted; a bare token
   (`[A-Za-z0-9_:./+-]+`) is resolved through the date keywords
   (`is_udf` → `to_udf`, e.g. `FDOM`) and otherwise quoted. Anything else —
   including `;`, `--`, `/*`, extra keywords — raises
   `ParserError("invalid BETWEEN clause for '<key>'")`.
2. `_where_element`, dict values (after the existing partial-match check):
   - comparison dict → a **new** dict with *every* operator kept and each
     operand passed through `is_valid(noquote=self.string_literal)`;
   - any other dict (JSONB payloads, JSONB operators, `ILIKE` from qsurl,
     mixed dicts) → a shallow copy, **unmodified** (no `is_valid()`); the
     builders own validation and quoting for these shapes.
   The caller's dict is never mutated (existing invariant).
3. `set_conditions`: keys that come from the explicit filter (`self.filter`,
   i.e. `filter` / `where_cond` / the slug's `filtering`) whose
   `base_key()` is declared in `cond_definition` **and** has no `{name}`
   placeholder in `self.query_raw` stay **filters** (recorded in a new
   `self._typed_filter_keys: set[str]`). Every other key keeps today's
   routing — in particular a filter key that *is* a template placeholder is
   still consumed as a placeholder (backwards compatible).
4. `_where_element` for a key in `_typed_filter_keys` whose type is in
   `TYPED_FILTER_FORMATS`, or whose type is `date`/`datetime` and whose value
   is a two-item list: return the value **raw** (no `is_valid()` quoting) —
   the builders quote and validate typed values themselves.
5. `_process_element`: a `ValueError` from `is_valid()` is handled like the
   existing `TypeError` (warning + legacy fallback), so a bad typed
   placeholder value never crashes the request.
6. `_get_function_replacement`: an `@name` that is not registered in
   `QS_VARIABLES` raises `ParserError("unknown variable '@<name>'")`.

**C. Validators.** The `array` / `json` entries of `type_validators` use a new
`is_collection()` check that rejects `str`/`bytes`, so `is_valid(T='array')`
never calls `to_unquoted` on a plain string. `is_array()` itself is unchanged
(it has other callers).

**D. Cython builders (`pgsql.pyx`, `sql.pyx`, `sqlserver.pyx`, `bigquery.pyx`).**
- `cond_definition` lookups use `base_key(key)` (so `tags|` resolves to the
  `array` type).
- The string `BETWEEN` branch is selected only for the canonical form:
  `str_value.startswith(("BETWEEN ", "NOT BETWEEN "))` instead of the
  substring test `"BETWEEN" in str_value` (a quoted string that merely
  contains the word now renders as plain equality). The injection-marker
  check stays as defence in depth.
- Comparison dicts (PostgreSQL, generic SQL, BigQuery): when every key is a
  comparison token, render each `col <op> <literal>` and join them with
  ` AND `, wrapped in parentheses when there is more than one; a single
  operator renders exactly as today.

**E. Rust builders (parity).** Same three changes in
`pgsql_parser.rs`, `sql_parser.rs`, `bigquery_parser.rs`, `mssql_parser.rs`
and `filter_common.rs` (anchored `BETWEEN` detection; base-key
`format_hint` lookup; AND-ed comparison dicts for pgsql/sql/bigquery), plus:
the PostgreSQL `array` (string), `tsrange`/`tstzrange` and `daterange`
branches quote the raw value with `pg_literal()`, matching Cython.

### Component Diagram
```
request JSON
   │
   ▼
AbstractParser.set_options()
   ├─ _extract_options()                     (unchanged)
   └─ _parser_conditions()
        ├─ set_conditions()  ── routes keys ──► self._conditions (placeholders)
        │      │   └─ NEW: explicit typed filter keys stay filters (_typed_filter_keys)
        │      └─ _process_element()  (ValueError handled)
        └─ set_where() ── _where_element()
               ├─ NEW: parse_between()  ─────────┐
               ├─ NEW: comparison dict kept whole │  filter_values.py
               ├─ NEW: other dicts passed raw     │  (base_key, parse_between,
               ├─ NEW: typed values passed raw ───┘   is_comparison_dict, TYPED_FILTER_FORMATS)
               └─ NEW: unknown @var → ParserError
   │
   ▼
builder.filter_conditions()  ── Rust fast path ──► *_filter_conditions (rust/src)
   └─ Cython fallback (pgsql / sql / sqlserver / bigquery .pyx)
        ├─ base_key() type lookup
        ├─ anchored BETWEEN
        └─ AND-ed comparison dicts
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `AbstractParser._where_element` | modifies | BETWEEN normalisation, whole-dict handling, raw typed values |
| `AbstractParser.set_conditions` | modifies | explicit typed filter keys stay filters |
| `AbstractParser._process_element` | modifies | `ValueError` handled like `TypeError` |
| `AbstractParser._get_function_replacement` | modifies | unknown `@name` → `ParserError` |
| `pgSQLParser._where_element` | unchanged | still handles `[].` path keys first, then `super()` |
| `pgSQLParser._filter_conditions_cy`, `SQLParser.filter_conditions`, `msSQLParser`/`sqlserver` builder, `bigQueryParser` builder | modifies | base-key lookup, anchored BETWEEN, AND-ed comparison dicts |
| `_qs_parsers` Rust extension | modifies | parity + quoting of typed PG values |
| `type_validators` (`validators.pyx`) | modifies | `array`/`json` reject `str` |
| `ParserError` | uses | HTTP 400 for malformed BETWEEN, `col`/`col!` collision, unknown `@name` |

### Data Models
```python
# querysource/parsers/filter_values.py
@dataclass(frozen=True)
class BetweenClause:
    """A validated, normalised [NOT] BETWEEN clause."""
    negated: bool
    low: str    # safe SQL literal: bare number or single-quoted string
    high: str   # safe SQL literal

    def render(self) -> str:
        """Return ``"BETWEEN <low> AND <high>"`` or ``"NOT BETWEEN ..."``."""
```

### New Public Interfaces
```python
# querysource/parsers/filter_values.py
KEY_SUFFIX_CHARS: str = '|!~#@:'
COMPARISON_OPERATORS: tuple[str, ...] = ('>=', '<=', '<>', '!=', '<', '>')
TYPED_FILTER_FORMATS: frozenset[str]  # array, numrange, int4range, int8range, tsrange, tstzrange, daterange

def base_key(key: str) -> str: ...
def is_comparison_dict(value: dict) -> bool: ...
def parse_between(key: str, value: str) -> BetweenClause | None: ...
```

---

## 3. Module Breakdown

#### Delegation-eligible modules

| Module | Eligible? | Decided patterns / exact contracts | Why not (if no) |
|---|---|---|---|
| M1: filter value grammar | yes | signatures in §3 M1; bound grammar in §2 B.1; errors are `ParserError` with the exact messages in §7 | — |
| M2: pre-processing fixes | no | — | ordering of the new branches inside `_where_element` / `set_conditions` interacts with partial matching, `@` functions, `is_parseable` and the pgsql `[].` override; needs judgement |
| M3: array validator | yes | `is_collection(value) -> bool` = `isinstance(value, (list, tuple, dict, ndarray))`; swap it into the `array`/`json` rows only | — |
| M4: Cython builder parity | yes | three mechanical edits per builder listed in §2 D and §6 Edit Sites | — |
| M5: Rust builder parity | no | — | Rust value model (`FilterValue`, `entries`) differs per parser; requires reading each builder and keeping existing Rust unit tests green |
| M6: documentation | yes | replace the known-issue blocks named in §7 with the fixed behaviour | — |

### Module 1: Filter value grammar
- **Path**: `querysource/parsers/filter_values.py` (new)
- **Responsibility**: shared, dependency-free grammar for key suffixes,
  comparison dicts, typed filter formats and `[NOT] BETWEEN` parsing /
  normalisation.
- **Depends on**: `querysource.exceptions.ParserError`,
  `querysource.types.validators.is_udf`, `querysource.utils.functions.to_udf`
- **Interface Skeleton**:
  ```python
  # querysource/parsers/filter_values.py  (new)
  from dataclasses import dataclass
  from ..exceptions import ParserError          # verified: querysource/exceptions.py:86
  from ..types.validators import is_udf          # verified: querysource/types/validators.pyx:206
  from ..utils.functions import to_udf           # verified: querysource/utils/functions.pyx:821

  KEY_SUFFIX_CHARS: str = '|!~#@:'               # mirrors pgsql.pyx JSONB_KEY_SUFFIXES and abstract key check
  COMPARISON_OPERATORS: tuple[str, ...] = ('>=', '<=', '<>', '!=', '<', '>')  # mirrors sql.pyx COMPARISON_TOKENS
  TYPED_FILTER_FORMATS: frozenset[str] = frozenset(
      {'array', 'numrange', 'int4range', 'int8range', 'tsrange', 'tstzrange', 'daterange'}
  )

  @dataclass(frozen=True)
  class BetweenClause:
      """A validated, normalised ``[NOT] BETWEEN`` clause."""
      negated: bool
      low: str
      high: str

      def render(self) -> str:
          """Return ``'BETWEEN <low> AND <high>'`` (``'NOT BETWEEN ...'`` when negated)."""

  def base_key(key: str) -> str:
      """Return ``key`` without trailing suffix characters (``KEY_SUFFIX_CHARS``)."""

  def is_comparison_dict(value: dict) -> bool:
      """True when ``value`` is non-empty and every key is in ``COMPARISON_OPERATORS``."""

  def parse_between(key: str, value: str) -> BetweenClause | None:
      """Parse ``[NOT] BETWEEN <lo> AND <hi>`` (case-insensitive, whole string).

      A ``!`` suffix on ``key`` also negates. Returns None when ``value`` does not
      start with ``BETWEEN``/``NOT BETWEEN`` (after stripping whitespace).
      Raises:
          ParserError: the value starts like a BETWEEN clause but a bound is not a
              number, a single-quoted literal or a bare token (see §2 B.1).
      """
  ```

### Module 2: Pre-processing fixes
- **Path**: `querysource/parsers/abstract.pyx` (+ `abstract.pxd` for the new attribute)
- **Responsibility**: fix bugs 1, 2, 3 and the `@variable` drop at their
  origin, per §2 B.
- **Depends on**: Module 1
- **Interface Skeleton**:
  ```python
  # modifies querysource/parsers/abstract.pyx
  from .filter_values import (                          # new import, after line 20 (verified: abstract.pyx:20)
      TYPED_FILTER_FORMATS, base_key, is_comparison_dict, parse_between,
  )
  from ..exceptions import EmptySentence, ParserError   # extends verified: abstract.pyx:17

  cdef class AbstractParser:
      # new attribute (declared in abstract.pxd, initialised in set_attributes()):
      #   cdef public set _typed_filter_keys            # set_attributes verified: abstract.pyx:69

      cdef object _get_function_replacement(self, object function, str key, object val):  # verified: abstract.pyx:442
          """Call the registered ``@function``.

          Raises:
              ParserError: ``function`` is not registered in ``QS_VARIABLES``.
          """

      async def _process_element(self, name: str, value: object, connection: object):  # verified: abstract.pyx:506
          """Unchanged contract; a ``ValueError`` from ``is_valid`` is handled like ``TypeError``."""

      async def set_conditions(self, conditions: dict, connection: object) -> dict:  # verified: abstract.pyx:544
          """Route keys to placeholders or filters.

          Explicit-filter keys whose base key is declared in ``cond_definition`` but has
          no ``{name}`` placeholder in ``query_raw`` stay filters and are recorded in
          ``self._typed_filter_keys``. All other routing is unchanged.
          """

      async def _where_element(self, key, value, connection):  # verified: abstract.pyx:567
          """Normalise one filter entry.

          BETWEEN strings → ``(base_key(key), clause.render())``; comparison dicts keep
          every operator; other dicts are copied unmodified; typed filter values are
          returned raw. Raises ParserError for malformed BETWEEN and unknown ``@name``.
          """

      async def set_where(self, _filter: dict, connection: object) -> object:
          """Unchanged signature; raises ParserError when two keys normalise to the
          same column (``col`` + ``col!`` BETWEEN collision)."""
  ```

### Module 3: Array validator
- **Path**: `querysource/types/validators.pyx`
- **Responsibility**: `is_valid(T='array'|'json')` never accepts a plain string.
- **Depends on**: —
- **Interface Skeleton**:
  ```python
  # modifies querysource/types/validators.pyx
  cpdef bool_t is_collection(object value):  # new, next to is_array (verified: validators.pyx:259)
      """True for list, tuple, dict or numpy ndarray; False for str/bytes."""

  # type_validators rows (verified: validators.pyx:460-461):
  #   "array": [ is_collection, to_unquoted ],
  #   "json":  [ is_collection, to_unquoted ],
  ```

### Module 4: Cython builder parity
- **Path**: `querysource/parsers/pgsql.pyx`, `querysource/parsers/sql.pyx`,
  `querysource/parsers/sqlserver.pyx`, `querysource/parsers/bigquery.pyx`
- **Responsibility**: base-key type lookup, anchored `BETWEEN`, AND-ed
  comparison dicts (pgsql / sql / bigquery), per §2 D.
- **Depends on**: Module 1 (`base_key`, `is_comparison_dict`)
- **Interface Skeleton**:
  ```python
  # no new public methods; behaviour changes inside:
  #   pgSQLParser._filter_conditions_cy(self, sql)   verified: pgsql.pyx:345
  #   SQLParser.filter_conditions(self, sql)         verified: sql.pyx:141
  #   sqlserver filter_conditions(self, sql)         verified: sqlserver.pyx:91
  #   bigquery filter_conditions(self, sql)          verified: bigquery.pyx:152
  from .filter_values import base_key, is_comparison_dict  # new import in each builder
  ```

### Module 5: Rust builder parity
- **Path**: `rust/src/pgsql_parser.rs`, `rust/src/sql_parser.rs`,
  `rust/src/bigquery_parser.rs`, `rust/src/mssql_parser.rs`,
  `rust/src/filter_common.rs`
- **Responsibility**: byte-identical output with Module 4 for every new test
  case; PostgreSQL typed values quoted with `pg_literal()`.
- **Depends on**: — (no import edge; semantics defined by §2 D/E and the
  shared test corpus)
- **Interface Skeleton**:
  ```rust
  // no new #[pyfunction]; existing entry points keep their signatures:
  pub fn pgsql_filter_conditions(sql: &str, filter_dict: &Bound<'_, PyDict>,
                                 cond_definition: &Bound<'_, PyDict>) -> PyResult<String>;  // verified: pgsql_parser.rs:743
  // new private helper shared by the parsers (filter_common.rs):
  pub(crate) fn base_key(key: &str) -> &str;            // strip '|' '!' '~' '#' '@' ':'
  pub(crate) fn is_canonical_between(value: &str) -> bool;  // starts_with "BETWEEN " | "NOT BETWEEN "
  ```

### Module 6: Documentation
- **Path**: `docs/QUERYSOURCE_DIALECT.md`
- **Responsibility**: replace the known-issue notes (5.3 multi-operator note,
  5.4 BETWEEN, 7.1 implicit containment, 8 typed columns, 13.3 unknown
  `@name`) with the fixed behaviour and verified rendered SQL.
- **Depends on**: Modules 2, 4, 5 (documents their output)

---

## 4. Test Specification

All new tests go in `tests/test_dialect_filter_preprocessing.py` and render
through the **full** pipeline: build the parser with a request `conditions`
dict, `await parser.set_options()`, `await parser.build_query()`, and assert
on the SQL. Each rendering test is parametrised over `["rust", "cython"]`
(Cython forced by patching the module's `HAS_RUST` to `False`; Rust skipped
when the staged `_qs_parsers` is stale, same pattern as
`tests/test_pgsql_partial_matching.py:15-33`).

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_parse_between_numbers` | M1 | `BETWEEN 100 AND 500` → `BETWEEN 100 AND 500` |
| `test_parse_between_quoted_and_bare_dates` | M1 | `'2025-01-01'` and `2025-01-01` bounds both → `'2025-01-01'` |
| `test_parse_between_keyword_bound` | M1 | `BETWEEN FDOM AND LDOM` → resolved, quoted dates |
| `test_parse_between_negated` | M1 | leading `NOT` and `!` key suffix → `negated=True` |
| `test_parse_between_rejects` | M1 | `;`, `--`, `/*`, `UNION SELECT`, a third `AND`, empty bound → `ParserError` |
| `test_parse_between_not_a_clause` | M1 | `"betweenness"`, `"x BETWEEN"` → `None` |
| `test_base_key` / `test_is_comparison_dict` | M1 | suffix stripping; mixed / empty dicts → False |
| `test_is_valid_array_rejects_str` | M3 | `is_valid('t', 'vip', 'array')` does not raise and does not call `to_unquoted` |

### Integration Tests (full pipeline, PostgreSQL unless noted)
| Test | Description |
|---|---|
| `test_between_numeric` | `{"amount": "BETWEEN 100 AND 500"}` → `(amount BETWEEN 100 AND 500)` |
| `test_between_dates` | quoted bounds → `(created_at BETWEEN '2025-01-01' AND '2025-12-31')` |
| `test_not_between_suffix` | `{"amount!": "BETWEEN 1 AND 5"}` → `(amount NOT BETWEEN 1 AND 5)` |
| `test_between_malformed_400` | injection markers → `ParserError` |
| `test_between_word_inside_string` | `{"note": "IN BETWEEN"}` → `note='IN BETWEEN'` |
| `test_between_sql_dialects` | generic SQL, SQL Server, BigQuery render the same predicate |
| `test_implicit_containment_all_keys` | `{"attributes": {"status": "active", "tier": "gold"}}` → `attributes @> '{"status":"active","tier":"gold"}'::jsonb` (escaped-brace form) |
| `test_explicit_containment_unchanged` | `{"@>": {...}}` output byte-identical to before |
| `test_jsonb_operator_dicts_unchanged` | `@>|`, `@!`, `@$`, `->>`, `->` outputs unchanged |
| `test_multi_operator_comparison` | `{"x": {">": 1, "<": 9}}` → `(x > '1' AND x < '9')` on pg, sql, bigquery |
| `test_single_operator_comparison_unchanged` | `{"qty": {">": 0}}` → `qty > '0'` |
| `test_partial_match_unchanged` | FEAT-180 corpus still passes through the full pipeline |
| `test_typed_array_scalar` | `cond_definition {"tags": "array"}`, filter `{"tags": "vip"}`, template without `{tags}` → `'vip'::character varying = ANY(tags)` |
| `test_typed_array_list_and_overlap` | `["a","b"]` → `<@`; `tags|` → `&&` |
| `test_typed_ranges` | `numrange`, `int4range`, `tstzrange`, `daterange` renderings |
| `test_typed_date_list_between` | `cond_definition {"d": "date"}`, `{"d": ["2025-01-01","2025-02-01"]}` → `d BETWEEN '2025-01-01' AND '2025-02-01'`; `d!` → `NOT BETWEEN` |
| `test_typed_key_that_is_placeholder` | key with `{name}` in template is still substituted as a placeholder (backwards compatibility) |
| `test_flat_typed_key_unchanged` | a flat (non-`filter`) declared key keeps placeholder routing |
| `test_array_placeholder_bad_value_no_crash` | flat `{"tags": "vip"}` typed `array` → no exception |
| `test_unknown_variable_400` | `{"filter": {"d": "@nope"}}` → `ParserError("unknown variable '@nope'")` |
| `test_registered_variable_unchanged` | a registered `@name` still resolves |
| `test_caller_dict_not_mutated` | the request `filter` dict is identical after `set_options()` |

### Test Data / Fixtures
```python
SQL = "SELECT * FROM public.t {where_cond}"

async def render(conditions: dict, *, parser_cls=pgSQLParser, query: str = SQL,
                 path: str = "cython", monkeypatch=None) -> str:
    """Full pipeline: set_options() + build_query(); path selects Rust or Cython."""
```

---

## 5. Acceptance Criteria

- [ ] `tests/test_dialect_filter_preprocessing.py` passes on both the Rust and
      the Cython path (`pytest tests/test_dialect_filter_preprocessing.py -v`).
- [ ] Every example marked as a known issue in `docs/QUERYSOURCE_DIALECT.md`
      (5.3, 5.4, 7.1, 8, 13.3) renders the documented SQL through the full
      pipeline, and the doc is updated accordingly.
- [ ] Rust and Cython render byte-identical SQL for every integration case.
- [ ] Malformed `BETWEEN`, a `col`/`col!` collision and an unknown `@name`
      raise `ParserError` (HTTP 400); none of them crash with another
      exception type.
- [ ] No request can make the parser raise `ValueError` from
      `is_valid(T='array')`.
- [ ] Existing suites pass unchanged: `tests/test_pgsql_partial_matching.py`,
      `tests/test_sql_partial_matching.py`, `tests/test_mssql_partial_matching.py`,
      `tests/test_bigquery_partial_matching.py`,
      `tests/test_partial_matching_conformance.py`,
      `tests/test_sql_parser_combinations.py`, `tests/test_jsonb_unnest_plan.py`,
      `tests/test_pgsql_jsonb_unnest_parity.py`, `tests/e2e/test_qs_dry_run.py`.
- [ ] Rust unit tests pass (`cargo test --no-default-features` in `rust/`),
      apart from the 4 failures already present on `dev`.
- [ ] `ruff check` is clean for the new/changed Python files.
- [ ] No behaviour change for: flat declared keys, filter keys that are
      template placeholders, explicit JSONB operators, partial matching,
      single-operator comparisons.

---

## 6. Codebase Contract

> Verified against `dev` @ `83136229` on 2026-10-09.

### Verified Imports
```python
from querysource.exceptions import ParserError              # verified: querysource/exceptions.py:86 (default_code = 400)
from querysource.types.validators import Entity, is_valid, field_components  # verified: abstract.pyx:19
from querysource.parsers.partial_matching import validate_partial_match_dict  # verified: partial_matching.py:135
from querysource.parsers import QS_FILTERS, QS_VARIABLES    # verified: querysource/parsers/__init__.py:6,9
from querysource.models import QueryObject                 # verified: tests/test_pgsql_partial_matching.py:9
from querysource.parsers.pgsql import pgSQLParser           # verified: tests/test_pgsql_partial_matching.py:11
```

### Existing Class Signatures
```python
# querysource/parsers/abstract.pyx
cdef class AbstractParser:
    cdef void set_attributes(self)                                   # line 69
    async def set_options(self)                                      # merges def_conditions < flat < nested 'conditions'
    cdef object _get_function_replacement(self, object function, str key, object val)  # line 442 — returns None if unregistered
    cdef object _merge_conditions_and_filters(self, dict conditions) # line 475 — {**conditions, **self.filter}
    cdef bint _handle_keys(self, str key, object val, dict _filter)  # line 482 — also reduces dicts with next(reversed(...)) (placeholder path)
    async def _process_element(self, name, value, connection)       # line 506 — catches TypeError only (line 524)
    async def set_conditions(self, conditions: dict, connection) -> dict  # line 544
    async def _where_element(self, key, value, connection)           # line 567 — dict reduction at line 581; '@' at ~597; prefix passthrough line 600
    async def set_where(self, _filter: dict, connection) -> object

# querysource/parsers/pgsql.pyx
COMPARISON_TOKENS = ('>=', '<=', '<>', '!=', '<', '>',)
JSONB_OPERATORS = ('@>', '<@', '@>|', '@!', '@$', '->', '->>',)
JSONB_KEY_SUFFIXES = '|!~#@:'
cdef tuple jsonb_condition(str col, dict value)    # implicit containment when no operator keys
cdef class pgSQLParser(SQLParser):
    async def filter_conditions(self, sql)          # line 333 — Rust fast path, Cython fallback
    async def _filter_conditions_cy(self, sql)      # line 345 — type lookup line 375; dict reduction line 405; date-list BETWEEN lines 448-454; string BETWEEN line 490
    async def _where_element(self, key, value, connection) -> tuple  # line 597 — '[].' path keys, else super()

# querysource/types/validators.pyx
cpdef bool_t is_udf(object value)                   # line 206
cpdef bool_t is_array(object value)                 # line 259 — isinstance(value, (list, dict, Sequence, ndarray)) → True for str
cdef dict type_validators                           # line 458; "array" line 460, "json" line 461
cpdef object is_valid(object key, object value, str T=None, bint noquote=False)  # line 620

# querysource/utils/functions.pyx
def to_udf(str value, *args, **kwargs)              # line 821 — calls the zero-arg function named `value.lower()`
```

```rust
// rust/src/pgsql_parser.rs
fn pg_safe_identifier_key(key: &str) -> Option<String>        // line 25
fn pg_literal(value: &str) -> String                           // line ~51
fn pg_validate_between(value: &str) -> bool                    // line ~66
fn jsonb_condition(key: &str, dict: &Bound<'_, PyDict>) -> JsonbOutcome  // handles whole dicts correctly
fn process_dict_value(key: &str, entries: &[(String, FilterValue)], _format: Option<&str>) -> Option<String>  // line 478 — uses entries[0] (line 487)
pub fn pgsql_filter_conditions(...)                            // line 743 — format_hint via cond_definition.get_item(&key) (line 754, suffixed key)
// typed branches: array line ~657 (value unquoted), tsrange ~681, daterange ~686 (unquoted)

// rust/src/sql_parser.rs — dict comparison takes dict_val.iter().next() (line 281); string BETWEEN line 359
// rust/src/bigquery_parser.rs — comparison entries[0] (line 230); string BETWEEN line 315; format lookup line 363
// rust/src/mssql_parser.rs — format lookup line 85; no comparison-dict branch
// rust/src/filter_common.rs — string BETWEEN line 175
// rust/src/validators.rs — bq_quote_string (line 214) strips a pre-quote if present: raw input is safe
```

### Integration Points
| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `parse_between` | `AbstractParser._where_element` | call before `is_parseable` / `is_valid` | `abstract.pyx:567` |
| `is_comparison_dict` | `_where_element` dict branch | replaces `next(reversed(...))` | `abstract.pyx:581` |
| `_typed_filter_keys` | `set_conditions` → `_where_element` | instance attribute | `abstract.pyx:544` |
| `base_key` | Cython builders' type lookup | `self.cond_definition.get(base_key(key))` | `pgsql.pyx:375`, `sql.pyx:177`, `sqlserver.pyx:136` |
| `is_collection` | `type_validators` | table rows | `validators.pyx:460-461` |

### Does NOT Exist (Anti-Hallucination)
- ~~`querysource.parsers.filter_values`~~ — created by M1.
- ~~`AbstractParser._typed_filter_keys`~~ — created by M2.
- ~~`querysource.types.validators.is_collection`~~ — created by M3.
- ~~a `get_config_var` definition in `querysource/`~~ — `is_valid` calls it inside a `try`, it is not defined in this package; do not rely on it.
- ~~comparison-dict support in `sqlserver.pyx` / `mssql_parser.rs`~~ — dicts that are not partial matches fall through; out of scope.
- ~~`HAVING` for non-plan queries~~ — only the JSONB-unnest plan renders `having`.
- ~~an end-to-end (set_options) test of filters in the current suite~~ — existing filter tests set `parser.filter` directly.

### Edit Sites (Blueprint Anchors)

Verified against: `83136229`

| File | Action | Verbatim anchor line | Verified at | Occurrences |
|---|---|---|---|---|
| `querysource/parsers/filter_values.py` | CREATE | — | — | — |
| `tests/test_dialect_filter_preprocessing.py` | CREATE | — | — | — |
| `querysource/parsers/abstract.pyx` | MODIFY | `from .partial_matching import validate_partial_match_dict` | `abstract.pyx:20` | 1 |
| `querysource/parsers/abstract.pyx` | MODIFY | `cdef object _get_function_replacement(self, object function, str key, object val):` | `abstract.pyx:442` | 1 |
| `querysource/parsers/abstract.pyx` | MODIFY | `                except TypeError as exc:` | `abstract.pyx:524` | 1 |
| `querysource/parsers/abstract.pyx` | MODIFY | `async def set_conditions(self, conditions: dict, connection: object) -> dict:` | `abstract.pyx:544` | 1 |
| `querysource/parsers/abstract.pyx` | MODIFY | `            op, v = next(reversed(value.items()))` | `abstract.pyx:581` | 1 |
| `querysource/parsers/abstract.pyx` | MODIFY | `            elif prefix in ('|', '!', '&', '>', '<'):` | `abstract.pyx:600` | 1 |
| `querysource/parsers/abstract.pxd` | MODIFY | `cdef bint _distinct` | `abstract.pxd:37` | 1 |
| `querysource/types/validators.pyx` | MODIFY | `    "array": [ is_array, to_unquoted ],` | `validators.pyx:460` | 1 |
| `querysource/types/validators.pyx` | MODIFY | `    "json": [ is_array, to_unquoted ],` | `validators.pyx:461` | 1 |
| `querysource/types/validators.pyx` | MODIFY | `cpdef bool_t is_array(object value):` | `validators.pyx:259` | 1 |
| `querysource/parsers/pgsql.pyx` | MODIFY | `                    _format = self.cond_definition[key]` | `pgsql.pyx:375` | 1 |
| `querysource/parsers/pgsql.pyx` | MODIFY | `                    op, v = next(reversed(value.items()))  # never popitem(): the filter dict is the caller's` | `pgsql.pyx:405` | 1 |
| `querysource/parsers/pgsql.pyx` | MODIFY | `                    elif "BETWEEN" in str_value:` | `pgsql.pyx:490` | 1 |
| `querysource/parsers/sql.pyx` | MODIFY | `                    _format = self.cond_definition[key]` | `sql.pyx:177` | 1 |
| `querysource/parsers/sql.pyx` | MODIFY | `                    op, v = next(reversed(value.items()))  # never popitem(): the filter dict is the caller's` | `sql.pyx:197` | 1 |
| `querysource/parsers/sql.pyx` | MODIFY | `                    if "BETWEEN" in str_value:` | `sql.pyx:227` | 1 |
| `querysource/parsers/sqlserver.pyx` | MODIFY | `                    _format = self.cond_definition[key]` | `sqlserver.pyx:136` | 1 |
| `querysource/parsers/sqlserver.pyx` | MODIFY | `                    if "BETWEEN" in str_value:` | `sqlserver.pyx:170` | 1 |
| `querysource/parsers/bigquery.pyx` | MODIFY | `                    op, v = next(reversed(value.items()))  # never popitem(): the filter dict is the caller's` | `bigquery.pyx:245` | 1 |
| `querysource/parsers/bigquery.pyx` | MODIFY | `                    if "BETWEEN" in str_value:` | `bigquery.pyx:285` | 1 |
| `rust/src/pgsql_parser.rs` | MODIFY | `    let (op, v) = &entries[0];` | `pgsql_parser.rs:487` | 1 |
| `rust/src/pgsql_parser.rs` | MODIFY | `                .get_item(&key)` | `pgsql_parser.rs:754` | 1 |
| `rust/src/pgsql_parser.rs` | MODIFY | `    if value.contains("BETWEEN") {` | `pgsql_parser.rs:629` | 1 |
| `rust/src/pgsql_parser.rs` | MODIFY | `        Some("array") => {` (typed branches through `Some("daterange")`) | `pgsql_parser.rs:656-689` | 1 (unverified count — check before use) |
| `rust/src/sql_parser.rs` | MODIFY | `            if let Some((op_obj, v_obj)) = dict_val.iter().next() {` | `sql_parser.rs:281` | 1 |
| `rust/src/sql_parser.rs` | MODIFY | `    if value.contains("BETWEEN") {` | `sql_parser.rs:359` | 1 |
| `rust/src/sql_parser.rs` | MODIFY | `            .get_item(&key)?` | `sql_parser.rs:252` | 1 (unverified count — check before use) |
| `rust/src/bigquery_parser.rs` | MODIFY | `    let (op, v) = &entries[0];` | `bigquery_parser.rs:230` | 1 |
| `rust/src/bigquery_parser.rs` | MODIFY | `    if value.contains("BETWEEN") {` | `bigquery_parser.rs:315` | 1 |
| `rust/src/bigquery_parser.rs` | MODIFY | `                .get_item(&key)` | `bigquery_parser.rs:363` | 1 (unverified count — check before use) |
| `rust/src/mssql_parser.rs` | MODIFY | `            .get_item(&key)` | `mssql_parser.rs:85` | 1 (unverified count — check before use) |
| `rust/src/filter_common.rs` | MODIFY | `    if value.contains("BETWEEN") {` | `filter_common.rs:175` | 1 |
| `docs/QUERYSOURCE_DIALECT.md` | MODIFY | `### 5.4 \`BETWEEN\` (known issue)` | `QUERYSOURCE_DIALECT.md` §5.4 | 1 |

---

## 7. Implementation Notes & Constraints

### Patterns to Follow
- `querysource/parsers/partial_matching.py` (FEAT-180) is the model for M1:
  pure Python, validated once in the pre-processing layer, errors as
  `ParserError` with stable messages, builders re-validate defensively.
- Never mutate the caller's filter dict (the `next(reversed(...))` comments
  exist because linked dashboards re-send the same dict).
- Error messages (stable, asserted by tests):
  - `invalid BETWEEN clause for '<key>'`
  - `conflicting filters for '<column>'` (`col` and `col!` normalise to the same key)
  - `unknown variable '@<name>'`
- Rust/Cython parity tests follow `tests/test_pgsql_partial_matching.py`
  (`PATHS` parametrisation with a stale-extension skip).
- Rebuild before testing: `python setup.py build_ext --inplace` for `.pyx`
  changes; `make build-rust && make stage-rust` for Rust changes (Python
  loads the source-tree `_qs_parsers` `.so`).
- Logging through `self.logger`; no `print` (note: `is_valid` has a legacy
  `print` — do not extend it).

### Known Risks / Gotchas
- **Silent drop → HTTP 400.** Malformed `BETWEEN` clauses and unknown
  `@name` values were silently dropped; they now fail the request. A client
  that relied on the silent drop will see 400s. Mitigation: clear error
  messages; release note; §8 Q1.
- **Typed explicit-filter routing.** A dashboard that sends a declared,
  non-placeholder key inside `filter` today gets *no* condition; after the fix
  it gets a real `WHERE` condition. This is the documented intent, but it is a
  behaviour change. Flat keys are deliberately not changed to keep the blast
  radius small.
- **Raw dict operands.** Non-comparison dicts now reach the builders without
  `is_valid()`. The PostgreSQL JSONB builder, the `PG_TEXT_OPERATORS` branch
  (strips a pre-quote only if present) and BigQuery's `bq_quote_string`
  (same) are safe with raw input; CQL/SOQL builders only act on comparison
  tokens, which are still validated. Verify each builder's handling in the
  task, do not assume.
- **Ordering inside `_where_element`.** The BETWEEN check must run before
  `is_parseable()` and before `field_components()` prefix handling; the
  partial-match check must stay first for dicts; the `@` check must keep
  priority over BETWEEN only when the value starts with `@`.
- **`_handle_keys` (placeholder path)** also reduces dicts with
  `next(reversed(...))`. It only affects dict-valued *placeholders* and is
  left unchanged; do not "fix" it in passing.
- **Rust staleness.** Tests silently run Cython only when the staged
  `_qs_parsers` is stale; the AC requires both paths, so CI/QA must run
  `make stage-rust` first.
- **`make clean` wipes `.venv` `.so` files** (`find . -name "*.so" -delete`):
  never run `make build` as part of this feature's validation.

### External Dependencies
| Package | Version | Reason |
|---|---|---|
| — | — | no new dependencies |

---

## Worktree Strategy

- **Isolation**: one feature worktree for FEAT-165; the `sdd-coder` engine
  gives each task its own sub-worktree inside it.
- **Module dependency graph**:
  - M2 → M1 (`abstract.pyx` imports `parse_between`, `base_key`,
    `is_comparison_dict`, `TYPED_FILTER_FORMATS` from `filter_values.py`).
  - M4 → M1 (builders import `base_key`, `is_comparison_dict`).
  - M6 → M2, M4, M5 (documents their rendered output).
  - M3 and M5 have no import edge to other modules and can run concurrently
    with M1. The integration test file is written with M2 (it needs the full
    pipeline) and extended by M4/M5 for dialect parity.
- **Shared files**: none — each file is modified by exactly one module
  (`tests/test_dialect_filter_preprocessing.py` is created by M2; M4/M5 tasks
  that add cases to it must be serialized after M2).
- **Exclusive resources**: Cython extension rebuild (M2, M3, M4 —
  `build_ext --inplace`) and Rust rebuild + stage (M5 —
  `make build-rust && make stage-rust`). These tasks are `parallel: false`.
- **Cross-feature dependencies**: none. Builds on FEAT-180 (partial matching)
  and FEAT-153 (JSONB unnest), both merged.

---

## 8. Open Questions

- [ ] Q1: Is any production slug or dashboard known to send unregistered
      `@name` values or malformed `BETWEEN` clauses (and therefore relying on
      the silent drop)? If so, should the 400 be gated behind a config flag
      for one release? — *Owner: Jesus Lara*
- [ ] Q2: Should flat (non-`filter`) declared keys without a template
      placeholder also become typed filters in a later feature, or stay
      placeholder-only permanently? — *Owner: Jesus Lara*
- [x] Q3: Error type for invalid input in this layer — *Decided in spec*:
      `ParserError` (HTTP 400), following the FEAT-180 precedent.

---

## 9. Design Research Cross-Check

> Model: — · Status: skipped (no accepted exploration document — spec scaffolded
> from verified bug notes; §3b precondition not met)

| # | Suggestion (kind) | Disposition | Reason | Landed in |
|---|---|---|---|---|

Summary: **0** confirmed · **0** rejected · **0** escalated.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-10-09 | Jesus Lara | Initial draft from the dialect-doc verification findings |
