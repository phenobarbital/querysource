---
# SDD flow type and base branch (FEAT-145).
# - type: feature  (default)  → base_branch: dev (or any non-main branch)
# - type: hotfix              → base_branch MUST be: main
type: feature
base_branch: dev
# projects: parts of the codebase this doc concerns: `querysource` or a subsystem
#   (providers, parsers, rust-parsers, outputs, multiquery, handlers, datasources,
#   auth, cache, scheduler) or an area (sdd-tooling, dev-loop, docs, ci). Unknown values warn, not fail.
projects: [parsers, rust-parsers, querysource]
# tags: free-form kebab-case keywords for organizing specs (e.g. bigquery, cache).
tags: [postgres, jsonb, aggregation, group-by, unnest]
---

# Brainstorm: Group & Aggregate by JSONB Array Elements (PostgreSQL)

**Date**: 2026-09-28
**Author**: Jesus Lara (with Claude)
**Status**: exploration
**Recommended Option**: A

---

## Problem Statement

Many PostgreSQL tables served through QuerySource store one-to-many detail as a
JSONB **array of objects** inside the row. For example, a student row carries
`graduation_details`:

```json
"graduation_details": [
  {"course": "Pilates Studio", "category": "Comprehensive",
   "course_date": "2025-09-19", "diploma_number": "POL15058"}
]
```

Callers can already **filter** rows on these arrays with the recent JSONB filter
operators (`@>`, `<@`, `@>|` any-of, `->`, `->>`). They cannot **group and
aggregate by the values inside the array**. A question like "count the number
of graduates per course (optionally per category or per year), for licensee
Asia" needs:

1. unnesting (exploding) the array: one row per element,
2. projecting element keys (`course`, `category`, `course_date`),
3. filtering at element level, not row level,
4. aggregating with `GROUP BY` on the element keys.

Today this means writing a hand-crafted raw-SQL slug per question
(`CROSS JOIN LATERAL jsonb_array_elements(...)`). That does not scale, and
request-time `fields` / `group_by` cannot express it. The row-level `@>` filter
alone also gives misleading counts. Filtering `course = 'Pilates Studio'` and
grouping by course still counts that student's *other* diplomas, because the
row matched.

**Affected users**: API consumers and dashboard builders who query pg slugs,
the qsurl/LLM path (FEAT-152) later, and slug authors who today write bespoke
SQL for every aggregation.

## Constraints & Requirements

- **Zero behaviour change for existing queries.** A query that uses none of the
  new syntax must render byte-identical SQL on both the Cython and the Rust
  paths. The same rule was applied to the `@>` / `@>|` work (commit `8936386`).
- **PostgreSQL provider only** (`pgSQLParser`). Other dialects are untouched.
- **Declared by the request and/or by the slug** (user decision: "Both").
  The slug can declare aliases and an allowlist; requests may use path strings
  directly, subject to validation and the slug's allowlist when one exists.
- **Path-string syntax** (user decision), fitting the existing list-shaped
  `fields` / `group_by` / `filter` conditions, e.g.
  `group_by: ["graduation_details[].course"]`.
- **v1 aggregates** (user decision): `count(*)`, `count(DISTINCT …)`, `min`,
  `max`, `sum`, `avg`, **time bucketing** of element date fields, and
  **element-level filtering**.
- **Filter scope is explicit per filter** (user decision). Existing row filters
  (`{"graduation_details": {"@>": …}}`) keep their row-level meaning. A filter
  key written as a path (`"graduation_details[].course": "Pilates Studio"`)
  targets the unnested element.
- **Works for any pg slug shape** (user decision). The rendered query (raw SQL
  or table-based) is wrapped as a subquery or CTE, and unnest and aggregation
  happen outside it.
- **Empty or NULL arrays are excluded by default** (`CROSS JOIN LATERAL`). An
  opt-in switches to `LEFT JOIN LATERAL … ON true`, which yields a `NULL` group.
- **Flat-row results** (user decision), e.g. `{course, category, total}`, so
  every output writer and destination works unchanged.
