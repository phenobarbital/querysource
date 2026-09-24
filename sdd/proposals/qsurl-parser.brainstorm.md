---
# SDD flow type and base branch (FEAT-145).
# - type: feature  (default)  → base_branch: dev (or any non-main branch)
# - type: hotfix              → base_branch MUST be: main
type: feature
base_branch: dev
# projects: parts of the codebase this doc concerns: `querysource` or a subsystem
#   (providers, parsers, rust-parsers, outputs, multiquery, handlers, datasources,
#   auth, cache, scheduler) or an area (sdd-tooling, dev-loop, docs, ci). Unknown values warn, not fail.
projects: [rust-parsers, querysource, providers, handlers, ci]
# tags: free-form kebab-case keywords for organizing specs (e.g. bigquery, cache).
tags: [qsurl, chumsky, pyo3, url-dialect, htsql, pushdown, lark]
---

# Brainstorm: qsurl — HTSQL-style URL query dialect (chumsky 0.13 + PyO3)

**Date**: 2026-09-24
**Author**: Jesus Lara
**Status**: exploration
**Recommended Option**: A

---

## Problem Statement

QuerySource exposes stored queries by slug, but the only way to shape a slug
from a GET request today is the ad-hoc querystring convention parsed by
`QueryHandler.parse_qs` / `query_parameters` (`querysource/utils/handlers.py:29-65`):
every parameter becomes a condition key, operators are encoded as key
suffixes (`col!`, `col~`) or nested dict/list values, and nothing tells the
caller *why* an input was rejected. That surface is fine for front-ends that
know the convention, but it is a poor target for LLM agents and for humans
writing a URL by hand:

- there is no closed grammar, so an agent cannot be given a schema it can
  self-correct against; malformed input is silently dropped
  (`querysource/parsers/sql.pyx:135`, "skip (reject) this condition");
- boolean structure is impossible — the pushdown filter is an implicit AND
  list (`querysource/parsers/sql.pyx:113-248`), there is no `or`, `not`,
  `contains`, `endswith` or `regex` at the database layer;
- literals are untyped strings, so `2024-01-01` and `'2024-01-01'` are the
  same thing to the driver;
- the dialect a caller sees depends on the backend (`col~` means `ILIKE 'x%'`
  only in `pgsql.pyx:270-278`), so nothing is engine-agnostic.

The accepted proposal (`sdd/proposals/qsurl-spec.md`) defines **qsurl**: a
compact, closed, HTSQL-style URL dialect —

```
/queries/hisense_stores{store_id,name,city}?state_code='CA'&opened>=2024-01-01:sort(name):top(50)
```

— compiled by a fast Rust parser (chumsky 0.13, pratt combinators) into an
engine-neutral JSON IR (`slug`, `fields`, boolean `filter` tree with the
`{column, expression, value}` leaves QuerySource already uses in
`querysource/types/dt/filters.py`, `sort`, `limit`, `offset`, `distinct`,
plus a `requires` capability set). The driver decides what to push down and
what to post-filter; the parser never sees SQL, CQL, Flux or SOQL. A
reference implementation exists (user-provided `qsurl.tar.gz`: `src/ast.rs`,
`src/parser.rs`, `src/ir.rs`, `src/python.rs`, `examples/parse.rs`, 11
`cargo test`s) and is the starting point for the crate.

**Who is affected**: LLM agents and tool-calling clients (primary), humans
composing ad-hoc reads, front-ends that want typed/structured filtering
without learning per-backend conventions, and QuerySource maintainers who
get one front-end that compiles to the existing execution path.

## Constraints & Requirements

- **Engine-agnostic by contract**: the IR never contains dialect fragments;
  agnosticism is delivered by *partial pushdown* — what a driver declares it
  supports goes into the native query, the rest is applied to the result
  dataframe. Results must be identical on every engine; only cost moves.
- **Rust first, Python parity**: parser in Rust (`chumsky = 0.13` with the
  `pratt` feature, `pyo3 = 0.29` behind a `python` cargo feature, as in the
  reference crate) as a **separate crate `rust/qsurl/`** (Round 1 decision),
  plus a **pure-Python fallback built on Lark** with a versioned `.lark`
  grammar (Round 2 decision). A shared corpus of cases must produce
  byte-identical IR on both implementations.
- **Closed grammar**: only reads; unknown pipeline operators return the list
  of valid ones; errors carry `offset`, `message`, `expected`, `pointer`.
- **Full phase-1 scope** (Round 1 decision): crate + Python module +
  IR→conditions translation + driver capabilities + residual post-filter
  stage in `QS` + aiohttp route `/api/v1/services/qsurl/{path:.*}` (and
  `?q=` on that same route) + output negotiation unchanged.
- **Existing pushdown convention is the target**: the translation layer
  emits the `conditions` dict `AbstractParser` already consumes
  (`fields`, `filter`/`where_cond`, `ordering`, `_limit`, `_offset`;
  `querysource/parsers/abstract.pyx:196-310`). No new placeholder syntax in
  stored queries.
- **Capabilities live next to the code that renders them**: a class
  attribute on `BaseProvider` with per-driver overrides (Round 2 decision).
- **No regression on the legacy route** `/api/v2/services/queries/{slug}`
  (`slug:format` suffix, `queryformat`, `_download`, PBAC pre-flight).
- **PBAC**: the new route must run the same `slug:execute` enforcement as
  `QueryService.query` (`querysource/handlers/service.py:134-225`).
- **Security**: URL is percent-decoded exactly once by the handler; the
  parser never sees `%xx`; identifiers reach the native builders only
  through the existing validating paths (`field_components`, Rust
  `process_fields` / `*_filter_conditions`).
- **Build/CI**: `make build-rust`, `make stage-rust`, `release.yml`
  `CIBW_BEFORE_BUILD` and `package-data` must ship a second extension
  (`querysource/qsurl/_qsurl*.so`) the same way they ship `_qs_parsers`.
- **Toolchain**: the reference crate uses `edition = "2024"` and let-chains
  (`src/ir.rs:176-178`), i.e. Rust ≥ 1.88. Local toolchain is 1.90; CI uses
  `dtolnay/rust-toolchain@stable`. The existing crate is `edition = "2021"`.
