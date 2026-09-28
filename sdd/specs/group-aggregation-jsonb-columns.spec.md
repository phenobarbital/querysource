---
type: feature
base_branch: dev
projects: [parsers, rust-parsers, querysource]
tags: [postgres, jsonb, aggregation, group-by, unnest]
---

# Feature Specification: Group & Aggregate by JSONB Array Elements (PostgreSQL)

**Feature ID**: FEAT-153
**Date**: 2026-09-28
**Author**: Jesus Lara (with Claude)
**Status**: approved
**Target version**: 5.2.0
**Brainstorm**: `sdd/proposals/group-aggregation-jsonb-columns.brainstorm.md` (accepted, Option A)

---

## 1. Motivation & Business Requirements

### Problem Statement

Many PostgreSQL tables served through QuerySource store one-to-many detail as a
JSONB **array of objects** inside the row. A student row, for example, carries
`graduation_details`:
`[{"course": "Pilates Studio", "category": "Comprehensive", "course_date": "2025-09-19", "diploma_number": "POL15058"}]`.

Callers can already **filter** rows on such arrays (`@>`, `<@`, `@>|`, `->`,
`->>`, commit `8936386`). They cannot **group and aggregate by the values
inside the array**. A question like "count graduates per course (per category,
per year), for licensee Asia" needs four things:

- unnesting the array,
- projecting element keys,
- filtering per element,
- `GROUP BY` / aggregates on element keys.

Today every such question needs a hand-written raw-SQL slug. Row-level `@>`
filtering alone also gives misleading counts. Filter
`course = 'Pilates Studio'`, group by course, and the student's *other*
diplomas are counted too, because the row matched.

### Goals

- **G1:** Request-time **path strings** (`<column>[].<key>[.<key>…][::cast]`)
  are usable in `fields`, `group_by` / `grouping`, `ordering` / `order_by` and
  `filter` keys of any **parser-rendered** pg slug.
- **G2:** Aggregates `count(*)`, `count(x)`, `count(distinct x)`, `min`, `max`,
  `sum`, `avg`, plus time-bucket helpers `year` / `quarter` / `month` /
  `week` / `day`.
- **G3:** Element-level filters are explicit: they use path-keyed filter
  entries. Existing row filters, including `@>` / `@>|`, keep their meaning.
- **G4:** Aggregate filtering is available through a new `having` condition.
- **G5:** A slug-side declaration, `QueryModel.attributes.jsonb_unnest`,
  provides:
  - aliases,
  - an allowlist of array columns,
  - a `strict` mode,
  - the empty-array policy,
  - `safe_cast`,
  - an opt-in containment pre-filter.
- **G6:** Results are flat rows, so every output writer works unchanged.
- **G7:** **Byte-identical SQL** for every query that does not use the
  feature, on both the Rust and Cython paths.
- **G8:** Rust fast path with Cython fallback, and identical output from
  both.

### Non-Goals (explicitly out of scope)

- **`is_raw=True` slugs.** They bypass the parser
  (`querysource/providers/sql.py:152`), and path tokens or `having` have no
  effect on them, the same as every other parser condition today. *Resolved
  at spec time.*
- **Non-PostgreSQL dialects** (MySQL, SQL Server, BigQuery, …).
- **Unnesting more than one distinct array column in one query.** Rejected
  in v1.
- **Nested arrays** (`a[].b[].c`).
- **SQL functions or expressions as element-filter values.** Values are
  always literals.
- **qsurl (FEAT-152) exposure** of grouping or aggregation. That is a later
  feature.
- **Pandas-side explode/group, Option C.** It was rejected in the brainstorm
  because it pulls every row just to count. See
  `sdd/proposals/group-aggregation-jsonb-columns.brainstorm.md`.
- **A jsonpath renderer, Option D.** Postgres 12+ is the floor, so it stays
  possible later.

---

## 2. Architectural Design

### Overview

A **JSONB-unnest planner** is added to `pgSQLParser.build_query`. It is
**activated** only when at least one of these holds:

- a path token (a string containing `[].`) appears in `fields`, `grouping`,
  `ordering` or a `filter` key,
- the request sends a non-empty `having`,
- a name in `fields`, `grouping`, `ordering` or `having` matches an alias
  declared in `attributes.jsonb_unnest.aliases`.

Otherwise the planner is not called, and `build_query` runs exactly as today
(G7).

When the planner is active ("plan mode"), it works in these steps:

1. **Validate and plan.** Every entry of `fields`, `grouping`, `ordering`,
   and every `having` key, is parsed with a strict grammar (§2 Data Models).
   Filter entries are split into two groups:
   - path-keyed entries become **element filters**,
   - all other entries stay **row filters**, which are rendered by the
     unchanged `filter_conditions`.

   Any violation raises `ValueError` from the planner, which
   `pgSQLParser` re-raises as `ParserError` (HTTP 400).
2. **Render the inner query.** This is the existing pipeline (row filters,
   QS filters, `filtering_options`, `filter_conditions`) with `fields`,
   `grouping` and `ordering` cleared. Leftover structural placeholders are
   then blanked: `{grouping}`, `{group_by}`, `{order_by}`, `{ordering}`,
   `{offset}`, `{limit}`.
3. **Wrap.** The result is
   `SELECT <select list> FROM (<inner>) AS _qs_src <lateral> [WHERE <element filters>]`,
   where `<lateral>` is one of:
   - `CROSS JOIN LATERAL jsonb_array_elements(CASE jsonb_typeof(_qs_src.<col>) WHEN 'array' THEN _qs_src.<col> ELSE '[]'::jsonb END) AS _qs_e0(elem)` (default, `empty: "exclude"`),
   - the same with `LEFT JOIN LATERAL … ON true` (`empty: "include"`).

   With no array column (plan mode triggered only by `having` or aliases of
   row columns), there is no lateral join.
4. **Outer clauses.** `self.grouping` and `self.ordering` are replaced with
   the rendered expressions. The existing `group_by()` is called; the
   planner's `HAVING` is appended; then the existing `order_by()` and
   `limiting()` run. Because the inner query sits at parenthesis depth 1,
   the depth-aware `GROUP BY` logic never touches it.
5. **Finalize.** The existing `format_map` / `safe_substitute` passes run.
   All planner literals go through `pg_literal`, which escapes braces as
   `\x7b` / `\x7d`.

**Decisions carried from the brainstorm (all resolved):**
- **Declaration.** Both the request and the slug may declare.
- **Syntax.** Path strings.
- **Filter scope.** Explicit per filter.
- **Result shape.** Flat rows.
- **Slug shapes.** Any parser-rendered slug, via the subquery wrap.
- **Empty or NULL arrays.** Excluded by default; `empty: "include"` yields a
  NULL group.
- **Invalid tokens.** A parser error, never dropped silently. Invalid JSONB
  *row* filters keep being dropped, unchanged.