- **Security.** Today `fields` and `grouping` are spliced into SQL verbatim
  (`sql.pyx:328`, `sql.pyx:250`, `sql_parser.rs:388`). Only filter keys pass
  the FEAT-103 identifier check (`pgsql.pyx:233-243`). The new syntax must be
  **grammar-validated and rendered by QuerySource itself**:
  - column and alias identifiers are checked,
  - JSON keys are rendered as quoted literals via `pg_literal`,
  - casts and functions come from a closed allowlist,
  - values go through the existing quoting (`pg_literal` / `Entity.quoteString`).
  User input is never concatenated into the query.
- Async-first, Google docstrings and type hints, no new runtime dependencies.

---

## Options Explored

### Option A: Unnest planner inside `pgSQLParser` (subquery wrap + LATERAL)

Add a small **JSONB-unnest planning stage** to `pgSQLParser.build_query`. It is
activated only when an array path token (`<column>[].<key>[.<key>…]`) appears
in `fields`, `group_by` / `grouping`, `filter` keys or `ordering`, or when the
slug declares unnest aliases in `QueryModel.attributes`.

When active, the parser:

1. Parses and validates every path token against a strict mini-grammar:
   - identifier column,
   - `[]` array marker,
   - dotted JSON keys,
   - optional `::cast` from an allowlist (`text`, `int`, `bigint`, `numeric`,
     `date`, `timestamp`, `timestamptz`, `boolean`),
   - aggregate wrappers from an allowlist: `count`, `count(distinct …)`, `min`,
     `max`, `sum`, `avg`,
   - bucket functions: `year` / `quarter` / `month` / `week` / `day`, which
     render as `date_trunc`,
   - an optional `as <alias>`.
2. Renders the **inner query** exactly as today: `query_raw`, row filters
   (including `@>` / `@>|`), but with no `GROUP BY`, `ORDER BY` or `LIMIT`.
   Fields default to `*` so the array column is available.
3. Wraps it as `(<inner>) AS _qs_src` and adds one
   `CROSS JOIN LATERAL jsonb_array_elements(_qs_src.<col>) AS _qs_e0(elem)` per
   distinct array column. With the opt-in it adds
   `LEFT JOIN LATERAL … ON true` instead.
4. Rewrites each path to `(_qs_e0.elem->>'course')` or
   `((_qs_e0.elem->>'course_date')::date)`. Nested keys use `#>> '{a,b}'`.
5. Renders **element-level filters** (path-keyed filter entries, supporting
   scalar, list/IN, and comparison-dict values) in the outer `WHERE`. For
   index use, each equality or IN element filter also adds a row-level
   containment pre-filter, `col @> '[{"course": "…"}]'::jsonb` (or `@>|` for
   IN), to the inner query. This lets a GIN index prune rows before unnesting,
   and the result is unchanged.
6. Emits the outer `SELECT <group cols>, <aggregates>`, then
   `GROUP BY <group cols>`, then `ORDER BY` (aliases allowed) and `LIMIT` /
   `OFFSET`, on the outer query.

Output column names default to the last JSON key (`course`). On a collision
they fall back to `<column>_<key>`, and an explicit `as` alias wins. Queries
with no path tokens skip the stage entirely, so their SQL is byte-identical.

The **slug-side declaration** lives in `QueryModel.attributes`, e.g.
`attributes.jsonb_unnest`. It can:

- name aliases (`course := graduation_details[].course`) so requests can use
  `group_by: ["course"]`,
- set the empty-array policy,
- act as an **allowlist**: when present, request paths outside it are
  rejected.

✅ **Pros:**
- Pure SQL pushdown. Postgres does the aggregation, so a small flat result
  comes back and there are no memory surprises.
- Fits the existing condition shapes (`fields`, `group_by`, `filter`,
  `ordering`), so no new request envelope is needed.
- Wrapping any rendered query means raw-SQL slugs work too.
- Filter scope is explicit, and the `@>` pre-filter keeps index use.
- Fully opt-in: the new code path runs only when path tokens or slug
  declarations are present.