- **Performance**: parsing must be negligible next to query execution
  (sub-millisecond for typical URLs, ~8 KB practical URL ceiling).

---

## Options Explored

### Option A: Separate `rust/qsurl/` crate + Lark fallback + pushdown/residual layer in `querysource/qsurl/`

Port the reference crate as-is into `rust/qsurl/` (`ast.rs`, `parser.rs`,
`ir.rs`, `python.rs`, `examples/parse.rs`), keep pyo3 optional behind the
`python` feature so `cargo test` needs no interpreter, and let maturin
install it as `querysource.qsurl._qsurl`. A new Python package
`querysource/qsurl/` wraps it: `parse()` tries `_qsurl`, falls back to a Lark
parser driven by a versioned `grammar.lark`, and both return the same IR
dict. A `translate` module splits the IR against the executing provider's
`capabilities` into (a) the `conditions` dict the dialect parser already
understands and (b) a *residual* (filter subtree, sort keys, window) that a
new dataframe stage in `QS.query()` applies right before `_output_format`
(both on cache hits and provider fetches). A new aiohttp handler serves
`/api/v1/services/qsurl/{path:.*}`, accepts the whole query in the path or
as `<slug>?q=<rest>`, enforces PBAC, maps parse/lowering errors to HTTP 400
with the error JSON as body, and hands the result to the existing output
negotiation.

✅ **Pros:**
- `cargo test` is pure Rust (no libpython dance — the existing crate needs
  `--no-default-features` for that, `rust/Cargo.toml:20-29`).
- The IR is a stable, documented contract; tool-calling clients can submit
  the IR directly later without touching the parser.
- Correct on every engine from day one thanks to the residual stage; the
  `requires` set makes pushdown decisions explicit and testable.
- Fallback guarantees the sdist / pure-Python install still works; the
  `.lark` grammar doubles as the exportable grammar for constrained decoding.
- Zero changes to stored queries, dialect parsers or output writers.

❌ **Cons:**
- Two extension modules to build, stage and ship (Makefile, `release.yml`,
  `package-data`) — more CI surface.
- Two grammars to keep in sync (mitigated by the shared parity corpus).
- Adds a dataframe hop to single-slug `QS` when a residual exists (today
  single queries never materialise pandas outside the writers).
- Requires per-provider capability audits to be honest (a wrong declaration
  silently produces wrong pushdown).

📊 **Effort:** High

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `chumsky` 0.13 (`features = ["pratt"]`) | Parser combinators + pratt precedence for `\| & !` | Same version as the reference crate; `Rich` errors give `expected`/`found`/`span` |
| `pyo3` 0.29 (optional, feature `python`, `extension-module`) | Python binding | Matches `rust/Cargo.toml` (`pyo3 = "0.29"`); local cache has 0.29.2 |
| `serde` 1 + `serde_json` 1 | IR serialisation, error JSON | Already resolved in the workspace cache (1.0.151) |
| `maturin` ≥1.15,<2.0 | Build/install the cdylib | Already a dev dependency (`pyproject.toml:167`, `Makefile:17`) |
| `lark` 1.3.1 | Pure-Python fallback parser (Earley/LALR) | Present transitively (`cel-python`, `rfc3987-syntax`); must become a direct dependency via `uv add lark` |
| `pandas` | Residual filter/sort/window stage | Already used by `querysource/types/dt/filters.py` |

🔗 **Existing Code to Reuse:**
- `rust/Cargo.toml`, `rust/pyproject.toml` — template for the second crate (`module-name`, `bindings = "pyo3"`, release profile, the `extension-module` feature note).
- `querysource/qs_parsers/__init__.py` — the `HAS_RUST` two-level import fallback to replicate for `_qsurl`.
- `querysource/types/dt/filters.py:22` `build_condition`, `:204` `create_filter` — leaf evaluation for the residual stage (`contains`, `startswith`, `endswith`, `regex`, `is_null`, `not_null`, comparisons).
- `querysource/parsers/abstract.pyx:196-310` — the reserved `conditions` keys the translation layer targets (`fields`, `_limit`, `_offset`, `ordering`, `where_cond`/`filter`).
- `querysource/parsers/sql.pyx:113-248`, `querysource/parsers/pgsql.pyx:164-300` — pushdown value conventions (`{op: v}`, lists → `IN`, `col!`, `col~` → `ILIKE 'x%'`, `'null'`/`'!null'`).
- `querysource/handlers/service.py:134-260` (`QueryService.query`) — PBAC pre-flight, format negotiation and `QS` invocation to mirror in the new handler.
- `querysource/handlers/abstract.py:56-86` `AbstractHandler.format`, `:107` `NotFound`, `:133` `Error` — response helpers.
- `querysource/services.py:171-189` — route registration block to extend.
- `Makefile:63-80` (`build-rust`, `stage-rust`), `.github/workflows/release.yml:45-53` (`CIBW_BEFORE_BUILD`) — build/ship pattern for a second `.so`.
- `tests/test_rust_parsers.py:1-15` — skip-if-no-extension test pattern; `tests/e2e/conftest.py` — dry-run `QS` harness with in-memory definitions.

---

### Option B: qsurl as a module inside the existing `_qs_parsers` crate

Add `chumsky`/`serde` to `rust/Cargo.toml`, drop the reference crate's
`python` feature and register `qsurl_parse` / `qsurl_requires` in
`rust/src/lib.rs` next to the dialect helpers. Everything else (Python
wrapper, Lark fallback, translation, residual stage, handler) is identical
to Option A.

✅ **Pros:**
- One build, one `.so`, no Makefile/CI/package-data changes.
- The Python wrapper only needs `from querysource.qs_parsers import _qs_parsers`.

❌ **Cons:**
- pyo3 is always linked, so `cargo test` for the grammar needs the
  `--no-default-features` workaround the existing crate documents.
- Couples an unrelated grammar to the dialect string-processing crate;
  every qsurl change rebuilds all parsers and vice-versa.
- Loses the standalone `cargo run --example parse` CLI unless duplicated.
- Rejected in Round 1 (user chose the separate crate).