- **HAVING.** In v1.
- **Time buckets.** Helper functions only, no `date_trunc` passthrough.
- **Cast failures.** Fail by default; `safe_cast: true` opts into
  regex-guarded casts.
- **Multiple arrays.** A second distinct array column is rejected.
- **Rust parity.** Rust fast path plus Cython fallback.
- **Non-strict slugs.** The grammar is always enforced. When
  `jsonb_unnest.columns` is declared, only those arrays may be unnested.
- **Postgres version.** 12+.

**Spec-time refinement (deviation from the brainstorm, flagged):** the
brainstorm proposed an *automatic* `@>` row pre-filter for element equality
and IN filters. That rewrite is **not** result-preserving:

- Element comparison is on `->>` text.
- JSON containment is type-sensitive. `"5"` does not contain `5`.

If a key holds numbers in JSON, an automatic pre-filter would drop matching
rows. The pre-filter is therefore **opt-in per array column**
(`columns.<col>.prefilter: true`), which asserts that its keys are JSON
strings. It is emitted only for `=` and `IN` element filters on paths with no
cast. See §8 Q2.

### Component Diagram

```
Request conditions / QueryModel
        │
        ▼
AbstractParser.set_options ── _extract_options ──► _having_sync (NEW: pops `having`)
        │                                           _query_fields_sync / _grouping_sync / _ordering_sync / _query_filter_sync
        ▼
pgSQLParser.build_query
        │
        ├─ plan mode? (cheap detection)
        │     no ─► existing pipeline, unchanged (G7)
        │     yes
        │      ▼
        │   _rs.pgsql_unnest_plan(...)  ──(PyValueError → ParserError; other exc → fallback)──►
        │   jsonb_unnest.unnest_plan(...)   (Cython fallback, querysource/parsers/jsonb_unnest.pyx)
        │      │ UnnestPlan dict
        │      ▼
        │   inner = existing process_fields/filters/filter_conditions (fields/grouping/ordering cleared,
        │           self.filter = plan["row_filter"]) → blank structural placeholders
        │      ▼
        │   _rs.pgsql_unnest_wrap(inner, plan) | jsonb_unnest.unnest_wrap(inner, plan)
        │      ▼
        │   group_by(plan["group_by"]) → + HAVING → order_by(plan["order_by"]) → limiting
        ▼
format_map / safe_substitute → query_parsed
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `pgSQLParser.build_query` (`querysource/parsers/pgsql.pyx:458`) | modifies | plan detection + planner call + wrap + HAVING; untouched path when inactive |
| `SQLParser.group_by` / `order_by` / `limiting` (`querysource/parsers/sql.pyx:250,292,308`) | uses | run on the outer SQL with rendered expressions |
| `pgSQLParser.filter_conditions` (`pgsql.pyx:206`) | uses | renders row filters + opt-in pre-filters on the inner SQL, unchanged |
| `AbstractParser._extract_options` (`querysource/parsers/abstract.pyx:147`) | modifies | adds `_having_sync()` so `having` is not merged into WHERE filters |
| `AbstractParser` declarations (`querysource/parsers/abstract.pxd`) | modifies | `cdef public dict having`, `cdef void _having_sync(self)` |
| `QueryObject` (`querysource/models.py:24`) | extends | `having: Optional[dict]` |
| `QueryModel.attributes` (`querysource/models.py:54`) | uses (data) | `jsonb_unnest` key; no schema migration |
| `sqlProvider._PARSER_CONDITION_KEYS` (`querysource/providers/sql.py:60`) | modifies | add `"having"` so direct-query mode engages the parser |
| `_qs_parsers` Rust module (`rust/src/lib.rs`) | extends | registers `pgsql_unnest_plan`, `pgsql_unnest_wrap` |
| `ParserError` (`querysource/exceptions.py:86`, `default_code = 400`) | uses | validation failures |
| `setup.py` Extension list | modifies | new Cython extension `querysource.parsers.jsonb_unnest` |

### Data Models

**Path/expression grammar** (identical in Rust and Cython; whitespace around
tokens is tolerated, and keywords are case-insensitive):

```
item        := expr [ WS "as" WS ident ]                       # fields entries
group_item  := expr | alias_ref                                # grouping entries
order_item  := (expr | alias_ref) [ WS ("asc"|"desc") ] [ WS "nulls" WS ("first"|"last") ]
expr        := agg | bucket | ref
agg         := "count(*)" | "count(" [ "distinct" WS ] ref ")"
             | ("min"|"max"|"sum"|"avg") "(" (ref | bucket) ")"