❌ **Cons:**
- New grammar and rendering logic in the parser. Needs careful tests: SQL
  golden strings plus `sqlglot.parse_one(..., read="postgres")` validity, as
  in `tests/test_pgsql_jsonb_filters.py`.
- Wrapping changes the query shape when the feature is active. Slugs whose
  `query_raw` already has its own `GROUP BY` / `LIMIT` are aggregated over the
  inner result. That is well-defined but must be documented.
- Rust parity: `pgsql_filter_conditions` (Rust) handles filter keys. Path-keyed
  filters must be removed from `self.filter` **before** the Rust or Cython
  filter builders run. Otherwise the FEAT-103 check silently drops them
  (`[` is not an identifier character).

📊 **Effort:** Medium

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| PostgreSQL ≥ 9.4 | `jsonb_array_elements`, `LATERAL`, `->>`, `#>>`, `date_trunc` | all stable, no extensions |
| `orjson` (existing) | JSON literal rendering for the `@>` pre-filter | already used by `jsonb_operand` |
| `sqlglot` (existing, tests) | Validate rendered SQL parses as postgres | already a test dependency |

🔗 **Existing Code to Reuse:**
- `querysource/parsers/pgsql.pyx` — `pg_literal`, `jsonb_operand`,
  `jsonb_any_of_condition`, `jsonb_condition` for literal rendering and the
  containment pre-filter; the FEAT-103 identifier check pattern.
- `querysource/parsers/sql.pyx` — `group_by`, `order_by`, `limiting`,
  `process_fields` for the outer query (they operate on strings and can run on
  the wrapped SQL).
- `querysource/parsers/abstract.pyx` — `_query_fields_sync`, `_grouping_sync`
  (where `fields` and `group_by` / `grouping` are collected).
- `querysource/models.py` — `QueryModel.attributes` (jsonb) to carry the slug
  declaration; no schema migration needed.
- `tests/test_pgsql_jsonb_filters.py` — `_render` helper and the `use_rust`
  parametrisation to copy for the new tests.

---

### Option B: Slug-declared "virtual columns" only

The slug declares named projections in `attributes`, e.g.
`"virtual_columns": {"course": "graduation_details[].course", "year":
"year(graduation_details[].course_date::date)"}`. The parser treats them as
ordinary column names in `fields`, `group_by`, `filter` and `ordering`.
Requests never see paths. They group by `course` as if it were a real column.
Rendering is the same subquery + LATERAL technique as Option A.

✅ **Pros:**
- The smallest attack surface: requests only reference names the slug author
  vetted.
- The friendliest API for dashboards and for LLM / qsurl callers
  (`group_by=course`).
- Slug authors control casts, bucket granularity and empty-array policy.

❌ **Cons:**
- No ad-hoc exploration: every new element key needs a slug edit. This
  conflicts with the user's "request conditions too" decision.
- Name collisions between virtual and real columns need a precedence rule.
- The rendering engine is still the same as Option A; only the entry point
  differs.

📊 **Effort:** Medium (slightly lower than A: no request-side grammar)

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| PostgreSQL ≥ 9.4 | same SQL primitives as A | — |

🔗 **Existing Code to Reuse:**
- `querysource/models.py` `QueryModel.attributes` — declaration storage.
- `querysource/parsers/pgsql.pyx` — literal rendering helpers.

---

### Option C: Post-query explode + group in pandas (residual / MultiQuery stage)

Leave SQL untouched. Fetch the filtered rows, then `explode` the JSONB column,
`json_normalize` the elements, and group or aggregate in pandas. This reuses
the MultiQuery `GroupBy` operator
(`querysource/queries/multi/operators/GroupBy.py`) or the qsurl residual stage
(`querysource/qsurl/residual.py`), which already has a row cost guard
(`QSURL_MAX_RESIDUAL_ROWS`).

✅ **Pros:**
- No parser changes at all, so zero risk to existing SQL rendering.
- Provider-agnostic: works for Mongo / BigQuery / REST sources that return
  nested JSON.
- `GroupBy.aggregation_functions()` already defines an aggregation vocabulary.