📊 **Effort:** Medium

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `chumsky` 0.13, `serde`, `serde_json` | as in A | added to the existing crate |

🔗 **Existing Code to Reuse:**
- `rust/src/lib.rs:33-80` — `#[pymodule] fn _qs_parsers` registration list.
- Everything listed under Option A except the build/CI items.

---

### Option C: Python-first (Lark only), Rust as a later accelerator

Ship only the Lark grammar and a Python lowering (`ast` → IR) in
`querysource/qsurl/`; defer the Rust crate to a follow-up spec once the IR
and the pushdown contract have stabilised in production.

✅ **Pros:**
- Lowest effort; single implementation, no build/CI changes.
- Grammar iterations are cheap while the dialect is still being tuned.

❌ **Cons:**
- Lark (Earley by default, LALR with care) is 1–2 orders of magnitude slower
  than chumsky; with `~8 KB` URLs on a hot endpoint this is measurable.
- Contradicts the accepted proposal ("parser en Rust") and the user's
  explicit request (chumsky 0.13 + PyO3).
- The Rust port later becomes a parity project anyway.

📊 **Effort:** Low

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `lark` 1.3.1 | grammar + parser | direct dependency |

🔗 **Existing Code to Reuse:**
- Same Python-side items as Option A (filters, handlers, services).

---

### Option D (unconventional): Compile qsurl to a MultiQuery definition

Instead of touching `QS`, lower the IR into an inline MultiQuery: the slug
becomes a `queries` source with the pushdown conditions, and the residual
becomes `Filter` / ordering / window operators executed by the existing
multi-query pipeline (`querysource/queries/multi/operators/filter/flt.py`),
served through `QueryHandler` (`querysource/handlers/multi.py`).

✅ **Pros:**
- Reuses the dataframe operator pipeline and its registry without adding a
  stage to `QS`; residual semantics are already tested there.
- Naturally extends to phase-2 aggregation (`:group(...)`) via existing
  operators.

❌ **Cons:**
- Execution path, caching, PBAC pre-flight and output handling become the
  MultiQuery ones — a single-slug read pays the multi-query threading and
  DataFrame materialisation even when there is no residual.
- `Filter` combines conditions with a single `operator` (`&` or `|`,
  `flt.py:24-27`), so nested `and`/`or`/`not` trees need a new operator anyway.
- Rejected in Round 2 (user chose the post-filter stage in `QS`).

📊 **Effort:** Medium

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| none new | | |

🔗 **Existing Code to Reuse:**
- `querysource/queries/multi/operators/filter/flt.py` — `Filter` operator.
- `querysource/handlers/multi.py` — `QueryHandler`.

---

## Recommendation

**Option A** is recommended because:

- It is the only option that satisfies every decision taken in discovery:
  Rust parser on chumsky 0.13 with the pyo3 binding behind a `python`
  feature (Round 1), separate crate under `rust/qsurl/` (Round 1), full
  phase-1 scope including the HTTP route (Round 1), a Lark fallback with a
  parity corpus (Round 1/2), a residual post-filter stage in `QS`
  (Round 2), capabilities declared on providers (Round 2) and a dedicated,
  unambiguous route `/api/v1/services/qsurl/{path:.*}` with `?q=` (Round 2).
- The trade-off accepted is build surface: a second maturin target and a
  second staged `.so`. That cost is paid once in `Makefile`, `release.yml`
  and `package-data`, following a pattern that already exists for
  `_qs_parsers`, and it buys a pure-Rust `cargo test` loop and an
  independently versioned grammar.
- The residual stage is the one genuinely new runtime piece. It is bounded:
  it only runs when `requires - capabilities(provider)` is non-empty, it
  reuses `create_filter`/`build_condition` for leaves, and it is the place
  where a future cost guard (reject residual-only filters on partition-keyed
  stores) naturally lives.
- Option B's coupling and Option C's speed regression are avoidable; Option
  D changes the execution path for every qsurl read to solve a problem only
  residual reads have.

---

## Feature Description

### User-Facing Behavior

- A client (human, front-end or LLM agent) issues
  `GET /api/v1/services/qsurl/<query>` where `<query>` is the percent-encoded
  qsurl string, e.g.
  `/api/v1/services/qsurl/hisense_stores%7Bstore_id,name%7D%3Fstate_code%3D'CA':top(50)`,
  or the equivalent split form `GET /api/v1/services/qsurl/hisense_stores?q=<rest>`
  where `<rest>` is everything after the slug (`{...}?...:...`). The server
  concatenates `slug + q`, decodes once, and parses.
- Output format is negotiated exactly as today (`Accept`, `queryformat=`,
  `_download`, `_filename`); the `slug:format` colon suffix of the legacy
  route is **not** supported here because `:` introduces pipeline operators.
- A valid query returns the same payload the legacy slug route would return
  for the equivalent conditions: same writers, same `DataNotFound` → 204,
  same PBAC 404 on deny.
- Any grammar or lowering error returns **400** with a JSON body that is the
  parser's error object verbatim: `kind` (`parse` | `lower`), `offset`,
  `message`, `found`, `expected`, `pointer` (query with a `^` under the
  offset). Unknown pipeline operators list the valid ones
  (`:sort, :top, :limit, :skip, :offset, :distinct`).
- Capabilities the executing driver does not support are still honoured
  (applied in memory); the response is correct, only slower. Capabilities
  nobody supports in phase 1 (`functions`, `navigation`) return 400 with a
  `kind: "unsupported"` error naming the offending feature.
- From Python: `from querysource.qsurl import parse, requires` returns the
  IR as a `dict` / the capability list; `querysource.qsurl.HAS_RUST` tells
  whether the Rust extension or the Lark fallback is active.

### Internal Behavior

1. **Crate `rust/qsurl/`** (`ast.rs`, `parser.rs`, `ir.rs`, `python.rs`,
   `examples/parse.rs`, `Cargo.toml` with `crate-type = ["cdylib","rlib"]`,
   `python = ["dep:pyo3"]`; `pyproject.toml` with
   `module-name = "querysource.qsurl._qsurl"`, `features = ["python", "pyo3/extension-module"]`).
   `parse_to_json(&str) -> Result<String, Error>` is the FFI surface;
   `python.rs` exposes `parse(src) -> str` (JSON IR) and
   `requires(src) -> list[str]`, raising `ValueError` whose message is the
   error JSON. Keeping the FFI to strings avoids a `pythonize` dependency
   and makes the parity corpus a byte comparison.
