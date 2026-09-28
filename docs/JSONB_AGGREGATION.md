# Grouping & aggregating by JSONB array elements (PostgreSQL)

FEAT-153. Group, aggregate and filter **inside a JSONB array of objects** stored in a
row, from a request or from a slug declaration, with flat rows as the result.

Available on PostgreSQL slugs whose SQL is rendered by the parser
(`pgSQLParser.build_query`). `is_raw` slugs bypass the parser, so path tokens and
`having` have no effect on them, the same as every other parser condition.
Requires PostgreSQL 12+ (the SQL itself only needs 9.4+).

A query that does **not** use the syntax below renders byte-identical SQL to
before the feature, on both the Rust and the Cython path.

## Quick example — graduates per course

A student row carries `graduation_details`:

```json
[{"course": "Pilates Studio", "category": "Comprehensive", "course_date": "2025-09-19", "diploma_number": "POL15058"}]
```

Graduates per course and category, licensee Asia, diplomas from 2025 onward, groups
with more than 5 graduates, top 10:

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

For a slug whose template is `SELECT * FROM students {where_cond}` the parser renders:

```sql
SELECT (_qs_e0.elem ->> 'course') AS "course", (_qs_e0.elem ->> 'category') AS "category", count(DISTINCT _qs_src.student_uid) AS "graduates" FROM (SELECT * FROM students  WHERE licensee='Asia') AS _qs_src CROSS JOIN LATERAL jsonb_array_elements(CASE jsonb_typeof(_qs_src.graduation_details) WHEN 'array' THEN _qs_src.graduation_details ELSE '[]'::jsonb END) AS _qs_e0(elem) WHERE ((_qs_e0.elem ->> 'course_date')::date) >= '2025-01-01' GROUP BY (_qs_e0.elem ->> 'course'), (_qs_e0.elem ->> 'category') HAVING count(DISTINCT _qs_src.student_uid) > 5 ORDER BY "graduates" DESC LIMIT 10
```

The result is flat (illustrative values):

| course | category | graduates |
|---|---|---|
| Pilates Studio | Comprehensive | 12 |
| Pilates Mat | Mat | 7 |

Because the result is flat, every output writer (JSON, CSV, Excel, …) works unchanged.

### How it works

When a request uses the feature ("plan mode"), the parser:

1. validates every `fields`, `group_by`, `ordering`, `having` entry and every path-keyed
   filter key against a strict grammar;
2. renders the **inner** query exactly as before, with the row filters only, and with
   `fields`, `grouping` and `ordering` cleared;
3. blanks the structural placeholders (`{grouping}`, `{group_by}`, `{order_by}`,
   `{ordering}`, `{offset}`, `{limit}`) in the inner query, so `LIMIT`, `OFFSET`,
   `GROUP BY` and `ORDER BY` never appear inside `(...) AS _qs_src`;
4. wraps it as `SELECT <select> FROM (<inner>) AS _qs_src <lateral join> [WHERE <element filters>]`;
5. appends `GROUP BY`, `HAVING`, `ORDER BY` and `LIMIT`/`OFFSET` on the **outer** query.

The plan mode activates when any of these holds; otherwise the query is untouched:

- a token containing `[].` appears in `fields`, `group_by`/`grouping`,
  `ordering`/`order_by` or a `filter` key;
- the request sends a non-empty `having`;
- a name in `fields`, `grouping`, `ordering`, a filter key or `having` matches an alias
  declared in `attributes.jsonb_unnest.aliases`.

A slug whose own template already has a `GROUP BY` or `LIMIT` is aggregated over the
inner result. That is well defined: the template's `GROUP BY` stays inside `_qs_src`.

## Path syntax

```
<column>[].<key>[.<key>…][::cast]
```

- `column` is `[A-Za-z_][A-Za-z0-9_]{0,62}`, rendered bare (validated, never quoted).
- `key` is `[A-Za-z0-9_-]{1,128}`. **Keys with spaces are not supported.** Keys are
  always rendered as SQL literals (`->> 'key'`), never as identifiers.
