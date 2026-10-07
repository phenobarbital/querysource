<!--
  sdd/templates/design_research.prompt.md — neutral design-research brief (FEAT-545).
  Rendered by /sdd-spec section 3b and piped to `codex exec ... --output-schema
  design_research.schema.json`.
  FORBIDDEN INPUTS: never paste the spec draft, the spec author's reasoning, a preferred
  conclusion, or any text written by the model that will author the spec. The brief carries
  ONLY the accepted exploration document (brainstorm/proposal) and verified code anchors.
  Placeholders (double-curly-brace tokens, deliberately NOT written with literal braces in
  this comment — a renderer that does a naive whole-document string replace must not also
  rewrite this sentence): problem_statement, constraints_and_goals,
  recommended_option_or_scope, code_context_paths, open_questions, question.
-->
# Independent design review — read-only

You are an independent design reviewer for **QuerySource**, an async-first Python
library for querying heterogeneous data sources (aiohttp, asyncdb, Cython parsers, `querysource/` package).
You have read-only access to the repository in your working directory.

## Rules
1. Read the code you cite. Every `affected_paths` entry must be a repo-relative
   path you actually opened; suggestions with unverifiable paths are discarded.
2. Judge the design intent below against what exists in the repository: what is
   missing, what is risky, what would be simpler, what the codebase already
   provides that the intent re-invents.
3. Do not restate the intent, do not praise it, do not write code. Propose at
   most 12 concrete, falsifiable suggestions, each tagged with a kind
   (`architecture` | `api` | `testing` | `risk` | `alternative`), a risk level and
   your confidence.
4. Output exactly ONE JSON object conforming to the schema you were given — no
   markdown fences, no prose before or after.

## Accepted design intent (verbatim from the exploration document)

### Problem statement
> Querysource requires a way to support like, ilike and partial-matching (startswith,
> endswith, contains, postgres regexp) in "where_cond"|"filter" options, when a field in
> "where_cond" is a dictionary, like this:
>
> ```json
> "where_cond": { "full_name": { "startswith": "andre" } }
> ```
>
> we need to add a partial-matching expression in "WHERE" parser, the partial-matching
> expressions will be:
> - startswith: the "field" will starts with the value
> - endswith: "field" will end with value
> - "contains": word or phrase is contained by "field" value (with a length no less
>   than 3, if length is 1 or 2, raise an error)
> - "regex": field will match the regex

The request is to let a dict-typed `where_cond`/`filter` value carry a partial-matching
operator (`{"full_name": {"startswith": "andre"}}`) and have the WHERE builders render
it as `LIKE` / `ILIKE` / PostgreSQL regex. The builders live in `filter_conditions` of
`querysource/parsers/pgsql.pyx` and `querysource/parsers/sql.pyx`, each with a Rust fast
path (`rust/src/pgsql_parser.rs`, `rust/src/sql_parser.rs`) and a Cython fallback. The
PostgreSQL builder already has the exact extension point needed: FEAT-152 added
`PG_TEXT_OPERATORS = ('ILIKE', 'NOT ILIKE')` as accepted dict operators in both Cython
and Rust, and qsurl (`querysource/qsurl/translate.py`) already maps
`startswith`/`contains`/`endswith` to ILIKE patterns with a `like_escape()` helper. The
recommendation is to extend that allowlist with the new vocabulary in both builders,
escape wildcards before quoting, exempt the new names in `jsonb_condition`, and raise
the "contains too short" error in `AbstractParser._where_element` (the only hook that
runs before both paths, because the pgsql Rust dispatch swallows exceptions). Per the
resolved questions (§5) the scope covers every SQL dialect: case-sensitive and `i*`
case-insensitive variants with a full `not_*` family, `LOWER(col) LIKE LOWER(pattern)`
on dialects without ILIKE, and regex (`~`, `~*`, `!~`, `!~*`, bounded length) on
PostgreSQL only.

---

### Constraints and goals
- **Two builders per dialect.** Every operator added to `pgsql.pyx` must be mirrored in
  `rust/src/pgsql_parser.rs` (and `sql.pyx` ↔ `sql_parser.rs` if the generic dialect is in
  scope). Tests render both paths and skip the Rust one when the installed `.so` is stale;
  Python loads the source-tree `_qs_parsers` `.so`, so `make build-rust` followed by
  `make stage-rust` is required before tests see Rust changes.
  *Evidence*: F006, F011, F014

- **JSONB dispatch runs first.** `jsonb_condition` counts known operator keys; a dict with
  none is rendered as implicit containment `col @> '<json>'::jsonb`. FEAT-152 had to add an
  early exemption for `PG_TEXT_OPERATORS`; the new names need the same, in Cython and Rust.
  *Evidence*: F004

- **Rust errors are swallowed on pgsql.** `pgSQLParser.filter_conditions` catches every
  Rust exception and re-renders through Cython. A user-facing validation error (contains
  shorter than 3) raised only inside Rust would vanish. It must be raised either in
  `_where_element` (pre-dispatch) or identically in the Cython path. The generic
  `SQLParser.filter_conditions` has no such guard.
  *Evidence*: F005