2. **Python package `querysource/qsurl/`**:
   - `__init__.py` — `HAS_RUST` import ladder (in-wheel `._qsurl`, then
     top-level `_qsurl` for `maturin develop`), else `from ._fallback import parse`;
     public `parse(src) -> dict`, `requires(src) -> list[str]`;
     `QSUrlError(QueryException)` with `.offset/.message/.expected/.pointer/.kind`
     and `.to_dict()`, raised by both back-ends.
   - `grammar.lark` + `_fallback.py` — Lark grammar (LALR where possible)
     plus a transformer producing the same AST shape and the same lowering
     rules as `ir.rs` (flip inverted comparisons, `=null`/`!col` → `is_null`,
     lists only with `=`/`!=`, single-leaf filter wrapped in `{"and":[...]}`,
     `dtype` only for unquoted date/datetime, `requires` sorted as the Rust
     `BTreeSet` order). Shipped via `package-data`.
   - `capabilities.py` — the closed vocabulary (`select, alias, filter, or,
     not, in_list, null_check, text_match, regex, functions, navigation,
     sort, limit, offset, distinct`) as constants, plus the phase-1
     "nobody supports" set.
   - `translate.py` — `split(ir, capabilities) -> (conditions, residual)`.
     Pushdown mapping onto the existing flat convention: root `and`
     conjuncts that are single leaves with `==`/`!=`/`<`/`<=`/`>`/`>=`
     → `{col: v}` / `{col: {op: v}}` / `col!`; list with `==`/`!=` →
     `{col: [..]}` / `{"col!": [..]}` (`in_list`); `is_null`/`not_null` →
     `'null'`/`'!null'` (`null_check`); `startswith` → `col~` only when the
     driver declares `text_match` (today: PostgreSQL only); `fields` →
     `conditions['fields']` (alias as `col AS alias` only where the driver
     declares `alias`); `sort` → `ordering` (`"col"`/`"col DESC"`,
     `sql.pyx:292-306`); `limit`/`offset` → `_limit`/`_offset`
     (`abstract.pyx:206-232`). Everything else — any `or`/`not` subtree,
     `contains`, `not_contains`, `endswith`, `regex`, sort/limit/offset when
     not declared, alias when not declared, `distinct` — goes to the
     residual. If the filter root is `or`, nothing is pushed down and the
     whole tree is residual (see cost-guard open question).
   - `residual.py` — `apply(df, residual) -> df`: evaluates the boolean tree
     over a pandas DataFrame using `build_condition` for leaves and
     `&`/`|`/`~` composition for nodes, then sort, offset, limit, distinct.
3. **Capabilities on providers**: `BaseProvider.capabilities: frozenset[str]`
   (`querysource/providers/abstract.py:33`) with a minimal default
   (`select, filter, in_list, null_check`); `sqlProvider`
   (`querysource/providers/sql.py:32`) adds `sort, limit, offset`;
   `pgProvider` (`querysource/providers/pg.py`) adds `text_match` (startswith
   via `col~`) and `alias`; `cassandraProvider`
   (`querysource/providers/cassandra.py`) restricts to `select, filter,
   in_list, null_check, limit`; other providers inherit the default until
   audited. `QS` reads `self._qs.capabilities` after `build_provider()`.
4. **`QS` residual stage**: `QS.__init__` accepts a `residual` kwarg (kept in
   `self._residual`); `QS.query()` (`querysource/queries/qs.py:428-596`)
   applies `residual.apply` to the result — after cache deserialisation on
   hits and after `check_empty` on provider fetches — before
   `await self._output_format(...)`. Record lists are materialised to a
   DataFrame only when a residual exists; an empty post-filter result raises
   `DataNotFound` like an empty provider result does. Cache key stays the
   pushdown query checksum (the residual is applied on top of cached rows).
5. **Handler `QSUrlService`** (`querysource/handlers/qsurl.py`, exported in
   `querysource/handlers/__init__.py`): resolves `path` + optional `q`,
   percent-decodes once, `parse()` → 400 on `QSUrlError`, PBAC
   `slug:execute` with the same tenant-aware branch as `QueryService.query`
   (`service.py:186-225`), builds `QS(slug, conditions=pushdown,
   residual=residual, request=request)`, and delegates output/format handling
   to the same helpers the legacy handler uses. Route registered in
   `querysource/services.py` next to the existing queries block
   (`services.py:181-189`): `add_get('/api/v1/services/qsurl/{path:.*}', ...)`.
   The `q` parameter must not collide with `EXCLUDED_QUERY_PARAMETERS`
   (`querysource/conf.py:387-389`: `auth, apikey, api_key, authorization`).
6. **Build & ship**: `make build-rust` runs `maturin develop` for both
   manifests; `make stage-rust` also copies `_qsurl*.so` into
   `querysource/qsurl/`; `release.yml` `CIBW_BEFORE_BUILD` builds the second
   wheel and extracts its `.so`; `pyproject.toml` `package-data` adds
   `"querysource.qsurl" = ["*.so", "*.pyd", "*.lark"]`; `lark` becomes a
   direct dependency.
7. **Tests**: `cargo test` in `rust/qsurl` (ported 11 tests + new ones);
   `tests/qsurl/` with a JSON parity corpus run against Rust and Lark
   (`pytest.mark.skipif(not HAS_RUST)` for the Rust half), translation
   unit tests per capability set, residual tests on DataFrames, handler
   tests with the fake-request pattern of
   `tests/handlers/test_query_param_exclusion.py`, and a dry-run e2e using
   `tests/e2e/conftest.py` proving the rendered SQL for a pushdown-only
   query on PostgreSQL.

### Edge Cases & Error Handling

- **Whitespace**: decoded `%20` around any token is tolerated
  (`parser.rs:35-36`); the handler must not strip or re-encode.