bucket      := ("year"|"quarter"|"month"|"week"|"day") "(" ref ")"
ref         := path | ident
path        := ident "[]" "." key { "." key } [ "::" cast ]
ident       := [A-Za-z_][A-Za-z0-9_]{0,62}                     # rendered bare (validated), never quoted
key         := [A-Za-z0-9_\- ]{1,128}  (rendered only as a pg_literal, never as an identifier)
cast        := "text"|"int"|"integer"|"bigint"|"numeric"|"float"|"date"|"timestamp"|"timestamptz"|"boolean"
alias_ref   := ident that is a select alias in this plan or a declared jsonb_unnest alias
```

**Rendering rules:**

- **Path, no cast:** `a[].k` → `(_qs_e0.elem ->> 'k')`.
- **Nested path:** `a[].k1.k2` → `(_qs_e0.elem -> 'k1' ->> 'k2')`.
- **Path with cast:** `a[].k::date` → `((_qs_e0.elem ->> 'k')::date)`.
- **Bucket:** `month(p)` → `(date_trunc('month', <p as date>)::date)`.
  - A path with no cast is implicitly cast to `::date`.
  - A path cast to `timestamp` or `timestamptz` keeps its cast, and the
    result is still `::date`.
- **`sum` / `avg`:** over a path with no cast, an implicit `::numeric` is
  applied.
- **Row columns:** a plain `ident` renders as `_qs_src.<ident>`.
- **Default aliases:**
  - a path uses its last key,
  - a bucket uses `<unit>_<lastkey>`,
  - `count(*)` uses `count`,
  - `agg(ref)` uses `<agg>_<name>`.
- **Alias collisions** raise an error. An explicit `as` alias always wins.
- **`GROUP BY` / `ORDER BY`:**
  - `GROUP BY` uses the full rendered expressions.
  - `ORDER BY` uses select aliases when the item is a select alias;
    otherwise it uses the rendered expression.
- **Plan mode rule:** every `fields` entry must match `item`. Raw SQL
  expressions are rejected in plan mode. They remain allowed, verbatim, only
  outside plan mode, as today.
- **Empty `fields`:** in plan mode, the select list is the group keys
  followed by `count(*) AS count`.

**Element filter entries** (path-keyed `filter` items) accept these forms:

| Key form | Value | Renders |
|---|---|---|
| `a[].k` | scalar | `expr = <lit>` |
| `a[].k!` | scalar | `expr <> <lit>` |
| `a[].k` | list | `expr IN (<lit>, …)` |
| `a[].k!` | list | `expr NOT IN (…)` |
| `a[].k` | `{op: v}`, op ∈ `>=, <=, <>, !=, <, >, =` | `expr op <lit>` |
| `a[].k` | `null` / `'null'` | `expr IS NULL` |
| `a[].k!` | `null` / `'null'` | `expr IS NOT NULL` |

Values arrive already processed by `set_where` / `is_valid`, so strings may be
single-quoted. The planner strips one outer quote pair, undoes `''`
doubling, then re-quotes with `pg_literal`. This mirrors the
`PG_TEXT_OPERATORS` branch at `pgsql.pyx:282-285`. Integers, floats and
booleans are rendered bare after type checking. Anything else raises
`ValueError`.

**`having` condition:** `{"<alias or agg expr>": scalar | {op: v, …}}`.

- Keys must be a select alias in the plan, or an `agg` expression that
  matches the grammar.
- `HAVING` renders the underlying expression, never the alias, because
  Postgres does not resolve aliases in `HAVING`.
- Several operators in one dict are AND-ed.
- Values:
  - numbers render bare,
  - strings go through `pg_literal`,
  - anything else raises.
- `having` with no aggregate in the select list raises.

**Slug declaration** (`QueryModel.attributes["jsonb_unnest"]`, all keys optional):

```python
# Shape (validated by the planner; unknown keys → ValueError)
{
    "columns": {                          # when present: the ONLY array columns requests may unnest
        "<array_col>": {
            "empty": "exclude" | "include",   # default "exclude"
            "safe_cast": bool,                # default: top-level safe_cast
            "prefilter": bool,                # default False — keys are JSON strings, emit @> pre-filter
        },
    },
    "aliases": {"<name>": "<expr per grammar>"},  # usable as bare names in fields/grouping/ordering/having/filter keys
    "strict": bool,       # default False — True: raw path tokens are rejected; only aliases may introduce paths
    "safe_cast": bool,    # default False
}
```

**`safe_cast: true`:** a cast of a path is wrapped as
`CASE WHEN <text> ~ '<regex>' THEN <text>::<cast> END`. The regexes are
brace-free on purpose:

| Cast | Regex |
|---|---|
| `date` | `^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]` |
| `int` / `integer` / `bigint` | `^-?[0-9]+$` |
| `numeric` / `float` | `^-?[0-9]+(\.[0-9]+)?$` |
| `timestamp` / `timestamptz` | the `date` regex as prefix |
| `boolean` | `^(true|false)$` |
| `text` | never guarded |

Known limit: a well-shaped but impossible date (`2025-02-30`) still raises
at query time (§7, §8 Q1).

**`UnnestPlan`** is the contract returned by both planner implementations,
as a plain `dict`:

```python
{
    "select": list[str],        # rendered "expr AS alias" items, in request order
    "group_by": list[str],      # rendered expressions
    "order_by": list[str],      # rendered "expr|alias [ASC|DESC] [NULLS FIRST|LAST]"
    "having": list[str],        # rendered conditions (AND-ed by the caller)
    "element_where": list[str], # rendered element filter conditions (AND-ed)
    "lateral": str,             # rendered join clause, "" when no array column
    "row_filter": dict,         # filter entries left for filter_conditions (+ opt-in pre-filters in @>/@>| dict form)
}
```

### New Public Interfaces

```python
# querysource/parsers/jsonb_unnest.pyx (Cython fallback) — mirrored by _qs_parsers
def unnest_plan(fields: list, grouping: list, ordering: list, filter: dict,
                having: dict, config: dict) -> dict | None: ...
def unnest_wrap(inner_sql: str, plan: dict) -> str: ...