❌ **Cons:**
- Pulls **every matching row** (including full JSON arrays) over the wire just
  to count. That is heavy on large tables and runs into the residual row cap.
- Aggregation happens after `LIMIT` / pagination unless carefully handled, so
  results can be wrong when the provider limits rows.
- Time bucketing and count-distinct in pandas are fine, but element-level
  filtering and pagination of *groups* get awkward.
- Doesn't meet the "define complex queries easily on the DB provider" goal.

📊 **Effort:** Low–Medium

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `pandas` (existing) | `DataFrame.explode`, `pd.json_normalize`, `groupby().agg()` | already a dependency |

🔗 **Existing Code to Reuse:**
- `querysource/queries/multi/operators/GroupBy.py` — `GroupBy`,
  `GroupBy.aggregation_functions()`.
- `querysource/qsurl/residual.py` — residual pandas stage and cost guard.

---

### Option D (unconventional): SQL/JSON path (`jsonpath`) set-returning projection

Instead of `LATERAL jsonb_array_elements`, expose PostgreSQL 12+ SQL/JSON
paths directly. Fields and group keys would be written as jsonpath
expressions, e.g. `graduation_details$[*].course`, and rendered as
`jsonb_path_query(col, '$[*].course') #>> '{}'` in the `SELECT` list.
Element filters would use jsonpath predicates, e.g.
`'$[*] ? (@.course_date >= "2025-01-01").course'`, with
`jsonb_path_query(col, $path, vars)` binding values through the `vars` jsonb
argument, not through string interpolation.

✅ **Pros:**
- Very expressive: nested arrays, predicates, wildcards and `.datetime()`
  casting all live in one standard language (SQL:2016).
- Values can be bound via the `vars` argument, which is a clean injection
  story.
- No subquery wrap needed for simple cases. A set-returning function in
  `SELECT` plus `GROUP BY` works.

❌ **Cons:**
- Requires PostgreSQL ≥ 12. Deployment floor unknown.
- Multiple SRFs in one `SELECT` zip together, which is surprising when two
  paths come from different arrays or when combining projections.
- jsonpath strings from requests are themselves a language to validate. The
  surface is harder to allowlist than a tiny `col[].key` grammar.
- Less familiar to slug authors than `->>`.

📊 **Effort:** Medium–High

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| PostgreSQL ≥ 12 | `jsonb_path_query`, `jsonb_path_exists`, jsonpath `vars` | version floor to confirm |

🔗 **Existing Code to Reuse:**
- `querysource/parsers/pgsql.pyx` — `pg_literal` for the jsonpath literal and
  `jsonb_dumps` for `vars`.

---

## Recommendation

**Option A** is recommended. It includes Option B's slug-side
aliases / allowlist as its "slug declaration" half.

- It is the only option that meets **every** user decision at once:
  - request-time path strings, and slug declaration ("Both"),
  - explicit row- vs element-level filters,
  - any slug shape (via the subquery wrap),
  - flat rows,
  - SQL pushdown, so Postgres does the counting.
- Option B alone blocks ad-hoc request exploration. Its value (a safe,
  friendly alias layer and an allowlist) is kept inside A as
  `attributes.jsonb_unnest`.
- Option C is attractive for non-SQL providers, but it moves the whole array
  payload to the app just to count, and it breaks under `LIMIT`. Keep it in
  mind as a later, separate capability for Mongo/REST sources.
- Option D is more powerful but raises the PG version floor and the
  validation surface, which is more than needed for "group by
  `array[].key`". A can adopt jsonpath later as an alternative renderer if
  nested-array needs appear.

**Trade-off accepted:** A adds a new rendering branch to the pg parser and a
mini-grammar to maintain. We contain the risk as follows:

- the branch runs only when a path token or slug declaration is present,
- golden-SQL regression tests prove existing queries are byte-identical on
  both the Rust and Cython paths,
- everything user-supplied is validated against closed allowlists.

---

## Feature Description

### User-Facing Behavior

Request-time example: "graduates per course and category, Asia licensee, 2025
diplomas only, top 10":