- **`=` vs `==`**, **`:top` vs `:limit`**, **`:skip` vs `:offset`** are
  synonyms; `:top`/`:skip` given twice is a parse error (`parser.rs:323-332`).
- **Inverted comparison** `100<price` → `price > 100`; text operators with
  the column on the right are a lowering error (`ir.rs:147-163`).
- **Lists** only with `=`/`!=`; `name~('a','b')` is a lowering error.
- **Bare literal** as a condition (`?1=2`, `?'x'`) is a lowering error.
- **Typed literals**: unquoted `2024-01-01` → `dtype: date`, quoted stays
  string; pushdown emits the string value and lets the column type cast;
  the residual stage compares with `pd.to_datetime` when `dtype` is set.
- **Root `or`** → full residual; large tables on non-indexed engines can
  become a full scan (cost guard: open question).
- **Slug not found / PBAC deny** → identical to legacy (404), evaluated
  *before* parsing side effects reach the database.
- **Empty after residual** → `DataNotFound` → 204, consistent with today.
- **Rust extension missing** → Lark fallback with a one-time `warning`
  log; behaviour identical by contract (parity corpus).
- **`?q=` plus a path that already contains `{`/`?`/`:`** → 400
  (`kind: "parse"`, message explains that `q` must carry everything after
  the slug).
- **Percent-encoding**: `/` inside quoted strings must be encoded as `%2F`
  by the client; the wildcard route captures the raw remainder.
- **Distinct** has no pushdown in phase 1 (`AbstractParser._distinct` is
  set but no `conditions` key populates it); it is always residual.

---

## Capabilities

### New Capabilities
- `qsurl-parser`: Rust (chumsky 0.13) + Lark parser for the qsurl dialect
  producing the engine-neutral IR with `requires`.
- `qsurl-pushdown-translation`: IR → `conditions` dict + residual, driven by
  provider capabilities.
- `provider-capabilities`: `BaseProvider.capabilities` declaration with
  per-driver overrides.
- `qs-residual-stage`: in-memory filter/sort/window stage in `QS.query()`.
- `qsurl-http-route`: `GET /api/v1/services/qsurl/{path:.*}` (+ `?q=`) with
  PBAC and structured 400 errors.

### Modified Capabilities
- none (legacy `/api/v2/services/queries/{slug}` untouched).

---

## Impact & Integration

| Affected Component | Impact Type | Notes |
|---|---|---|
| `rust/qsurl/` (new crate) | adds | `Cargo.toml`, `pyproject.toml`, `src/{lib,ast,parser,ir,python}.rs`, `examples/parse.rs` ported from the reference tarball |
| `querysource/qsurl/` (new package) | adds | `__init__.py`, `grammar.lark`, `_fallback.py`, `capabilities.py`, `translate.py`, `residual.py`, `errors` |
| `querysource/providers/abstract.py` | extends | `capabilities` class attribute on `BaseProvider` |
| `querysource/providers/sql.py`, `pg.py`, `cassandra.py` (+ audited others) | extends | per-driver `capabilities` overrides |
| `querysource/queries/qs.py` | modifies | `residual` kwarg; residual stage before `_output_format` on both cache and provider paths |
| `querysource/handlers/qsurl.py` (new), `handlers/__init__.py` | adds | `QSUrlService` handler |
| `querysource/services.py` | extends | route `/api/v1/services/qsurl/{path:.*}` |
| `pyproject.toml` | modifies | `lark` direct dependency; `package-data` for `querysource.qsurl` |
| `Makefile` | modifies | `build-rust` / `stage-rust` for two manifests |
| `.github/workflows/release.yml` | modifies | build + extract second `.so` in `CIBW_BEFORE_BUILD` |
| `tests/qsurl/`, `tests/handlers/`, `tests/e2e/` | adds | parity corpus, translation, residual, handler, dry-run e2e |
| `docs/` | adds | dialect reference (grammar, operator table, error contract) |

No breaking changes. New runtime dependency: `lark`. New build dependency:
none (maturin already present). Deployment: wheels gain a second extension.

---

## Code Context

### User-Provided Code

The user supplied the reference crate as `~/Descargas/qsurl.tar.gz`
(extracted during discovery). Key excerpts, verbatim:

```toml
# Source: user-provided qsurl/Cargo.toml
[package]
name = "qsurl"
version = "0.1.0"
edition = "2024"

[lib]
crate-type = ["cdylib", "rlib"]

[features]
default = []
python = ["dep:pyo3"]

[dependencies]
chumsky = { version = "0.13.0", features = ["pratt"] }
pyo3 = { version = "0.29.2", optional = true, features = ["extension-module"] }
serde = { version = "1.0.229", features = ["derive"] }
serde_json = "1.0.151"
```

```rust
// Source: user-provided qsurl/src/python.rs
#[pyfunction]
fn parse(src: &str) -> PyResult<String> {
    crate::parse_to_json(src).map_err(|e| PyValueError::new_err(e.to_json()))
}

#[pyfunction]
fn requires(src: &str) -> PyResult<Vec<String>> { /* ir["requires"] as Vec<String> */ }

#[pymodule]
fn qsurl(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parse, m)?)?;
    m.add_function(wrap_pyfunction!(requires, m)?)?;
    Ok(())
}
```

```rust
// Source: user-provided qsurl/src/lib.rs (error contract)
#[derive(Debug, Clone, Serialize)]
pub struct ParseError {
    pub offset: usize,
    pub message: String,
    pub found: Option<String>,
    pub expected: Vec<String>,
    pub pointer: String,
}
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Error { Parse(ParseError), Lower { message: String } }
pub fn parse(src: &str) -> Result<Query, ParseError>
pub fn parse_to_ir(src: &str) -> Result<serde_json::Value, Error>
pub fn parse_to_json(src: &str) -> Result<String, Error>
```

```rust
// Source: user-provided qsurl/src/ir.rs (capability vocabulary)
#[serde(rename_all = "snake_case")]
pub enum Feature { Select, Alias, Filter, Or, Not, InList, NullCheck, TextMatch,
                   Regex, Functions, Navigation, Sort, Limit, Offset, Distinct }
pub fn lower(q: &Query) -> Result<Value, LowerError>
```

