---
id: FEAT-180
title: Partial-matching operators (like, ilike, startswith, endswith, contains, regex) for where_cond/filter dict values
slug: filter-with-partial-matching
type: feature
mode: enrichment
status: accepted
source:
  kind: inline
  jira_key: null
  jira_url: null
  fetched_at: 2026-10-07
  summary_oneline: Support like/ilike and partial-matching operators (startswith, endswith, contains, regex) in where_cond/filter dict fields
overall_confidence: medium
base_branch: dev
projects: [parsers, rust-parsers]
tags: [where-cond, partial-matching, like, ilike, regex, postgresql, sqlserver, bigquery]
research_state: sdd/state/FEAT-180/
created: 2026-10-07
updated: 2026-10-07
---

# FEAT-180 — Partial-matching operators for `where_cond` / `filter` dict values

> **Mode**: enrichment
> **Confidence**: medium (localization high; all six open questions resolved by the requester on 2026-10-07, see §5)
> **Source**: `inline`
> **Audit**: [`sdd/state/FEAT-180/`](../state/FEAT-180/)

---

## 0. Origin

The original request, preserved verbatim at `sdd/state/FEAT-180/source.md`.

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

**Initial signals** (extracted, not interpreted):
- Verbs: "support", "add" → feature / enrichment (no negation, no bug)
- Named entities: `where_cond`, `filter`, "WHERE parser", `like`, `ilike`, `startswith`, `endswith`, `contains`, `regex` ("postgres regexp")
- Components / labels: parsers (Cython) + Rust parsers
- Acceptance criteria provided: partial — 4 operator definitions and 1 validation rule (contains ≥ 3 chars → error)

---

## 1. Synthesis Summary

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

## 2. Codebase Findings

> All entries are grounded in `sdd/state/FEAT-180/findings/`. **No fabricated paths or symbols.**
> Note: the `wikitoolkit` knowledge graph was not installed in `.venv` for this run; research used grep/read/git only.

### 2.1 Localization

| # | Path | Symbol | Lines | Role | Evidence |
|---|------|--------|-------|------|----------|
| 1 | `querysource/parsers/pgsql.pyx` | `PG_TEXT_OPERATORS`, `COMPARISON_TOKENS`, `JSONB_OPERATORS` | 27-36 | operator allowlists for dict filter values (extend) | F003 |
| 2 | `querysource/parsers/pgsql.pyx` | `pgSQLParser._filter_conditions_cy` (dict branch) | 359-400 | Cython renderer: ILIKE branch strips the `is_valid` pre-quote, then `pg_literal` | F003 |
| 3 | `querysource/parsers/pgsql.pyx` | `jsonb_condition` | 234-296 | classifies dict values; must exempt new operator names | F004 |
| 4 | `querysource/parsers/pgsql.pyx` | `pgSQLParser.filter_conditions` | 305-313 | Rust dispatch wrapper; `except Exception: pass` → Cython | F005 |
| 5 | `rust/src/pgsql_parser.rs` | `PG_TEXT_OPERATORS`, `pg_validate_operator`, `process_dict_value`, `jsonb_condition` | 37-42, 75-90, 343-400, 474-537 | Rust twin of rows 1-3 | F006, F004 |
| 6 | `querysource/parsers/sql.pyx` | `SQLParser.filter_conditions` (dict branch) | 152-162 | generic Cython: COMPARISON_TOKENS only, else discard | F002 |
| 7 | `rust/src/sql_parser.rs` | `validate_operator`, `filter_conditions` | 14-57, 246-258 | generic Rust: COMPARISON ∪ VALID_OPERATORS only | F007 |
| 8 | `querysource/parsers/abstract.pyx` | `AbstractParser._where_element`, `set_where` | 564-609 | pre-dispatch: keeps last `(op, v)`, pre-quotes `v` via `is_valid` | F009 |
| 9 | `querysource/qsurl/translate.py` | `_TEXT_PATTERNS`, `like_escape` | 12-23 | existing startswith/contains/endswith → ILIKE semantics + escaping | F010 |
| 10 | `querysource/types/dt/filters.py` | DataFrame filter expressions | 70-108 | naming precedent (`contains`, `startswith`, `regex`, `not_*`), raises `QueryException` | F013 |
| 11 | `tests/qsurl/test_pg_ilike.py` | `PATHS`, `_render` | 1-60 | dual-path (rust/cython) test harness to copy | F011 |
| 12 | `rust/src/filter_common.rs`, `querysource/parsers/sqlserver.pyx`, `querysource/parsers/bigquery.pyx` | `extract_filter_value`, `filter_conditions` | 55-133 / 108-135 / 209-215 | no text-operator support in mssql / bq (scope boundary) | F008 |