- **`is_valid` pre-quoting invariant.** `_where_element` replaces the dict operand with
  `is_valid(key, v)`, which wraps non-numeric strings in `'...'` and doubles embedded
  quotes (pinned by commit `f313e115`). Builders must strip that pair, *then* escape
  LIKE metacharacters, *then* add wildcards and quote via `pg_literal`.
  *Evidence*: F009, F003, F006

- **Single operator per dict.** `_where_element` keeps only the *last* `(op, v)` pair;
  Rust reads the *first*. The feature must specify single-operator dicts only.
  *Evidence*: F009, F006

- **Existing vocabulary.** qsurl and the DataFrame post-filters already use the names
  `startswith`, `endswith`, `contains`, `regex` (plus a `not_*` family) with
  case-insensitive semantics for the LIKE-based ones. Parser-level operators should
  match to avoid two meanings for one word.
  *Evidence*: F010, F013

- **Legacy key-suffix prefix match.** `{"name~": "v"}` already renders
  `name ILIKE 'v%'` in both builders (str branch). It must keep working untouched.
  *Evidence*: F003, F006

### Recommended option / probable scope
### What's New

- **Partial-matching operator vocabulary** accepted as the single key of a dict-typed
  `where_cond`/`filter` value (resolved U1, U3, U5, U6). Every operator has a `not_`
  twin; the LIKE-based ones have a case-sensitive form and an `i`-prefixed
  case-insensitive form:

  | Operator (+ `not_` twin) | Operand handling | PostgreSQL | Generic SQL / SQL Server / BigQuery |
  |--------------------------|------------------|------------|-------------------------------------|
  | `like` | raw pattern, user supplies `%`/`_` | `col LIKE 'pat'` | `col LIKE 'pat'` |
  | `ilike` | raw pattern | `col ILIKE 'pat'` (alias of existing `ILIKE`) | `LOWER(col) LIKE LOWER('pat')` |
  | `startswith` | `like_escape(v) + '%'` | `col LIKE 'v%'` | `col LIKE 'v%'` |
  | `istartswith` | same | `col ILIKE 'v%'` | `LOWER(col) LIKE LOWER('v%')` |
  | `endswith` | `'%' + like_escape(v)` | `col LIKE '%v'` | `col LIKE '%v'` |
  | `iendswith` | same | `col ILIKE '%v'` | `LOWER(col) LIKE LOWER('%v')` |
  | `contains` | `'%' + like_escape(v) + '%'`; **len(v) ≥ 3 else `ParserError`** | `col LIKE '%v%'` | `col LIKE '%v%'` |
  | `icontains` | same (same length rule) | `col ILIKE '%v%'` | `LOWER(col) LIKE LOWER('%v%')` |
  | `regex` | pattern quoted by `pg_literal`; max length guard | `col ~ 'pat'` | `ParserError` (PostgreSQL only) |
  | `iregex` | same | `col ~* 'pat'` | `ParserError` |
  | `not_regex` / `not_iregex` | same | `col !~ 'pat'` / `col !~* 'pat'` | `ParserError` |

  `not_` twins render `NOT LIKE` / `NOT ILIKE` / `NOT (LOWER(col) LIKE LOWER(...))`.
  The `%`/`_`/`\` escaping of `startswith`/`endswith`/`contains` operands needs an
  `ESCAPE '\'` clause on dialects whose default escape differs (SQL Server has none by
  default; BigQuery uses `\` natively; PostgreSQL and MySQL default to `\`).

- **Pre-dispatch validator** in `AbstractParser._where_element` (resolved U4): when the
  dict operator is a partial-matching name, require a `str` operand, enforce the
  `contains`/`icontains`/`not_contains`/`not_icontains` minimum length (≥ 3), and cap the
  regex pattern length, raising `ParserError` (a `QueryException`) so the error surfaces
  on both the Rust and Cython paths. `startswith`, `endswith` and `like` accept any length.
- **Shared LIKE-escape helper** reachable from the Cython builders (reuse
  `querysource.qsurl.translate.like_escape` or lift it to `querysource/types/validators`)
  with a Rust twin in `pgsql_parser.rs`.

### What Changes

- **`querysource/parsers/pgsql.pyx`::`PG_TEXT_OPERATORS` + `_filter_conditions_cy` + `jsonb_condition`** —
  extend the allowlist, add the operator→(SQL op, prefix, suffix, escape?) rendering
  branch next to the ILIKE one (strip → escape → wildcard → `pg_literal`), exempt the new
  names in `jsonb_condition`.  *Evidence*: F003, F004
- **`rust/src/pgsql_parser.rs`::`PG_TEXT_OPERATORS` + `pg_validate_operator` + `process_dict_value` + `jsonb_condition`** —
  same change in Rust, with unit tests in the module's `#[cfg(test)]` block.  *Evidence*: F006, F004
- **`querysource/parsers/abstract.pyx`::`_where_element`** — operator/operand validation
  before `is_valid` pre-quoting.  *Evidence*: F009, F005