```rust
// Source: user-provided qsurl/src/parser.rs (grammar, phase 1)
// query      := prefix? slug selection? filter? pipe*
// prefix     := '/' | '/queries/' | 'queries/'
// slug       := [A-Za-z0-9_-]+
// selection  := '{' field (',' field)* ','? '}'
// field      := path (':as(' ident ')')?
// path       := ident ('.' ident)*
// filter     := '?' expr
// expr       := expr '|' expr | expr '&' expr | '!' expr | '(' expr ')' | operand (cmp operand)?
// cmp        := '==' | '=' | '!=' | '<=' | '>=' | '<' | '>' | '~' | '!~' | '^=' | '$=' | '=~'
// operand    := list | literal | call | path
// literal    := null | true | false | datetime | date | number | string
// pipe       := ':sort(' sortkey (',' sortkey)* ')' | ':top(' int ')' | ':limit(' int ')'
//             | ':skip(' int ')' | ':offset(' int ')' | ':distinct'
pub fn query<'a>() -> impl Parser<'a, &'a str, Query, Err<'a>>
```

Reference-crate test names (11, `src/lib.rs:127-297`) to port:
`example_query_lowers_to_querysource_ir`, `works_without_prefix_selection_or_filter`,
`tolerates_decoded_whitespace`, `precedence_or_and_not_and_parens`,
`null_checks_lists_and_text_operators`, `functions_navigation_alias_and_flipped_comparison`,
`literals_are_typed`, `unknown_pipe_operator_lists_valid_ones`,
`syntax_error_points_at_offset`, `lowering_errors_are_explicit`, `duplicate_top_is_rejected`.

### Verified Codebase References

#### Classes & Signatures
```python
# From querysource/queries/qs.py:49-121
class QS(BaseQuery):
    def __init__(self, slug: str = '', conditions: dict = None, request: web.Request = None,
                 loop: asyncio.AbstractEventLoop = None, *, tenant: str | None = None,
                 definition: "LoadedDefinition | None" = None,
                 principal: "QSPrincipal | None" = None, **kwargs): ...   # line 55
    async def build_provider(self): ...                                    # line 157 — sets self._qs = self._provider(**args) at ~L279
    async def query(self, output_format: str | None = None): ...           # line 428 — calls await self._output_format(self._result, error) on cache hit (~L505) and after provider fetch (~L593)

# From querysource/queries/base.py:24-137
class BaseQuery(AbstractQuery):
    async def output(self, result, error): ...                             # line 127
    def output_format(self, frmt: str = 'native', **kwargs): ...          # line 132 — self._output_format = OutputFactory(self, frmt=frmt, **kwargs)

# From querysource/providers/abstract.py:33-330
class BaseProvider(ABC):
    __parser__: AbstractParser = None                                      # line 35
    _parser_options: dict = {}                                             # line 36
    replacement: dict = {"fields": "*", ..., "where_cond": "", "and_cond": "", "filter": ""}  # line 38
    def __init__(self, slug: str = '', query: Any = None, qstype: str = '', connection: Callable = None,
                 definition: Union[QueryModel, dict] = None, conditions: dict = None,
                 request: web.Request = None, **kwargs): ...              # line 48
    async def query(self): ...  # abstract                                 # line 297
    @property
    def parser(self): return self._parser                                  # line 311
    def checksum(self): return get_hash(self._query)                       # line 323

# From querysource/providers/sql.py:32-48
class sqlProvider(BaseProvider):
    __parser__ = SQLParser                                                 # line 48
# From querysource/providers/pg.py:21
    __parser__ = pgSQLParser
# From querysource/providers/cassandra.py:31
    __parser__ = CQLParser

# From querysource/parsers/abstract.pyx (Cython)
cdef class AbstractParser:                                                 # line 27
    def __cinit__(self, *args, definition: object, conditions: object, query: str = None, **kwargs)  # line 30
    cdef void _query_fields_sync(self)      # line 199 — self.fields = self.conditions.pop('fields', [])
    cdef void _query_limit_sync(self)       # line 206 — int(self.conditions.pop('_limit', 0)) or 'querylimit'
    cdef void _offset_pagination_sync(self) # line 214 — self.conditions.pop('_offset', 0); 'paged'; 'page'
    cdef void _ordering_sync(self)          # line 259 — 'order_by' + 'ordering' (str → split(','))
    cdef void _query_filter_sync(self)      # line 294 — 'where_cond' else 'filter' else definition.filtering
# From querysource/parsers/abstract.pxd:17-36
    cdef public dict filter; cdef public list fields; cdef public list ordering
    cdef public int32_t _limit; cdef public int32_t _offset; cdef bint _distinct

# From querysource/parsers/sql.pyx
    self._base_sql: str = 'SELECT {fields} FROM {tablename} {filter} {grouping} {offset} {limit}'  # line 98
    async def filter_conditions(self, sql)   # line 113 — Rust fast path _rs.filter_conditions(sql, dict(self.filter), dict(self.cond_definition)); Cython fallback handles {op: v} (COMPARISON_TOKENS line 25), list → IN / 'key!' → NOT IN, 'null'/'!null', '!value', BETWEEN, bool
    async def order_by(self, sql)            # line 292 — ', '.join(self.ordering)
    async def limiting(self, sql, limit=None, offset=None)  # line 308 — LIMIT/OFFSET or {limit}/{offset} placeholders
    async def process_fields(self, sql)      # ~line 328 — Rust _rs.process_fields(sql, self.fields, bool(self._add_fields), self.query_raw); replaces ' * FROM' with '{fields}'
# From querysource/parsers/pgsql.pyx:164-300
    async def filter_conditions(self, sql)   # line 164 — _rs.pgsql_filter_conditions(sql, self.filter, cond_def)
    # Cython fallback: key suffix '~' → "{name} ILIKE '{base}%'" (line ~270), '!~' → NOT ILIKE, jsonb_condition, BETWEEN for date/datetime lists, array formats

# From querysource/types/dt/filters.py
def build_condition(expression: str, column: str, value, condition: dict, df: pd.DataFrame = None) -> str  # line 22
def create_filter_chain(expression: list, column: str, df: pd.DataFrame) -> list                          # line 173
def create_filter(_filter: list, df: pd.DataFrame) -> list                                                 # line 204
# Supported expressions in build_condition: is_null, not_null, is_empty, ==, !=, >, <, >=, <=, regex, not_regex,
# fullmatch, contains, not_contains, startswith, not_startswith, endswith, not_endswith, gt/lt (str len), {"$column": ...}

# From querysource/queries/multi/operators/filter/flt.py:11
class Filter(AbstractOperator):   # conditions: [{column, expression, value}], operator '&' | '|'

# From querysource/handlers/service.py:31-818
class QueryService(QueryHandler):
    async def query(self, request)          # line 134 — slug from match_info, `slug, _format = slug.split(':')` (line 175), PBAC via _enforce_owned_slug / _enforce_pbac (lines 186-225), queryformat/_download/_filename params
# From querysource/handlers/abstract.py:32-527
class AbstractHandler:
    def format(self, request: web.Request, args: dict, ctype: str = None) -> str   # line 56
    def NotFound(...)   # line 107
    def Error(...)      # line 133
    async def _enforce_pbac(...)        # line 322
    async def _enforce_owned_slug(...)  # line 440
# From querysource/utils/handlers.py
def _is_excluded_param(key: str) -> bool     # line 17 — key.lower() in EXCLUDED_QUERY_PARAMETERS
class QueryHandler(BaseHandler):             # line 27
    def query_parameters(self, request: web.Request = None) -> dict   # line 29
    def parse_qs(self, request: web.Request = None) -> Optional[dict] # line 37

# From querysource/exceptions.py
class QueryException(Exception)   # line 6
class QueryError(QueryException)  # line 49
class DataNotFound(QueryException)# line 53
class ParserError(QueryException) # line 86
```