```json
{
  "fields": ["graduation_details[].course", "graduation_details[].category",
             "count(distinct student_uid) as graduates"],
  "group_by": ["graduation_details[].course", "graduation_details[].category"],
  "filter": {
    "licensee": "Asia",
    "graduation_details[].course_date::date": {">=": "2025-01-01"}
  },
  "ordering": ["graduates DESC"],
  "querylimit": 10
}
```

The result is flat rows, e.g.
`[{"course": "Pilates Studio", "category": "Comprehensive", "graduates": 42}, …]`.

Time bucketing: `fields: ["year(graduation_details[].course_date::date) as
year", "graduation_details[].course", "count(*) as diplomas"]`, with
`group_by: ["year", "graduation_details[].course"]`. Group-by entries may
refer to a select alias defined in `fields`.

Slug-side declaration, in `QueryModel.attributes`:

```json
{"jsonb_unnest": {
   "columns": {"graduation_details": {"empty": "exclude"}},
   "aliases": {"course": "graduation_details[].course",
               "category": "graduation_details[].category",
               "diploma_year": "year(graduation_details[].course_date::date)"},
   "strict": true}}
```

With this declaration, requests can use `group_by: ["course"]`, and
`strict: true` rejects any request path not covered by `aliases`.

Row-level filters are unchanged. `{"graduation_details": {"@>":
[{"course": "Pilates Studio"}]}}` still means "students holding a Pilates
Studio diploma". The path-keyed form means "only the Pilates Studio
elements".

### Internal Behavior

1. **Detect.** After conditions are collected (`_query_fields_sync`,
   `_grouping_sync`, filter collection), a new pg-only planner scans `fields`,
   `grouping`, `filter` keys and `ordering` for array-path tokens, and reads
   `attributes.jsonb_unnest`. If nothing is found, it is a no-op and the
   existing flow runs unchanged.
2. **Validate and plan.** It parses each token into a structured plan with
   these parts:
   - array column,
   - key path,
   - cast,
   - aggregate or bucket function,
   - alias,
   - scope (group key, metric, element filter, order key).

   Anything outside the grammar or allowlists raises a clear parser error.
   It does not silently drop the token. This differs deliberately from
   invalid JSONB filters, which *are* dropped, and the difference needs a
   decision (see Open Questions).
3. **Extract.** It removes path-keyed entries from `self.filter`, and path
   tokens from `fields` / `grouping` / `ordering`, so the existing Rust and
   Cython builders only see what they already understand. It adds the derived
   `@>` / `@>|` pre-filters to `self.filter` in the existing dict form, so they
   render through the already-tested code.
4. **Render inner.** Run the normal `build_query` steps to produce the inner
   SQL without grouping, ordering or limit.
5. **Render outer.** Wrap the inner SQL, add one LATERAL join per array
   column, then add the element `WHERE`, the `SELECT` list (group keys,
   metrics), `GROUP BY`, `ORDER BY` and `LIMIT` / `OFFSET`.
6. **Finalize.** The existing `format_map` / `safe_substitute` passes run on
   the final SQL. All braces in JSON literals are `pg_literal`-escaped, as the
   `@>` work already guarantees (`test_build_query_survives_format_passes`).

### Edge Cases & Error Handling

- **Casts in existing fields.** A field such as `tags::text[]` must **not** be
  detected. The token rule requires `[]` immediately followed by `.`.
- **Empty or NULL arrays.** Excluded by default. With `empty: "include"`,
  `LEFT JOIN LATERAL … ON true` yields `NULL` group keys. `count(*)` then
  counts those rows as 1, and `count(elem)` as 0. Document this.
- **Non-array JSONB values.** `jsonb_array_elements` raises on a scalar or
  object. Guard with
  `CASE jsonb_typeof(col) WHEN 'array' THEN col ELSE '[]'::jsonb END` so a
  single malformed row does not fail the query.
- **Bad casts.** A cast failure on dirty data, e.g. `'2025-13-40'::date`,
  surfaces as a DB error. See Open Questions for a safe-cast option.
- **Multiple array columns.** Two different arrays produce a cross product of
  their elements. Reject in v1 unless the slug explicitly allows it.