- **`querysource/parsers/sql.pyx` + `rust/src/sql_parser.rs` (dict branch)** — resolved U2
  (all SQL dialects): LIKE-based operators plus the `LOWER(col) LIKE LOWER(...)` form for
  `i*` variants; `regex*` raises `ParserError`.  *Evidence*: F002, F007
- **`querysource/parsers/sqlserver.pyx` + `rust/src/mssql_parser.rs` / `rust/src/filter_common.rs`** —
  resolved U2: add a dict-value branch (none exists today; `filter_common.rs` needs a
  `Dict` variant in `FilterValue` and `extract_filter_value`) rendering the same LIKE /
  `LOWER()` forms with an explicit `ESCAPE '\'` clause.  *Evidence*: F008
- **`querysource/parsers/bigquery.pyx` + `rust/src/bigquery_parser.rs` (dict branch)** —
  resolved U2: extend the existing COMPARISON_TOKENS-only dict branch with the LIKE /
  `LOWER()` forms using `bq_quote_string`.  *Evidence*: F008
- **Tests** — new `tests/test_pg_partial_matching.py` parametrized over rust/cython like
  `tests/qsurl/test_pg_ilike.py`; extend `tests/test_rust_parsers.py`; a full-flow test
  (through `set_options`/`build_query`) asserting the `contains` error is raised even
  when the Rust path is active.  *Evidence*: F011, F005
- **Docs** — operator table in the where_cond documentation (`docs/`), cross-linking
  `docs/QSURL.md`'s text_match section.  *Evidence*: F010

### What's Untouched (Non-Goals)

- qsurl grammar / pushdown rules (it already emits ILIKE dicts; declaring a `regex`
  pushdown capability is a separate decision).
- Non-SQL dialects (CQL, Mongo, Elastic, ArangoDB, Rethink, Influx) — SQL dialects only
  (PostgreSQL, generic SQL, SQL Server, BigQuery) per U2.
- Regex on dialects other than PostgreSQL (BigQuery `REGEXP_CONTAINS` could follow later).
- The legacy `field~` / `field!~` key-suffix prefix match (kept as-is).
- Multi-operator dicts such as `{"startswith": "a", "endswith": "z"}`.
- `column_filter` / DataFrame post-filtering vocabulary in `types/dt/filters.py`.

### Patterns to Follow

- FEAT-152 ILIKE branch: identical strip-pre-quote → `pg_literal` logic and comment block
  in Cython and Rust; `pytest.param(..., skipif=stale extension)` dual-path tests.
  *Evidence*: F003, F006, F011
- `like_escape` (`\` → `\\`, `%` → `\%`, `_` → `\_`) applied *before* adding wildcards;
  quoting stays the builder's job.  *Evidence*: F010
- Operator allowlist constants at module top (`COMPARISON_TOKENS`, `PG_TEXT_OPERATORS`),
  never string concatenation of raw user operators.  *Evidence*: F003, F006, F007

### Integration Risks

- **JSONB name collision**: a JSONB column filtered by a literal key named `contains`
  (`{"meta": {"contains": "x"}}`) would stop being containment. *Mitigation*: reserve the
  operator names, document them, and apply the exemption only for single-key dicts with a
  string operand.  *Evidence*: F004
- **Swallowed Rust error**: a test asserting the error only via `_rs.pgsql_filter_conditions`
  would pass while real requests see no error. *Mitigation*: validate in `_where_element`
  and add a full-flow test.  *Evidence*: F005
- **Regex cost on the database**: unbounded user regex pushed to PostgreSQL. *Mitigation*:
  length guard mirroring `_MAX_REGEX_PATTERN_LENGTH` in `querysource/qsurl/residual.py`.
  *Evidence*: F010
- **Stale compiled extension**: tests silently exercise old Rust code. *Mitigation*:
  stale-extension skip guard plus `make build-rust && make stage-rust` in the task's
  acceptance commands.  *Evidence*: F011, F014
- **Four-dialect scope (U2) multiplies the surface**: SQL Server has no dict branch in
  either language and `filter_common.rs` has no `Dict` variant, so mssql is net-new code
  rather than an extension. *Mitigation*: decompose by dialect (PostgreSQL first, then
  generic SQL, BigQuery, SQL Server) with one shared operator table and escaping helper
  so semantics cannot drift.  *Evidence*: F008
- **LIKE escape character differs per dialect**: `like_escape` emits `\`-escapes; SQL
  Server needs an explicit `ESCAPE '\'`. *Mitigation*: the dialect renderer owns the
  `ESCAPE` clause; tests cover `%`, `_` and `\` in operands per dialect.  *Evidence*: F010

---

### Verified code anchors (paths only — open them yourself)
querysource/parsers/pgsql.pyx
rust/src/pgsql_parser.rs
querysource/parsers/sql.pyx
rust/src/sql_parser.rs
querysource/parsers/abstract.pyx
querysource/qsurl/translate.py
querysource/types/dt/filters.py
tests/qsurl/test_pg_ilike.py
rust/src/filter_common.rs
querysource/parsers/sqlserver.pyx
querysource/parsers/bigquery.pyx

### Questions still open in the exploration document
none

## Question
Given this accepted design intent and these verified code anchors, how would you build it? What is missing, risky, or better done another way?