#### Verified Imports
```python
# These imports have been confirmed to work:
from querysource.queries import QS                         # querysource/queries/__init__.py:6
from querysource.queries.base import BaseQuery             # querysource/queries/base.py:24
from querysource.providers.abstract import BaseProvider    # querysource/providers/abstract.py:33
from querysource.providers.sql import sqlProvider          # querysource/providers/sql.py:32
from querysource.parsers.abstract import AbstractParser    # querysource/parsers/abstract.pyx:27 (Cython)
from querysource.types.dt.filters import create_filter, build_condition   # querysource/types/dt/filters.py:22,204
from querysource.qs_parsers import HAS_RUST                # querysource/qs_parsers/__init__.py:10-25
from querysource.handlers import QueryService              # querysource/handlers/__init__.py:12
from querysource.handlers.abstract import AbstractHandler  # querysource/handlers/abstract.py:32
from querysource.utils.handlers import QueryHandler, _is_excluded_param   # querysource/utils/handlers.py:17,27
from querysource.conf import EXCLUDED_QUERY_PARAMETERS     # querysource/conf.py:387
from querysource.exceptions import QueryException, DataNotFound, QueryError   # querysource/exceptions.py:6,53,49
from querysource.outputs.dt import OutputFactory           # querysource/queries/base.py:14
from querysource.queries.multi.operators.filter import Filter   # querysource/queries/multi/operators/filter/__init__.py:4
```

#### Key Attributes & Constants
- `QS._qs` → `BaseProvider` instance (querysource/queries/qs.py:80, assigned in `build_provider` ~L279)
- `QS._result`, `QS._output_format` (OutputFactory), `QS.is_cached` (qs.py:80-83, base.py:133)
- `BaseProvider.__parser__`, `BaseProvider._parser_options`, `BaseProvider.replacement` (providers/abstract.py:35-46)
- `AbstractParser.filter: dict`, `.fields: list`, `.ordering: list`, `._limit`, `._offset`, `._distinct` (parsers/abstract.pxd:17-36)
- Reserved `conditions` keys consumed by the parser: `fields`, `_limit`/`querylimit`, `_offset`, `paged`, `page`, `group_by`/`grouping`, `order_by`/`ordering`, `filter_options`, `qry_options`, `where_cond`/`filter`, `cond_definition`, `hierarchy`, `slug`, `refresh` (parsers/abstract.pyx:176-320)
- `COMPARISON_TOKENS = ('>=', '<=', '<>', '!=', '<', '>')` (parsers/sql.pyx:25)
- `EXCLUDED_QUERY_PARAMETERS` default `{'auth', 'apikey', 'api_key', 'authorization'}` (conf.py:387-389)
- Route block for queries: `services.py:171-189` (`/api/v2/services/queries`, `/api/v2/services/queries/{slug}` GET/POST/PATCH/HEAD)
- `rust/Cargo.toml`: `pyo3 = "0.29"`, `rayon`, `regex`, `once_cell`; `[lib] name = "_qs_parsers"`; feature `extension-module` default; `[profile.release] opt-level=3, lto=true, codegen-units=1`
- `rust/pyproject.toml`: `[tool.maturin] module-name = "querysource.qs_parsers._qs_parsers"`, `bindings = "pyo3"`, `features = ["pyo3/extension-module"]`, `requires = ["maturin>=1.15,<2.0"]`
- `Makefile:17` `MATURIN := .venv/bin/maturin`; `:63-64` `build-rust`; `:72-80` `stage-rust` (copies `_qs_parsers*.so` into `querysource/qs_parsers/`)
- `pyproject.toml:192-194` `[tool.setuptools.package-data] "querysource.qs_parsers" = ["*.so", "*.pyd"]`
- `.github/workflows/release.yml:45-53` `CIBW_BEFORE_BUILD` (maturin build + zip extract of `_qs_parsers` `.so`)
- Toolchain observed: `rustc 1.90.0`, `cargo 1.90.0`; cached crates `pyo3-0.29.2`, `serde_json-1.0.151` (no `chumsky` cached yet — first build downloads it)
- `lark 1.3.1` and `pyparsing 3.3.2` installed transitively (`uv pip show`), neither declared in `pyproject.toml`
- `tests/e2e/conftest.py` — dry-run harness stubbing `querysource.parsers.abstract.AsyncDB` and `BaseQuery.get_definition_repository`