- Nested keys are supported: `a[].k1.k2`. Nested arrays (`a[].b[].c`) are not.
- Allowed casts (case-insensitive): `text`, `int`, `integer`, `bigint`, `numeric`,
  `float`, `date`, `timestamp`, `timestamptz`, `boolean`. Whitespace around `::` is
  tolerated.

| Input | Renders |
|---|---|
| `a[].k` | `(_qs_e0.elem ->> 'k')` |
| `a[].k1.k2` | `(_qs_e0.elem -> 'k1' ->> 'k2')` |
| `a[].k::date` | `((_qs_e0.elem ->> 'k')::date)` |
| `licensee` (row column) | `_qs_src.licensee` |
| `created_at::date` (row column) | `(_qs_src.created_at::date)` |

In plan mode every `fields` entry must match the grammar. Raw SQL expressions are
rejected in plan mode; outside plan mode they stay allowed, verbatim, as today.

## Aggregates and time buckets

Aggregates: `count(*)`, `count(x)`, `count(distinct x)`, `min(x)`, `max(x)`, `sum(x)`,
`avg(x)`. `x` is a path or a row column; `min`/`max`/`sum`/`avg` also accept a bucket,
for example `min(year(a[].d))`. Aggregates cannot be nested and `distinct` is only
valid inside `count`.

Time buckets: `year(p)`, `quarter(p)`, `month(p)`, `week(p)`, `day(p)`, rendered as
`(date_trunc('<unit>', <p as date>)::date)`. There is no `date_trunc` passthrough.

Implicit casts:

- a path with no cast inside a bucket is cast `::date`; a path cast to `timestamp` or
  `timestamptz` keeps its cast (the result is still `::date`);
- a path with no cast inside `sum` / `avg` is cast `::numeric`;
- a row column without a cast is used as is.

```sql
-- fields: ["year(graduation_details[].course_date) as year", "count(*)"], group_by: ["year(graduation_details[].course_date)"]
SELECT (date_trunc('year', ((_qs_e0.elem ->> 'course_date')::date))::date) AS "year", count(*) AS "count" FROM (SELECT * FROM students) AS _qs_src CROSS JOIN LATERAL jsonb_array_elements(CASE jsonb_typeof(_qs_src.graduation_details) WHEN 'array' THEN _qs_src.graduation_details ELSE '[]'::jsonb END) AS _qs_e0(elem) GROUP BY (date_trunc('year', ((_qs_e0.elem ->> 'course_date')::date))::date) ORDER BY "year"
```

```sql
-- fields: ["licensee", "sum(graduation_details[].points)", "avg(graduation_details[].points::int) as mean"], group_by: ["licensee"]
SELECT _qs_src.licensee AS "licensee", sum(((_qs_e0.elem ->> 'points')::numeric)) AS "sum_points", avg(((_qs_e0.elem ->> 'points')::int)) AS "mean" FROM (SELECT * FROM students) AS _qs_src CROSS JOIN LATERAL jsonb_array_elements(CASE jsonb_typeof(_qs_src.graduation_details) WHEN 'array' THEN _qs_src.graduation_details ELSE '[]'::jsonb END) AS _qs_e0(elem) GROUP BY _qs_src.licensee
```

**Output aliases** are always double-quoted. An explicit `as <ident>` wins. Default
aliases:

| Expression | Default alias |
|---|---|
| path / row column | last key / column name |
| bucket | `<unit>_<name>` |
| `count(*)` | `count` |
| `agg(x)` | `<func>_<name>` (`count(distinct x)` included) |
| `agg(bucket(x))` | `<func>_<unit>_<name>` |

Two entries that resolve to the same alias raise `duplicate output alias`; give one of
them an explicit `as` alias.

**Empty `fields`**: in plan mode the select list is the group keys followed by
`count(*) AS "count"`.

## Element filters vs row filters