### 2.2 Constraints Discovered

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

### 2.3 Recent History (Relevant)

| Commit | When | Author | Message | Touched files |
|--------|------|--------|---------|---------------|
| `4d57a4af` | 2026-10-01 | Jesus | fix(jsonb-not-or-operators): accept only list operands in Cython for Rust parity | `pgsql.pyx`, `pgsql_parser.rs` |
| `a4536ff1` | 2026-09-30 | Jesus Lara | another fix on filter parser and empty results | `pgsql.pyx` |
| `f313e115` | 2026-09-25 | Jesus Lara | docs(qsurl-parser): pin the is_valid() pre-quoting invariant the TASK-769 ILIKE fix depends on | `pgsql.pyx`, `pgsql_parser.rs` |
| `acb6bdf5` | 2026-09-25 | Jesus Lara | fix(qsurl-parser): resolve deferred code-review findings | `pgsql.pyx`, `pgsql_parser.rs` |
| `ec68025a` | 2026-09-24 | Jesus Lara | feat(qsurl-parser): TASK-769 — PostgreSQL ILIKE / NOT ILIKE dict operator (Rust + Cython) | `pgsql.pyx`, `pgsql_parser.rs` |

*Evidence*: F012. The area is hot (16 commits / 90 days) and the FEAT-152 ILIKE commit is
the direct template for this feature.

---

## 3. Probable Scope  *(mode = enrichment)*

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

## 4. Confidence Map

| ID | Claim | Evidence | Confidence | Reasoning |
|----|-------|----------|------------|-----------|
| C1 | Dict `where_cond` values are rendered in `filter_conditions` of `sql.pyx`/`pgsql.pyx`, each with Rust fast path + Cython fallback | F001, F002, F003, F006, F007 | high | direct reads of both implementations |
| C2 | pgsql already exposes the extension point (`PG_TEXT_OPERATORS`: ILIKE/NOT ILIKE) in Cython and Rust | F003, F006 | high | FEAT-152 code and comments |
| C3 | New operator names must be exempted in `jsonb_condition` or the dict renders as JSONB containment | F004 | high | `operators == 0` branch; FEAT-152 needed the same exemption |
| C4 | pgsql Rust-path exceptions are swallowed; the contains-too-short error must be raised pre-dispatch or in Cython too | F005 | high | bare `except Exception: pass` at `pgsql.pyx:311` |
| C5 | Dict operands arrive pre-quoted by `is_valid`; builders strip before escaping/quoting | F009, F003, F006 | high | pinned invariant (`f313e115`) + strip code in both builders |
| C6 | qsurl already defines startswith/contains/endswith → ILIKE with `like_escape`; reusable | F010 | high | direct read of `translate.py` |
| C7 | Generic SQL, mssql and BigQuery builders have no text-operator support; extending them is new work | F002, F007, F008 | medium | no LIKE handling found; mssql has no dict variant |
| C8 | `_where_element` is the right single hook for pre-dispatch validation | F009, F005 | medium | runs before both builders, but currently never raises — a behaviour change |
| C9 | Tests must be dual-path with stale-extension skip; `.so` must be rebuilt and staged | F011, F014 | high | existing test file + memory note |
| C10 | Legacy `field~` suffix prefix match must keep working | F003, F006 | medium | code present in both builders; no dedicated tests located |
| C11 | Pushing user regex to PostgreSQL needs a safety bound | F010 | low | inferred from `residual.py` precedent only |

Distribution: **7** high, **3** medium, **1** low.

---

## 5. Open Questions

### Resolved (during proposal phase, with the requester on 2026-10-07)

- [x] **U1 — Are `startswith` / `endswith` / `contains` case-insensitive or case-sensitive?** — *Resolved*: **Both variants.** Plain names are case-sensitive (`LIKE`); `i`-prefixed names (`istartswith`, `iendswith`, `icontains`) are case-insensitive (`ILIKE` on PostgreSQL).
  *Resolves claims*: C6