- **Aggregates without group keys.** Allowed, e.g. a total diploma count.
- **Group keys without aggregates.** Allowed; this is a distinct listing.
- **Slugs whose `query_raw` has its own `GROUP BY` or `LIMIT`.** The inner
  query keeps them, and the outer aggregation runs over the inner result.
- **Paging.** `querylimit` and `offset` apply to the **groups** (outer query).
  The inner query receives no limit, so no rows are lost.
- **Cache keys.** Conditions already feed the cache key, and the new tokens
  live inside existing condition fields, so no cache change is expected.
  Verify in the spec.
- **Rust fast path failure.** `pgsql_filter_conditions` falls back to Cython
  on exception (`pgsql.pyx:206-214`). The planner runs before both, so the
  behaviour is identical on either path.

---

## Capabilities

### New Capabilities
- `pg-jsonb-array-unnest`: detect, validate and render `col[].key` array paths
  as `LATERAL jsonb_array_elements` projections over a wrapped inner query.
- `pg-jsonb-element-aggregation`: aggregates (`count`, `count distinct`,
  `min`, `max`, `sum`, `avg`) and time buckets (`year` / `quarter` / `month` /
  `week` / `day`) over element keys, with `GROUP BY`, `ORDER BY` and `LIMIT`
  applied to the outer query.
- `pg-jsonb-element-filters`: path-keyed filter entries applied per element,
  with an automatic `@>` / `@>|` row pre-filter for GIN index use.
- `slug-jsonb-unnest-declaration`: `QueryModel.attributes.jsonb_unnest`
  aliases, empty-array policy and a strict allowlist.

### Modified Capabilities
- `pgsql-jsonb-filters` (commit `8936386`, `tests/test_pgsql_jsonb_filters.py`):
  its helpers are reused for the pre-filter; its behaviour is unchanged.

---

## Impact & Integration

| Affected Component | Impact Type | Notes |
|---|---|---|
| `querysource/parsers/pgsql.pyx` (`pgSQLParser.build_query`) | modifies | new planner stage; runs only when path tokens or declaration present |
| `querysource/parsers/sql.pyx` (`group_by`, `order_by`, `limiting`) | depends on | reused on outer SQL; no change expected |
| `rust/src/pgsql_parser.rs`, `rust/src/sql_parser.rs` | depends on / maybe modifies | planner strips path tokens before the Rust calls; a Rust port of the planner is optional (open question) |
| `querysource/parsers/abstract.pyx` | depends on | `_query_fields_sync`, `_grouping_sync` supply inputs |
| `querysource/models.py` `QueryModel.attributes` | extends (data only) | new `jsonb_unnest` key; no schema migration |
| Output writers / destinations | none | flat rows |
| `querysource/qsurl/` (FEAT-152) | future | could expose group/aggregate capability later; out of scope |
| Docs | extends | document the syntax next to the JSONB filter docs |

No breaking changes and no new dependencies. The Cython rebuild is needed
(`make build-inplace`), plus `make build-rust` and stage-rust only if the
Rust planner is ported.

---

## Code Context

### User-Provided Code

```json
// Source: user-provided (example row; graduation_details is a JSONB column)
{
  "student_uid": "71d6c31f-d807-4965-9870-6f56aaccbf59",
  "first_name": "Xinyue", "last_name": "Yang", "full_name": "Yang Xin Yue",
  "country": "China", "licensee": "Asia",
  "last_diploma_date": "2025-09-19", "is_requalified": true,
  "graduation_details": [
    {"course": "Pilates Studio", "category": "Comprehensive",
     "course_date": "2025-09-19", "diploma_number": "POL15058"}
  ]
}
```

Target question: "count the number of graduates grouped by course", combined
with filters.

### Verified Codebase References