A `filter` key that is a path (`a[].k`, optionally with `::cast` and a trailing `!`) is an
**element filter**: it is rendered in the *outer* `WHERE`, on each unnested element.
Every other filter entry is a **row filter**, rendered by the unchanged filter engine in
the *inner* query. That includes the existing JSONB operators `@>`, `<@`, `@>|`, `->`,
`->>`.

### The counting pitfall

Filtering rows with `@>` keeps **every** element of the rows that match, so the other
diplomas of a matching student are counted too:

```json
{"group_by": ["graduation_details[].course"],
 "fields": ["graduation_details[].course", "count(distinct student_uid) as graduates"],
 "filter": {"licensee": "Asia", "graduation_details": {"@>": [{"course": "Pilates Studio"}]}}}
```

```sql
SELECT (_qs_e0.elem ->> 'course') AS "course", count(DISTINCT _qs_src.student_uid) AS "graduates" FROM (SELECT * FROM students  WHERE licensee='Asia' AND graduation_details @> E'[\x7b"course":"Pilates Studio"\x7d]'::jsonb) AS _qs_src CROSS JOIN LATERAL jsonb_array_elements(CASE jsonb_typeof(_qs_src.graduation_details) WHEN 'array' THEN _qs_src.graduation_details ELSE '[]'::jsonb END) AS _qs_e0(elem) GROUP BY (_qs_e0.elem ->> 'course')
```

A student with a Studio diploma and a Mat diploma contributes to **both** groups. To
count only the Studio diplomas, use an element filter:

```json
{"filter": {"licensee": "Asia", "graduation_details[].course": "Pilates Studio"}}
```

```sql
SELECT (_qs_e0.elem ->> 'course') AS "course", count(DISTINCT _qs_src.student_uid) AS "graduates" FROM (SELECT * FROM students  WHERE licensee='Asia') AS _qs_src CROSS JOIN LATERAL jsonb_array_elements(CASE jsonb_typeof(_qs_src.graduation_details) WHEN 'array' THEN _qs_src.graduation_details ELSE '[]'::jsonb END) AS _qs_e0(elem) WHERE (_qs_e0.elem ->> 'course') = 'Pilates Studio' GROUP BY (_qs_e0.elem ->> 'course')
```

### Element filter forms

Comparison is on the `->>` text (or on the cast, when the key has one).

| Key form | Value | Renders |
|---|---|---|
| `a[].k` | scalar | `expr = <lit>` |
| `a[].k!` | scalar | `expr <> <lit>` |
| `a[].k` | list | `expr IN (<lit>, …)` |
| `a[].k!` | list | `expr NOT IN (…)` |
| `a[].k` | `{op: v}`, op ∈ `>= <= <> != < > =` | `expr op <lit>` (several ops are AND-ed) |
| `a[].k` | `null` / `'null'` | `expr IS NULL` |
| `a[].k!` | `null` / `'null'` | `expr IS NOT NULL` |

Values are **always literals**. Pre-quoted strings coming from the request pipeline are
unquoted and re-quoted, numbers and booleans become text literals (`'5'`, `'true'`;
PostgreSQL coerces the literal for a casted path), and braces are escaped. A value that
looks like SQL (`CURRENT_DATE`, a function call) stays a string literal: SQL functions
are not supported as element-filter values. A comparison dict carries **one** operator by
the time the planner sees it, because the upstream filter pass consumes one pair per key
(`having` keeps several).

```sql
-- filter: {"graduation_details[].course!": "Pilates Mat", "graduation_details[].category": ["Comprehensive", "Mat"], "graduation_details[].level": {">=": 3}, "graduation_details[].note": null}
... WHERE (_qs_e0.elem ->> 'course') <> 'Pilates Mat' AND (_qs_e0.elem ->> 'category') IN ('Comprehensive', 'Mat') AND (_qs_e0.elem ->> 'level') >= '3' AND (_qs_e0.elem ->> 'note') IS NULL GROUP BY ...
```