### Does NOT Exist (Anti-Hallucination)
- ~~`rust/qsurl/`~~, ~~`querysource/qsurl/`~~, ~~`querysource/parsers/qsurl*`~~ — nothing named qsurl exists in the repo yet
- ~~`BaseProvider.capabilities`~~ — no provider or parser declares capabilities today (the only `capabilities` symbol is a dict inside `querysource/queries/describe.py:236`, unrelated)
- ~~`QS.residual` / `QS.post_filter`~~ — single-slug `QS` has no dataframe stage; DataFrame filtering exists only in MultiQuery operators (`Filter`) and `types/dt/filters.py`
- ~~`querysource/queries/abstract.py`~~ — does not exist; the base class is `querysource/queries/base.py` (`BaseQuery`)
- ~~`querysource/handlers/query.py`~~ — does not exist; slug execution is `querysource/handlers/service.py` (`QueryService`), multi-query is `handlers/multi.py` (`QueryHandler`)
- ~~`.github/workflows/ci.yml`~~ — only `codeql-analysis.yml` and `release.yml` exist
- ~~`lark` / `pyparsing` in `pyproject.toml`~~ — transitive only; must be added explicitly
- ~~`contains` / `endswith` / `regex` / `or` / `not` pushdown~~ — the flat `filter` dict cannot express them in any dialect parser; only `startswith` (`col~` → `ILIKE 'x%'`) exists and only in `pgsql.pyx`
- ~~`distinct` conditions key~~ — `AbstractParser._distinct` is declared (`abstract.pxd:36`) but no `_extract_options` step populates it from `conditions`
- ~~`_rs.filter_conditions` accepting `{column, expression, value}` leaves~~ — the Rust/Cython pushdown builders consume the flat `{key: value}` dict only
- ~~`slug:format` on the new route~~ — the colon suffix belongs to the legacy handler (`service.py:175`) and conflicts with pipeline operators

---

## Parallelism Assessment

- **Internal parallelism**: three clusters are independent once the IR
  contract (from the reference crate) is frozen: (1) `rust/qsurl/` crate +
  Makefile/release/package-data wiring + `cargo test`; (2) Lark grammar,
  `_fallback.py` and the parity corpus; (3) provider `capabilities` +
  `translate.py` + `residual.py` + `QS` stage. The handler/route task
  depends on (1)/(2) for `parse()` and on (3) for execution.
- **Cross-feature independence**: no per-spec index in `sdd/tasks/index/`
  has pending tasks today, so no in-flight spec shares
  `querysource/queries/qs.py`, `querysource/providers/abstract.py`,
  `querysource/services.py`, `Makefile` or `release.yml`.
- **Recommended isolation**: `mixed`.
- **Rationale**: clusters (1) and (2) touch disjoint file sets
  (`rust/qsurl/**` + build files vs `querysource/qsurl/grammar.lark`,
  `_fallback.py`, `tests/qsurl/`) and can run in separate worktrees; cluster
  (3) and the handler modify core files (`qs.py`, providers, `services.py`)
  and should run sequentially in the feature worktree, merging (1)/(2) first.

---

## Open Questions

- [x] Flow type and base branch — *Owner: Jesus Lara*: `feature` on `dev`.
- [x] Crate placement — *Owner: Jesus Lara*: separate crate `rust/qsurl/` with the pyo3 binding behind the `python` feature, installed as `querysource.qsurl._qsurl`.
- [x] Phase-1 scope — *Owner: Jesus Lara*: parser + binding + HTTP route + execution through `QS` (full §7 of the proposal).
- [x] Behaviour without the Rust extension — *Owner: Jesus Lara*: pure-Python fallback on Lark with a versioned `.lark` grammar and a Rust/Lark parity corpus.
- [x] Residual execution — *Owner: Jesus Lara*: new dataframe post-filter stage in `QS.query()` reusing `types/dt/filters.py`.
- [x] Capability declaration — *Owner: Jesus Lara*: `capabilities` class attribute on `BaseProvider` with per-driver overrides.
- [x] Route shape — *Owner: Jesus Lara*: new route `/api/v1/services/qsurl/{path:.*}` plus `?q=` on that route; legacy `/api/v2/services/queries/{slug}` untouched.
- [ ] Cost guard for residual-only filters (root `or`, or any residual on partition-keyed stores such as Cassandra): reject with 400, cap with a `QSURL_MAX_RESIDUAL_ROWS` setting, or allow silently in phase 1? — *Owner: Jesus Lara*
- [ ] `functions` (`lower(name)`, `year(opened)`) and `navigation` (dotted paths): confirm phase 1 returns 400 `kind: "unsupported"` for both, with no driver declaring them. — *Owner: Jesus Lara*
- [ ] Text-operator semantics: `contains`/`startswith`/`endswith` case-insensitive everywhere (dataframe uses `case=False`, PG uses `ILIKE`) — confirm as the contract, and whether `endswith`/`contains` should get a PG pushdown (`ILIKE '%x'` / `ILIKE '%x%'`) in this feature or later. — *Owner: Jesus Lara*
- [ ] Alias pushdown: emit `col AS alias` in `fields` for SQL dialects (subject to the Rust `process_fields` identifier validation) or keep alias always residual (rename after fetch)? — *Owner: Jesus Lara*
- [ ] FFI shape: keep the reference `parse() -> str` (JSON) + `json.loads` in the wrapper, or return a Python `dict` from Rust (needs `pythonize` or manual conversion)? Default proposal: JSON string. — *Owner: Jesus Lara*
- [ ] HTTP 400 body: raw parser error object at the top level (proposal §5) or wrapped in the `AbstractHandler.Error` envelope? — *Owner: Jesus Lara*
- [ ] Rust `rust-version` / edition for `rust/qsurl/`: keep the reference `edition = "2024"` (needs ≥1.85, let-chains need ≥1.88) or align with the existing crate's 2021? CI uses `stable`. — *Owner: Jesus Lara*
- [ ] Datetime literals with timezone (`2024-01-01T10:30:00Z`): pass the ISO string to the driver as-is, or normalise via `cond_definition` types? — *Owner: Jesus Lara*
- [ ] Should the parity corpus also be exported as GBNF/Lark artefacts for constrained decoding in this feature, or is that a follow-up? — *Owner: Jesus Lara*