- [x] **U2 — Which dialects are in scope?** — *Resolved*: **All SQL dialects** — PostgreSQL, generic `SQLParser` (MySQL/SQLite), SQL Server and BigQuery, in both Cython and Rust builders. Regex stays PostgreSQL-only.
  *Resolves claims*: C7
- [x] **U3 — `regex` semantics** — *Resolved*: `regex`→`~`, `iregex`→`~*`, `not_regex`→`!~` (`not_iregex`→`!~*`), with a maximum pattern length guard mirroring `_MAX_REGEX_PATTERN_LENGTH` in `querysource/qsurl/residual.py`.
  *Resolves claims*: C11
- [x] **U4 — Where does the ≥3-character rule apply and which exception?** — *Resolved*: **`contains` only** (and its `i`/`not_` forms), raised as `ParserError` pre-dispatch in `AbstractParser._where_element` so it surfaces on both Rust and Cython paths. `startswith`, `endswith`, `like` accept any length.
  *Resolves claims*: C4, C8
- [x] **U5 — Include the negated family?** — *Resolved*: **Full `not_*` family** for every operator (`not_like`, `not_ilike`, `not_startswith`, `not_istartswith`, `not_endswith`, `not_iendswith`, `not_contains`, `not_icontains`, `not_regex`, `not_iregex`).
  *Resolves claims*: C6
- [x] **U6 — How do `i*` variants render on dialects without ILIKE?** — *Resolved*: **`LOWER(col) LIKE LOWER(pattern)`**, explicit and collation-independent, on generic SQL, SQL Server and BigQuery.
  *Resolves claims*: C7

### Unresolved (defer to spec / implementation)

- *(none)*

---

## 6. Recommended Next Step

**`/sdd-spec FEAT-180`** — *Rationale*: localization is high-confidence (C1–C5), all six
open questions are resolved, and the change extends an existing, recently exercised
extension point (FEAT-152 ILIKE) plus a pre-dispatch validator. The four-dialect scope
(U2) is large but mechanical: the spec should define one shared operator table and
escaping helper, then decompose tasks per dialect (PostgreSQL → generic SQL → BigQuery →
SQL Server) with Cython and Rust twins in each task.

### Alternatives

- **`/sdd-brainstorm FEAT-180`** — if you want to weigh a different placement for
  validation (e.g. a dedicated operator-normalisation step vs. `_where_element`) or a
  cross-dialect operator abstraction before committing to the PG-first scope.
- **`/sdd-task FEAT-180`** — not advised: the change spans Cython + Rust + abstract
  parser + tests and has five open semantic questions.
- **Manual review** — if the dialect scope (U2) should be decided by product before any spec.

---

## 7. Research Audit

| Artifact | Path |
|----------|------|
| State checkpoints | `sdd/state/FEAT-180/state.json` |
| Source (raw) | `sdd/state/FEAT-180/source.md` |
| Research plan | `sdd/state/FEAT-180/research_plan.json` |
| Findings (digests) | `sdd/state/FEAT-180/findings/F001-*.md` … `F014-*.md` |
| Synthesis (JSON) | `sdd/state/FEAT-180/synthesis.json` |
| Synthesis reasoning | not persisted |

**Budget consumed** (profile `default`):
- Files read: 21 / 40
- Grep calls: 18 / 25
- Git calls: 1 / 10
- Wall time: ~900s / 300s (all 13 planned queries executed; wall budget exceeded because
  of tool round-trips, no query was skipped)
- Truncated: **no**
- Wiki: **unavailable** (`wikitoolkit` not installed in `.venv`); grep/read/git only.

**Mode determination**: `auto` → resolved to `enrichment` (feature verbs "support", "add";
no failure described).

**Gates**: the plan-approval and review gates were auto-passed (autonomous run). The Q&A
gate ran interactively on 2026-10-07: 6 questions (U1–U6, U6 raised as a follow-up to
U2), 6 answered. `status` was set to `accepted` by the requester on 2026-10-07.

---

## 8. Provenance

| Field | Value |
|-------|-------|
| Generated by | `/sdd-proposal v1.0` |
| Synthesis prompt | `sdd/templates/synthesis.prompt.md v1.0` |
| Plan prompt | `sdd/templates/research_plan.prompt.md v1.0` |
| Schema versions | state=1.0, synthesis=1.0, research_plan=1.0 |
| Operator | Jesus Lara (via Claude Code) |