A path filter key accepts only the `!` suffix; other JSONB key suffixes (`|~#@:`) on a
path key are a reference error. A path filter also takes part in the single-array rule
and in `strict` / `columns` checks, and it alone is enough to unnest the array.

## having

`having` is a request condition (it is never merged into `WHERE`): a mapping of a select
alias (or an aggregate expression) to a scalar or to `{op: value}`.

```json
{"having": {"graduates": {">=": 2, "<": 100}, "count(*)": 3}}
```

- The key must be a select alias whose expression is an aggregate, a declared alias of
  an aggregate, or an aggregate expression per the grammar. Otherwise
  `unknown having key`.
- `HAVING` renders the **underlying expression**, never the alias (PostgreSQL does not
  resolve aliases in `HAVING`).
- Several operators in one dict, and several keys, are AND-ed. Operators:
  `= >= <= <> != < >`.
- Values: numbers render bare, strings go through the literal quoting, anything else is
  an error. A non-mapping `having`, or `having` with no aggregate in the select list,
  is an error.

```sql
-- fields: ["graduation_details[].course", "count(*) as n", "max(graduation_details[].course_date) as latest"], having: {"n": {">=": 2, "<": 100}}
SELECT (_qs_e0.elem ->> 'course') AS "course", count(*) AS "n", max((_qs_e0.elem ->> 'course_date')) AS "latest" FROM (SELECT * FROM students) AS _qs_src CROSS JOIN LATERAL jsonb_array_elements(CASE jsonb_typeof(_qs_src.graduation_details) WHEN 'array' THEN _qs_src.graduation_details ELSE '[]'::jsonb END) AS _qs_e0(elem) GROUP BY (_qs_e0.elem ->> 'course') HAVING count(*) >= 2 AND count(*) < 100
```

A `having`-only request (no array column) renders no lateral join.

## Ordering and paging

- An `ordering` entry that names a select alias renders as that alias
  (`"graduates" DESC`); otherwise the expression is rendered. Grammar:
  `(expr | alias) [ASC|DESC] [NULLS FIRST|LAST]`.
- `group_by` entries render the full expression (a select alias is replaced by its
  expression).
- `LIMIT` / `OFFSET` apply to the **groups** (the outer query), never inside `_qs_src`.

```sql
-- template: SELECT {fields} FROM {schema}.{table} {filter} {grouping} {offset} {limit}, querylimit 5, ordering ["n DESC"]
SELECT (_qs_e0.elem ->> 'course') AS "course", count(*) AS "n" FROM (SELECT * FROM public.students) AS _qs_src CROSS JOIN LATERAL jsonb_array_elements(CASE jsonb_typeof(_qs_src.graduation_details) WHEN 'array' THEN _qs_src.graduation_details ELSE '[]'::jsonb END) AS _qs_e0(elem) GROUP BY (_qs_e0.elem ->> 'course') ORDER BY "n" DESC LIMIT 5
```

Ordering appended by query function filters is discarded in plan mode (a warning is
logged), because it refers to inner row columns the outer query cannot see.

## Slug declaration (`attributes.jsonb_unnest`)

A slug can declare the feature in `QueryModel.attributes["jsonb_unnest"]` (no schema
migration: `attributes` is a JSON column). All keys are optional and unknown keys are an
error:

```json
{
  "jsonb_unnest": {
    "columns": {
      "graduation_details": {"empty": "exclude", "safe_cast": true, "prefilter": false}
    },
    "aliases": {
      "course": "graduation_details[].course",
      "diploma_year": "year(graduation_details[].course_date)"
    },
    "strict": false,
    "safe_cast": false
  }
}
```

| Key | Meaning |
|---|---|
| `columns` | When present, the **only** array columns a request may unnest (an undeclared column is an error). Per column: `empty` (`exclude` default / `include`), `safe_cast` (default: the top-level value), `prefilter` (default `false`). |
| `aliases` | `name → expression` (any expression of the grammar). Usable as bare names in `fields`, `grouping`, `ordering`, `having` and filter keys. An alias wins over a real column of the same name. |
| `strict` | `true`: raw path tokens in the request are rejected; only aliases may introduce paths. |
| `safe_cast` | `true`: regex-guarded casts for every path (see below). |