# _qs_parsers (Rust fast path)
_rs.pgsql_unnest_plan(fields, grouping, ordering, filter, having, config) -> dict | None
_rs.pgsql_unnest_wrap(inner_sql, plan) -> str
```

**Request example**, graduates per course and category, Asia, 2025
diplomas, top 10:

```json
{
  "fields": ["graduation_details[].course", "graduation_details[].category",
             "count(distinct student_uid) as graduates"],
  "group_by": ["graduation_details[].course", "graduation_details[].category"],
  "filter": {"licensee": "Asia",
             "graduation_details[].course_date::date": {">=": "2025-01-01"}},
  "having": {"graduates": {">": 5}},
  "ordering": ["graduates DESC"],
  "querylimit": 10
}
```

---

## 3. Module Breakdown

#### Delegation-eligible modules

| Module | Eligible? | Decided patterns / exact contracts | Why not (if no) |
|---|---|---|---|
| M1: Cython planner | yes | grammar, rendering rules, `UnnestPlan` keys, error type (`ValueError`) fixed in §2 | — |
| M2: Rust planner | yes | same contract as M1, registration pattern of `pgsql_filter_conditions`, `PyValueError` for validation | — |
| M3: Parser & provider integration | yes | hook points, dispatch rule (Rust `ValueError` → `ParserError`, no fallback; other exc → Cython), placeholder blanking list fixed in §2/§6 | — |
| M4: Tests (golden SQL, parity, regression, integration) | yes | fixture shapes and assertions fixed in §4 | — |
| M5: Documentation | yes | file path and required sections fixed in §3 M5 | — |

### Module 1: Cython unnest planner (`pg-jsonb-array-unnest`, `pg-jsonb-element-aggregation`, `pg-jsonb-element-filters`, `slug-jsonb-unnest-declaration`)
- **Path**: `querysource/parsers/jsonb_unnest.pyx` (new) + `setup.py` Extension entry
- **Responsibility**:
  - grammar parsing and validation,
  - config validation,
  - alias expansion,
  - strict and columns enforcement,
  - rendering of select, group, order, having, element filters and the
    lateral join,
  - opt-in pre-filters,
  - wrapping.

  Pure functions with no parser state.
- **Depends on**: nothing in this spec. It duplicates the small literal
  helper (`pg_literal` semantics, `pgsql.pyx:37`) as a module-level
  `cdef str _pg_literal(str)`, because `pgsql.pyx`'s helpers are `cdef` and
  not importable.
- **Interface Skeleton**:
  ```python
  # querysource/parsers/jsonb_unnest.pyx  (new)
  # cython: language_level=3, embedsignature=True
  """JSONB array unnest planner for pgSQLParser (FEAT-153) — Cython fallback of _qs_parsers."""

  ALLOWED_CASTS: tuple      # ('text','int','integer','bigint','numeric','float','date','timestamp','timestamptz','boolean')
  AGGREGATES: tuple         # ('count','min','max','sum','avg')
  BUCKETS: tuple            # ('year','quarter','month','week','day')
  ARRAY_ALIAS: str          # '_qs_e0'
  SOURCE_ALIAS: str         # '_qs_src'

  def is_plan_candidate(fields: list, grouping: list, ordering: list,
                        filter: dict, having: dict, config: dict) -> bool:
      """Cheap activation check: any '[].' token, non-empty having, or a declared alias name used.

      Must not parse or raise; used on every pg query, so O(n) string scans only.
      """

  def unnest_plan(fields: list, grouping: list, ordering: list, filter: dict,
                  having: dict, config: dict) -> dict | None:
      """Build the UnnestPlan (§2 Data Models) or return None when not a plan candidate.

      Args:
          fields: parser fields (request or definition).
          grouping: parser grouping.
          ordering: parser ordering.
          filter: parser filter AFTER set_where (values may be pre-quoted).
          having: raw `having` condition ({} when absent).
          config: attributes.get('jsonb_unnest', {}) ({} when absent).

      Returns:
          The plan dict, or None.

      Raises:
          ValueError: invalid grammar/config/cast/function, alias collision,
              >1 distinct array column, column not in `columns`, raw path in
              strict mode, `having` without aggregate, unsupported filter value.
      """

  def unnest_wrap(inner_sql: str, plan: dict) -> str:
      """Return 'SELECT <select> FROM (<inner_sql>) AS _qs_src <lateral> [WHERE <element_where>]'.

      inner_sql is expected to have its structural placeholders already blanked.
      """
  ```

### Module 2: Rust unnest planner (fast path)
- **Path**: `rust/src/pgsql_unnest.rs` (new); `rust/src/lib.rs` gets `mod`
  and registration.
- **Responsibility**: a byte-identical port of M1's `unnest_plan` and
  `unnest_wrap`. Validation failures raise `PyValueError` with the same
  message text as M1. It reuses `pg_literal` semantics (`pgsql_parser.rs:51`
  is private: either make it `pub(crate)` or reimplement it; the task
  decides, with no behaviour change).
- **Depends on**: M1 (contract and golden fixtures only, no code import).
- **Interface Skeleton**:
  ```rust
  // rust/src/pgsql_unnest.rs  (new)
  /// Build the UnnestPlan dict (FEAT-153); mirrors jsonb_unnest.unnest_plan.
  #[pyfunction]
  #[pyo3(signature = (fields, grouping, ordering, filter_dict, having, config))]
  pub fn pgsql_unnest_plan<'py>(
      py: Python<'py>,
      fields: Vec<String>,
      grouping: Vec<String>,
      ordering: Vec<String>,
      filter_dict: &Bound<'py, PyDict>,
      having: &Bound<'py, PyDict>,
      config: &Bound<'py, PyDict>,
  ) -> PyResult<Option<Bound<'py, PyDict>>>;   // Err(PyValueError) on validation failure

  /// Wrap the inner SQL; mirrors jsonb_unnest.unnest_wrap.
  #[pyfunction]
  #[pyo3(signature = (inner_sql, plan))]
  pub fn pgsql_unnest_wrap(inner_sql: &str, plan: &Bound<'_, PyDict>) -> PyResult<String>;

  // rust/src/lib.rs  (modifies: after `mod pgsql_parser;` — verified rust/src/lib.rs:17,
  //                   and after the pgsql_filter_conditions registration — verified rust/src/lib.rs:67)
  mod pgsql_unnest;
  m.add_function(wrap_pyfunction!(pgsql_unnest::pgsql_unnest_plan, m)?)?;
  m.add_function(wrap_pyfunction!(pgsql_unnest::pgsql_unnest_wrap, m)?)?;
  ```

### Module 3: Parser & provider integration
- **Paths**:
  - `querysource/parsers/pgsql.pyx`
  - `querysource/parsers/abstract.pyx`, `querysource/parsers/abstract.pxd`
  - `querysource/models.py`
  - `querysource/providers/sql.py`
- **Responsibility**:
  - extract `having`,
  - detect plan mode,
  - dispatch Rust → Cython,
  - convert errors to `ParserError`,
  - render the inner query with cleared fields, grouping and ordering,
  - blank the placeholders,
  - wrap, then run `group_by` + `HAVING` + `order_by` + `limiting`,
  - reject `add_fields=True` in plan mode (`ValueError` → `ParserError`),
  - register `having` as a parser condition key.
- **Depends on**: M1 (imports `querysource.parsers.jsonb_unnest`). M2 is
  optional at runtime, because dispatch uses `HAS_RUST` plus
  `hasattr(_rs, 'pgsql_unnest_plan')`.
- **Interface Skeleton**:
  ```python
  # querysource/parsers/abstract.pxd  (modifies: after `cdef public list grouping` — verified abstract.pxd:21;
  #                                     after `cdef void _grouping_sync(self)` — verified abstract.pxd:72)
  cdef public dict having
  cdef void _having_sync(self)

  # querysource/parsers/abstract.pyx  (modifies)
  #   set_attributes: add `self.having = {}` next to `self.grouping = []` (verified abstract.pyx:81; anchor occurs twice → use set_attributes context)
  #   _extract_options: call `self._having_sync()` after `self._grouping_sync()` (verified abstract.pyx:155-156)
  cdef void _having_sync(self):
      """Pop the `having` condition (dict) so it is never merged into WHERE filters.

      Non-dict values raise ValueError (surfaces as ParserError from the provider).
      """

  # querysource/parsers/pgsql.pyx  (modifies pgSQLParser.build_query — verified pgsql.pyx:458)
  from .jsonb_unnest import is_plan_candidate, unnest_plan, unnest_wrap   # new import
  from ..exceptions import EmptySentence, ParserError                     # extends pgsql.pyx:14

  STRUCTURAL_PLACEHOLDERS: tuple  # ('{grouping}', '{group_by}', '{order_by}', '{ordering}', '{offset}', '{limit}')

  cdef class pgSQLParser(SQLParser):
      def _unnest_plan(self) -> dict | None:
          """Return the UnnestPlan via _rs.pgsql_unnest_plan (fast path) or jsonb_unnest.unnest_plan.

          Rust ValueError → ParserError (no fallback, same message);
          any other Rust exception → Cython fallback. Cython ValueError → ParserError.
          """
      def _unnest_wrap(self, inner_sql: str, plan: dict) -> str:
          """Blank STRUCTURAL_PLACEHOLDERS in inner_sql, then wrap via Rust or Cython."""
      async def build_query(self, querylimit: int = None, offset: int = None):  # verified pgsql.pyx:458
          """Unchanged when _unnest_plan() is None; otherwise inner → wrap → GROUP BY → HAVING → ORDER BY → LIMIT."""

  # querysource/models.py  (modifies QueryObject — after `group_by: Optional[list]`, verified models.py:33)
  having: Optional[dict]

  # querysource/providers/sql.py  (modifies _PARSER_CONDITION_KEYS — verified sql.py:63)
  #   add "having" to the frozenset line  `"group_by", "grouping", "order_by", "ordering",`
  ```

### Module 4: Tests
- **Paths**:
  - `tests/test_pgsql_jsonb_unnest.py` (new): golden SQL, grammar errors,
    Rust/Cython parity.
  - `tests/test_pgsql_jsonb_unnest_regression.py` (new): byte-identical
    output for queries that don't use the feature.
  - `tests/integration/test_pgsql_jsonb_unnest_live.py` (new): only runs
    when a Postgres DSN is available.
- **Responsibility**: see §4.
- **Depends on**: M1 and M3. The Rust-parametrised cases also depend on M2,
  and are skipped when `_qs_parsers` lacks `pgsql_unnest_plan`.

### Module 5: Documentation
- **Path**: `docs/JSONB_AGGREGATION.md` (new).
- **Responsibility**:
  - the grammar,
  - rendering examples (request JSON → SQL),
  - element vs row filters (with the count pitfall),
  - `having`,
  - buckets,
  - the slug `jsonb_unnest` declaration,
  - `safe_cast` and its limits,
  - `prefilter` and when it's safe,
  - the empty-array policy,
  - errors,
  - non-goals (`is_raw`, other dialects, multiple arrays).
- **Depends on**: M1 (final rendering), M3.

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_plan_candidate_detection` | M1 | `[].` / `having` / alias trigger; `tags::text[]`, `attrs @>` filters, plain fields do **not** trigger |
| `test_path_rendering` | M1 | single key, nested key, cast, implicit `::date` in bucket, implicit `::numeric` in sum/avg |
| `test_aggregates_and_aliases` | M1 | count(*) / count(distinct) / min / max / sum / avg, default aliases, explicit `as`, collision → ValueError |
| `test_buckets` | M1 | year/quarter/month/week/day → `date_trunc('<unit>', …)::date` |
| `test_element_filters` | M1 | scalar, `!`, list IN / NOT IN, comparison dict, null / not null, pre-quoted values un-/re-quoted, SQL-function-looking values stay literals |
| `test_row_filters_untouched` | M1 | non-path filter entries returned verbatim in `row_filter` (incl. `@>`, `@>|`) |
| `test_having` | M1 | alias key renders underlying expr; multiple ops AND-ed; unknown key / no aggregate → ValueError |
| `test_config_validation` | M1 | unknown keys, bad `empty`, `columns` allowlist, `strict` rejects raw paths but allows aliases |
| `test_multiple_arrays_rejected` | M1 | two distinct array columns → ValueError; same column twice → one lateral |
| `test_empty_include` | M1 | `LEFT JOIN LATERAL … ON true` |
| `test_safe_cast` | M1 | regex-guarded CASE rendering per cast; brace-free regex literals |
| `test_prefilter_opt_in` | M1 | no pre-filter by default; with `prefilter: true`, `=` → `@>` and IN → `@>|` dict entries added to `row_filter`; none for casted paths / comparison ops |
| `test_injection_rejected` | M1 | `;`, quotes, parentheses, comments in idents/keys/aliases/casts/ops → ValueError |
| `test_rust_cython_parity` | M2 | every golden fixture renders identically with `_rs` and Cython; same ValueError messages |
| `test_build_query_plan_mode` | M3 | end-to-end `pgSQLParser` render for the §2 request example; `sqlglot.parse_one(sql, read="postgres")` succeeds; `LIMIT` only on outer query |
| `test_placeholders_blanked` | M3 | table-based slug (`SELECT {fields} FROM {schema}.{table} {filter} {grouping} {offset} {limit}`) → no LIMIT/GROUP BY inside `_qs_src` |
| `test_having_not_a_filter` | M3 | `having` never appears as a WHERE filter; direct-query mode engages parser with only `having` |
| `test_invalid_raises_parser_error` | M3 | invalid token → `ParserError` (code 400), both Rust and Cython paths, no fallback on Rust ValueError |
| `test_add_fields_rejected_in_plan_mode` | M3 | `add_fields=True` + plan mode → `ParserError` |
| `test_non_plan_queries_byte_identical` | M4 | a matrix of existing query shapes (fields, group_by, ordering, JSONB filters, `@>|`, limits, offsets, raw template with `{where_cond}`) renders the same SQL as captured from the pre-feature parser (fixtures frozen in the test file) on both Rust and Cython |

### Integration Tests
| Test | Description |
|---|---|
| `test_live_graduates_per_course` | temp table with `graduation_details jsonb`; rows incl. empty array, NULL, non-array, numeric-valued key; asserts counts for group-by course, element filter vs row filter difference, `having`, buckets, `empty: include` NULL group, `safe_cast` NULLs bad values. Skipped without a pg DSN. |

### Test Data / Fixtures
```python
@pytest.fixture
def students_rows() -> list[dict]:
    """Rows shaped like the brainstorm example: student_uid, licensee, graduation_details (list of
    {course, category, course_date, diploma_number}); include [], None, {"not": "array"}, and a
    student with two diplomas in different courses."""

@pytest.fixture
def unnest_config() -> dict:
    return {"columns": {"graduation_details": {"empty": "exclude"}},
            "aliases": {"course": "graduation_details[].course",
                        "diploma_year": "year(graduation_details[].course_date::date)"}}

# Parser render helper: reuse the `_render` pattern from tests/test_pgsql_jsonb_filters.py:42
# and the `use_rust` monkeypatch parametrisation (tests/test_pgsql_jsonb_filters.py:178-195).
```

---

## 5. Acceptance Criteria

- [ ] AC1 (G7): `pytest tests/test_pgsql_jsonb_unnest_regression.py -q` passes. Every non-plan query shape renders byte-identical SQL on the Rust and Cython paths.
- [ ] AC2: `pytest tests/test_pgsql_jsonb_filters.py tests/test_rust_parsers.py tests/test_grouping_sync.py -q` passes unchanged.
- [ ] AC3 (G1, G2, G6): the §2 request example renders the expected golden SQL, and it parses with `sqlglot` (`read="postgres"`).
- [ ] AC4 (G3): path-keyed filters render in the outer `WHERE` on `_qs_e0.elem`. Non-path filters, including `@>` / `@>|`, render in the inner query exactly as today.
- [ ] AC5 (G4): `having` keyed by alias renders the underlying aggregate expression. `having` never becomes a WHERE filter.
- [ ] AC6 (G5): `strict`, the `columns` allowlist, aliases, `empty: include`, `safe_cast` and `prefilter` each behave as in §2, and each has a test.
- [ ] AC7: an invalid token, config, cast, function or alias raises `ParserError` with `code == 400` on both paths. A Rust `ValueError` does **not** fall back to Cython.
- [ ] AC8: a second distinct array column raises `ParserError`. `add_fields=True` in plan mode raises `ParserError`.
- [ ] AC9: `LIMIT` / `OFFSET` / `GROUP BY` / `ORDER BY` never appear inside `(... ) AS _qs_src` for table-based slugs.
- [ ] AC10 (G8): `test_rust_cython_parity` passes after `make build-inplace && make build-rust` and stage-rust.
- [ ] AC11: `cargo test` passes in `rust/`, including the new `pgsql_unnest` unit tests.
- [ ] AC12: the live integration test passes against Postgres 12+ when a DSN is configured (it may be skipped in CI without a DB).
- [ ] AC13: `ruff check querysource/providers/sql.py querysource/models.py tests/test_pgsql_jsonb_unnest*.py` is clean.
- [ ] AC14: `docs/JSONB_AGGREGATION.md` exists and covers every M5 item.
- [ ] AC15: no new runtime dependencies in `pyproject.toml`.

---

## 6. Codebase Contract

> Verified against base commit `5830325` (dev, 2026-09-28).

### Verified Imports
```python
from querysource.parsers import pgsql                      # tests/test_pgsql_jsonb_filters.py (module import)
from querysource.parsers.pgsql import pgSQLParser          # querysource/providers/pg.py:12
from querysource.models import QueryObject                 # querysource/models.py:24
from querysource.exceptions import ParserError             # querysource/exceptions.py:86 (used by querysource/handlers/service.py:18)
from querysource.qs_parsers import _qs_parsers as _rs      # querysource/parsers/pgsql.pyx:20 (inside try/except ImportError)
# inside pgsql.pyx (relative): from ..exceptions import EmptySentence   # pgsql.pyx:14
```

### Existing Class Signatures
```python
# querysource/parsers/pgsql.pyx
HAS_RUST: bool                                               # lines 19-23
COMPARISON_TOKENS = ('>=', '<=', '<>', '!=', '<', '>',)      # line 26
JSONB_OPERATORS = ('@>', '<@', '@>|', '->', '->>',)          # line 29
JSONB_KEY_SUFFIXES = '|!~#@:'                                # line 32
PG_TEXT_OPERATORS = ('ILIKE', 'NOT ILIKE',)                  # line 34
cdef str pg_literal(str value)                               # line 37 (cdef — NOT importable from Python)
cdef str jsonb_dumps(object value)                           # line 59
cdef str jsonb_operand(object value)                         # line 71
cdef str jsonb_any_of_condition(str col, object operand)     # line 86
cdef tuple jsonb_condition(str col, dict value)              # line 148
cdef class pgSQLParser(SQLParser):                           # line 199 (pgsql.pxd declares no extra members)
    async def filter_conditions(self, sql)                   # line 206 — Rust try, `except Exception: pass` fallback
    async def _filter_conditions_cy(self, sql)               # line 216 — FEAT-103 key check lines 233-243; is_valid-quoted value unquote pattern lines 282-285
    async def build_query(self, querylimit: int = None, offset: int = None)  # line 458
        # 476 process_fields → 478-493 QS filters → 495 filtering_options → 497 filter_conditions
        # → 501 group_by → 502-503 order_by → 504-509 limiting → 510-514 _conditions format_map
        # → 515-521 safe_substitute / format_map(NullDefault) → 523 query_parsed

# querysource/parsers/sql.pyx
self._base_sql = 'SELECT {fields} FROM {tablename} {filter} {grouping} {offset} {limit}'  # line 98
async def group_by(self, sql: str)          # line 250 — Rust _rs.group_by; outer GROUP BY found at depth 0 only
async def order_by(self, sql: str)          # line 292 — Rust _rs.order_by appends " ORDER BY ..." (not depth-aware)
async def limiting(self, sql, limit=None, offset=None)  # line 308 — fills {limit}/{offset} placeholders if present, else appends
async def process_fields(self, sql: str)    # line 328 — no fields + '{fields}' in query_raw → _conditions['fields'] = '*'

# querysource/parsers/abstract.pyx
cdef void set_attributes(self)              # line 69 (self.grouping = [] at line 81)
cdef void define_conditions(self, object conditions)  # line 96 — self.attributes = definition.attributes (line 104)
cdef void _extract_options(self)            # line 147 — calls _grouping_sync at line 156, _col_definition_sync line 162
cdef void _query_fields_sync(self)          # line 199
cdef void _grouping_sync(self)              # line 237
cdef void _ordering_sync(self)              # line 259
cdef void _query_filter_sync(self)          # line 293 — where_cond | filter | definition.filtering
async def set_options(self)                 # line 391 — leftover conditions are merged into filters (set_conditions)
async def set_conditions(self, conditions, connection) -> dict  # line 524 — {**conditions, **self.filter}
async def _where_element(self, key, value, connection)          # line 547 — dict value: popitem() + is_valid
async def set_where(self, _filter, connection)                  # line 581

# querysource/parsers/abstract.pxd
cdef public list grouping                   # line 21
cdef public dict attributes                 # (in "Query Options" block)
cdef void _grouping_sync(self)              # line 72

# querysource/types/validators.pyx
cpdef object is_valid(object key, object value, str T = None, bint noquote = False)  # line 620 — strings → quoteString; lists/dicts verbatim; 'null' → 'null'

# querysource/models.py
class QueryObject(ClassDict):               # line 24
    group_by: Optional[list]                # line 33
class QueryModel(Model):                    # line 48
    attributes: Optional[dict]              # line 54 (jsonb)
    fields: List[str]                       # line 64
    grouping: List[str]                     # line 67

# querysource/providers/sql.py
class sqlProvider(...):
    _PARSER_CONDITION_KEYS = frozenset({...})   # lines 60-67 ("group_by", "grouping", "order_by", "ordering", at line 63)
    async def prepare_connection(self)          # is_raw early-return line 152; build_query wrapped → ParserError lines 156-161

# querysource/exceptions.py
class ParserError(QueryException):          # line 86
    default_code = 400

# rust/src/lib.rs
mod pgsql_parser;                                                                  # line 17
m.add_function(wrap_pyfunction!(pgsql_parser::pgsql_filter_conditions, m)?)?;     # line 67

# rust/src/pgsql_parser.rs
fn pg_safe_identifier_key(key: &str) -> Option<String>   # line 24 (private)
fn pg_literal(value: &str) -> String                     # line 51 (private)
const JSONB_OPERATORS: &[&str]                           # line 82
pub fn pgsql_filter_conditions(sql, filter_dict, cond_definition) -> PyResult<String>  # line 625
#[cfg(test)]                                             # line 708

# rust/src/sql_parser.rs
pub fn group_by(sql: &str, grouping: Vec<String>) -> String   # line 388
pub fn order_by(sql: &str, ordering: Vec<String>) -> String   # line 426
pub fn limiting(sql: &str, limit: &str, offset: &str) -> String  # line 439

# setup.py — Extension(name='querysource.parsers.pgsql', sources=['querysource/parsers/pgsql.pyx'], extra_compile_args=COMPILE_ARGS, language="c")  # lines 56-61
```

### Integration Points
| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `jsonb_unnest.unnest_plan` | `pgSQLParser.build_query` | Cython fallback call | `pgsql.pyx:458` |
| `_rs.pgsql_unnest_plan` | `pgSQLParser.build_query` | Rust fast path (`HAS_RUST` + `hasattr`) | `pgsql.pyx:19-23` |
| `AbstractParser._having_sync` | `_extract_options` | call after `_grouping_sync()` | `abstract.pyx:156` |
| `QueryObject.having` | request conditions | ClassDict field | `models.py:33` |
| `"having"` parser key | `sqlProvider._has_parser_conditions` | `_PARSER_CONDITION_KEYS` | `providers/sql.py:60-67,86-89` |
| planner `ValueError` | `ParserError` (400) | re-raise in `pgSQLParser`; provider also wraps any build error | `providers/sql.py:156-161`, `exceptions.py:86` |

### Does NOT Exist (Anti-Hallucination)
- ~~Any `jsonb_array_elements` / `LATERAL` / `jsonb_to_recordset` usage in `querysource/`~~. A grep found none.
- ~~Identifier validation of `fields` / `grouping` / `ordering` in the parsers~~. They are spliced verbatim; only filter keys are checked (FEAT-103).
- ~~Aggregate allowlist or `HAVING` support in `SQLParser` / `pgSQLParser`~~. `HAVING` appears only as a terminator keyword in `group_by`.
- ~~`AbstractParser.having` / `_having_sync`~~. New in this spec.
- ~~A Python-importable `pg_literal`~~. It is `cdef` in `pgsql.pyx` and private in Rust.
- ~~`querysource.parsers.jsonb_unnest`~~, ~~`rust/src/pgsql_unnest.rs`~~. New in this spec.
- ~~`pg_input_is_valid` use~~. It is Postgres 16+ only, and the floor is 12.
- ~~A grouping capability in qsurl~~. `querysource/qsurl/capabilities.py` has no GROUP token.
- ~~A `docs/` page for JSONB filters~~. There is no existing JSONB doc to extend; M5 creates a new one.

### Edit Sites (Blueprint Anchors)

Verified against: `5830325`

| File | Action | Verbatim anchor line | Verified at | Occurrences |
|---|---|---|---|---|
| `querysource/parsers/jsonb_unnest.pyx` | CREATE | — | — | — |
| `setup.py` | MODIFY | `        name='querysource.parsers.pgsql',` (add a sibling Extension block after this one's closing `),`) | `setup.py:57` | 1 |
| `rust/src/pgsql_unnest.rs` | CREATE | — | — | — |
| `rust/src/lib.rs` | MODIFY | `mod pgsql_parser;` | `lib.rs:17` | 1 |
| `rust/src/lib.rs` | MODIFY | `    m.add_function(wrap_pyfunction!(pgsql_parser::pgsql_filter_conditions, m)?)?;` | `lib.rs:67` | 1 |
| `querysource/parsers/pgsql.pyx` | MODIFY | `from ..exceptions import EmptySentence` | `pgsql.pyx:14` | 1 |
| `querysource/parsers/pgsql.pyx` | MODIFY | `    async def build_query(self, querylimit: int = None, offset: int = None):` | `pgsql.pyx:458` | 1 |
| `querysource/parsers/pgsql.pyx` | MODIFY | `        sql = await self.process_fields(sql)` | `pgsql.pyx:476` | 1 |
| `querysource/parsers/pgsql.pyx` | MODIFY | `        sql = await self.group_by(sql)` | `pgsql.pyx:501` | 1 |
| `querysource/parsers/abstract.pxd` | MODIFY | `    cdef public list grouping` | `abstract.pxd:21` | 1 |
| `querysource/parsers/abstract.pxd` | MODIFY | `    cdef void _grouping_sync(self)` | `abstract.pxd:72` | 1 |
| `querysource/parsers/abstract.pyx` | MODIFY | `        self.grouping = []` **in `set_attributes`** (context: preceded by `        self.ordering = []`, followed by `        self.program_slug = None`) | `abstract.pyx:81` | 2 |
| `querysource/parsers/abstract.pyx` | MODIFY | `        self._grouping_sync()` **in `_extract_options`** (context: preceded by `        self._offset_pagination_sync()`, followed by `        self._ordering_sync()`) | `abstract.pyx:156` | 2 (156 in `_extract_options`; 367 in the legacy async wrappers block — use the quoted context) |
| `querysource/parsers/abstract.pyx` | MODIFY | `    cdef void _grouping_sync(self):` (add `_having_sync` after this method) | `abstract.pyx:237` | 1 |
| `querysource/models.py` | MODIFY | `    group_by: Optional[list]` | `models.py:33` | 1 |
| `querysource/providers/sql.py` | MODIFY | `        "group_by", "grouping", "order_by", "ordering",` | `providers/sql.py:63` | 1 |
| `tests/test_pgsql_jsonb_unnest.py` | CREATE | — | — | — |
| `tests/test_pgsql_jsonb_unnest_regression.py` | CREATE | — | — | — |
| `tests/integration/test_pgsql_jsonb_unnest_live.py` | CREATE | — | — | — |
| `docs/JSONB_AGGREGATION.md` | CREATE | — | — | — |

---

## 7. Implementation Notes & Constraints

### Patterns to Follow
- **Rust fast path + Cython fallback**, like `pgSQLParser.filter_conditions` (`pgsql.pyx:206-214`). One exception: a Rust `ValueError` is a *validation* verdict and is re-raised as `ParserError`, never masked by a fallback.
- **Literals only through `pg_literal` semantics**, including braces → `\x7b` / `\x7d` in `E''` strings, so later `format_map` passes can't break the SQL. JSON keys are **always** rendered as literals (`->> 'key'`), never as identifiers.
- **Pre-quoted value handling**, from `pgsql.pyx:282-285`: strip one outer `'…'` pair, then `.replace("''", "'")`, then `pg_literal`.
- **Test patterns** from `tests/test_pgsql_jsonb_filters.py`: the `_render` helper (line 42), the `use_rust` monkeypatch parametrisation (178-195), and `sqlglot.parse_one(sql, read="postgres")` validity checks.
- Google docstrings, type hints, and `self.logger` for debug output of the rendered plan. Never `print`.
- **Rebuild after edits.** After `.pyx` / `.pxd` / `setup.py` edits run `make build-inplace`. After Rust edits run `make build-rust`, then stage-rust: Python loads the source-tree `_qs_parsers` `.so`, and `make build-rust` alone leaves it stale.

### Known Risks / Gotchas
- **Structural placeholders in the inner query.** Table-based slugs keep `{grouping} {offset} {limit}` in the rendered inner SQL. `limiting()` would fill `{limit}` *inside* the subquery. Blank every entry of `STRUCTURAL_PLACEHOLDERS` in the inner SQL before wrapping (AC9).
- **`order_by` is not depth-aware.** It only appends, so calling it on the outer SQL is correct. Never call it on the inner.
- **`having` leaking into WHERE.** `set_options` merges every leftover condition into filters (`abstract.pyx` `set_conditions`: `{**conditions, **self.filter}`). `_having_sync` must pop `having` inside `_extract_options`, before that merge.
- **Filter dicts are mutated upstream.** `_where_element` does `value.popitem()`, so an element-filter comparison dict carries **one** operator by the time the planner sees it, which is existing behaviour. Document it; `having` keeps multiple operators because it is popped before `set_where`.
- **`is_valid` pass-through.** SQL-constant-looking strings (`CURRENT_DATE`, UDF names) pass through verbatim. For element filters the planner treats every string as a literal (`pg_literal`), which makes SQL functions a non-goal.
- **Casts on dirty data.** By default a bad value fails the whole query with a DB error. With `safe_cast`, regex guards turn malformed values into NULL, but impossible dates (`2025-02-30`) still raise (§8 Q1).
- **JSON type sensitivity of `@>`.** This is why the pre-filter is opt-in (§2 refinement, §8 Q2).
- **Non-array JSONB values.** They are guarded by the `CASE jsonb_typeof(...) WHEN 'array'` wrapper. A NULL column yields zero elements, or one NULL row with `empty: include`.
- **`count(*)` with `empty: include`** counts a NULL-group row as 1. Use `count(<path>)` to count 0. Document this.
- **Aliases shadow real columns.** A declared alias name that equals a real column name wins in plan mode. Document it.
- **Queries whose own `GROUP BY` / `LIMIT` is in the template** are aggregated over the inner result. That is well-defined; document it.
- **Performance.** `is_plan_candidate` runs on every pg query, so it must be a cheap scan. The planner itself runs once per plan-mode query.
- **Cache keys.** `having` and the path tokens live inside conditions, so no cache change is expected. `test_having_not_a_filter` also asserts that two requests differing only in `having` render different SQL.

### External Dependencies
| Package | Version | Reason |
|---|---|---|
| PostgreSQL | ≥ 12 (deployment floor; features used need ≥ 9.4) | `jsonb_array_elements`, `LATERAL`, `->`/`->>`, `jsonb_typeof`, `date_trunc` |
| `orjson` | existing | JSON text for opt-in pre-filter operands |
| `sqlglot` | existing (tests) | SQL validity assertions |
| `pyo3` / `maturin` | existing | Rust extension |

---

## 8. Open Questions

- [x] Flow type / base branch — *Resolved in brainstorm*: feature → dev
- [x] Declaration point — *Resolved in brainstorm*: both request conditions and slug definition (slug may allowlist)
- [x] v1 aggregate scope — *Resolved in brainstorm*: count / count distinct, min/max/sum/avg, time bucketing, element-level filtering
- [x] Result shape — *Resolved in brainstorm*: flat rows
- [x] Filter scope — *Resolved in brainstorm*: explicit per filter (row filters unchanged; path-keyed filters are element-level)
- [x] Syntax — *Resolved in brainstorm*: path strings (`col[].key`)
- [x] Slug shapes — *Resolved in brainstorm*: any pg slug, via subquery wrap — *refined at spec time*: any **parser-rendered** slug; `is_raw` slugs are a Non-Goal (the parser never runs)
- [x] Empty/NULL arrays — *Resolved in brainstorm*: excluded by default, opt-in NULL group
- [x] Invalid path tokens — *Resolved in brainstorm*: raise a parser error (4xx); never drop silently. Invalid JSONB filters keep their current drop behaviour.
- [x] `HAVING` scope — *Resolved in brainstorm*: in v1, as a `having` condition keyed by metric/group alias with comparison-dict values
- [x] Time-bucket syntax — *Resolved in brainstorm*: helper functions `year` / `quarter` / `month` / `week` / `day` (path) rendered as `date_trunc`; no passthrough
- [x] Cast failures on dirty data — *Resolved in brainstorm*: fail by default; slug opt-in `safe_cast: true` renders regex-guarded casts (bad values become NULL)
- [x] Multiple distinct array columns — *Resolved in brainstorm*: rejected in v1 with a parser error
- [x] Rust parity — *Resolved in brainstorm*: port the planner to Rust too (Rust fast path + Cython fallback, identical-output tests)
- [x] Non-strict slug restrictions — *Resolved in brainstorm*: grammar validation always; if the slug declares `jsonb_unnest.columns`, only those arrays may be unnested; with no declaration any grammar-valid identifier is allowed
- [x] Minimum PostgreSQL version — *Resolved in brainstorm*: PG 12+ in all deployments (v1 still uses only 9.4+ features; jsonpath stays available later)
- [x] Q1: `safe_cast` cannot reject well-shaped impossible dates (`2025-02-30`) on Postgres 12–15. Is the documented limitation acceptable, or should a follow-up add `pg_input_is_valid` when the server is ≥ 16? Non-blocking; v1 ships with the documented limitation. — *Owner: Jesus Lara*: documented limitation.
- [x] Q2: the containment pre-filter was changed from automatic (brainstorm) to opt-in per column (`prefilter: true`), because `@>` is JSON-type-sensitive and could drop rows whose key is numeric. Confirm the opt-in default. Non-blocking; the spec defaults to opt-in. — *Owner: Jesus Lara*: opt-in

---

## 9. Design Research Cross-Check

> Model: — · Status: skipped (not requested — user accepted the brainstorm and chose to skip the codex pass) · Transcript: —

| # | Suggestion (kind) | Disposition | Reason | Landed in |
|---|---|---|---|---|

Summary: **0** confirmed · **0** rejected · **0** escalated.

---

## Worktree Strategy

- **Isolation:** one feature worktree, `feat-FEAT-153-group-aggregation-jsonb-columns`. The `sdd-coder` engine gives each task its own sub-worktree inside it.
- **Module dependency graph:**
  - M2 → M1: the port targets M1's contract and golden fixtures; no code import.
  - M3 → M1: `pgsql.pyx` imports `querysource.parsers.jsonb_unnest`.
  - M4 → M1, M3; the M4 parity cases also → M2.
  - M5 → M1, M3: it documents the final rendering.
  - M1 has no dependencies. **M2 and M3 can run concurrently** once M1 lands.
- **Shared files:**
  - none between M1 and M2.
  - M3 alone owns `pgsql.pyx`, `abstract.pyx` / `.pxd`, `models.py` and `providers/sql.py`.
  - `setup.py` is M1-only.
  - `rust/src/lib.rs` is M2-only.
- **Exclusive resources:**
  - Cython rebuild (`make build-inplace`): M1 and M3 tasks.
  - Rust rebuild plus stage-rust (`make build-rust`): M2 tasks.
  - These tasks are `parallel: false` with respect to each other's builds.
- **Cross-feature dependencies:** none pending. The JSONB filter work (`8936386`) and qsurl (FEAT-152) are already on `dev`.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-09-28 | Jesus Lara (with Claude) | Initial draft from accepted brainstorm; is_raw non-goal; pre-filter made opt-in |
