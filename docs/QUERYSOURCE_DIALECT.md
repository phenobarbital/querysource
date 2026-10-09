# The QuerySource JSON Dialect

A complete guide to the JSON request dialect that QuerySource uses to shape a
stored query ("slug"): projecting fields, filtering rows, partial matching,
JSONB filtering, grouping, ordering, paging, calling functions, and choosing the
output format.

> **Scope.** This guide covers the *JSON* dialect (the request body / conditions
> dict). For the compact URL dialect see [QSURL.md](QSURL.md). Detailed
> references that this guide summarises are linked at the end of each chapter.

---

## Table of contents

1. [Introduction](#1-introduction)
2. [Anatomy of a request](#2-anatomy-of-a-request)
3. [Query templates and placeholders](#3-query-templates-and-placeholders)
4. [Projection: `fields`, `add_fields`, `distinct`](#4-projection-fields-add_fields-distinct)
5. [Filtering: the `filter` grammar](#5-filtering-the-filter-grammar)
6. [Partial matching (LIKE / ILIKE / regex)](#6-partial-matching-like--ilike--regex)
7. [JSONB filtering (PostgreSQL)](#7-jsonb-filtering-postgresql)
8. [Typed columns: arrays and ranges](#8-typed-columns-arrays-and-ranges)
9. [Grouping](#9-grouping)
10. [Ordering](#10-ordering)
11. [Limits, offsets and pagination](#11-limits-offsets-and-pagination)
12. [Aggregating inside JSONB arrays](#12-aggregating-inside-jsonb-arrays)
13. [Function calling](#13-function-calling)
14. [Output formats and writer options](#14-output-formats-and-writer-options)
15. [Dialect support by provider](#15-dialect-support-by-provider)
16. [Validation, safety and errors](#16-validation-safety-and-errors)
17. [Cookbook](#17-cookbook)
18. [Quick reference](#18-quick-reference)

---

## 1. Introduction

A **slug** is a named, stored query (`QueryModel`): a query template
(`query_raw`), a provider (`pg`, `bigquery`, `mongo`, ...), optional default
`fields`, `filtering`, `grouping`, `ordering`, a `conditions` dict of default
placeholder values, and a `cond_definition` dict that types those placeholders.

A client never sends SQL. It sends a **JSON object of conditions**. QuerySource
hands that object to the provider's **parser** (`querysource/parsers/`, Cython
with a Rust fast path in `rust/src/`), which:

1. separates *options* (`fields`, `filter`, `ordering`, ...) from *placeholder
   values* and from *ad-hoc filters*;
2. validates and quotes every value;
3. renders the final query from the slug template.

### Where the dialect is accepted

| Entry point | How the conditions arrive |
|---|---|
| `POST /api/v2/services/queries/{slug}` | JSON request body |
| `GET /api/v2/services/queries/{slug}` | query-string parameters |
| `POST /api/v1/queries/run`, `/api/v1/queries/test` | JSON body (ad-hoc / dry-run) |
| Python: `QS(slug=..., conditions={...})` | the `conditions` dict |
| `MultiQuery` | each query's `conditions` block |

For HTTP calls the JSON body and the query string are merged, and **query-string
parameters win** over body keys with the same name:

```text
conditions = {**json_body, **query_string_params}
```

### A first example

```http
POST /api/v2/services/queries/store_visits
Content-Type: application/json

{
  "fields": ["store_id", "store_name", "visits"],
  "filter": {
    "region": "West",
    "visits": {">": 10},
    "store_name": {"icontains": "market"}
  },
  "ordering": ["visits DESC"],
  "querylimit": 50
}
```

For a slug whose template is `SELECT * FROM public.store_visits {where_cond}`,
the PostgreSQL parser renders (filter values are always rendered as literals;
PostgreSQL coerces `'10'` to the column type):

```sql
SELECT store_id, store_name, visits FROM public.store_visits
 WHERE region='West' AND visits > '10' AND store_name ILIKE '%market%'
 ORDER BY visits DESC LIMIT 50
```

---

## 2. Anatomy of a request

Every top-level key of the request falls into exactly one of three categories.

### 2.1 Option keys

Option keys are reserved names that control the shape of the query. They are
removed from the conditions before anything else is processed.

| Key | Alias | Type | Meaning |
|---|---|---|---|
| `fields` | — | `list[str]` or `str` | Columns/expressions to project. Replaces the slug's stored fields. |
| `add_fields` | — | `bool` | When `true`, `fields` are *appended* to the template's projection instead of replacing it. |
| `filter` | `where_cond` | `dict` | Row filters (`WHERE`). See chapter 5. `where_cond` takes precedence when both are sent. |
| `filter_options` | — | `dict` | Extra filters merged over `filter`. |
| `grouping` | `group_by` | `list[str]` or comma-separated `str` | `GROUP BY` columns. Both keys are concatenated. |
| `ordering` | `order_by` | `list[str]` or comma-separated `str` | `ORDER BY` items. Both keys are concatenated. |
| `having` | — | `dict` | `HAVING` conditions (PostgreSQL JSONB aggregation plan, chapter 12). |
| `querylimit` | `_limit` | `int` | Row limit (`LIMIT`). `_limit` wins when both are sent. |
| `_offset` | — | `int` | Row offset (`OFFSET`). |
| `paged`, `page` | — | `bool`, `int` | Request page-based pagination (chapter 11). |
| `distinct` | — | `bool` | `SELECT DISTINCT` (provider dependent, see chapter 15). |
| `refresh` | — | `bool` | Bypass the QuerySource cache for this call. |
| `conditions` | — | `dict` | Nested placeholder values, merged *over* the flat ones. |
| `cond_definition` | — | `dict` | Extra placeholder type declarations (merged with the slug's). |
| `hierarchy` | — | `list` | Hierarchy used by query filter functions (chapter 13.4). |
| `qry_options` | — | `dict` | Provider-specific options. |
| `tablename`, `schema`, `database` | — | `str` | Override the target object (provider specific; rarely needed). |

Flags such as `refresh` and `paged` accept booleans and the usual textual forms
(`true/false`, `yes/no`, `1/0`, `on/off`).

### 2.2 Placeholder values

A slug template may contain named placeholders such as `{firstdate}` or
`{store_id}`. Any request key that is **declared in `cond_definition`** is a
placeholder value: it is validated and converted according to its declared type
and substituted into the template. It never becomes a `WHERE` filter.

Values are merged in this order (later wins):

```text
slug.conditions (defaults)  <  flat request keys  <  request["conditions"]
```

```json
{
  "firstdate": "2025-01-01",
  "conditions": {"lastdate": "2025-01-31"}
}
```

### 2.3 Ad-hoc filters

**Any key that is neither an option key nor a declared placeholder becomes a
`WHERE` filter.** These two requests are therefore equivalent:

```json
{"filter": {"region": "West"}}
```

```json
{"region": "West"}
```

Prefer the explicit `filter` form: it is unambiguous and survives a later change
to the slug's `cond_definition`.

---

## 3. Query templates and placeholders

The slug's `query_raw` is a template. The parser fills these structural
placeholders; any unused one is replaced by an empty string.

| Placeholder | Filled with |
|---|---|
| `{fields}` | the projection (`fields`), or `*` |
| `{schema}`, `{table}` | `schema` / `tablename` |
| `{where_cond}` | ` WHERE <filters>` |
| `{filter}` | ` WHERE <filters>` |
| `{and_cond}` | ` AND <filters>` — for templates that already have a `WHERE` |
| `{grouping}` | `GROUP BY ...` |
| `{limit}`, `{offset}` | `LIMIT n`, `OFFSET n` |
| `{<name>}` | the value of a placeholder declared in `cond_definition` |

```sql
-- typical templates
SELECT * FROM public.stores {where_cond}
SELECT {fields} FROM {schema}.{table} {filter} {grouping} {offset} {limit}
SELECT * FROM visits WHERE visit_date BETWEEN {firstdate} AND {lastdate} {and_cond}
```

If a template has **no** filter placeholder, filters are appended: with
` AND ...` when the template already contains `WHERE`, otherwise with
` WHERE ...`. Projection, grouping and ordering are also spliced in when the
template has no placeholder for them:

- `fields` replaces a `SELECT * FROM` projection;
- `grouping` extends an existing outer `GROUP BY`, or inserts one before an
  outer `ORDER BY` / `LIMIT` / `OFFSET`;
- `ordering` extends an existing outer `ORDER BY` (as tie-breakers), or appends
  one. `GROUP BY` / `ORDER BY` nested inside subqueries, CTEs or window clauses
  are never touched.

### 3.1 Placeholder types (`cond_definition`)

`cond_definition` maps a placeholder name to a type. The type drives validation
and quoting of the value:

| Type | Accepts | Rendered as |
|---|---|---|
| `string`, `varchar`, `field` | string | quoted string |
| `int`, `integer`, `float`, `numeric`, `decimal` | number | bare number |
| `boolean` | boolean | `true` / `false` |
| `date`, `datetime`, `timestamp` | date text, or a date keyword (chapter 13) | quoted date |
| `epoch` | epoch number | converted date |
| `uuid` | UUID text | UUID |
| `array`, `json` | list / dict | unquoted |
| `udf` | a date keyword (`TODAY`, `FDOM`, ...) | the function result |
| `literal` | anything | escaped verbatim |

```json
{
  "query_raw": "SELECT * FROM sales WHERE sale_date BETWEEN {firstdate} AND {lastdate} {and_cond}",
  "cond_definition": {"firstdate": "date", "lastdate": "date"},
  "conditions": {"firstdate": "FDOM", "lastdate": "LDOM"}
}
```

---

## 4. Projection: `fields`, `add_fields`, `distinct`

### 4.1 `fields`

`fields` is a list of columns or SQL expressions. It **replaces** the slug's
default fields (or the template's `*`).

```json
{"fields": ["store_id", "store_name", "count(*) as visits"]}
```

```sql
SELECT store_id, store_name, count(*) as visits FROM ...
```

A comma-separated string is also accepted: `"fields": "store_id,store_name"`.

### 4.2 `add_fields`

With `"add_fields": true`, the requested fields are appended to the template's
existing projection:

```json
{"fields": ["region"], "add_fields": true}
```

```sql
-- template: SELECT store_id, store_name FROM stores
SELECT store_id, store_name, region FROM stores
```

`add_fields` cannot be combined with the JSONB aggregation plan (chapter 12).

### 4.3 `distinct`

`"distinct": true` requests `SELECT DISTINCT`. Support depends on the provider
(see chapter 15); on SQL providers prefer an explicit `grouping`, or put
`DISTINCT` in the slug template.

---

## 5. Filtering: the `filter` grammar

`filter` (alias `where_cond`) is a dict of `column → condition`. All entries are
combined with **AND**. The *shape* of the value, plus an optional **suffix** on
the key, selects the operator.

### 5.1 Equality, inequality and NULL

| Request | SQL |
|---|---|
| `{"status": "active"}` | `status='active'` |
| `{"store_id": 42}` | `store_id='42'` (numbers are quoted; the database coerces them) |
| `{"is_active": true}` | `is_active = true` |
| `{"status": "!active"}` | `status != 'active'` (leading `!` on the value) |
| `{"status!": "active"}` | `status != 'active'` (trailing `!` on the key) |
| `{"closed_at": "null"}` | `closed_at IS NULL` |
| `{"closed_at": "!null"}` | `closed_at IS NOT NULL` |

`null` / `NULL` and `!null` / `!NULL` are the only spellings for NULL tests.

### 5.2 Lists: `IN` / `NOT IN`

| Request | SQL |
|---|---|
| `{"store_id": [1, 2, 3]}` | `store_id IN ('1','2','3')` |
| `{"store_id!": [4, 5]}` | `store_id NOT IN ('4','5')` |

### 5.3 Comparisons

There are two equivalent forms.

**Dict form** — a single operator key:

```json
{"qty": {">": 0}, "price": {"<=": 99.9}}
```

```sql
qty > '0' AND price <= '99.9'
```

Allowed operators: `>=`, `<=`, `<>`, `!=`, `<`, `>`. Values are quoted
literals; the database coerces them to the column type.

**List form** — the first item is the operator:

```json
{"qty": [">=", 10], "deleted_at": ["IS", "null"]}
```

```sql
qty >= '10' AND deleted_at IS null
```

Allowed operators: `<`, `>`, `>=`, `<=`, `<>`, `!=`, `IS`, `IS NOT`. A list whose
first item is not one of these is an `IN` list.

> A dict holds **one** comparison per column: if it carries several operators
> only the **last** one is kept (`{">": 1, "<": 9}` renders `x < '9'`). To
> express a range, use a pair of typed placeholders in the slug template (see
> 3.1), or the JSONB-aggregation element filters, which accept several
> operators.
>
> On PostgreSQL, a dict whose key is not a recognised operator is treated as
> implicit JSONB containment (chapter 7), not as an error.

### 5.4 `BETWEEN` (known issue)

The parser has a branch that renders a string value containing `BETWEEN` as a
range predicate (`{"amount": "BETWEEN 100 AND 500"}` → `(amount BETWEEN 100 AND 500)`)
and drops clauses containing `;`, `--`, `/*`, `UNION` or `SELECT`.

> **Currently broken.** Value preprocessing quotes the whole string before the
> filter builder sees it, so the request above renders invalid SQL:
> `(amount 'BETWEEN 100 AND 500')`. Do not use the `BETWEEN` value form until
> this is fixed.

For ranges, use a pair of typed placeholders in the slug template
(`WHERE sale_date BETWEEN {firstdate} AND {lastdate}`, see 3.1).

### 5.5 Key suffixes

A filter key may carry a trailing suffix that changes the operator. The suffix
is stripped from the column name.

| Suffix | Meaning |
|---|---|
| `!` | negation: `!=`, `NOT IN`; `<>` / `NOT IN` / `IS NOT NULL` on JSONB element paths (chapter 12) |

Keys must be identifier-safe: after stripping the suffix characters
(`| ! ~ # @ :`) only letters, digits, `_` and `.` may remain. A dotted key
(`t.store_id`) addresses a qualified column. A purely numeric key is
double-quoted (`"2024"`).

### 5.6 `filter_options`

`filter_options` is merged *over* `filter`, so it can add or override entries.
It is useful when a client keeps a base filter and layers user selections on
top:

```json
{
  "filter": {"region": "West"},
  "filter_options": {"store_type": "outlet"}
}
```

### 5.7 Slug defaults

When the request sends no `filter`/`where_cond`, the slug's stored `filtering`
is used. A request-level filter **replaces** the slug's default filter; it is
not merged with it.

---

## 6. Partial matching (LIKE / ILIKE / regex)

Partial matching uses the dict form with a **named operator** as the single key:

```json
{"filter": {"full_name": {"istartswith": "andre"}}}
```

```sql
full_name ILIKE 'andre%'
```

### 6.1 Operators

| Operator | Matches | Case | Operand |
|---|---|---|---|
| `like` / `not_like` | a ready `LIKE` pattern | sensitive | raw pattern (you write the `%` / `_`) |
| `ilike` / `not_ilike` | a ready `LIKE` pattern | insensitive | raw pattern |
| `startswith` / `not_startswith` | prefix | sensitive | escaped, `%` appended |
| `istartswith` / `not_istartswith` | prefix | insensitive | escaped, `%` appended |
| `endswith` / `not_endswith` | suffix | sensitive | escaped, `%` prepended |
| `iendswith` / `not_iendswith` | suffix | insensitive | escaped, `%` prepended |
| `contains` / `not_contains` | substring | sensitive | escaped, wrapped in `%`; **minimum 3 characters** |
| `icontains` / `not_icontains` | substring | insensitive | escaped, wrapped in `%`; **minimum 3 characters** |
| `regex` / `not_regex` | regular expression | sensitive | PostgreSQL only |
| `iregex` / `not_iregex` | regular expression | insensitive | PostgreSQL only |

For the `startswith` / `endswith` / `contains` families the operand is a plain
value: any `%`, `_` or escape character in it is escaped, so it matches
literally. Use `like` / `ilike` when you want to write the wildcards yourself.

### 6.2 Examples (PostgreSQL)

| Request | SQL |
|---|---|
| `{"n": {"like": "A_%"}}` | `n LIKE 'A_%'` |
| `{"n": {"startswith": "50%"}}` | `n LIKE '50\%%'` |
| `{"n": {"iendswith": ".com"}}` | `n ILIKE '%.com'` |
| `{"n": {"icontains": "pilates"}}` | `n ILIKE '%pilates%'` |
| `{"n": {"not_icontains": "test"}}` | `n NOT ILIKE '%test%'` |
| `{"sku": {"regex": "^AB[0-9]+$"}}` | `sku ~ '^AB[0-9]+$'` |
| `{"sku": {"not_iregex": "^tmp"}}` | `sku !~* '^tmp'` |

On generic SQL / SQL Server, case-insensitive operators fold with `LOWER()` and
escaped patterns use `ESCAPE '!'`
(`LOWER(n) LIKE LOWER('%pilates%')`). BigQuery uses `LOWER()` folding with
backslash escaping.

### 6.3 Rules

- **One operator per field**: a partial-matching operator cannot share its dict
  with another key.
- The operand must be a string.
- Regex patterns: non-empty, at most 200 characters, no nested quantifiers
  (e.g. `(a+)+`); only PostgreSQL supports them.
- The operator names are **reserved**. On PostgreSQL a dict whose single key is
  one of them is always a partial match, never implicit JSONB containment (see
  7.1). To filter a JSON key with a colliding name use `->>` or `@>` explicitly.

Invalid partial matches raise `ParserError` (HTTP 400). Full rendering tables
per dialect: [FILTER_OPERATORS.md](FILTER_OPERATORS.md).

---

## 7. JSONB filtering (PostgreSQL)

On PostgreSQL, a dict value that is **not** a comparison or a partial-matching
operator is treated as a JSONB filter on a `jsonb` column. JSON operands are
serialised safely (`'...'::jsonb` literals; braces are escaped as `E'\x7b...'`
so later template passes cannot break them).

### 7.1 Containment: always use explicit `@>`

Send the object to match as the operand of `@>`:

```json
{"filter": {"attributes": {"@>": {"status": "active", "tier": "gold"}}}}
```

```sql
attributes @> E'\x7b"status":"active","tier":"gold"\x7d'::jsonb
```

(`\x7b` / `\x7d` are the escaped `{` / `}`; PostgreSQL reads the literal as
`'{"status":"active","tier":"gold"}'`.)

> **Implicit containment is unreliable.** The parser also treats a dict with
> plain keys (`{"attributes": {"status": "active"}}`) as containment, but value
> preprocessing keeps only the **last** key of the dict and wraps string values
> in extra quotes, so the request above matches the JSON string `"'active'"`
> instead of `"active"`. Only a single key with a non-string value works
> (`{"attributes": {"level": 5}}` → `attributes @> '{"level":5}'::jsonb`).
> Always use the explicit `@>` form.

### 7.2 JSONB operators

| Operator | Operand | Meaning | SQL |
|---|---|---|---|
| `@>` | dict / list / scalar / JSON text | column contains operand | `col @> '<json>'::jsonb` |
| `<@` | dict / list / scalar / JSON text | column is contained by operand | `col <@ '<json>'::jsonb` |
| `@>\|` | list of operands | **any of**: contains at least one | `(col @> a OR col @> b ...)` |
| `@!` | list of operands | **none of**: contains none | `NOT (col @> a OR col @> b ...)` |
| `@$` | list of operands | **not all**: lacks at least one | `((NOT col @> a) OR (NOT col @> b) ...)` |
| `->>` | `{key: value, ...}` | compare the *text* of top-level keys | `col ->> 'key' = 'value'` |
| `->` | `{key: value, ...}` | compare the *JSONB value* of keys | `col -> 'key' = '<json>'::jsonb` |

Notes:

- `@>` with a list operand is **conjunctive** (the array must contain every
  element). Use `@>|` for "any of".
- In `->>` / `->`, a `null` value renders `IS NULL`; several keys are AND-ed.
  `->>` compares text, so non-string values are compared by their JSON text
  (`true`, `1`).
- Several JSONB operators in one dict are AND-ed.
- A dict that mixes JSONB operators with plain keys, or with comparison
  operators, is **dropped**, as is any operand that cannot be rendered. Validate
  operands on the client side.
- Operands of `@>`, `<@`, `@>|`, `@!`, `@$`, `->>` and `->` are lists or
  dicts, so they reach the builder intact: string values inside them are
  serialised correctly.

### 7.3 Examples

```json
{
  "filter": {
    "tags":    {"@>": ["vip"]},
    "courses": {"@>|": [{"course": "Pilates Studio"}, {"course": "Pilates Mat"}]},
    "blocked": {"@!": [{"reason": "fraud"}]},
    "meta":    {"->>": {"color": "red", "size": null}},
    "config":  {"->": {"retries": 5}}
  }
}
```

```sql
tags @> '["vip"]'::jsonb
AND (courses @> E'\x7b"course":"Pilates Studio"\x7d'::jsonb OR courses @> E'\x7b"course":"Pilates Mat"\x7d'::jsonb)
AND NOT (blocked @> E'\x7b"reason":"fraud"\x7d'::jsonb)
AND (meta ->> 'color' = 'red' AND meta ->> 'size' IS NULL)
AND config -> 'retries' = '5'::jsonb
```

Filtering *rows* by a JSONB array keeps every element of each matching row. To
filter, group and count the **elements** of a JSONB array, use the aggregation
plan in chapter 12.

On **BigQuery**, a dotted key on a JSON column is extracted with
`JSON_VALUE(col, '$.path')` instead.

---

## 8. Typed columns: arrays and ranges

The PostgreSQL filter builder has special renderings for columns whose
`cond_definition` type is `array`, `numrange`, `int4range` / `int8range`,
`tsrange` / `tstzrange` or `daterange` (for example `'vip'::character varying = ANY(tags)`,
`12.5::numeric <@ price_band`, or the `|` key suffix for array overlap `&&`).

> **Not reachable from a request today.** Every key declared in
> `cond_definition` is classified as a *placeholder value* (2.2) before filters
> are built, so these renderings are never used:
>
> - a `numrange` / `daterange` / `date` key is consumed as a placeholder; if the
>   template has no `{name}` placeholder, the condition silently disappears;
> - an `array` key with a string value raises `ValueError` and the request
>   fails.
>
> Until this is fixed, filter array columns through the slug template
> (e.g. `WHERE {tag} = ANY(tags)` with a typed placeholder), or store the data
> as `jsonb` and use the JSONB operators of chapter 7.

---

## 9. Grouping

`grouping` (alias `group_by`) adds a `GROUP BY` clause. Combine it with
aggregate expressions in `fields`:

```json
{
  "fields": ["region", "store_type", "count(*) as stores", "sum(revenue) as revenue"],
  "grouping": ["region", "store_type"]
}
```

```sql
SELECT region, store_type, count(*) as stores, sum(revenue) as revenue
  FROM stores GROUP BY region, store_type
```

- Accepts a list or a comma-separated string. `group_by` and `grouping` are
  concatenated when both are sent.
- If the template already has an outer `GROUP BY`, the requested columns are
  appended to it.
- When no grouping is requested, the slug's stored `grouping` (if any) is used.
- `having` is available in the PostgreSQL JSONB aggregation plan (chapter 12);
  for other queries, put the `HAVING` clause in the slug template.

---

## 10. Ordering

`ordering` (alias `order_by`) adds an `ORDER BY` clause. Each item is a column
or expression with an optional direction:

```json
{"ordering": ["visits DESC", "store_name"]}
```

```sql
ORDER BY visits DESC, store_name
```

- Use `"col DESC"`. The Django-style `"-col"` prefix is **not** supported: it
  is rendered verbatim (`ORDER BY -created_at`), which negates the value instead
  of reversing the order, and fails on non-numeric columns.
- `NULLS FIRST` / `NULLS LAST` are accepted.
- Accepts a list or a comma-separated string; `order_by` and `ordering` are
  concatenated.
- If the template already has an outer `ORDER BY`, the requested items are
  appended as tie-breakers.
- When no ordering is requested, the slug's stored `ordering` is used.

---

## 11. Limits, offsets and pagination

| Key | Effect |
|---|---|
| `querylimit` (alias `_limit`) | `LIMIT n` |
| `_offset` | `OFFSET n` |
| `paged`, `page` | page-based pagination |

```json
{"ordering": ["created_at DESC"], "querylimit": 25, "_offset": 50}
```

```sql
... ORDER BY created_at DESC LIMIT 25 OFFSET 50
```

The template may place the clauses with `{limit}` / `{offset}`; otherwise they
are appended. Always combine paging with a deterministic `ordering`. Deployments
may enforce a maximum number of rows regardless of `querylimit`.

---

## 12. Aggregating inside JSONB arrays

*PostgreSQL only.* QuerySource can unnest a JSONB **array of objects** and
group, aggregate and filter its elements, returning flat rows. It is activated
by **path tokens** of the form `column[].key[.key…][::cast]` in `fields`,
`grouping`, `ordering` or a `filter` key, or by a non-empty `having`.

```json
{
  "fields": ["graduation_details[].course",
             "count(distinct student_uid) as graduates"],
  "group_by": ["graduation_details[].course"],
  "filter": {
    "licensee": "Asia",
    "graduation_details[].course_date::date": {">=": "2025-01-01"}
  },
  "having": {"graduates": {">": 5}},
  "ordering": ["graduates DESC"],
  "querylimit": 10
}
```

The main elements of the dialect:

- **Paths**: `a[].k`, nested `a[].k1.k2`, with optional casts `text`, `int`,
  `integer`, `bigint`, `numeric`, `float`, `date`, `timestamp`, `timestamptz`,
  `boolean`.
- **Aggregates**: `count(*)`, `count(x)`, `count(distinct x)`, `min`, `max`,
  `sum`, `avg`.
- **Time buckets**: `year(p)`, `quarter(p)`, `month(p)`, `week(p)`, `day(p)`.
- **Aliases**: `expr as name`; outputs are always double-quoted aliases.
- **Element filters** (path keys) filter individual elements; ordinary keys
  remain row filters. Element filters accept `=`, lists (`IN`), `!`, `null`, and
  a dict with several operators (`{">=": 3, "<": 9}`, AND-ed).
- **`having`**: `{alias_or_aggregate: value | {op: value}}`, operators
  `= >= <= <> != < >`.
- **Slug declaration** under `attributes.jsonb_unnest` (`columns`, `aliases`,
  `strict`, `safe_cast`).

Only one distinct array column per query is supported. The complete reference,
including rendered SQL, the counting pitfall, `safe_cast` and errors, is in
[JSONB_AGGREGATION.md](JSONB_AGGREGATION.md).

---

## 13. Function calling

The dialect can call functions in three places: **as a value** (date keywords
and named functions), **as an `@variable`** registered by the deployment, and
**as a query filter function** that rewrites the filter itself.

### 13.1 Value functions (date keywords)

A string value that names a built-in function from
`querysource/utils/functions.pyx` is **replaced by the result of calling that
function with no arguments**. The lookup is case-insensitive. The
officially-supported relative-date keywords are:

| Keyword | Result |
|---|---|
| `TODAY` | today's date, formatted **`MM/DD/YYYY`** (e.g. `10/09/2026`) |
| `YESTERDAY` | yesterday's date (`YYYY-MM-DD`) |
| `CURRENT_YEAR` | current year (e.g. `2026`) |
| `CURRENT_MONTH` | current month number |
| `LAST_YEAR` | the same date one year ago |
| `FDOM` | first day of the current month (`YYYY-MM-DD`) |
| `LDOM` | last day of the current month (`YYYY-MM-DD`) |

The list can be changed per deployment with the `UDF_LIST` environment variable
(comma separated). Other zero-argument helpers of the same module can be named
the same way, for example `fdow` / `ldow` (Monday / Sunday of the current
week, `YYYY-MM-DD`) or `ldopm` (last day of the previous month).

```json
{"filter": {"sale_date": {">=": "FDOM"}}, "firstdate": "FDOM", "lastdate": "LDOM"}
```

```sql
-- filter part
sale_date >= '2026-10-01'
```

Note the different formats: `FDOM`, `LDOM`, `YESTERDAY` return `YYYY-MM-DD`
while `TODAY` returns `MM/DD/YYYY`; use `TODAY` only where the column or the
placeholder accepts that format. Date keywords work for filters and for placeholders typed `date`, `datetime`,
`timestamp` or `udf`.

### 13.2 Database constants and functions

| Value | Treatment |
|---|---|
| `CURRENT_DATE`, `CURRENT_TIMESTAMP` | recognised as PostgreSQL constants (list overridable with `PG_CONSTANTS`) |
| `now()` | recognised as a PostgreSQL function (allowlist overridable with `PG_UDF`) |

These are intended for **placeholder** values declared in the slug template:
with `WHERE created_at >= {since}` and `"since": "CURRENT_DATE"` (typed `date`)
the parser renders `WHERE created_at >= CURRENT_DATE`. In a `filter` they are
rendered as quoted literals (`d='CURRENT_DATE'`, `d='now()'`), so the
database receives the text, not a function call — use a date keyword (13.1)
instead. JSONB
element filters (chapter 12) never evaluate SQL functions.

### 13.3 `@variables`

A value that starts with `@` calls a **deployment variable function**:

```json
{"filter": {"visit_date": "@yesterday"}, "lastdate": "@today"}
```

The available `@` names are registered in the deployment's settings
(`QUERYSOURCE_VARIABLES`, a map of name → dotted import path). Each function is
called as `fn(key, value)` and its return value replaces the `@name` value (it
is then validated and quoted as usual). An unregistered `@name` makes the
condition **silently disappear** from the query (no error), so call `GET /api/v1/queries/vocabulary` or the agent dialect reference to list
the names your deployment accepts.

### 13.4 Query filter functions

A deployment can also register **filter functions** in `QUERYSOURCE_FILTERS`
(name → dotted import path). When a request contains a key with that name, the
key is removed from the conditions and the function runs (in a worker thread)
as:

```python
where, ordering = fn(<request value>, where=<current filter>, program=<program slug>, hierarchy=<hierarchy>)
```

It returns a dict merged into the filter and an optional list of ordering items
appended to `ordering`. QuerySource ships `querysource.libs.functions.query_options`,
which navigates an organisational hierarchy (for example
`territory_id → region_id → district_id → market_id → store_id`; override it with
the `hierarchy` option):

| Option | Effect |
|---|---|
| `null_rolldown` | sets every level below the deepest filtered level to `IS NULL` |
| `select_child` | the next level `IS NOT NULL`, deeper levels `IS NULL` |
| `select_children` | as `select_child`, and orders by the child (and grandchild) `DESC` |
| `select_stores` | the last level `IS NOT NULL` |

```json
{
  "query_options": {"select_child": true},
  "hierarchy": ["region_id", "district_id", "store_id"],
  "filter": {"region_id": 3}
}
```

```sql
WHERE region_id='3' AND district_id IS NOT NULL AND store_id IS NULL
```

(assuming the deployment registers `query_options` under that name).

---

## 14. Output formats and writer options

The output format is chosen by a suffix on the slug or by a query parameter:

```text
POST /api/v2/services/queries/store_visits:csv
GET  /api/v2/services/queries/store_visits?queryformat=xlsx
```

| Key | Where | Meaning |
|---|---|---|
| `:<format>` / `queryformat` | URL | output format (`json`, `csv`, `xlsx`, `html`, ... depending on the installed writers) |
| `queryformat=<format>=<template>` | URL | render through a template; options in `_report_options` |
| `_download` | query string | serve as a downloadable attachment |
| `_filename` | query string | file name for the download |
| `_csv_options` | body | CSV writer options (`delimiter`, `quoting`, ...) |
| `_output_options` | body | options for the other writers |
| `_graph_options` | body | options for graph writers |

These keys are consumed by the handler and never reach the parser.

---

## 15. Dialect support by provider

The core options (`fields`, `filter`, `ordering`, `grouping`, `querylimit`,
`_offset`, placeholders) are understood by every parser. Specific features
depend on the target:

| Feature | PostgreSQL | Generic SQL | SQL Server | BigQuery | Others (Mongo, Elastic, Arango, Rethink, Cassandra, Influx, ...) |
|---|---|---|---|---|---|
| Equality / IN / comparisons / NULL | yes | yes | yes | yes | translated to the native query language where supported |
| Partial matching (`*like`, `*startswith`, ...) | yes | yes (`LOWER()`, `ESCAPE '!'`) | yes | yes | no |
| Regex operators | yes | no (`ParserError`) | no | no | no |
| JSONB operators (`@>`, `@>\|`, `->>`, ...) | yes | no | no | `JSON_VALUE` extraction | no |
| Typed arrays / ranges (chapter 8) | not reachable (see 8) | no | no | no | no |
| JSONB array aggregation, `having` | yes | no | no | no | no |
| `distinct` option | template | template | template | template | RethinkDB |

Slugs flagged `is_raw` bypass the parser entirely: none of the options in this
guide apply to them.

---

## 16. Validation, safety and errors

QuerySource never concatenates user input into SQL without validation:

- **Keys** must be identifier-safe (`[A-Za-z0-9_.]` after stripping suffix
  characters). Unsafe keys are **silently dropped** by the parser — validate on
  the client (the agent toolkit rejects them up front).
- **Operators** are allow-listed per form (comparison, list, JSONB,
  partial-matching). Unknown comparison operators are dropped; unknown
  partial-matching operators raise `ParserError`.
- **Values** are quoted for the target dialect (`'` doubled; PostgreSQL uses
  escape-string literals when needed).
- **`BETWEEN`** clauses with `;`, `--`, `/*`, `UNION` or `SELECT` are dropped
  (but see the known issue in 5.4).
- **Regex** patterns are length-limited and checked for catastrophic nested
  quantifiers.
- **JSONB** operands are serialised with `orjson`; invalid JSON drops the
  condition.

Errors surface as HTTP responses:

| Status | Typical cause |
|---|---|
| 400 | `ParserError`: invalid partial match, invalid JSONB aggregation path/alias/`having`, slug not found |
| 403 | the caller lacks `slug:execute` permission |
| 204 | the query returned no rows |

---

## 17. Cookbook

**Active stores in two regions, newest first**

```json
{
  "fields": ["store_id", "store_name", "region", "opened_at"],
  "filter": {"status": "active", "region": ["West", "North"], "closed_at": "null"},
  "ordering": ["opened_at DESC"],
  "querylimit": 100
}
```

**Everything except one region, positive stock**

```json
{"filter": {"warehouse_alias!": "DC01", "qty": {">": 0}}, "ordering": ["qty DESC"]}
```

**Case-insensitive search box**

```json
{"filter": {"customer_name": {"icontains": "smith"}, "email": {"iendswith": "@acme.com"}}}
```

**Revenue per region this month**

```json
{
  "fields": ["region", "sum(amount) as revenue"],
  "filter": {"sale_date": {">=": "FDOM"}},
  "grouping": ["region"],
  "ordering": ["revenue DESC"]
}
```

**JSONB: users having any of two roles, and not suspended**

```json
{
  "filter": {
    "profile": {"@>|": [{"role": "admin"}, {"role": "manager"}]},
    "flags":   {"->>": {"suspended": "false"}}
  }
}
```

**Slug with date placeholders and an extra ad-hoc filter**

```json
{
  "firstdate": "@yesterday",
  "lastdate": "@today",
  "filter": {"store_id": ["101", "102"]},
  "fields": ["store_id", "visits"],
  "querylimit": 50
}
```

**Download as CSV with `;` delimiter**

```http
POST /api/v2/services/queries/store_visits:csv?_download=true&_filename=visits.csv

{"filter": {"region": "West"}, "_csv_options": {"delimiter": ";"}}
```

---

## 18. Quick reference

```text
OPTIONS
  fields | add_fields | distinct
  filter (where_cond) | filter_options
  grouping (group_by) | having
  ordering (order_by)
  querylimit (_limit) | _offset | paged | page
  conditions | cond_definition | refresh | hierarchy | qry_options
  tablename | schema | database

FILTER VALUES                               SQL
  col: 'v'                                  col = 'v'
  col: '!v'        |  'col!': 'v'           col != 'v'
  col: [a, b]      |  'col!': [a, b]        col IN (...) / NOT IN (...)
  col: {'>': 10}                            col > '10'        (>= <= <> != < >)
  col: ['>=', 10]                           col >= '10'       (+ IS, IS NOT)
  col: 'null' | '!null'                     IS NULL / IS NOT NULL
  col: true                                 col = true

PARTIAL MATCHING                            {op: 'text'}
  like ilike startswith istartswith endswith iendswith contains icontains
  regex iregex (PostgreSQL)  — each with a not_ variant

JSONB (PostgreSQL)
  col: {'@>': x} | {'<@': x}                containment (always explicit)
  col: {'@>|': [a, b]}                      any of
  col: {'@!': [a, b]}                       none of
  col: {'@$': [a, b]}                       not all
  col: {'->>': {k: v}} | {'->': {k: v}}     key comparisons

JSONB ARRAYS (PostgreSQL)
  col[].key[.key][::cast]  in fields / group_by / ordering / filter keys
  aggregates count sum avg min max · buckets year quarter month week day · having

FUNCTIONS
  TODAY YESTERDAY CURRENT_YEAR CURRENT_MONTH LAST_YEAR FDOM LDOM   (value keywords)
  @name                                     deployment variable (QUERYSOURCE_VARIABLES)
  <filter_fn>: {...}                        query filter function (QUERYSOURCE_FILTERS)
```

### Related documents

- [FILTER_OPERATORS.md](FILTER_OPERATORS.md) — partial-matching operators, per-dialect rendering
- [JSONB_AGGREGATION.md](JSONB_AGGREGATION.md) — JSONB array grouping and aggregation
- [QSURL.md](QSURL.md) — the URL dialect
- [DESCRIBE_API.md](DESCRIBE_API.md) — discovering a slug's fields and placeholders
- [COLUMN_FILTER_EXAMPLE.md](COLUMN_FILTER_EXAMPLE.md) — column filters in multi-queries