Without a declaration the grammar is still always enforced, and any grammar-valid array
column may be unnested. A request can declare nothing and simply send path tokens.

Only **one** distinct array column may be used per query; the same column any number of
times produces a single lateral join.

With the aliases above, a request of `{"group_by": ["course", "diploma_year"], "ordering": ["diploma_year DESC"]}` (and `safe_cast: true`) renders:

```sql
SELECT (_qs_e0.elem ->> 'course') AS "course", (date_trunc('year', (CASE WHEN (_qs_e0.elem ->> 'course_date') ~ '^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' THEN (_qs_e0.elem ->> 'course_date')::date END))::date) AS "diploma_year", count(*) AS "count" FROM (SELECT * FROM students) AS _qs_src CROSS JOIN LATERAL jsonb_array_elements(CASE jsonb_typeof(_qs_src.graduation_details) WHEN 'array' THEN _qs_src.graduation_details ELSE '[]'::jsonb END) AS _qs_e0(elem) GROUP BY (_qs_e0.elem ->> 'course'), (date_trunc('year', (CASE WHEN (_qs_e0.elem ->> 'course_date') ~ '^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' THEN (_qs_e0.elem ->> 'course_date')::date END))::date) ORDER BY "diploma_year" DESC
```

## Empty, NULL and non-array values

The array is unnested through
`jsonb_array_elements(CASE jsonb_typeof(col) WHEN 'array' THEN col ELSE '[]'::jsonb END)`,
so a NULL column, an empty array and a non-array value (for example
`{"not": "array"}`) never raise.

- `empty: "exclude"` (default): `CROSS JOIN LATERAL`. Rows with no elements disappear
  from the result.
- `empty: "include"`: `LEFT JOIN LATERAL … ON true`. Those rows yield one NULL-element
  row, so they show up as a NULL group.

With `include`, `count(*)` counts that NULL-group row as **1**. Use `count(<path>)` to
count 0:

```sql
-- fields: ["graduation_details[].course", "count(*) as rows", "count(graduation_details[].course) as diplomas"], columns.graduation_details.empty = "include"
SELECT (_qs_e0.elem ->> 'course') AS "course", count(*) AS "rows", count((_qs_e0.elem ->> 'course')) AS "diplomas" FROM (SELECT * FROM students) AS _qs_src LEFT JOIN LATERAL jsonb_array_elements(CASE jsonb_typeof(_qs_src.graduation_details) WHEN 'array' THEN _qs_src.graduation_details ELSE '[]'::jsonb END) AS _qs_e0(elem) ON true GROUP BY (_qs_e0.elem ->> 'course')
```

## safe_cast

By default a bad value fails the **whole query** with a database error (for example
`invalid input syntax for type date`). With `safe_cast: true` (top level or per column) a
cast of a path is wrapped in a regex guard and bad values become NULL:

```sql
-- fields: ["graduation_details[].course_date::date as d"], safe_cast: true
(CASE WHEN (_qs_e0.elem ->> 'course_date') ~ '^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' THEN (_qs_e0.elem ->> 'course_date')::date END) AS "d"
```

| Cast | Guard regex |
|---|---|
| `date`, `timestamp`, `timestamptz` | `^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]` |
| `int`, `integer`, `bigint` | `^-?[0-9]+$` |
| `numeric`, `float` | `^-?[0-9]+([.][0-9]+)?$` |
| `boolean` | `^(true|false)$` |
| `text` | never guarded |

The guard also applies to the implicit casts (buckets, `sum`/`avg`), and to element
filters on casted paths. The regexes are brace-free on purpose so later placeholder
substitution passes cannot break the SQL.