#### Classes & Signatures
```python
# From querysource/parsers/pgsql.pyx
JSONB_OPERATORS = ('@>', '<@', '@>|', '->', '->>',)          # line 29
JSONB_KEY_SUFFIXES = '|!~#@:'                                 # line 32
cdef str pg_literal(str value)                                # line 37
cdef str jsonb_operand(object value)                          # line 71
cdef str jsonb_any_of_condition(str col, object operand)      # line 86
cdef tuple jsonb_condition(str col, dict value)               # line 148 -> (handled: bool, cond: str|None)
cdef class pgSQLParser(SQLParser):                            # line 199
    async def filter_conditions(self, sql)                    # line 206 (Rust fast path, falls back to Cython on exception)
    async def _filter_conditions_cy(self, sql)                # line 216
        # FEAT-103 filter-key identifier check                # lines 233-243 (isalnum / '_' / '.' after rstrip of suffixes)
        # dict values -> jsonb_condition(key, value)          # line 261
    async def build_query(self, querylimit: int = None, offset: int = None)  # line 458
        # order: process_fields -> query filters -> filtering_options -> filter_conditions
        #        -> group_by -> order_by -> limiting -> format_map/safe_substitute

# From querysource/parsers/sql.pyx
self._base_sql = 'SELECT {fields} FROM {tablename} {filter} {grouping} {offset} {limit}'  # line 98
async def group_by(self, sql: str)          # line 250 (Rust _rs.group_by fast path; splices self.grouping verbatim)
async def process_fields(self, sql: str)    # line 328 (Rust _rs.process_fields fast path; splices self.fields verbatim)
async def build_query(self, querylimit=None, offset=None)  # line 356

# From querysource/parsers/abstract.pyx
cdef tuple KEYWORD_TOKENS = ('::', '@>', '<@', '->', '->>', '>=', '<=', '<>', '!=', '<', '>')  # line 25
cdef void _query_fields_sync(self)   # line 199: self.fields = conditions.pop('fields') or definition.fields
cdef void _grouping_sync(self)       # line 237: merges 'group_by' + 'grouping' (str split on ','), else definition.grouping

# From querysource/models.py
class QueryObject(ClassDict):        # line 24
    fields: list                     # line 31
    group_by: Optional[list]         # line 33
class QueryModel(Model):             # line 48
    attributes: Optional[dict]       # line 54 (jsonb)
    fields: List[str]                # line 64
    filtering: Optional[dict]        # line 65
    grouping: List[str]              # line 67

# From rust/src/sql_parser.rs
pub fn group_by(sql: &str, grouping: Vec<String>) -> String      # line 388
pub fn process_fields(...)                                       # line 476
# From rust/src/pgsql_parser.rs
fn jsonb_any_of_condition(...)   # line 219
fn jsonb_condition(key: &str, dict: &Bound<'_, PyDict>) -> JsonbOutcome  # line 290
pub fn pgsql_filter_conditions(...)                              # line 625

# From querysource/queries/multi/operators/GroupBy.py (Option C only)
class GroupBy(AbstractOperator):                 # line 32
    @classmethod aggregation_functions(cls) -> list[str]   # line 68
```

#### Verified Imports
```python
from querysource.parsers import pgsql            # used by tests/test_pgsql_jsonb_filters.py
from querysource.models import QueryObject       # tests/test_pgsql_jsonb_filters.py
from querysource.queries.multi.operators.GroupBy import GroupBy  # tests/test_catalog_groupby.py
```

#### Key Attributes & Constants
- `pgsql.HAS_RUST` → `bool` (pgsql.pyx:21-23), used to parametrise Rust/Cython tests
- `AbstractParser.fields`, `.grouping`, `.ordering`, `.filter`, `.attributes`,
  `.cond_definition` (abstract.pyx:69-94 `set_attributes`)
- `QueryModel.attributes` already read into `self.attributes`
  (abstract.pyx:104), e.g. `safe_substitution` (line 107). This is the
  precedent for slug-level parser options.
- Test helper `_render(path, filter_, query=SQL)` (tests/test_pgsql_jsonb_filters.py:42)

### Does NOT Exist (Anti-Hallucination)
- ~~Any `jsonb_array_elements` / `LATERAL` / `jsonb_to_recordset` usage in `querysource/`~~.
  A grep over `.py` / `.pyx` found none.