**Known limit:** a well-shaped but impossible date (`2025-02-30`) passes the guard and
still raises at query time on PostgreSQL 12–15. `pg_input_is_valid` (PostgreSQL 16+) is
deliberately not used because the floor is PG 12.

## Pre-filter (opt-in)

`columns.<col>.prefilter: true` adds a containment pre-filter to the **inner** query for
`=` and `IN` element filters, so a GIN index on the JSONB column can narrow the rows
before they are unnested:

```sql
-- filter: {"graduation_details[].course": ["Pilates Studio", "Pilates Mat"]}, prefilter: true
SELECT ... FROM (SELECT * FROM students  WHERE (graduation_details @> E'[\x7b"course":"Pilates Studio"\x7d]'::jsonb OR graduation_details @> E'[\x7b"course":"Pilates Mat"\x7d]'::jsonb)) AS _qs_src CROSS JOIN LATERAL ... WHERE (_qs_e0.elem ->> 'course') IN ('Pilates Studio', 'Pilates Mat') GROUP BY ...
```

The pre-filter is emitted only for a non-negated `=` (`@>`) or list (`@>|`) filter on a
path **with no cast**. It is entered as an extra row filter under the key
`<col>|` (or `<col>||`, … if that key is already taken).

**When it is safe:** only when the keys hold JSON **strings**. Element comparison is on
the `->>` text, but JSON containment is type-sensitive: `[{"level": "5"}]` does not
contain `[{"level": 5}]`. If a key holds numbers, the pre-filter would drop rows the
element filter accepts, which is why it is **off by default** and must be asserted per
array column.

## Errors

Every validation failure raises `ParserError` (HTTP 400) with a `jsonb_unnest: …`
message, on both the Rust and the Cython path. A Rust validation error is **not** retried
on the Cython path. Invalid path tokens are never silently dropped (an invalid JSONB
*row* filter keeps being dropped, unchanged).

| Message | Cause |
|---|---|
| `invalid reference '<text>'` | not `col`, `col::cast` or `col[].k[.k…][::cast]` |
| `unknown cast '<cast>'` | cast not in the allowed list |
| `invalid expression '<text>'` | bad function, nested aggregate, `distinct` outside `count`, empty argument |
| `invalid select item '<text>'` | bad `as` alias |
| `invalid order item '<text>'` | bad ordering entry |
| `invalid config: must be a mapping` / `unknown key '<k>'` / `unknown column option '<k>'` | malformed declaration |
| `invalid config: columns must map identifiers to mappings` / `aliases must map identifiers to expressions` | malformed `columns` / `aliases` |
| `invalid config: empty must be 'exclude' or 'include'` | bad `empty` |
| `invalid config: <name> must be a boolean` | non-boolean `strict` / `safe_cast` / `prefilter` |
| `raw path '<text>' not allowed in strict mode` | raw path with `strict: true` |
| `array column '<col>' is not declared in columns` | column outside the allowlist |
| `more than one array column ('<a>', '<b>')` | second distinct array column |
| `duplicate output alias '<alias>'` | two select entries with the same alias |
| `having must be a mapping` / `having requires an aggregate` | bad `having` |
| `unknown having key '<key>'` / `invalid having operator '<op>'` / `invalid having value for '<key>'` | bad `having` entry |
| `invalid filter value for '<key>'` / `invalid filter operator '<op>' for '<key>'` | bad element filter |
| `add_fields is not supported with aggregation` | `add_fields=True` in plan mode |

Every message is prefixed with `jsonb_unnest: ` (omitted above for brevity).

## Not supported

- `is_raw=True` slugs (the parser never runs on them).
- Dialects other than PostgreSQL.
- More than one distinct array column in one query; nested arrays (`a[].b[].c`).
- `add_fields=True` together with plan mode.
- SQL functions or expressions as element-filter values (values are always literals).
- A `date_trunc` passthrough (use the bucket helpers), keys containing spaces.
- Exposure through qsurl (grouping and aggregation are a later feature).