- ~~Identifier sanitisation of `fields` or `group_by` in the parsers~~. Only
  filter keys are checked (FEAT-103). The request-side `fields` allowlist in
  `querysource/handlers/_pagination.py` applies to the catalog/manager
  endpoints, not to slug queries.
- ~~Aggregate-function support in `SQLParser` / `pgSQLParser`~~. `fields`
  strings are passed through verbatim; no aggregate allowlist exists at the
  parser level.
- ~~`HAVING` handling in the parsers~~. `group_by` only looks for `HAVING` as a
  terminator keyword; there is no condition that produces one.
- ~~Group or aggregate capability in qsurl~~. `querysource/qsurl/capabilities.py`
  lists `SELECT, ALIAS, FILTER, OR, NOT, IN_LIST, NULL_CHECK, TEXT_MATCH,
  REGEX, FUNCTIONS, NAVIGATION, SORT, LIMIT, OFFSET, DISTINCT`; there is no
  grouping.

---

## Parallelism Assessment

- **Internal parallelism**: Limited. The path grammar/validator, the
  element-filter rendering and the slug-declaration parsing can be written in
  parallel as pure units. All of them converge on `pgSQLParser.build_query`
  in `pgsql.pyx`, and the outer-query rendering depends on the grammar. An
  optional Rust port would be a separable follow-up task.
- **Cross-feature independence**: `pgsql.pyx` and `rust/src/pgsql_parser.rs`
  were just touched by the JSONB filter work (`8936386`). The qsurl feature
  (FEAT-152, merged) also routes through `pgsql.pyx` text operators. No known
  in-flight spec edits these files; check `/sdd-status` before starting.
- **Recommended isolation**: `per-spec`.
- **Rationale**: Every task lands in the same Cython module and build step.
  Sequential tasks in one worktree avoid rebuild and merge churn on
  `pgsql.pyx`.

---

## Open Questions

- [x] Flow type / base branch — *Owner: Jesus Lara*: feature → dev
- [x] Declaration point — *Owner: Jesus Lara*: both request conditions and slug definition (slug may allowlist)
- [x] v1 aggregate scope — *Owner: Jesus Lara*: count / count distinct, min/max/sum/avg, time bucketing, element-level filtering
- [x] Result shape — *Owner: Jesus Lara*: flat rows
- [x] Filter scope — *Owner: Jesus Lara*: explicit per filter (row filters unchanged; path-keyed filters are element-level)
- [x] Syntax — *Owner: Jesus Lara*: path strings (`col[].key`)
- [x] Slug shapes — *Owner: Jesus Lara*: any pg slug, via subquery wrap
- [x] Empty/NULL arrays — *Owner: Jesus Lara*: excluded by default, opt-in NULL group
- [ ] Invalid path tokens: raise a parser error (recommended; they are explicit opt-in syntax) or drop silently like invalid JSONB filters? — *Owner: Jesus Lara*
- [ ] Is `HAVING` (e.g. `having: {"graduates": {">": 5}}`) in v1 or a follow-up? — *Owner: Jesus Lara*
- [ ] Time-bucket syntax: `year(path)` / `month(path)` helpers (recommended) or `date_trunc('month', path)` passthrough? — *Owner: Jesus Lara*
- [ ] Cast failures on dirty data: fail the query (default) or offer safe-cast (`CASE WHEN … ~ regex THEN …::date END`)? — *Owner: Jesus Lara*
- [ ] Multiple distinct array columns in one query: reject in v1 (recommended) or allow the cross product with a slug opt-in? — *Owner: Jesus Lara*
- [ ] Rust parity: Cython-only planner (Rust fast paths only receive stripped inputs), or also port the planner to `rust/src/pgsql_parser.rs`? — *Owner: Jesus Lara*
- [ ] Should non-strict slugs still restrict request paths to columns the slug exposes (e.g. `columns_definition`), or is grammar validation enough? — *Owner: Jesus Lara*
- [ ] Minimum PostgreSQL version in deployments (matters only if Option D's jsonpath is revisited). — *Owner: Jesus Lara*
