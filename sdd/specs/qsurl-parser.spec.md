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

# Feature Specification: qsurl — HTSQL-style URL query dialect (chumsky 0.13 + PyO3)

**Feature ID**: FEAT-152
**Date**: 2026-09-24
**Author**: Jesus Lara
**Status**: approved
**Target version**: next minor release of `querysource` (adds a second native extension)
**Exploration doc**: `sdd/proposals/qsurl-parser.brainstorm.md` (Recommended Option A) · accepted proposal `sdd/proposals/qsurl-spec.md`

---

## 1. Motivation & Business Requirements

### Problem Statement

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
- boolean structure is impossible: the pushdown filter is an implicit AND
  list (`querysource/parsers/sql.pyx:113-248`); there is no `or`, `not`,
  `contains`, `endswith` or `regex` at the database layer;
- literals are untyped strings, so `2024-01-01` and `'2024-01-01'` are the
  same thing to the driver;
- the dialect a caller sees depends on the backend (`col~` means `ILIKE 'x%'`
  only in `pgsql.pyx:275-278`), so nothing is engine-agnostic.

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

### Goals

- **G1 — Engine-agnostic by contract**: the IR never contains dialect
  fragments; agnosticism is delivered by *partial pushdown*. Results are
  identical on every engine; only cost moves.
- **G2 — Rust first, Python parity**: parser in Rust (`chumsky = 0.13` with
  `pratt`, `pyo3 = 0.29` behind a `python` cargo feature) as a separate
  crate `rust/qsurl/`, plus a pure-Python fallback on Lark driven by a
  versioned `grammar.lark`. A shared corpus produces byte-identical IR on
  both back-ends.
- **G3 — Closed grammar with LLM-readable errors**: only reads; unknown
  pipeline operators return the list of valid ones; every error carries
  `kind`, `offset`, `message`, `found`, `expected`, `pointer`.
- **G4 — Full phase-1 scope**: crate + Python module + IR→conditions
  translation + provider capabilities + residual post-filter stage in `QS`
  + aiohttp route `/api/v1/services/qsurl/{path:.*}` (with `?q=`) + output
  negotiation unchanged.
- **G5 — Existing pushdown convention is the target**: the translation
  layer emits the `conditions` dict `AbstractParser` already consumes
  (`fields`, `filter`, `ordering`, `_limit`, `_offset`;
  `querysource/parsers/abstract.pyx:199-301`). No new placeholder syntax in
  stored queries.
- **G6 — Capabilities live next to the code that renders them**: a class
  attribute on `BaseProvider` with per-driver overrides.
- **G7 — No regression on the legacy route** `/api/v2/services/queries/{slug}`.
- **G8 — PBAC**: the new route runs the same `slug:execute` enforcement as
  `QueryService.query` (`querysource/handlers/service.py:200-225`).
- **G9 — Security**: the URL is percent-decoded exactly once by the handler;
  identifiers are safe by grammar (`[A-Za-z_][A-Za-z0-9_]*`) and re-checked
  by the translator; untrusted literals never reach a string `eval`.
- **G10 — Build/CI**: `make build-rust`, `make stage-rust`, `release.yml`
  `CIBW_BEFORE_BUILD` and `package-data` ship the second extension
  (`querysource/qsurl/_qsurl*.so`) the same way they ship `_qs_parsers`.
- **G11 — Performance**: parsing is negligible next to query execution
  (sub-millisecond for typical URLs on the Rust path, ~8 KB practical URL
  ceiling).
- **G12 — Cost guard**: residual work is bounded by `QSURL_MAX_RESIDUAL_ROWS`
  and the provider `residual_scan` flag.

### Non-Goals (explicitly out of scope)

- Phase-2 features: relation navigation (`store.region.name`), aggregates /
  `:group(...)`, computed fields, function whitelists. The grammar already
  accepts `functions` and dotted paths; phase 1 answers 400 `unsupported`.
- Any change to the legacy `/api/v2/services/queries/{slug}` handler, the
  `slug:format` colon suffix, stored-query placeholders, or output writers.
- Accepting the IR JSON directly as a request body (tool-calling surface);
  the IR contract is designed for it but no endpoint is added here.
- A `distinct` pushdown: `AbstractParser` pops the key (`abstract.pyx:399`)
  but no SQL dialect renders `_distinct`; `distinct` is always residual.
- Placing qsurl inside the existing `_qs_parsers` crate (Option B), Lark-only
  (Option C) and compiling to a MultiQuery definition (Option D) were
  rejected in the brainstorm (`sdd/proposals/qsurl-parser.brainstorm.md`).
- Replacing `types/dt/filters.py` for MultiQuery; the residual evaluator is
  qsurl-only.

---

## 2. Architectural Design

### Overview

A client issues `GET /api/v1/services/qsurl/<query>` with the whole qsurl
string percent-encoded in the path, or the split form
`GET /api/v1/services/qsurl/<slug>?q=<rest>` where `rest` is everything
after the slug. `QSUrlService` (new handler) decodes once, joins `slug + q`,
and calls `querysource.qsurl.parse()` which returns the IR `dict` from the
Rust extension `querysource.qsurl._qsurl` (a separate crate in
`rust/qsurl/`, pyo3 behind the `python` feature, FFI is a JSON string) or,
when the extension is missing, from the Lark fallback driven by the
versioned `querysource/qsurl/grammar.lark`. Both back-ends produce
byte-identical JSON for the parity corpus and raise the same
`QSUrlError` (`kind` ∈ `parse | lower | unsupported | cost`).

PBAC `slug:execute` runs exactly as in `QueryService.query` (tenant-aware
branch included) *before* anything touches a datasource. The handler then
builds the `QS` object and its provider (`build_provider()`), reads the
provider's `capabilities` / `residual_scan`, and calls
`translate.split(ir, capabilities, residual_scan=...)` which returns
(a) the flat `conditions` dict the dialect parsers already consume, and
(b) a `ResidualPlan` (filter subtree, sort keys, window, distinct,
projection, renames) for everything the provider did not declare.
`functions` / `navigation` (declared by nobody in phase 1) → 400
`unsupported`; a residual filter with no pushed-down leaf on a provider
with `residual_scan = False` → 400 `cost` before any query runs.

`QS` gains a `residual` kwarg. `QS.query()` applies the plan on both the
cache-hit path and the provider-fetch path, right before
`self._output_format(...)`, after checking `len(rows) <= QSURL_MAX_RESIDUAL_ROWS`
(navconfig, default 50000; exceeding it → `QSUrlError(kind="cost")`).
Rows are materialised into a pandas DataFrame only when the plan is
non-empty; an empty result after the plan raises `DataNotFound` (→ 204 as
today). The cache key stays the pushdown checksum.

All qsurl errors surface as HTTP 400 through `AbstractHandler.Error`, whose
envelope gains an explicit, client-safe `detail` object (see §7, "Error
envelope"): `{"error": <message>, "status": 400, "error_id": ..., "detail":
{kind, offset, message, found, expected, pointer}}`. Text operators (`~`,
`^=`, `$=`) are case-insensitive on every engine: PostgreSQL pushes them
down as `ILIKE` through a new dict-operator token in the PG builders;
everywhere else they run in the residual stage with `case=False` semantics.
`=~` (regex) is always residual. Output negotiation (`Accept`,
`queryformat=`, `_download`, `_filename`) is unchanged and reuses
`DataOutput`.

### Component Diagram

```
GET /api/v1/services/qsurl/{path:.*}[?q=...]
        │
        ▼
QSUrlService (querysource/handlers/qsurl.py)               [M10]
  ├─ percent-decode once, join slug+q
  ├─ querysource.qsurl.parse(src) ──► IR dict              [M2]
  │       ├─ _qsurl (Rust, rust/qsurl/, chumsky+pyo3)      [M1]
  │       └─ _fallback (Lark, grammar.lark)                [M3]  ── to_gbnf() [M4]
  ├─ PBAC slug:execute (same branch as QueryService.query)
  ├─ QS(slug, conditions=..., residual=..., request=...)   [M8]
  │       └─ build_provider() → provider.capabilities       [M5]
  ├─ translate.split(ir, capabilities, residual_scan)       [M6]
  │       ├─ conditions  ──► AbstractParser (fields/filter/ordering/_limit/_offset)
  │       │                    └─ pgsql builders: {col: {"ILIKE": ...}}   [M9]
  │       └─ ResidualPlan ──► QS.query() → residual.apply(df, plan)      [M7]
  │                              (cost guard QSURL_MAX_RESIDUAL_ROWS)
  └─ DataOutput(request, query=qs, ctype=..., slug=...)  → writers (unchanged)
        └─ QSUrlError re-raised → AbstractHandler.Error(detail=err.to_dict(), code=400)
Build/ship: Makefile build-rust/stage-rust, release.yml, package-data        [M11]
Docs: docs/QSURL.md                                                          [M12]
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `rust/` (existing `_qs_parsers` crate) | sibling | New crate `rust/qsurl/` with its own `Cargo.toml` / `pyproject.toml`; the existing crate is untouched except nothing (`rust/Cargo.toml` has no `[workspace]`, so nesting is safe) |
| `querysource/qs_parsers/__init__.py` | pattern reuse | `HAS_RUST` two-level import ladder replicated for `_qsurl` |
| `BaseProvider` (`querysource/providers/abstract.py:33`) | extends | `capabilities: frozenset[str]` + `residual_scan: bool` class attributes |
| `sqlProvider` / `pgProvider` / `cassandraProvider` | extends | per-driver `capabilities` overrides (`sql.py:48`, `pg.py:21`, `cassandra.py:31`) |
| `QS` (`querysource/queries/qs.py:49`) | modifies | `residual` kwarg; `_apply_residual()` called on cache-hit (`qs.py:505`) and provider (`qs.py:586`) paths |
| `AbstractParser` conditions keys (`abstract.pyx:199-301`) | uses | translator emits `fields`, `filter`, `ordering`, `_limit`, `_offset` |
| PG builders (`rust/src/pgsql_parser.rs:77`, `querysource/parsers/pgsql.pyx:226`) | extends | dict-operator tokens `ILIKE` / `NOT ILIKE` accepted (value quoted via `pg_literal` / `Entity.quoteString`) |
| `AbstractHandler.Error` (`handlers/abstract.py:133`) + `build_error_payload` (`utils/errors.py:43`) | extends | `detail: dict | None` pass-through so a client-safe structured error survives `debug=False` |
| `DataOutput.response` (`querysource/outputs/output.py:210`) | extends | `except QSUrlError: raise` so the handler maps it to 400 instead of the generic writer error |
| `QueryService.query` (`handlers/service.py:134`) | mirrors | PBAC pre-flight, format negotiation and `DataOutput` invocation copied, not called |
| `querysource/services.py:189` | extends | `add_get('/api/v1/services/qsurl/{path:.*}', ...)` after the queries block |
| `querysource/conf.py:387` | extends | `QSURL_MAX_RESIDUAL_ROWS = config.getint(..., fallback=50000)` |
| `Makefile:63,73`, `.github/workflows/release.yml:47`, `pyproject.toml:118,195` | modifies | second maturin target staged and shipped; `lark` direct dependency; `package-data` for `querysource.qsurl` |
| `tests/e2e/conftest.py`, `tests/handlers/test_queryservice_pbac_smoke.py` | pattern reuse | dry-run `QS` harness and `__new__`-built handler fixtures |

### Data Models

```python
# IR contract (produced by rust/qsurl/src/ir.rs and querysource/qsurl/_fallback.py; JSON)
{
  "slug": "hisense_stores",
  "fields": ["store_id", "name", {"column": "region.name", "alias": "region"}],
  "filter": {                       # null | {"and": [...]} | {"or": [...]}  (root is ALWAYS and/or)
    "and": [
      {"column": "state_code", "expression": "==", "value": "CA"},
      {"column": "opened", "expression": ">=", "value": "2024-01-01", "dtype": "date"},
      {"or": [
        {"column": "city", "expression": "contains", "value": "san"},
        {"column": "zip", "expression": "startswith", "value": "9"}
      ]},
      {"not": {"column": "closed_at", "expression": "is_null"}},
      {"column": {"fn": "lower", "args": ["name"]}, "expression": "==", "value": "acme"}
    ]
  },
  "sort": [{"column": "name", "order": "asc"}],
  "limit": 50,                      # int | null
  "offset": null,                   # int | null
  "distinct": false,
  "requires": ["select", "alias", "filter", "or", "not", "null_check", "text_match", "functions", "navigation", "sort", "limit"]
}
# Leaf: {"column": str | {"fn": str, "args": [operand...]}, "expression": str, "value"?: any, "dtype"?: "date" | "datetime"}
#   expression ∈ == != < <= > >= contains not_contains startswith endswith regex is_null not_null
#   `value` is absent for is_null / not_null; lists appear only with == / != (membership).
#   `dtype` appears only for UNQUOTED date / datetime literals; the value is the ISO string as written.
# Node: {"and": [...]} | {"or": [...]} | {"not": node}
# `requires` is emitted in Feature DECLARATION order (Rust BTreeSet<Feature>, Ord derived from variant
#   order — rust/qsurl/src/ir.rs Feature enum): select, alias, filter, or, not, in_list, null_check,
#   text_match, regex, functions, navigation, sort, limit, offset, distinct. NOT alphabetical.
```

```python
# querysource/qsurl/errors.py  — the error object (also the 400 `detail`)
{
  "kind": "parse" | "lower" | "unsupported" | "cost",
  "offset": int,            # 0 for lower/unsupported/cost
  "message": str,
  "found": str | None,      # parse only
  "expected": list[str],    # parse only (may be empty)
  "pointer": str            # query + "\n" + spaces + "^" (parse); "" otherwise
}
```

```python
# querysource/qsurl/plan.py — what the provider did NOT do (applied in memory by residual.apply)
@dataclass(frozen=True)
class ResidualPlan:
    filter: dict | None = None                 # IR node/leaf subtree, same shape as above
    sort: tuple[tuple[str, bool], ...] = ()    # (column, descending)
    project: tuple[str, ...] = ()              # final column order (original names); () = keep all
    distinct: bool = False
    offset: int | None = None
    limit: int | None = None
    rename: tuple[tuple[str, str], ...] = ()   # (column, alias), applied LAST
    def is_empty(self) -> bool: ...
# Apply order (fixed): filter → sort → project → distinct → offset → limit → rename
```

### New Public Interfaces

```python
# querysource/qsurl/__init__.py
HAS_RUST: bool
def parse(src: str) -> dict: ...                 # IR dict; raises QSUrlError
def requires(src: str) -> list[str]: ...         # IR["requires"]; raises QSUrlError
def to_gbnf() -> str: ...                        # lazy re-export of querysource.qsurl.gbnf.to_gbnf
class QSUrlError(QueryException): ...            # .kind .offset .message .found .expected .pointer .to_dict()

# querysource/qsurl/translate.py
def split(ir: dict, capabilities: frozenset[str], *, residual_scan: bool = True) -> tuple[dict, ResidualPlan]: ...

# querysource/qsurl/residual.py
def apply(rows: "pd.DataFrame | list", plan: ResidualPlan) -> "pd.DataFrame | list": ...

# querysource/providers/abstract.py
class BaseProvider:
    capabilities: frozenset[str]   # default {"select", "filter", "in_list", "null_check"}
    residual_scan: bool = True

# querysource/queries/qs.py
QS(slug, conditions=..., request=..., residual: ResidualPlan | None = None)

# HTTP
GET /api/v1/services/qsurl/{path:.*}      (+ optional ?q=<rest>)
```

---

## 3. Module Breakdown

#### Delegation-eligible modules

| Module | Eligible? | Decided patterns / exact contracts | Why not (if no) |
|---|---|---|---|
| M1: Rust crate `rust/qsurl/` | yes | Port of the reference crate verbatim; `#[pymodule] fn _qsurl`; `Cargo.toml`/`pyproject.toml` fixed in §3 | — |
| M2: Python package core | yes | `HAS_RUST` ladder, lazy back-end resolution, `QSUrlError` fields, capability constants, `ResidualPlan` all fixed | — |
| M3: Lark fallback + parity corpus | yes | Grammar = `parser.rs` doc comment; lowering rules = `ir.rs` (listed in §3); corpus format fixed | — |
| M4: GBNF export | yes | Input `grammar.lark`, output GBNF string; validated against corpus valid inputs | — |
| M5: Provider capabilities | yes | Exact sets per provider fixed in §3 | — |
| M6: `translate.split` | yes | Pushdown table and window/alias/projection rules fixed in §3 | — |
| M7: `residual.apply` | yes | Leaf semantics table + apply order fixed in §3 | — |
| M8: `QS` residual stage + cost guard | yes | Insertion points `qs.py:505` and `qs.py:586`, conf key, error kinds fixed | — |
| M9: PG `ILIKE` dict operator | yes | Token set, escaping and quoting path fixed | — |
| M10: Handler + route + envelope `detail` | yes | Mirrors `QueryService.query` §-by-§; envelope change fixed in §7 | — |
| M11: Build & ship | yes | Exact Makefile / release.yml / package-data edits fixed | — |
| M12: Docs | yes | `docs/QSURL.md` outline fixed | — |

### Module 1: Rust crate `rust/qsurl/`
- **Path**: `rust/qsurl/Cargo.toml`, `rust/qsurl/pyproject.toml`, `rust/qsurl/src/{lib,ast,parser,ir,python}.rs`, `rust/qsurl/examples/parse.rs`
- **Responsibility**: the chumsky parser, lowering to IR, error JSON, and the pyo3 binding. Ported from the reference tarball (`~/Descargas/qsurl.tar.gz`, see §6 "User-Provided Code") with these deltas only: the pymodule is named `_qsurl`; `[lib] name = "qsurl"` (rlib for `cargo test` / example) and maturin `module-name = "querysource.qsurl._qsurl"`; `rust-version = "1.88"` declared; the 11 reference tests kept and extended (see §4).
- **Depends on**: nothing in the repo (toolchain: rustc ≥ 1.88, local 1.90; CI `dtolnay/rust-toolchain@stable`).
- **Interface Skeleton** *(signatures + docstrings only)*:
  ```toml
  # rust/qsurl/Cargo.toml  (new)
  [package]
  name = "qsurl"
  version = "0.1.0"
  edition = "2024"
  rust-version = "1.88"
  description = "qsurl — HTSQL-style URL query dialect parser for QuerySource"
  [lib]
  name = "qsurl"
  crate-type = ["cdylib", "rlib"]
  [features]
  default = []
  python = ["dep:pyo3"]
  [dependencies]
  chumsky = { version = "0.13", features = ["pratt"] }
  pyo3 = { version = "0.29", optional = true, features = ["extension-module"] }   # same major as rust/Cargo.toml:12
  serde = { version = "1", features = ["derive"] }
  serde_json = "1"
  [profile.release]
  opt-level = 3
  lto = true
  codegen-units = 1            # mirrors rust/Cargo.toml:31-34
  ```
  ```toml
  # rust/qsurl/pyproject.toml  (new — mirrors rust/pyproject.toml:1-13)
  [build-system]
  requires = ["maturin>=1.15,<2.0"]
  build-backend = "maturin"
  [project]
  name = "qsurl"
  version = "0.1.0"
  requires-python = ">=3.10"
  [tool.maturin]
  module-name = "querysource.qsurl._qsurl"
  bindings = "pyo3"
  features = ["python", "pyo3/extension-module"]
  ```
  ```rust
  // rust/qsurl/src/lib.rs  (new — reference lib.rs verbatim; public surface)
  pub mod ast; pub mod ir; pub mod parser;
  #[cfg(feature = "python")] mod python;
  pub use ast::Query; pub use ir::{Feature, LowerError};
  #[derive(Debug, Clone, Serialize)]
  pub struct ParseError { pub offset: usize, pub message: String, pub found: Option<String>, pub expected: Vec<String>, pub pointer: String }
  #[derive(Debug, Clone, Serialize)] #[serde(tag = "kind", rename_all = "snake_case")]
  pub enum Error { Parse(ParseError), Lower { message: String } }
  impl Error { pub fn to_json(&self) -> String }
  /// Parse a percent-decoded qsurl string into the AST (input is trimmed first).
  pub fn parse(src: &str) -> Result<Query, ParseError>
  /// Parse + lower to the IR (serde_json::Value).
  pub fn parse_to_ir(src: &str) -> Result<serde_json::Value, Error>
  /// Parse + lower + serialise; the FFI surface.
  pub fn parse_to_json(src: &str) -> Result<String, Error>
  ```
  ```rust
  // rust/qsurl/src/python.rs  (new — reference python.rs with the module renamed)
  /// Parse `src` and return the IR as a JSON string; raises ValueError whose message is the error JSON.
  #[pyfunction] fn parse(src: &str) -> PyResult<String>
  /// Validate `src` and return its `requires` capability list (declaration order).
  #[pyfunction] fn requires(src: &str) -> PyResult<Vec<String>>
  #[pymodule] fn _qsurl(m: &Bound<'_, PyModule>) -> PyResult<()>   // name MUST match module-name's last segment
  ```
  `ast.rs`, `parser.rs`, `ir.rs`, `examples/parse.rs`: reference files verbatim (grammar and lowering rules reproduced in §6 "User-Provided Code" and Module 3).

### Module 2: Python package core `querysource/qsurl/`
- **Path**: `querysource/qsurl/__init__.py`, `querysource/qsurl/errors.py`, `querysource/qsurl/capabilities.py`, `querysource/qsurl/plan.py`
- **Responsibility**: public API (`parse`, `requires`, `HAS_RUST`, `QSUrlError`, `to_gbnf`), the error type shared by both back-ends, the closed capability vocabulary, and the `ResidualPlan` dataclass consumed by M6/M7/M8. Back-end resolution is **lazy** (inside `parse()`), so the package imports cleanly before M3 lands and without the Rust extension.
- **Depends on**: `querysource.exceptions.QueryException` (`querysource/exceptions.py:6`). Optional runtime: `_qsurl` (M1), `._fallback` (M3), `.gbnf` (M4).
- **Interface Skeleton**:
  ```python
  # querysource/qsurl/__init__.py  (new)
  """qsurl — HTSQL-style URL query dialect for QuerySource.

  Public API: parse(), requires(), to_gbnf(), HAS_RUST, QSUrlError.
  """
  try:                                   # ladder mirrors querysource/qs_parsers/__init__.py:10-25
      from . import _qsurl as _rs        # in-wheel: querysource/qsurl/_qsurl*.so
      HAS_RUST = True
  except ImportError:
      try:
          import _qsurl as _rs           # maturin develop (top-level)
          HAS_RUST = True
      except ImportError:
          _rs = None
          HAS_RUST = False

  def parse(src: str) -> dict:
      """Parse a percent-decoded qsurl string into the IR dict.

      Uses the Rust extension when HAS_RUST, else the Lark fallback (imported lazily
      from ._fallback on first call; logs one warning per process).
      Raises:
          QSUrlError: kind "parse" or "lower" (from either back-end).
      """
  def requires(src: str) -> list[str]:
      """Return IR["requires"] for `src` (declaration order); raises QSUrlError."""
  def to_gbnf() -> str:
      """Return the GBNF rendering of grammar.lark (lazy import of .gbnf)."""
  __all__ = ("HAS_RUST", "QSUrlError", "ResidualPlan", "parse", "requires", "to_gbnf")
  ```
  ```python
  # querysource/qsurl/errors.py  (new)
  from ..exceptions import QueryException            # verified: querysource/exceptions.py:6
  ERROR_KINDS: tuple[str, ...] = ("parse", "lower", "unsupported", "cost")
  class QSUrlError(QueryException):
      """Structured qsurl error (grammar, lowering, capability or cost); HTTP 400.

      str(err) is the error JSON (so it round-trips through any logger); to_dict()
      returns the object placed in the 400 envelope's `detail`.
      """
      default_code: int = 400
      def __init__(self, kind: str, message: str, *, offset: int = 0, found: str | None = None,
                   expected: list[str] | None = None, pointer: str = "", code: int = 400) -> None: ...
      @classmethod
      def from_json(cls, payload: str) -> "QSUrlError":
          """Build from the JSON string the Rust binding puts in ValueError.args[0]."""
      def to_dict(self) -> dict:
          """{"kind","offset","message","found","expected","pointer"}."""
  ```
  ```python
  # querysource/qsurl/capabilities.py  (new)
  SELECT, ALIAS, FILTER, OR, NOT, IN_LIST, NULL_CHECK, TEXT_MATCH, REGEX, FUNCTIONS, NAVIGATION, SORT, LIMIT, OFFSET, DISTINCT = (
      "select", "alias", "filter", "or", "not", "in_list", "null_check", "text_match", "regex",
      "functions", "navigation", "sort", "limit", "offset", "distinct")
  ALL: tuple[str, ...]                     # declaration order above (== Rust `requires` order)
  BASE: frozenset[str] = frozenset({SELECT, FILTER, IN_LIST, NULL_CHECK})       # BaseProvider default
  UNSUPPORTED_PHASE1: frozenset[str] = frozenset({FUNCTIONS, NAVIGATION})       # → 400 "unsupported"
  def validate(caps: frozenset[str]) -> frozenset[str]:
      """Return caps unchanged; raise ValueError naming any token outside ALL."""
  ```
  ```python
  # querysource/qsurl/plan.py  (new)
  @dataclass(frozen=True)
  class ResidualPlan:
      """Work left for the in-memory stage; see §2 Data Models for field semantics and apply order."""
      filter: dict | None = None
      sort: tuple[tuple[str, bool], ...] = ()
      project: tuple[str, ...] = ()
      distinct: bool = False
      offset: int | None = None
      limit: int | None = None
      rename: tuple[tuple[str, str], ...] = ()
      def is_empty(self) -> bool:
          """True when every field is at its default (nothing to apply)."""
  ```

### Module 3: Lark fallback + parity corpus
- **Path**: `querysource/qsurl/grammar.lark`, `querysource/qsurl/_fallback.py`, `tests/qsurl/corpus.json`, `tests/qsurl/test_parity.py`
- **Responsibility**: a Lark (LALR; Earley only if LALR conflicts are unavoidable, documented in the grammar header) parser with a transformer that builds the same AST shape as `ast.rs` and applies the same lowering as `ir.rs`, producing an IR dict that `json.dumps(ir, separators=(",", ":"), ensure_ascii=False)` renders byte-identical to the Rust JSON for every corpus entry; plus the corpus itself.
- **Depends on**: M2 (`QSUrlError`, `capabilities.ALL` order). `lark>=1.3.1` (direct dependency, M11).
- **Lowering rules to reproduce (from `ir.rs`, verified in the reference tarball)**:
  1. Comparison with the column on the right is flipped (`100<price` → `price > 100`); text operators (`~ !~ ^= $= =~`) with the column on the right → lower error `operator \`X\` requires the column on the left-hand side`.
  2. `col=null` → `is_null`; `col!=null` → `not_null`; bare `col` → `not_null`; `!col` → `is_null` (sugar; no `not` node); any other `!expr` → `{"not": ...}` + requires `not`.
  3. Lists only with `=`/`!=` (→ `in_list`); any other operator with a list → lower error `operator \`X\` does not accept a list; use \`=\` or \`!=\` for membership`.
  4. Bare literal as a condition → lower error `a bare literal is not a condition; compare it against a column`; comparison with no column on either side → `a comparison needs a column or function on at least one side`.
  5. `dtype` only for unquoted `date` / `datetime` literals; value is the ISO string as written.
  6. A single-leaf (or single `not`) filter is wrapped as `{"and": [leaf]}`; root is always `and`/`or`.
  7. `requires` sorted in `capabilities.ALL` declaration order; `select` only when fields non-empty; `filter` only when a filter exists; `alias` per aliased field; `navigation` for any dotted path (fields, filter operands, sort keys); `functions` for any call.
  8. `:top`/`:limit` twice or `:skip`/`:offset` twice → parse error `:top/:limit given more than once` / `:skip/:offset given more than once`; unknown `:op` → parse error `unknown pipeline operator \`:op\`; expected one of: :sort, :top, :limit, :skip, :offset, :distinct`.
  9. Whitespace tolerated around every token; input trimmed; `=`/`==` synonyms; string literals `'...'` (with `''` escape) and `"..."` (with `\"` escape); numbers `-?int(.digits)?` → int / float; `null`, `true`, `false`.
  10. Parse errors: `offset` is the byte offset of the furthest error, `expected` the chumsky expected-token list (the Lark side reproduces `offset` and `message` exactly for the corpus' error cases; `expected` parity is required only where the corpus pins it).
- **Interface Skeleton**:
  ```python
  # querysource/qsurl/_fallback.py  (new)
  GRAMMAR_PATH: Path                       # importlib.resources path to grammar.lark
  def parse(src: str) -> dict:
      """Lark implementation of querysource.qsurl.parse (same IR, same QSUrlError)."""
  def requires(src: str) -> list[str]:
      """parse(src)["requires"]."""
  ```
  ```json
  // tests/qsurl/corpus.json  (new) — list of cases; both back-ends must agree byte-for-byte
  [{"id": "example_query", "input": "/queries/hisense_stores{store_id,name,city}?state_code='CA'&opened>=2024-01-01:sort(name):top(50)",
    "ir": {...}},                                          // valid case: exact IR
   {"id": "unknown_pipe", "input": "stores?state='CA':order(name)",
    "error": {"kind": "parse", "offset": 18, "message": "unknown pipeline operator `:order`; expected one of: :sort, :top, :limit, :skip, :offset, :distinct"}}]
  ```

### Module 4: GBNF export
- **Path**: `querysource/qsurl/gbnf.py`, `tests/qsurl/test_gbnf.py`
- **Responsibility**: convert `grammar.lark` (rules + terminals, no Lark directives) into a GBNF grammar string for constrained decoding; the test validates every corpus *valid* input against the exported GBNF with a minimal pure-Python GBNF matcher shipped in the test module (no new dependency).
- **Depends on**: M3 (`grammar.lark`).
- **Interface Skeleton**:
  ```python
  # querysource/qsurl/gbnf.py  (new)
  def to_gbnf(grammar_path: Path | None = None) -> str:
      """Render the Lark grammar as GBNF (root rule `root ::= query`).

      Raises:
          ValueError: if the grammar uses a Lark construct with no GBNF equivalent.
      """
  ```

### Module 5: Provider capabilities
- **Path**: modifies `querysource/providers/abstract.py:36`, `querysource/providers/sql.py:48`, `querysource/providers/pg.py:21`, `querysource/providers/cassandra.py:31`
- **Responsibility**: declare, next to `__parser__`, what each provider can render natively. Exact sets (phase 1):
  | Provider | `capabilities` | `residual_scan` |
  |---|---|---|
  | `BaseProvider` (default, all unaudited providers) | `{select, filter, in_list, null_check}` | `True` |
  | `sqlProvider` (MySQL, MSSQL, Oracle, SQLite… via `SQLParser`) | base ∪ `{alias, sort, limit, offset}` | `True` |
  | `pgProvider` | sql ∪ `{text_match}` (requires M9) | `True` |
  | `cassandraProvider` | `{select, filter, in_list, null_check, limit}` | `False` |
- **Depends on**: M2 (`querysource.qsurl.capabilities` constants), M9 (for `pgProvider`'s `text_match`).
- **Interface Skeleton**:
  ```python
  # querysource/providers/abstract.py  (modifies querysource/providers/abstract.py:36)
  from ..qsurl import capabilities as qsurl_caps          # leaf module: constants only, no back-import
  class BaseProvider(ABC):                                 # verified: querysource/providers/abstract.py:33
      __parser__: AbstractParser = None                    # line 35
      _parser_options: dict = {}                           # line 36 — new attributes go right after
      capabilities: frozenset[str] = qsurl_caps.BASE
      """qsurl capabilities this provider renders natively (querysource/qsurl/capabilities.py)."""
      residual_scan: bool = True
      """False when a residual-only filter would be a full scan the store must not run (Cassandra)."""
  # querysource/providers/sql.py:48      capabilities = BASE | {ALIAS, SORT, LIMIT, OFFSET}
  # querysource/providers/pg.py:21       capabilities = sqlProvider.capabilities | {TEXT_MATCH}
  # querysource/providers/cassandra.py:31 capabilities = frozenset({SELECT, FILTER, IN_LIST, NULL_CHECK, LIMIT}); residual_scan = False
  ```

### Module 6: IR → conditions + residual (`translate.split`)
- **Path**: `querysource/qsurl/translate.py`, `tests/qsurl/test_translate.py`
- **Responsibility**: the pushdown decision. Pure function of `(ir, capabilities, residual_scan)`; never imports providers.
- **Depends on**: M2 (`QSUrlError`, `capabilities`, `ResidualPlan`).
- **Rules (fixed)**:
  1. `needed = set(ir["requires"])`. If `needed & UNSUPPORTED_PHASE1` → `QSUrlError("unsupported", f"capability `{cap}` is not supported by any provider in this release")`, naming the first offending capability in declaration order. Filter leaves whose `column` is a dict (`fn`) or contains `.` never reach the tables below (they are covered by this rule).
  2. Identifier guard (defence in depth behind the grammar): every column, alias and sort key must match `^[A-Za-z_][A-Za-z0-9_]*$`, else `QSUrlError("lower", ...)`.
  3. **Filter**. Only *direct children of a root `and`* that are leaves are pushdown candidates; a root `or`, any `or`/`not` subtree, and every leaf the table below marks residual go to `plan.filter` as an `{"and": [...]}` (or the whole root `or`) preserving order. Leaf → flat-dict mapping (`conditions["filter"]`):
     | expression | value | pushdown key/value | needs |
     |---|---|---|---|
     | `==` | scalar (str/int/float/bool; date/datetime as the ISO string) | `{col: v}` | `filter` |
     | `!=` | scalar | `{f"{col}!": v}` (`sql.pyx:199`, `!=`) | `filter` |
     | `< <= > >=` | scalar | `{col: {op: v}}` (`COMPARISON_TOKENS`, `sql.pyx:25,155`) | `filter` |
     | `==` | list | `{col: [..]}` → `IN` (`sql.pyx:170-179`) | `in_list` |
     | `!=` | list | `{f"{col}!": [..]}` → `NOT IN` (`sql.pyx:177`) | `in_list` |
     | `is_null` / `not_null` | — | `{col: "null"}` / `{col: "!null"}` (`sql.pyx:190-197`) | `null_check` |
     | `startswith` / `contains` / `endswith` | str | `{col: {"ILIKE": pat}}` with `pat` = `esc(v)+"%"` / `"%"+esc(v)+"%"` / `"%"+esc(v)`; `esc` doubles `\` and prefixes `%` and `_` with `\` (M9 quotes the literal) | `text_match` |
     | `not_contains` | str | `{col: {"NOT ILIKE": "%"+esc(v)+"%"}}` | `text_match` |
     | `regex` | str | never (residual) | `regex` |
     A leaf is pushed only when its `needs` ⊆ `capabilities` **and** its column is not already used as a pushdown key in this conjunction (the flat dict cannot hold two conditions on one key; the second and later ones go residual). `dtype` is dropped on pushdown (the column type casts the ISO string) and kept on residual leaves.
  4. **Fields**. `requested = [name for each field]`. If `plan.filter` or residual sort references columns outside `requested`, `conditions["fields"] = requested + missing` and `plan.project = requested`; otherwise `conditions["fields"] = requested`. Aliases: when `alias ∈ capabilities` **and** the plan is otherwise empty → `"col AS alias"` entries (`process_fields` joins the list verbatim, `rust/src/sql_parser.rs:502`); in every other case aliases go to `plan.rename` and the pushed field is the bare column. No fields → key absent (`SELECT *`).
  5. **Sort**. Pushed as `conditions["ordering"] = ["col", "col DESC", ...]` (`abstract.pyx:267`, `sql.pyx:302`) when `sort ∈ capabilities` **and** no sort key is an alias-only name; otherwise `plan.sort`.
  6. **Window**. `conditions["_limit"]` / `conditions["_offset"]` (`abstract.pyx:209,218`) only when `limit`/`offset ∈ capabilities` **and** `plan.filter is None` **and** `not ir["distinct"]` **and** (no sort or sort pushed). Otherwise `plan.limit` / `plan.offset` (both together: a pushed limit with a residual offset is never emitted).
  7. `distinct` → always `plan.distinct` (no dialect renders `_distinct`).
  8. **Cost pre-check**: if `not residual_scan and plan.filter is not None and conditions.get("filter") in (None, {})` → `QSUrlError("cost", "residual-only filter on a provider that forbids scans (residual_scan=False); push down at least one condition")`.
  9. Return `(conditions, plan)`; `conditions` never contains reserved keys other than `fields`, `filter`, `ordering`, `_limit`, `_offset`.
- **Interface Skeleton**:
  ```python
  # querysource/qsurl/translate.py  (new)
  IDENT_RE: re.Pattern[str]                      # ^[A-Za-z_][A-Za-z0-9_]*$
  def like_escape(value: str) -> str:
      """Escape `\\`, `%` and `_` for use inside an ILIKE pattern (quoting is the builder's job)."""
  def split(ir: dict, capabilities: frozenset[str], *, residual_scan: bool = True) -> tuple[dict, ResidualPlan]:
      """Split the IR into the parser `conditions` dict and the in-memory ResidualPlan.

      Raises:
          QSUrlError: kind "unsupported" (functions/navigation), "lower" (bad identifier),
              or "cost" (residual-only filter with residual_scan=False).
      """
  ```

### Module 7: Residual stage evaluator (`residual.apply`)
- **Path**: `querysource/qsurl/residual.py`, `tests/qsurl/test_residual.py`
- **Responsibility**: apply a `ResidualPlan` to a result set with vectorised pandas operations, **without** building expression strings for `eval` (see §7 Known Risks: `build_condition` interpolates raw values into eval strings, `types/dt/filters.py:82-91`, which is unsafe for URL-supplied literals). Semantics mirror `build_condition` where they overlap and honour the case-insensitive text contract everywhere.
- **Depends on**: M2 (`ResidualPlan`, `QSUrlError`); `pandas`.
- **Leaf semantics (fixed)**:
  | expression | pandas evaluation |
  |---|---|
  | `== != < <= > >=` | `df[c] <op> v`; when `dtype` ∈ {date, datetime}: `pd.to_datetime(df[c], utc=True, errors="coerce") <op> pd.Timestamp(v, tz="UTC")`; `==`/`!=` with a list → `isin` / `~isin` |
  | `is_null` | `df[c].isnull() \| (df[c] == "")` (mirrors `filters.py:55-58`) |
  | `not_null` | negation of the above |
  | `contains` / `not_contains` | `df[c].astype("string").str.contains(re.escape(v), case=False, na=False, regex=True)` (negated) |
  | `startswith` / `endswith` | `df[c].astype("string").str.lower().str.startswith(v.lower(), na=False)` / `.str.endswith(...)` |
  | `regex` | `df[c].astype("string").str.contains(v, case=True, na=False, regex=True)`; `re.error` → `QSUrlError("lower", ...)` |
  | node `and` / `or` / `not` | `&` / `\|` / `~` over boolean Series |
  Unknown column in a leaf, sort key or projection → `QSUrlError("lower", f"column `{c}` not in result")`.
- **Interface Skeleton**:
  ```python
  # querysource/qsurl/residual.py  (new)
  def evaluate(df: pd.DataFrame, node: dict) -> pd.Series:
      """Boolean mask for an IR filter node/leaf over df (recursive; no eval)."""
  def apply(rows: pd.DataFrame | list, plan: ResidualPlan) -> pd.DataFrame | list:
      """Apply plan in the fixed order filter → sort → project → distinct → offset → limit → rename.

      A list input (records / asyncdb Record) is materialised with pd.DataFrame([dict(r) for r in rows])
      and returned as list[dict] (df.to_dict("records")); a DataFrame is returned as a DataFrame.
      An empty plan returns rows untouched. Raises QSUrlError on unknown columns or bad regex.
      """
  ```

### Module 8: `QS` residual stage + cost guard
- **Path**: modifies `querysource/queries/qs.py:83,505,586`, `querysource/conf.py:387`; `tests/qsurl/test_qs_residual.py`, `tests/e2e/test_qsurl_dry_run.py`
- **Responsibility**: carry the plan through `QS` and apply it on both result paths, guarded by `QSURL_MAX_RESIDUAL_ROWS`.
- **Depends on**: M2 (`ResidualPlan`), M7 (`residual.apply`).
- **Interface Skeleton**:
  ```python
  # querysource/conf.py  (modifies querysource/conf.py:387 — insert before the EXCLUDED_QUERY_PARAMETERS block)
  QSURL_MAX_RESIDUAL_ROWS: int = config.getint("QSURL_MAX_RESIDUAL_ROWS", fallback=50000)
  """Rows the pushdown result may have before a qsurl residual plan is applied in memory."""

  # querysource/queries/qs.py  (modifies querysource/queries/qs.py:55-83, 505, 586)
  class QS(BaseQuery):                                              # verified: querysource/queries/qs.py:49
      def __init__(self, slug: str = '', conditions: dict = None, request: web.Request = None,
                   loop: asyncio.AbstractEventLoop = None, *, tenant: str | None = None,
                   definition: "LoadedDefinition | None" = None, principal: "QSPrincipal | None" = None,
                   residual: "ResidualPlan | None" = None, **kwargs):   # new keyword-only kwarg
          ...
          self._residual: "ResidualPlan | None" = residual          # set next to self.is_cached (line 83)
      def _apply_residual(self, result):
          """Return result with self._residual applied, or result untouched when there is no plan.

          Raises:
              QSUrlError: kind "cost" when len(result) > QSURL_MAX_RESIDUAL_ROWS (checked BEFORE
                  materialising a DataFrame); kind "lower" from residual.apply.
              DataNotFound: when the plan leaves zero rows (same as an empty provider result).
          """
      # call sites: cache hit  → `self._result = self._apply_residual(result)` replaces qs.py:505
      #             provider   → `result = self._apply_residual(result)` inserted after the
      #                          check_empty() block (qs.py:582-585), before `self._result = result` (qs.py:586)
      # The cache-hit call lives in the try's `else:` clause (qs.py:504), which Python's except clauses
      # do NOT cover, so QSUrlError is never mistaken for a cache miss. The provider call is outside the
      # try/except at qs.py:511-580, so the generic `except Exception → 500` cannot swallow it either.
  ```

### Module 9: PostgreSQL `ILIKE` dict operator (Rust + Cython)
- **Path**: modifies `rust/src/pgsql_parser.rs:77`, `querysource/parsers/pgsql.pyx:226`; `tests/qsurl/test_pg_ilike.py`, one case appended to `tests/test_rust_parsers.py`
- **Responsibility**: accept `{col: {"ILIKE": pattern}}` and `{col: {"NOT ILIKE": pattern}}` in the PG dict-operator branch (both the Rust fast path and the Cython fallback), rendering `{col} ILIKE '<quoted pattern>'` with the pattern quoted by the existing literal helpers (`pg_literal`, `rust/src/pgsql_parser.rs:50`; `Entity.quoteString`, `pgsql.pyx:228`). Metacharacter escaping of the *user* text is the translator's job (M6 `like_escape`); the builder only quotes.
- **Depends on**: nothing (independent of the qsurl package). Requires `make build-rust` and `make build-inplace` after editing.
- **Interface Skeleton**:
  ```rust
  // rust/src/pgsql_parser.rs  (modifies rust/src/pgsql_parser.rs:77)
  const VALID_OPERATORS: &[&str] = &["<", ">", ">=", "<=", "<>", "!=", "IS NOT", "IS"];   // unchanged
  const PG_TEXT_OPERATORS: &[&str] = &["ILIKE", "NOT ILIKE"];                              // new
  /// Validate that an operator is in the allowlist (now also PG_TEXT_OPERATORS).
  fn pg_validate_operator(op: &str) -> bool                                                 // line 37; extended
  // process_dict_value (line 342): a PG_TEXT_OPERATORS op with a Str value renders
  //   format!("{} {} {}", key, op, pg_literal(v)); a non-string value with these ops is rejected (None).
  ```
  ```cython
  # querysource/parsers/pgsql.pyx  (modifies querysource/parsers/pgsql.pyx:226)
  PG_TEXT_OPERATORS = ('ILIKE', 'NOT ILIKE')
  # in the dict branch: `if op in COMPARISON_TOKENS:` (line 226) gains
  #   `elif op in PG_TEXT_OPERATORS and isinstance(v, str): where_cond.append(f"{key} {op} {Entity.quoteString(v)}")`
  ```

### Module 10: HTTP handler, route and error envelope
- **Path**: `querysource/handlers/qsurl.py` (new); modifies `querysource/handlers/__init__.py:12,22`, `querysource/services.py:189`, `querysource/handlers/abstract.py:133-139,173`, `querysource/utils/errors.py:50`, `querysource/outputs/output.py:237`; `tests/handlers/test_qsurl_service.py`, `tests/test_error_formatter.py` (one case)
- **Responsibility**: the route, PBAC, decode-once, parse → translate → `QS` → `DataOutput`, and mapping every `QSUrlError` to a 400 whose envelope carries the error object in `detail` in **every** mode (production redaction otherwise strips it: `build_error_payload` returns only `error/status/error_id` when `debug=False`, `utils/errors.py:140-146`).
- **Depends on**: M2, M5, M6, M8.
- **Interface Skeleton**:
  ```python
  # querysource/utils/errors.py  (modifies querysource/utils/errors.py:50)
  def build_error_payload(*, category: str, status: int, exception: Optional[BaseException] = None,
                          debug: bool = False, logger: Optional[logging.Logger] = None,
                          public_message: Optional[str] = None,
                          public_detail: Optional[dict] = None) -> dict[str, Any]:
      """... public_detail: caller-asserted client-safe object; when given it is emitted as
      payload["detail"] in every mode (it replaces the debug-mode str(exception) detail; `trace`
      is still debug-only)."""

  # querysource/handlers/abstract.py  (modifies querysource/handlers/abstract.py:133-139, 173)
  def Error(self, reason: dict = None, message: str = None, exception: BaseException = None,
            stacktrace: str = None, code: int = 400, detail: dict | None = None) -> HTTPException:
      """... detail: client-safe structured error; when given, `message` is also public
      (public_message=message) regardless of self.debug, and detail → build_error_payload(public_detail=...)."""

  # querysource/outputs/output.py  (modifies querysource/outputs/output.py:237 — first except clause)
  #   except QSUrlError: raise          # let the handler answer 400 with the structured detail

  # querysource/handlers/qsurl.py  (new)
  class QSUrlService(AbstractHandler):                     # verified: querysource/handlers/abstract.py:32
      """GET /api/v1/services/qsurl/{path:.*}: parse a qsurl query, enforce PBAC, execute through QS."""
      async def query(self, request: web.Request) -> web.Response:
          """Handle one qsurl read.

          Steps, in order:
          (1) source = match_info["path"]; with the "?q=" form the path must be a bare slug
              (else 400 kind "parse": "q must carry everything after the slug") and source =
              f"{path}{q}". Percent-decoding happens exactly once (see §7 Known Risks); `q` is
              removed from params so it never becomes a condition key.
          (2) ir = parse(source); QSUrlError → 400 with detail.
          (3) slug = ir["slug"]; PBAC slug:execute with the tenant-aware branch of
              QueryService.query (service.py:200-225) verbatim.
          (4) queryformat / _download / _filename / writer_options negotiation as
              service.py:227-292 (no `slug:format` suffix on this route).
          (5) caps, scan = await self.resolve_capabilities(request, slug, tenant)  (below).
          (6) conditions, plan = translate.split(ir, caps, residual_scan=scan); QSUrlError → 400.
              `conditions` is merged over the surviving query params ({**params, **conditions}).
          (7) query = await self.get_source(request, slug, conditions, driver=args, tenant=tenant,
              definition=request.get('qs_definition'), residual=plan); await query.build_provider()
              with the SlugNotFound / TenantError / ParserError / ProviderError mapping of
              service.py:311-345; datasource:use / driver:use PBAC as service.py:346-367.
          (8) DataOutput(request, query=query, ctype=queryformat, slug=slug, **output_args).response();
              any QSUrlError (residual stage, cost guard) propagates through DataOutput (M10 re-raise)
              and is answered here as
              `raise self.Error(message=err.message, exception=err, code=400, detail=err.to_dict())`.

          Why (5) precedes (7): BaseProvider.__init__ hands `conditions` to the dialect parser at
          construction (providers/abstract.py:119-125) and the parser pops the reserved keys
          synchronously, so the pushdown dict must exist BEFORE the executing QS is built; the
          capabilities it depends on are a class attribute of the provider that only
          QS.build_provider() resolves. Hence one lazy probe, then one real QS.
          """
      async def resolve_capabilities(self, request: web.Request, slug: str, tenant: str | None) -> tuple[frozenset[str], bool]:
          """Resolve (capabilities, residual_scan) of the provider class that will execute `slug`.

          Builds a probe `QS(slug, conditions={}, request=request, tenant=tenant, lazy=True)`, awaits
          build_provider(), reads `probe._qs.capabilities` / `probe._qs.residual_scan`, then
          `await probe.close()`. Slug resolution errors map exactly as in step 5.
          """
  # querysource/handlers/__init__.py:12,22 — `from .qsurl import QSUrlService` + `'QSUrlService',` in __all__
  # querysource/services.py:189 — after the add_head(...) line:
  #   qsurl = QSUrlService()
  #   r = self.app.router.add_get('/api/v1/services/qsurl/{path:.*}', qsurl.query, allow_head=False); routes.append(r)
  ```
  > Design note for the task author: the probe in `resolve_capabilities` exists because
  > `BaseProvider.__init__` instantiates the dialect parser with `conditions` at construction
  > (`providers/abstract.py:119-125`) and the parser pops the reserved keys synchronously; there is no
  > public hook to add conditions after `build_provider()`. The probe costs one definition lookup
  > (cached by the definition repository) and opens no datasource connection (`lazy=True`,
  > `QueryConnection(lazy=...)`, `qs.py:118-121`). `QS.build_provider()` is the only sanctioned way to
  > map slug → provider class (§6 "Does NOT Exist": there is no standalone resolver).

### Module 11: Build & ship
- **Path**: modifies `Makefile:63,73`, `.github/workflows/release.yml:47`, `pyproject.toml:118,195`
- **Responsibility**: build, stage and ship the second extension exactly like `_qs_parsers`; declare `lark` as a direct dependency; ship `grammar.lark`.
- **Depends on**: M1 (manifest), M2 (package directory exists for staging).
- **Interface Skeleton**:
  ```make
  # Makefile  (modifies Makefile:63-64 and :73-80)
  build-rust:
  	$(MATURIN) develop --release --manifest-path rust/Cargo.toml
  	$(MATURIN) develop --release --manifest-path rust/qsurl/Cargo.toml
  stage-rust:
  	# existing _qs_parsers block unchanged, then the same recipe for rust/qsurl:
  	#   maturin build --release -i python --manifest-path rust/qsurl/Cargo.toml --out $(RUST_WHEEL_OUT)
  	#   newest $(RUST_WHEEL_OUT)/qsurl-*.whl → unzip → copy '_qsurl*.so' into querysource/qsurl/
  ```
  ```yaml
  # .github/workflows/release.yml  (modifies .github/workflows/release.yml:47-53)
  # CIBW_BEFORE_BUILD gains, after the _qs_parsers extract and before `cd {project}`:
  #   cd {project}/rust/qsurl && rm -rf target/wheels &&
  #   maturin build --release --manylinux off --interpreter python3 &&
  #   python3 -c "<same zip-extract one-liner, matching '_qsurl' and copying into {project}/querysource/qsurl/>" &&
  ```
  ```toml
  # pyproject.toml  (modifies pyproject.toml:118 and :195)
  dependencies = [ ..., "navigator-auth>=0.15.8", "lark>=1.3.1", ]     # the working tree already carries this line uncommitted
  [tool.setuptools.package-data]
  "querysource.qs_parsers" = ["*.so", "*.pyd"]
  "querysource.qsurl" = ["*.so", "*.pyd", "*.lark"]
  ```
  `uv lock` must be re-run so `uv.lock` records `lark` as direct.

### Module 12: Documentation
- **Path**: `docs/QSURL.md` (new)
- **Responsibility**: dialect reference for humans and agents: grammar (from `parser.rs`), operator table (URL → `expression`), IR contract, `requires` vocabulary and per-provider capability table (§ Module 5), error contract with the 400 envelope example, `?q=` form and percent-encoding rules, `QSURL_MAX_RESIDUAL_ROWS` / `residual_scan`, `to_gbnf()` usage, Python API.
- **Depends on**: M10 (final route/envelope).

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `cargo test` (11 reference tests + `pymodule_is_named__qsurl` compile check + `requires_is_declaration_ordered`) | M1 | `cargo test` in `rust/qsurl` needs no interpreter (pyo3 is optional) |
| `test_parse_uses_rust_when_available` / `test_parse_falls_back_to_lark` | M2 | `HAS_RUST` ladder; monkeypatch `_rs=None` → fallback path + single warning |
| `test_qsurlerror_from_json_roundtrip` | M2 | `QSUrlError.from_json(str(err)).to_dict() == err.to_dict()`; `code == 400` |
| `test_capabilities_validate_rejects_unknown` | M2 | `validate({"select","foo"})` → `ValueError` |
| `test_residualplan_is_empty` | M2 | defaults → empty; any field set → non-empty |
| `test_parity_corpus[<id>]` | M3 | Lark JSON == Rust JSON bytes per corpus case (Rust half `skipif(not HAS_RUST)`); error cases compare `kind/offset/message` |
| `test_lark_lowering_rules` | M3 | one assertion per rule 1–10 of Module 3 independent of the corpus |
| `test_gbnf_accepts_all_valid_corpus_inputs` / `test_gbnf_rejects_unknown_pipe` | M4 | exported GBNF matches every valid corpus input; rejects `:order(name)` |
| `test_provider_capability_sets` | M5 | exact sets and `residual_scan` per Module 5 table; every value ⊆ `capabilities.ALL` |
| `test_split_pushdown_table[<expr>]` | M6 | every row of the Module 6 leaf table on `pgProvider.capabilities` and on `BASE` |
| `test_split_root_or_is_full_residual` / `test_split_duplicate_key_goes_residual` | M6 | root `or` → `filter` absent, `plan.filter` = whole tree; two leaves on one column → second residual |
| `test_split_window_rules` | M6 | limit/offset pushed only when filter fully pushed, no distinct, sort pushed |
| `test_split_alias_rules` / `test_split_projection_adds_missing_columns` | M6 | `col AS alias` only when plan otherwise empty; missing columns appended + `plan.project` |
| `test_split_unsupported_and_cost` | M6 | `functions` → `unsupported`; residual-only + `residual_scan=False` → `cost`; bad identifier → `lower` |
| `test_residual_leaf_semantics[<expr>]` | M7 | every row of the Module 7 table incl. case-insensitive `startswith`/`endswith`, `utc=True` datetimes, `is_null` on `""` |
| `test_residual_apply_order` / `test_residual_list_roundtrip` | M7 | filter → sort → project → distinct → offset → limit → rename; list in → list out |
| `test_residual_unknown_column` / `test_residual_bad_regex` | M7 | `QSUrlError(kind="lower")` |
| `test_qs_applies_residual_on_cache_hit` / `test_qs_applies_residual_after_fetch` | M8 | monkeypatched `connection.from_cache` / `_qs.query`; plan applied before `_output_format` |
| `test_qs_cost_guard` / `test_qs_empty_after_residual_raises_datanotfound` | M8 | `QSURL_MAX_RESIDUAL_ROWS` monkeypatched to 3; 4 rows → `QSUrlError("cost")`; empty → `DataNotFound` |
| `test_pg_ilike_dict_operator_rust` / `test_pg_ilike_dict_operator_cython` | M9 | `{"city": {"ILIKE": "%san%"}}` → `city ILIKE '%san%'`; non-string value rejected; `'` doubled |
| `test_build_error_payload_public_detail_survives_prod` | M10 | `debug=False` + `public_detail` → `detail` present, `trace` absent |
| `test_qsurl_service_parse_error_is_400_with_detail` | M10 | fake request (`tests/handlers/test_queryservice_pbac_smoke.py` `__new__` pattern); body has `detail.kind == "parse"` and `pointer` |
| `test_qsurl_service_pbac_before_parse_side_effects` | M10 | `_enforce_pbac` 404 → `get_source` never awaited |
| `test_qsurl_service_q_form` / `test_qsurl_service_q_with_nonbare_path_is_400` | M10 | `stores?q={a}?b=1` joins; `stores{a}?q=...` → 400 parse |
| `test_qsurl_service_decodes_once` | M10 | `%2527` reaches the parser as `%27`, not `'` |
| `test_qsurl_service_q_not_in_conditions` | M10 | `q` never appears in the conditions passed to `QS` |

### Integration Tests
| Test | Description |
|---|---|
| `tests/e2e/test_qsurl_dry_run.py::test_pushdown_only_pg_sql` | dry-run `QS` harness (`tests/e2e/conftest.py`): `hisense_stores{store_id,name}?state_code='CA'&opened>=2024-01-01:sort(-name):top(50)` on a PostgreSQL definition renders `SELECT store_id, name … WHERE state_code='CA' AND opened >= '2024-01-01' ORDER BY name DESC LIMIT 50` (exact text pinned in the test) and an empty plan |
| `tests/e2e/test_qsurl_dry_run.py::test_or_filter_is_residual_on_pg` | root `or` → no WHERE from qsurl, plan carries the tree; residual applied to a stubbed result |
| `tests/e2e/test_qsurl_dry_run.py::test_cassandra_residual_only_is_cost` | `cassandraProvider` + `?city~'san'` → `QSUrlError("cost")` before any query |
| `tests/qsurl/test_wheel_layout.py` | `importlib.resources.files("querysource.qsurl") / "grammar.lark"` exists; `_qsurl` importable when `HAS_RUST` |

### Test Data / Fixtures
```python
# tests/qsurl/conftest.py
@pytest.fixture(scope="session")
def corpus() -> list[dict]:
    """tests/qsurl/corpus.json loaded once; ids are used as pytest param ids."""

@pytest.fixture
def stores_df() -> pd.DataFrame:
    """8 rows: store_id, name, city (mixed case, one None, one ''), state_code, opened (ISO strings
    with and without tz), closed_at (nullable) — enough to hit every Module 7 leaf branch."""

@pytest.fixture
def pg_caps() -> frozenset[str]:
    return pgProvider.capabilities

# Corpus MUST include (ids): example_query, no_prefix_selection_or_filter, decoded_whitespace,
# precedence_or_and_not_parens, null_checks_lists_text_ops, functions_navigation_alias_flipped,
# typed_literals (int/float/bool/null/date/datetime with Z and +02:00/quoted date),
# unknown_pipe_operator (error), syntax_error_offset (error), lowering_errors (3 error cases),
# duplicate_top (error), duplicate_skip (error), q_form_equivalent, distinct_only, eight_kb_url.
```

---

## 5. Acceptance Criteria

> This feature is complete when ALL of the following are true:

- [ ] AC1 `cargo test --manifest-path rust/qsurl/Cargo.toml` passes with no Python interpreter linked (pyo3 optional); `cargo run --example parse -- "/queries/x{a}?a=1:top(1)"` prints the IR.
- [ ] AC2 `make build-rust` installs both `querysource.qs_parsers._qs_parsers` and `querysource.qsurl._qsurl`; `python -c "from querysource.qsurl import HAS_RUST; assert HAS_RUST"` after it.
- [ ] AC3 `pytest tests/qsurl -q` passes with `HAS_RUST` true **and** with the extension hidden (`QSURL_FORCE_FALLBACK=1` env honoured by the ladder for tests only): every corpus case yields byte-identical IR JSON on both back-ends.
- [ ] AC4 Every error raised by either back-end is a `QSUrlError` with `kind ∈ {parse, lower, unsupported, cost}` and `offset/message/found/expected/pointer`; unknown `:op` messages list `:sort, :top, :limit, :skip, :offset, :distinct`.
- [ ] AC5 `GET /api/v1/services/qsurl/<encoded>` and `GET /api/v1/services/qsurl/<slug>?q=<rest>` return the same payload the legacy route returns for the equivalent conditions (dry-run SQL pinned in `tests/e2e/test_qsurl_dry_run.py`); `q` never reaches `conditions`; the URL is decoded exactly once.
- [ ] AC6 The new route enforces `slug:execute` (tenant-aware branch included) before `get_source`, then `datasource:use` / `driver:use` after `build_provider()`, mirroring `QueryService.query`; PBAC deny → 404; slug not found → 400 "Slug Not Found" as today; `DataNotFound` → 204.
- [ ] AC7 Every qsurl error is HTTP 400 through `AbstractHandler.Error` and the body contains `detail` = `QSUrlError.to_dict()` **with `debug=False`** (production) as well as with `debug=True`; the legacy envelope for every other error is byte-identical to today (`tests/test_error_formatter.py`, `tests/test_feat102_error_redaction.py` unchanged and green).
- [ ] AC8 `functions` / `navigation` in `requires` → 400 `kind: "unsupported"` naming the capability; no provider declares them.
- [ ] AC9 `BaseProvider.capabilities` / `residual_scan` exist with the Module 5 sets; every declared token is in `capabilities.ALL`; unaudited providers inherit the base set.
- [ ] AC10 `translate.split` obeys every row of the Module 6 table and rules 3–8 (window/alias/projection/duplicate-key/cost) — one test per rule.
- [ ] AC11 Text operators are case-insensitive on every engine: PostgreSQL renders `ILIKE` via the dict operator (Rust and Cython builders agree, `tests/test_rust_parsers.py`), all other providers evaluate them in the residual stage with lower-cased comparison; `regex` is always residual; `%`/`_`/`\` in user text are escaped before reaching `ILIKE`.
- [ ] AC12 `QS(residual=plan)` applies the plan on the cache-hit path and the provider path before `_output_format`; `len(rows) > QSURL_MAX_RESIDUAL_ROWS` → `QSUrlError("cost")` before any DataFrame is built; `residual_scan=False` + residual-only filter → `QSUrlError("cost")` before any query executes; the cache key is unchanged (pushdown checksum).
- [ ] AC13 The residual evaluator never calls `eval`/`exec`/`DataFrame.query`/`DataFrame.eval` (asserted by a test that greps `querysource/qsurl/residual.py`).
- [ ] AC14 Datetime literals: pushdown carries the ISO string verbatim; residual compares with `pd.to_datetime(..., utc=True)`; unquoted `2024-01-01` carries `dtype: "date"`, quoted does not.
- [ ] AC15 `distinct` is always residual; `sort`/`limit`/`offset` are pushed only per Module 6 rules 5–6 (no limit pushed above a residual filter, distinct or residual sort).
- [ ] AC16 `querysource.qsurl.to_gbnf()` returns a grammar that accepts every valid corpus input (`tests/qsurl/test_gbnf.py`).
- [ ] AC17 `make stage-rust` leaves `querysource/qsurl/_qsurl*.so` next to `grammar.lark`; `pyproject.toml` `package-data` includes `"querysource.qsurl" = ["*.so", "*.pyd", "*.lark"]`; `lark>=1.3.1` is a direct dependency and `uv.lock` is updated; `release.yml` `CIBW_BEFORE_BUILD` builds and extracts `_qsurl` exactly like `_qs_parsers`.
- [ ] AC18 Legacy route untouched: `pytest tests/handlers tests/e2e/test_qs_dry_run.py tests/test_rust_parsers.py -q` green; `slug:format` still works on `/api/v2/services/queries/{slug}`.
- [ ] AC19 `ruff check querysource/qsurl querysource/handlers/qsurl.py querysource/queries/qs.py querysource/providers tests/qsurl` clean; Google-style docstrings and type hints on every new function/class.
- [ ] AC20 `docs/QSURL.md` documents grammar, operator table, IR, capabilities per provider, error contract with a 400 example, `?q=` form, cost guard settings and `to_gbnf()`.
- [ ] AC21 Performance: `pytest -m perf tests/qsurl/test_perf.py` shows the Rust parse of the 8 KB corpus URL under 1 ms median and the Lark parse under 100 ms median (opt-in marker, not run by default).

---

## 6. Codebase Contract

> **CRITICAL — Anti-Hallucination Anchor**
> Verified against base commit `77e2742` (dev, 2026-09-24). Line numbers are exact at that commit.

### User-Provided Code

The user supplied the reference crate as `~/Descargas/qsurl.tar.gz` (re-extracted at spec time;
structure: `qsurl/{Cargo.toml,README.md,examples/parse.rs,src/{ast,ir,lib,parser,python}.rs}`).
Verbatim excerpts:

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
// Source: user-provided qsurl/src/python.rs (module must be renamed `_qsurl` for maturin's module-name)
#[pyfunction]
fn parse(src: &str) -> PyResult<String> {
    crate::parse_to_json(src).map_err(|e| PyValueError::new_err(e.to_json()))
}
#[pyfunction]
fn requires(src: &str) -> PyResult<Vec<String>> {
    let ir = crate::parse_to_ir(src).map_err(|e| PyValueError::new_err(e.to_json()))?;
    Ok(ir["requires"].as_array().map(|a| a.iter().filter_map(|v| v.as_str().map(str::to_owned)).collect()).unwrap_or_default())
}
#[pymodule]
fn qsurl(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parse, m)?)?;
    m.add_function(wrap_pyfunction!(requires, m)?)?;
    Ok(())
}
```

```rust
// Source: user-provided qsurl/src/lib.rs (error contract + entry points; lines 29-125)
#[derive(Debug, Clone, Serialize)]
pub struct ParseError { pub offset: usize, pub message: String, pub found: Option<String>, pub expected: Vec<String>, pub pointer: String }
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Error { Parse(ParseError), Lower { message: String } }
impl Error { pub fn to_json(&self) -> String { serde_json::to_string(self).expect("error is serializable") } }
fn pointer(src: &str, offset: usize) -> String {            // query + "\n" + spaces(char count up to offset) + "^"
    let col = src[..offset.min(src.len())].chars().count();
    format!("{src}\n{}^", " ".repeat(col))
}
pub fn parse(src: &str) -> Result<Query, ParseError>        // src.trim(); furthest-offset Rich error wins;
                                                            // message: Custom(msg) | "unexpected `c`|unexpected end of query at position N[; expected a, b]"
pub fn parse_to_ir(src: &str) -> Result<serde_json::Value, Error>
pub fn parse_to_json(src: &str) -> Result<String, Error>
```

```rust
// Source: user-provided qsurl/src/ir.rs (capability vocabulary; Ord = declaration order → `requires` order)
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Feature { Select, Alias, Filter, Or, Not, InList, NullCheck, TextMatch, Regex, Functions, Navigation, Sort, Limit, Offset, Distinct }
pub fn lower(q: &Query) -> Result<Value, LowerError>
// leaf(): (Eq, Null) → is_null; (Ne, Null) → not_null; (Eq|Ne, List) → membership; (_, List) → LowerError;
//         Regex → need Regex; Contains|NotContains|StartsWith|EndsWith → need TextMatch; dtype only for Date/DateTime
// lower_expr(): flipped comparison via CmpOp::flipped() (None for text ops → LowerError);
//         Truthy(col) → not_null; Not(Truthy(col)) → is_null (no Not feature); Not(other) → {"not":..} + Not; Or → + Or
// lower(): fields (alias → {"column","alias"} + Alias; dotted → Navigation); single leaf → {"and":[leaf]};
//         sort → [{"column","order":"asc"|"desc"}]; limit/offset → Option<u64>; distinct: bool
```

```rust
// Source: user-provided qsurl/src/ast.rs (serialisation of literals and operators)
#[derive(Serialize)] #[serde(untagged)]
pub enum Literal { Null, Bool(bool), Int(i64), Float(f64), Str(String), Date(String), DateTime(String) }   // dtype(): "null"|"bool"|"int"|"float"|"str"|"date"|"datetime"
pub enum CmpOp { Eq, Ne, Lt, Le, Gt, Ge, Contains, NotContains, StartsWith, EndsWith, Regex }           // as_str(): "==" "!=" "<" "<=" ">" ">=" "contains" "not_contains" "startswith" "endswith" "regex"
pub struct Query { pub slug: String, pub fields: Vec<Field>, pub filter: Option<Expr>, pub sort: Vec<SortKey>, pub limit: Option<u64>, pub offset: Option<u64>, pub distinct: bool }
pub enum PipeOp { Sort(Vec<SortKey>), Top(u64), Skip(u64), Distinct }   // PipeOp::KNOWN = ["sort","top","limit","skip","offset","distinct"]
```

```text
// Source: user-provided qsurl/src/parser.rs doc comment (grammar, phase 1; identical to sdd/proposals/qsurl-spec.md §2)
query      := prefix? slug selection? filter? pipe*
prefix     := '/' | '/queries/' | 'queries/'
slug       := [A-Za-z0-9_-]+
selection  := '{' field (',' field)* ','? '}'
field      := path (':as(' ident ')')?
path       := ident ('.' ident)*
filter     := '?' expr
expr       := expr '|' expr | expr '&' expr | '!' expr | '(' expr ')' | operand (cmp operand)?
cmp        := '==' | '=' | '!=' | '<=' | '>=' | '<' | '>' | '~' | '!~' | '^=' | '$=' | '=~'
operand    := list | literal | call | path
list       := '(' literal (',' literal)* ')'
call       := ident '(' (operand (',' operand)*)? ')'
literal    := null | true | false | datetime | date | number | string
date       := DDDD-DD-DD
datetime   := date 'T' DD:DD (':' DD)? ('Z' | ('+'|'-') DD:DD)?
number     := '-'? int ('.' digits)?
string     := '\'' ( '\'\'' | [^'] )* '\''  |  '"' ( '\"' | [^"] )* '"'
pipe       := ':sort(' sortkey (',' sortkey)* ')' | ':top(' int ')' | ':limit(' int ')' | ':skip(' int ')' | ':offset(' int ')' | ':distinct'
sortkey    := ('+' | '-')? path
// ident = chumsky text::ascii::ident() = [A-Za-z_][A-Za-z0-9_]*; every token is .padded() (whitespace tolerated)
// pipe precedence: prefix(3,'!') > infix left(2,'&') > infix left(1,'|')
```

Reference-crate test names (11, `src/lib.rs` tests module) to port: `example_query_lowers_to_querysource_ir`,
`works_without_prefix_selection_or_filter`, `tolerates_decoded_whitespace`, `precedence_or_and_not_and_parens`,
`null_checks_lists_and_text_operators`, `functions_navigation_alias_and_flipped_comparison`, `literals_are_typed`,
`unknown_pipe_operator_lists_valid_ones`, `syntax_error_points_at_offset`, `lowering_errors_are_explicit`,
`duplicate_top_is_rejected`.

### Verified Imports
```python
from querysource.queries import QS                                   # verified: querysource/queries/__init__.py:6
from querysource.queries.base import BaseQuery                       # verified: querysource/queries/base.py:24
from querysource.providers import BaseProvider                       # verified: querysource/providers/__init__.py:4,7
from querysource.providers.abstract import BaseProvider              # verified: querysource/providers/abstract.py:33
from querysource.providers.sql import sqlProvider                    # verified: querysource/providers/sql.py:32
from querysource.providers.pg import pgProvider                      # verified: querysource/providers/pg.py:14
from querysource.providers.cassandra import cassandraProvider        # verified: querysource/providers/cassandra.py:26
from querysource.parsers.abstract import AbstractParser              # verified: querysource/parsers/abstract.pyx:28 (Cython)
from querysource.types.dt.filters import build_condition, create_filter   # verified: querysource/types/dt/filters.py:22,204
from querysource.qs_parsers import HAS_RUST                          # verified: querysource/qs_parsers/__init__.py:10-25
from querysource.handlers import QueryService                        # verified: querysource/handlers/__init__.py:12
from querysource.handlers.abstract import AbstractHandler            # verified: querysource/handlers/abstract.py:32
from querysource.handlers.service import QueryService                # verified: querysource/handlers/service.py:31
from querysource.utils.handlers import QueryHandler, _is_excluded_param   # verified: querysource/utils/handlers.py:17,27
from querysource.utils.errors import build_error_payload, GENERIC_MESSAGES  # verified: querysource/utils/errors.py:22,43
from querysource.utils.functions import check_empty                  # verified: querysource/queries/qs.py:37 (Cython module querysource/utils/functions.pyx)
from querysource.conf import EXCLUDED_QUERY_PARAMETERS               # verified: querysource/conf.py:387
from querysource.exceptions import QueryException, DataNotFound, QueryError, SlugNotFound, ParserError, DriverError   # verified: querysource/exceptions.py:6,53,49,39,86,74
from querysource.outputs import DataOutput                           # verified: querysource/outputs/__init__.py:3,6
from querysource.outputs.output import DataOutput                    # verified: querysource/outputs/output.py:63
from querysource.auth import ResourceType                            # verified: querysource/handlers/service.py:14 (import site)
from querysource.tenants import QueryIdentity                        # verified: querysource/handlers/service.py:26 (import site)
from querysource.tenant_errors import TenantError                    # verified: querysource/handlers/service.py:25 (import site)
from querysource.types import graph_ouputs, mime_supported           # verified: querysource/handlers/service.py:27 (import site; `graph_ouputs` sic)
from querysource.conf import CSV_DEFAULT_DELIMITER, CSV_DEFAULT_QUOTING   # verified: querysource/handlers/service.py:15
from navconfig import config                                         # verified: querysource/conf.py:5
import lark                                                          # verified: uv pip show lark → 1.3.1 (transitive today; pyproject.toml:119 uncommitted line makes it direct)
```

### Existing Class Signatures
```python
# querysource/queries/qs.py
class QS(BaseQuery):                                                               # line 49
    def __init__(self, slug: str = '', conditions: dict = None, request: web.Request = None,
                 loop: asyncio.AbstractEventLoop = None, *, tenant: str | None = None,
                 definition: "LoadedDefinition | None" = None, principal: "QSPrincipal | None" = None,
                 **kwargs): ...                                                    # line 55-67
    # self._qs: BaseProvider = None (80); self.is_cached: bool = False (83); lazy = kwargs.pop('lazy', True) (116)
    # self.connection = QueryConnection(lazy=lazy, loop=self._loop) (118-121)
    async def build_provider(self): ...                                            # line 157; self._qs = self._provider(**args) at 283/332/381
    async def query(self, output_format: str | None = None): ...                   # line 428
    # cache-hit: try/except/else at 483-506 — `else: self._result = result; return await self._output_format(self._result, error)` (504-506)
    # provider:  `async with self.semaphore:` 511; try 512-580 (except Exception → self.Error(code=500) at 572-577; finally dispose 578-585)
    #            `if check_empty(result): raise DataNotFound(...)` 582-585; `self._result = result` 586; save_cache 588-592; return _output_format 594-596

# querysource/queries/base.py
class BaseQuery(AbstractQuery):                                                    # line 24
    def __init__(self, slug=None, conditions=None, request=None, loop=None, *, tenant=None, definition=None, principal=None, **kwargs)  # 26-49
    async def output(self, result, error): ...                                     # line 127
    def output_format(self, frmt: str = 'native', **kwargs): ...                   # line 132 — self._output_format = OutputFactory(self, frmt=frmt, **kwargs)

# querysource/interfaces/queries.py
class AbstractQuery(Connection):                                                   # line 45
    def __init__(...)                                                              # line 52; self._conditions = conditions or {} (105); self.kwargs = kwargs (114)

# querysource/providers/abstract.py
class BaseProvider(ABC):                                                           # line 33
    __parser__: AbstractParser = None                                              # line 35
    _parser_options: dict = {}                                                     # line 36
    replacement: dict = {...}                                                      # line 38-46
    def __init__(self, slug: str = '', query: Any = None, qstype: str = '', connection: Callable = None,
                 definition: Union[QueryModel, dict] = None, conditions: dict = None,
                 request: web.Request = None, **kwargs): ...                       # line 48-57
    # self._parser = self.__parser__(..., **self._parser_options) at 119-125 (parser built with conditions at construction)
    async def query(self): ...  # abstract                                         # line 297
    @property
    def parser(self): return self._parser                                          # line 311
    def refresh(self): ...                                                         # line 314
    def checksum(self): return get_hash(self._query)                               # line 323

# querysource/providers/sql.py:32  class sqlProvider(BaseProvider): __parser__ = SQLParser (48); _PARSER_PLACEHOLDERS (49-)
# querysource/providers/pg.py:14   class pgProvider(sqlProvider): __parser__ = pgSQLParser (21)
# querysource/providers/cassandra.py:26  class cassandraProvider(BaseProvider): __parser__ = CQLParser (31)

# querysource/parsers/abstract.pyx (Cython)
cdef class AbstractParser:                                                         # line 28
    cdef void _query_fields_sync(self)        # 199: self.fields = self.conditions.pop('fields', [])
    cdef void _query_limit_sync(self)         # 207: self.querylimit = int(self.conditions.pop('_limit', 0)) or 'querylimit'
    cdef void _offset_pagination_sync(self)   # 215: self._offset = self.conditions.pop('_offset', 0); 'paged'
    cdef void _ordering_sync(self)            # 259: 'order_by' + 'ordering' (str → split(',')); list joined as-is
    cdef void _query_filter_sync(self)        # 293: 'where_cond' else 'filter'
    async def set_options(self)               # 391: self._distinct = bool(self.conditions.pop('distinct', False)) at 399
# querysource/parsers/abstract.pxd:17-36  cdef public dict filter; list fields; list ordering; int32_t _limit; int32_t _offset; bint _distinct

# querysource/parsers/sql.pyx
COMPARISON_TOKENS = ('>=', '<=', '<>', '!=', '<', '>',)                            # line 25
self.valid_operators: tuple = ('<', '>', '>=', '<=', '<>', '!=', 'IS NOT', 'IS')   # line 96
self._base_sql = 'SELECT {fields} FROM {tablename} {filter} {grouping} {offset} {limit}'   # line 98
async def filter_conditions(self, sql)     # 113; Rust: _rs.filter_conditions(sql, dict(self.filter), dict(self.cond_definition)) (119)
    # fallback: key safety rstrip('|!~#@:') (133-139); dict → op in COMPARISON_TOKENS (155); list → IN / 'key!' NOT IN (170-179);
    #           'null'/'!null' (190-197); end == '!' → name != v (199); '!value' (204); bool (212)
async def order_by(self, sql)              # 292; ', '.join(self.ordering) (302)
async def limiting(self, sql, limit=None, offset=None)   # 308
async def process_fields(self, sql)        # 328; _rs.process_fields(sql, self.fields, bool(self._add_fields), self.query_raw) (331)

# querysource/parsers/pgsql.pyx
async def filter_conditions(self, sql)     # 164; Rust: _rs.pgsql_filter_conditions(sql, self.filter, cond_def) (169)
    # fallback dict branch: jsonb_condition (219); op, v = value.popitem() (225); `if op in COMPARISON_TOKENS:` (226) → Entity.quoteString (228)
    # str branch: end == '~' → ILIKE 'base%' where base = str_value[:-1] (275-278); end == '!~' → NOT ILIKE (279-282)

# rust/src/pgsql_parser.rs
fn pg_safe_identifier_key(key: &str) -> Option<String>   # 24; trim_end_matches('|' '!' '~' '#' '@' ':') (25)
fn pg_validate_operator(op: &str) -> bool                # 37; COMPARISON_TOKENS | VALID_OPERATORS | JSONB_OPERATORS
fn pg_literal(value: &str) -> String                     # 50; '' doubling; E'..' when { } \ present
const COMPARISON_TOKENS (74); const VALID_OPERATORS = ["<", ">", ">=", "<=", "<>", "!=", "IS NOT", "IS"] (77); const JSONB_OPERATORS (80)
fn process_dict_value(...)                               # 342; pg_validate_operator (354); format!("{} {} {}", key, op, safe_v) (362)
fn process_str_value(key, value, name, end, _format)     # 434; end == "~" → ILIKE 'base%' (442-447); end == "!~" (449-454)
pub fn pgsql_filter_conditions(sql: &str, filter_dict: &Bound<PyDict>, cond_definition: &Bound<PyDict>) -> PyResult<String>   # 540

# rust/src/validators.rs
static EVAL_FIELD = r"^(?:(@|!|#|~|:|))(\w*)(?:(\||\&|\!|\~|\#)|)+$"   # 11-13 (suffix capture = LAST char only)
pub fn field_components(field: &str) -> Vec<(String, String, String)>    # 120
# rust/src/sql_parser.rs
pub fn process_fields(sql: &str, fields: Vec<String>, add_fields: bool, query_raw: &str) -> String   # 476; fields.join(", ") verbatim (502) — NO identifier validation
# rust/src/lib.rs  #[pymodule] fn _qs_parsers (33-34); registration list 36-85

# querysource/types/dt/filters.py
def build_condition(expression: str, column: str, value, condition: dict, df: pd.DataFrame = None) -> str   # 22 — returns an EVAL STRING
    # is_null → "df[c].isnull() | (df[c] == '')" (55-58); contains → f"df['{c}'].str.contains(r'{value}', na=False, case=False)" (82)
    # startswith → f"df['{c}'].str.startswith('{value}')" (86, CASE-SENSITIVE); endswith (90, CASE-SENSITIVE); regex (145-152)
def create_filter_chain(expression: list, column: str, df) -> list   # 173
def create_filter(_filter: list, df: pd.DataFrame) -> list           # 204

# querysource/handlers/service.py
class QueryService(AbstractHandler):                                               # line 31
    async def query(self, request)     # 134; params = self.query_parameters(request) (164); args = self.match_parameters(request) (165)
        # slug, _format = slug.split(':') (175); NotFound on param error (182)
        # tenant = request.get('qs_tenant'); registry = request.app.get("qs_tenant_registry") (200-201)
        # registry → QueryIdentity(store=..., slug=slug); await self._enforce_owned_slug(request, identity=identity, action="slug:execute") (213-218)
        # else await self._enforce_pbac(request, resource_type=ResourceType.SLUG, resource_name=slug, action="slug:execute") (219-225)
        # queryformat (227-246); _download (248-252); _filename (254-258); writer_options (260-284); output_args (285-289); conditions = {**options, **params} (292)
        # query = await self.get_source(request, slug, conditions, driver=args, tenant=tenant, definition=request.get('qs_definition')) (307-310)
        # await query.build_provider() with SlugNotFound→400 / TenantError→err.code / ParserError / ProviderError,DriverError / Exception→Except (311-345)
        # datasource:use / driver:use PBAC (346-367); queryformat from query.accepts() (369-371)
        # DataOutput(request, query=query, ctype=queryformat, slug=slug, **output_args); return await output.response() (372-380)

# querysource/handlers/abstract.py
class AbstractHandler(BaseHandler):                                                # line 32
    debug: bool = DEBUG                                                            # line 36
    def format(self, request: web.Request, args: dict, ctype: str = None) -> str   # line 56
    def NoData(self, message='Data Not Found', headers=None) -> web.Response       # line 86 (204)
    def NotFound(self, message: str, exception: BaseException = None)              # line 107
    def Error(self, reason: dict = None, message: str = None, exception: BaseException = None,
              stacktrace: str = None, code: int = 400) -> HTTPException            # line 133-139; build_error_payload(... public_message=message if self.debug else None) at 167-174
    def Except(self, reason=None, message=None, exception=None, stacktrace=None, headers=None, code=500)   # line 204
    async def get_source(self, request, slug, conditions, **kwargs) -> QS          # line 269; QS(slug=slug, conditions=conditions, loop=self._loop, request=request, lazy=False, **kwargs) (277-284)
    async def _enforce_pbac(self, request, resource_type, resource_name: str, action: str) -> None      # line 322
    async def _enforce_owned_slug(self, request, identity: QueryIdentity, action: str) -> None          # line 440

# querysource/utils/errors.py
def build_error_payload(*, category: str, status: int, exception=None, debug: bool = False,
                        logger=None, public_message: Optional[str] = None) -> dict[str, Any]   # line 43-51
    # payload = {"error", "status", "error_id"} (140-144); if debug: payload["detail"] = detail; payload["trace"] = trace (145-147)
    # OutputError carve-out: exception message public even in prod (123-127)

# querysource/outputs/output.py
class DataOutput:                                                                  # line 63
    def __init__(self, request: web.Request, query: Union[AbstractQuery, DataFrame, list], ctype: str = 'json', slug: str = None, **kwargs)   # 67-74
    async def response(self)                                                       # line 210; writer.get_result() (236); except (NoDataFound, DataNotFound) → no_content 204 (237-245); except (DriverError, QueryException) → error response (259-)

# querysource/utils/handlers.py
def _is_excluded_param(key: str) -> bool                                           # line 17
class QueryHandler(BaseHandler):                                                   # line 27
    def query_parameters(self, request: web.Request = None) -> dict                # line 29
    def parse_qs(self, request: web.Request = None) -> Optional[dict]              # line 37

# querysource/exceptions.py
class QueryException(Exception):   # 6; default_code = 500; __init__(self, message: str, code: int | None = None, **kwargs) (16); __str__ → message (24)
class SlugNotFound(QueryException) # 39   class QueryError(QueryException) # 49   class DataNotFound(QueryException) # 53
class DriverError(QueryException)  # 74   class ParserError(QueryException) # 86  class OutputError(QueryException) # 104
```

### Key Attributes & Constants
- `EXCLUDED_QUERY_PARAMETERS: set` (`querysource/conf.py:387-389`) — `auth, apikey, api_key, authorization` by default; `q` does not collide, and `QSUrlService` removes `q` itself before conditions are built.
- `config.getint("NAME", fallback=N)` is the settings idiom (`querysource/conf.py:284-295`).
- Route block for queries: `querysource/services.py:171-189` (`qs = QueryService()` at 168; last line `add_head('/api/v2/services/queries/{slug}', qs.get_columns)` at 189; `QueryExecutor` block starts 192).
- `rust/Cargo.toml`: `pyo3 = "0.29"` (12), `[lib] name = "_qs_parsers"`, `crate-type = ["cdylib"]` (7-9), feature `extension-module` default (26-28), release profile (31-34), no `[workspace]`.
- `rust/pyproject.toml`: `module-name = "querysource.qs_parsers._qs_parsers"`, `bindings = "pyo3"`, `features = ["pyo3/extension-module"]`, `requires = ["maturin>=1.15,<2.0"]`.
- `Makefile:17` `MATURIN := .venv/bin/maturin`; `Makefile:63-64` `build-rust`; `Makefile:72-80` `RUST_WHEEL_OUT := target/wheels` + `stage-rust` (copies `_qs_parsers*.so` into `querysource/qs_parsers/`).
- `pyproject.toml:193-195` `[tool.setuptools.package-data]` `"querysource.qs_parsers" = ["*.so", "*.pyd"]`; `pyproject.toml:118` last dependency `"navigator-auth>=0.15.8",` (the working tree carries `"lark>=1.3.1",` at 119, **uncommitted**); `pyproject.toml:168` `maturin>=1.15,<2.0` dev dependency.
- `.github/workflows/release.yml:47-53` `CIBW_BEFORE_BUILD: >-` (rustup, `pip install maturin`, `cd {project}/rust`, `maturin build --release --manylinux off --interpreter python3`, zip-extract of the `_qs_parsers` `.so` into `{project}/querysource/qs_parsers/`, `cd {project}`); only `codeql-analysis.yml` and `release.yml` exist under `.github/workflows/`.
- `.gitignore:107` ignores `target/` (covers `rust/qsurl/target/`).
- Toolchain: `rustc 1.90.0`; `lark 1.3.1` installed (transitive).
- Tests: `tests/e2e/conftest.py` (fake Redis `AsyncDB` at 94-97, `BaseQuery.get_definition_repository` monkeypatched at 114-126, `tests/e2e/test_qs_dry_run.py` as the model); `tests/handlers/test_queryservice_pbac_smoke.py:19-32` (`QueryService.__new__` + `MagicMock(spec=web.Request)`); `tests/handlers/test_query_param_exclusion.py:19-33` (`_FakeRequest` with `MultiDict`); `tests/test_rust_parsers.py:8-14` (`skipif(not HAS_RUST)`); `tests/test_error_formatter.py`, `tests/test_feat102_error_redaction.py` guard the envelope.
- `tests/qsurl/` and `querysource/qsurl/` do not exist yet; `docs/` exists (markdown references such as `docs/PBAC_PROGRAMMATIC.md`).

### Integration Points
| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `querysource.qsurl.parse` | `_qsurl.parse(src) -> str` | `json.loads`; `ValueError.args[0]` → `QSUrlError.from_json` | reference `python.rs` |
| `querysource.qsurl.parse` | `_fallback.parse` | lazy import on `not HAS_RUST` | Module 2 |
| `BaseProvider.capabilities` | `querysource.qsurl.capabilities.BASE` | class attribute default | `providers/abstract.py:36` (insert after) |
| `translate.split` | `AbstractParser` reserved keys | `conditions["fields" \| "filter" \| "ordering" \| "_limit" \| "_offset"]` | `parsers/abstract.pyx:199,209,218,267,301` |
| `translate.split` (text ops) | PG dict operator | `{col: {"ILIKE": pat}}` | `rust/src/pgsql_parser.rs:342-362`, `parsers/pgsql.pyx:225-228` (M9) |
| `QS._apply_residual` | `residual.apply` | on cache-hit `else:` and after `check_empty` | `queries/qs.py:504-506, 582-586` |
| `QS._apply_residual` | `QSURL_MAX_RESIDUAL_ROWS` | `from ..conf import QSURL_MAX_RESIDUAL_ROWS` | `conf.py:387` (insert before) |
| `QSUrlService.query` | `AbstractHandler._enforce_pbac` / `_enforce_owned_slug` | same calls as `QueryService.query` | `handlers/service.py:200-225` |
| `QSUrlService.query` | `AbstractHandler.get_source(..., residual=plan)` | `**kwargs` forwarded to `QS(...)` | `handlers/abstract.py:269-284` |
| `QSUrlService.query` | `DataOutput(request, query=..., ctype=..., slug=..., **output_args).response()` | as `QueryService.query` | `handlers/service.py:372-380` |
| `QSUrlService.query` | `AbstractHandler.Error(detail=...)` → `build_error_payload(public_detail=...)` | new kwargs | `handlers/abstract.py:133-174`, `utils/errors.py:43-147` |
| `DataOutput.response` | `QSUrlError` | `except QSUrlError: raise` first | `outputs/output.py:237` |
| route | `QuerySource.setup()` router block | `add_get('/api/v1/services/qsurl/{path:.*}', qsurl.query, allow_head=False)` | `services.py:189` (insert after) |
| build | `maturin develop --manifest-path rust/qsurl/Cargo.toml` | Makefile targets | `Makefile:63-64,73-80` |
| ship | `CIBW_BEFORE_BUILD` | second build + extract | `.github/workflows/release.yml:47-53` |

### Does NOT Exist (Anti-Hallucination)
- ~~`rust/qsurl/`~~, ~~`querysource/qsurl/`~~, ~~`querysource/parsers/qsurl*`~~, ~~`querysource/handlers/qsurl.py`~~, ~~`tests/qsurl/`~~, ~~`docs/QSURL.md`~~ — all created by this feature.
- ~~`BaseProvider.capabilities`~~ / ~~`BaseProvider.residual_scan`~~ — no provider or parser declares capabilities today (the only `capabilities` symbol is a dict in `querysource/queries/describe.py:236`, unrelated).
- ~~`QS.residual`~~ / ~~`QS._apply_residual`~~ / ~~`QS.post_filter`~~ — single-slug `QS` has no DataFrame stage; DataFrame filtering exists only in MultiQuery operators and `types/dt/filters.py`.
- ~~a standalone slug → provider-class resolver~~ — the mapping happens inside `QS.build_provider()` (`qs.py:157-420`); Module 10 probes through it.
- ~~`querysource/queries/abstract.py`~~ — the base class is `querysource/queries/base.py` (`BaseQuery`) over `querysource/interfaces/queries.py` (`AbstractQuery`).
- ~~`querysource/handlers/query.py`~~ — slug execution is `handlers/service.py` (`QueryService`); multi-query is `handlers/multi.py` (`QueryHandler`).
- ~~`querysource/utils/functions.py`~~ — it is a Cython module `functions.pyx` (import path `querysource.utils.functions` still works).
- ~~`.github/workflows/ci.yml`~~ — only `codeql-analysis.yml` and `release.yml` exist.
- ~~`ILIKE` / `LIKE` in any operator allowlist~~ — `COMPARISON_TOKENS`, `VALID_OPERATORS`, `JSONB_OPERATORS` (`pgsql_parser.rs:74-80`, `sql.pyx:25,96`) contain no text operators; M9 adds `ILIKE`/`NOT ILIKE` to PostgreSQL only.
- ~~a free single-character key suffix for `endswith`/`contains`~~ — `EVAL_FIELD` accepts only `| & ! ~ #` as suffixes and the identifier strip only `| ! ~ # @ :`; `@` and `&` fail one of the two, `|`/`!`/`~` are taken, and multi-character suffixes (`!~`) capture only their last character. This is why M9 uses the dict-operator form instead of key suffixes.
- ~~`build_condition` producing case-insensitive `startswith`/`endswith`~~ — `filters.py:86,90` are case-sensitive; only `contains` passes `case=False`.
- ~~a `detail` field in production error envelopes~~ — `build_error_payload` emits `detail`/`trace` only when `debug=True` (`utils/errors.py:145-147`); `public_message` is honoured only when `self.debug` (`handlers/abstract.py:173`).
- ~~`distinct` rendering in SQL dialects~~ — `AbstractParser` pops `distinct` into `_distinct` (`abstract.pyx:399`) but only `rethink.pyx:441` reads it.
- ~~`process_fields` validating field identifiers~~ — `rust/src/sql_parser.rs:476-505` joins the list verbatim; identifier safety for `col AS alias` is guaranteed by the grammar and M6's `IDENT_RE`.
- ~~`slug:format` on the new route~~ — the colon suffix belongs to the legacy handler (`service.py:175`) and conflicts with pipeline operators.
- ~~`pythonize`~~ or any Rust→Python object conversion — the FFI is a JSON string by decision.

### Edit Sites (Blueprint Anchors)

Verified against: `77e2742` (occurrence counts via `grep -cF`).

| File | Action | Verbatim anchor line | Verified at | Occurrences |
|---|---|---|---|---|
| `rust/qsurl/Cargo.toml` | CREATE | — | — | — |
| `rust/qsurl/pyproject.toml` | CREATE | — | — | — |
| `rust/qsurl/src/lib.rs` | CREATE | — | — | — |
| `rust/qsurl/src/ast.rs` | CREATE | — | — | — |
| `rust/qsurl/src/parser.rs` | CREATE | — | — | — |
| `rust/qsurl/src/ir.rs` | CREATE | — | — | — |
| `rust/qsurl/src/python.rs` | CREATE | — | — | — |
| `rust/qsurl/examples/parse.rs` | CREATE | — | — | — |
| `querysource/qsurl/__init__.py` | CREATE | — | — | — |
| `querysource/qsurl/errors.py` | CREATE | — | — | — |
| `querysource/qsurl/capabilities.py` | CREATE | — | — | — |
| `querysource/qsurl/plan.py` | CREATE | — | — | — |
| `querysource/qsurl/grammar.lark` | CREATE | — | — | — |
| `querysource/qsurl/_fallback.py` | CREATE | — | — | — |
| `querysource/qsurl/gbnf.py` | CREATE | — | — | — |
| `querysource/qsurl/translate.py` | CREATE | — | — | — |
| `querysource/qsurl/residual.py` | CREATE | — | — | — |
| `querysource/handlers/qsurl.py` | CREATE | — | — | — |
| `tests/qsurl/conftest.py`, `tests/qsurl/corpus.json`, `tests/qsurl/test_parity.py`, `tests/qsurl/test_gbnf.py`, `tests/qsurl/test_translate.py`, `tests/qsurl/test_residual.py`, `tests/qsurl/test_qs_residual.py`, `tests/qsurl/test_pg_ilike.py`, `tests/qsurl/test_wheel_layout.py`, `tests/qsurl/test_perf.py`, `tests/handlers/test_qsurl_service.py`, `tests/e2e/test_qsurl_dry_run.py`, `docs/QSURL.md` | CREATE | — | — | — |
| `querysource/providers/abstract.py` | MODIFY | `    _parser_options: dict = {}` | `abstract.py:36` | 1 |
| `querysource/providers/sql.py` | MODIFY | `    __parser__ = SQLParser` | `sql.py:48` | 1 |
| `querysource/providers/pg.py` | MODIFY | `    __parser__ = pgSQLParser` | `pg.py:21` | 1 |
| `querysource/providers/cassandra.py` | MODIFY | `    __parser__ = CQLParser` | `cassandra.py:31` | 1 |
| `querysource/queries/qs.py` | MODIFY (init) | `        self.is_cached: bool = False` | `qs.py:83` | 1 |
| `querysource/queries/qs.py` | MODIFY (cache hit) | `                    return await self._output_format(self._result, error)  # pylint: disable=W0150` | `qs.py:506` | 1 |
| `querysource/queries/qs.py` | MODIFY (provider) | `            if check_empty(result):` | `qs.py:582` | 1 |
| `querysource/conf.py` | MODIFY | `EXCLUDED_QUERY_PARAMETERS: set = {` (the assignment; the name also appears in the comment at 385 and inside the set-comprehension at 388) | `conf.py:387` | 3 (name) / 1 (this exact line) |
| `querysource/handlers/__init__.py` | MODIFY (import) | `from .service import QueryService` | `__init__.py:12` | 1 |
| `querysource/handlers/__init__.py` | MODIFY (`__all__`) | `    'QueryService',` | `__init__.py:22` | 1 |
| `querysource/services.py` | MODIFY | `        r = self.app.router.add_head('/api/v2/services/queries/{slug}', qs.get_columns)` | `services.py:189` | 1 |
| `querysource/handlers/abstract.py` | MODIFY (signature) | `        code: int = 400` (inside `def Error(` at 133; `def Except(` at 204 uses `code: int = 500`) | `abstract.py:139` | 1 |
| `querysource/handlers/abstract.py` | MODIFY (call) | `            public_message=message if self.debug else None,` — the occurrence inside `Error` (preceded by `            logger=self.logger,` at 172 and followed by `        )` then `        args = {` / `"X-STATUS": str(code)`); the other two are in `NotFound` (120) and `Except` (239) | `abstract.py:173` | 3 |
| `querysource/utils/errors.py` | MODIFY (signature) | `    public_message: Optional[str] = None,` | `errors.py:50` | 1 |
| `querysource/utils/errors.py` | MODIFY (payload) | `    if debug:` followed by `        payload["detail"] = detail` | `errors.py:145-146` | 1 |
| `querysource/outputs/output.py` | MODIFY | `            except (NoDataFound, DataNotFound) as err:` (inside `response`, after `await writer.get_result()` at 236) | `output.py:237` | 1 |
| `rust/src/pgsql_parser.rs` | MODIFY (consts) | `const VALID_OPERATORS: &[&str] = &["<", ">", ">=", "<=", "<>", "!=", "IS NOT", "IS"];` | `pgsql_parser.rs:77` | 1 |
| `rust/src/pgsql_parser.rs` | MODIFY (validator) | `fn pg_validate_operator(op: &str) -> bool {` | `pgsql_parser.rs:37` | 1 |
| `rust/src/pgsql_parser.rs` | MODIFY (render) | `fn process_dict_value(` | `pgsql_parser.rs:342` | 1 |
| `querysource/parsers/pgsql.pyx` | MODIFY | `                    if op in COMPARISON_TOKENS:` (dict branch of the fallback, after `op, v = value.popitem()` at 225) | `pgsql.pyx:226` | 1 |
| `Makefile` | MODIFY | `build-rust:` | `Makefile:63` | 1 |
| `Makefile` | MODIFY | `stage-rust:` | `Makefile:73` | 1 |
| `.github/workflows/release.yml` | MODIFY | `          CIBW_BEFORE_BUILD: >-` | `release.yml:47` | 1 |
| `pyproject.toml` | MODIFY (deps) | `    "navigator-auth>=0.15.8",` (the working tree already has `    "lark>=1.3.1",` right after it, uncommitted — the task commits it) | `pyproject.toml:118` | 1 |
| `pyproject.toml` | MODIFY (package-data) | `"querysource.qs_parsers" = ["*.so", "*.pyd"]` | `pyproject.toml:195` | 1 |
| `uv.lock` | MODIFY | (regenerated by `uv lock`; no anchor) | — | — |
| `tests/test_rust_parsers.py` | MODIFY (append) | `pytestmark = pytest.mark.skipif(not HAS_RUST, reason="qs_parsers not installed")` (append a PG `ILIKE` dict-operator case at the end of the file) | `test_rust_parsers.py:14` | 1 |
| `tests/test_error_formatter.py` | MODIFY (append) | (append one `public_detail` case at the end of the file; no anchor needed) | — | — |

---

## 7. Implementation Notes & Constraints

### Patterns to Follow
- **Import ladder** for the extension: copy `querysource/qs_parsers/__init__.py:10-25` shape (`from . import _qsurl` → `import _qsurl` → fallback); keep back-end resolution lazy so the package imports without Lark or Rust present.
- **Second crate**: mirror `rust/Cargo.toml` release profile and `rust/pyproject.toml` maturin table; keep pyo3 **optional** behind `python` so `cargo test` never links libpython (the existing crate needs `--no-default-features` for that, `rust/Cargo.toml:20-28`; the new one must not).
- **Handler**: subclass `AbstractHandler`; reuse `self.query_parameters`, `self.match_parameters`, `self.json_data`, `self.format`, `self.get_source`, `self._enforce_pbac`, `self._enforce_owned_slug`, `DataOutput` — never call driver SDKs or `QS` internals beyond `_qs.capabilities` / `_qs.residual_scan`.
- **Error envelope**: never bypass `AbstractHandler.Error`; the `detail` kwarg is the only sanctioned way to expose structured, client-safe data in production. `QSUrlError` messages contain grammar/lowering text and the user's own query — never SQL, DSNs or paths.
- **Settings**: `QSURL_MAX_RESIDUAL_ROWS` via `config.getint(..., fallback=50000)` in `querysource/conf.py`; read it at call time from `..conf` (tests monkeypatch the module attribute).
- **Logging**: `self._logger` in `QS`, `self.logger` in handlers, `logging.getLogger(__name__)` in `querysource/qsurl/*`; the fallback warning is emitted once per process.
- **Tests**: `tests/qsurl/` (pytest, `asyncio_mode = auto`); Rust-dependent halves `skipif(not HAS_RUST)`; the dry-run `QS` harness from `tests/e2e/conftest.py`; handler tests with `QSUrlService.__new__` + `MagicMock(spec=web.Request)`.
- **Docstrings + type hints** (Google style) everywhere; `ruff check` clean; no `print`, no `requests`, no `time.sleep`.
- Provider `capabilities` values must come from `querysource.qsurl.capabilities` constants, never bare strings.

### Known Risks / Gotchas
- **Residual evaluator must not use string `eval`**: `build_condition` (`types/dt/filters.py:82-91,141-152`) interpolates raw values into Python expression strings that MultiQuery evaluates; qsurl literals are URL-controlled, so M7 evaluates leaves with vectorised pandas calls instead (same semantics, `re.escape` for `contains`). This refines the brainstorm's "reuse `types/dt/filters.py`" resolution: the *vocabulary and semantics* are reused, the eval-string mechanism is not (AC13). If the user prefers literal reuse, `build_condition` must first be hardened — flagged in §8.
- **Case-insensitivity gap in `filters.py`**: `startswith`/`endswith` there are case-sensitive; M7 lower-cases both sides. Do not "fix" `filters.py` in this feature (MultiQuery semantics are out of scope).
- **`requires` ordering**: Rust emits `BTreeSet<Feature>` in *declaration* order; the Lark side must sort by `capabilities.ALL` index, not alphabetically, or the parity corpus fails on every multi-capability case.
- **`serde(untagged)` literals**: `Date`/`DateTime` serialise as plain strings; `dtype` is the only marker. Floats must serialise like `serde_json` (`1.0` → `1.0`, `1e3` never produced: the grammar has no exponent); the Lark side must emit `float` for any literal with a `.`.
- **Flat filter dict holds one condition per key**: two leaves on the same column in one conjunction (`?price>10&price<20`) → the second goes residual. `popitem()` in the dict branch (`sql.pyx:154`, `pgsql.pyx:225`) mutates the inner dict, so `translate.split` must return freshly built dicts and `QS` must not reuse a `conditions` object across retries.
- **Window pushdown correctness**: pushing `_limit`/`_offset` above a residual filter, a residual sort or `distinct` changes results; Module 6 rule 6 is mandatory and tested (AC15).
- **Alias vs residual**: `col AS alias` renames the column before the residual stage sees it; aliases are pushed only when the plan is otherwise empty, else `plan.rename` runs last.
- **Cache path**: cached rows are the *pushdown* result; the plan is applied after deserialisation. The cache key is the provider checksum of the pushdown query, so two qsurl queries with the same pushdown and different residuals share a cache entry correctly.
- **Cost guard placement**: `len(result) > QSURL_MAX_RESIDUAL_ROWS` is checked on the raw result (list or DataFrame) *before* `pd.DataFrame(...)`; the `residual_scan=False` check happens in `translate.split`, before `QS` is built.
- **Handler exception routing**: `DataOutput.response` swallows `QueryException` into a generic error (`output.py:259-`); without the `except QSUrlError: raise` clause (M10) a residual-stage error would surface as a redacted 4xx/5xx with no `detail`. Likewise `QS.query()`'s `except Exception → 500` (`qs.py:572-577`) does not cover the two insertion points chosen in M8 — keep them there.
- **`q` form**: `path` must be a bare slug when `q` is present; the handler joins `f"{path}{q}"` with no separator (the client sends `q={...}?...:...`). `q` is removed from `params` before building conditions so it never becomes a condition key.
- **Percent-decoding**: aiohttp already decodes `match_info["path"]`; the handler must **not** `unquote()` twice (AC5 `%2527` test pins the exact behaviour — the task must verify which layer decodes and decode exactly once).
- **Two extension builds**: `maturin develop` for the second manifest reuses `.venv`; `make stage-rust` must pick the newest `qsurl-*.whl` (not `qs_parsers-*.whl`); `release.yml` must run both builds before `cibuildwheel` repairs the final wheel. Forgetting `package-data` for `*.lark` silently disables the fallback in wheels (AC17 test).
- **Nested crate**: `rust/qsurl/` sits under the `rust/` package directory; `rust/Cargo.toml` has no `[workspace]`, so cargo treats them independently; `target/` is git-ignored (`.gitignore:107`). `cargo package` of the outer crate would include `qsurl/` — irrelevant (never published) but worth an `exclude = ["qsurl"]` only if that ever changes.
- **Cython rebuild**: M9 edits `pgsql.pyx`; run `make build-inplace` and `make build-rust` before its tests; both builders must produce identical SQL (`tests/test_rust_parsers.py`).
- **Identifier safety**: `process_fields` and `order_by` join lists verbatim; the grammar (`text::ascii::ident`) plus `IDENT_RE` in `translate.split` are the only guards — never relax them, and never push a dotted or function column.
- **Legacy `col~` convention**: the existing `~` suffix strips the value's last character (`pgsql.pyx:276`, `pgsql_parser.rs:443`); qsurl does not use it (dict `ILIKE` instead), so no interaction.
- **8 KB URLs**: gunicorn/aiohttp header limits apply to the path; lists longer than that must use `?q=`; document it (M12).

### External Dependencies
| Package | Version | Reason |
|---|---|---|
| `chumsky` (Rust) | `0.13` with `pratt` | parser combinators + precedence climbing; `Rich` errors give `expected`/`found`/`span` |
| `pyo3` (Rust, optional) | `0.29` | binding, same major as `rust/Cargo.toml:12` (cache has 0.29.2) |
| `serde` / `serde_json` (Rust) | `1` | IR and error serialisation (cache has 1.0.151) |
| `maturin` | `>=1.15,<2.0` | already a dev dependency (`pyproject.toml:168`) |
| `lark` | `>=1.3.1` | pure-Python fallback parser; becomes a **direct** dependency (`pyproject.toml:119` uncommitted line) |
| `pandas` | existing | residual stage (already used by `types/dt/filters.py`) |
| Rust toolchain | `>= 1.88` (`edition = "2024"`, let-chains in `ir.rs`) | local 1.90; CI `dtolnay/rust-toolchain@stable` |

---

## 8. Open Questions

> Resolved items are carried from `sdd/proposals/qsurl-parser.brainstorm.md` verbatim; refinements made while writing this spec are marked *(spec refinement)* and can be vetoed at review.

- [x] Flow type and base branch — *Resolved in brainstorm*: `feature` on `dev`.
- [x] Crate placement — *Resolved in brainstorm*: separate crate `rust/qsurl/` with the pyo3 binding behind the `python` feature, installed as `querysource.qsurl._qsurl`. → §3 M1.
- [x] Phase-1 scope — *Resolved in brainstorm*: parser + binding + HTTP route + execution through `QS` (full §7 of the proposal). → §1 G4, §3 M1–M12.
- [x] Behaviour without the Rust extension — *Resolved in brainstorm*: pure-Python fallback on Lark with a versioned `.lark` grammar and a Rust/Lark parity corpus. → §3 M3, AC3.
- [x] Residual execution — *Resolved in brainstorm*: new dataframe post-filter stage in `QS.query()` reusing `types/dt/filters.py`. → §3 M7/M8. *(spec refinement)*: the residual stage reuses the `{column, expression, value}` vocabulary and `build_condition`'s semantics but evaluates leaves with vectorised pandas calls instead of the eval-string mechanism, because `build_condition` interpolates raw values into `eval` strings (§7 Known Risks, AC13).
- [x] Capability declaration — *Resolved in brainstorm*: `capabilities` class attribute on `BaseProvider` with per-driver overrides. → §3 M5.
- [x] Route shape — *Resolved in brainstorm*: new route `/api/v1/services/qsurl/{path:.*}` plus `?q=` on that route; legacy `/api/v2/services/queries/{slug}` untouched. → §3 M10.
- [x] Cost guard for residual-only filters — *Resolved in brainstorm*: configurable cap `QSURL_MAX_RESIDUAL_ROWS` (navconfig, `querysource/conf.py`, default 50000): when the pushdown result exceeds the cap before the residual is applied → 400 `kind: "cost"`. Additionally a provider may declare `residual_scan = False` (Cassandra); on such a provider a residual filter with no pushed-down leaf → 400 `kind: "cost"` before executing anything. → §3 M6 rule 8, M8, AC12.
- [x] `functions` and `navigation` — *Resolved in brainstorm*: grammar unchanged (parser accepts them, IR marks them in `requires`); no provider declares them in phase 1, so translation returns 400 `kind: "unsupported"` naming the capability. Phase 2 enables them without touching the grammar. → §3 M6 rule 1, AC8.
- [x] Text-operator semantics — *Resolved in brainstorm*: `~` contains, `^=` startswith, `$=` endswith are **case-insensitive on every engine**; PostgreSQL pushes down all three via `ILIKE`; `=~` regex stays residual. → §3 M6/M7/M9, AC11. *(spec refinement)*: the brainstorm proposed new key suffixes next to `col~`; spec-time verification showed no free single-character suffix exists (`EVAL_FIELD` vs identifier strip, §6 "Does NOT Exist"), so PostgreSQL pushdown uses a dict-operator token `{col: {"ILIKE": pattern}}` (plus `NOT ILIKE` for `!~`, which the brainstorm left residual) in both builders. Same observable behaviour, safer quoting (`pg_literal`).
- [x] Alias pushdown — *Resolved in brainstorm*: SQL providers declare `alias` and emit `col AS alias` in `fields`; non-SQL providers keep alias residual (`df.rename` after fetch). → §3 M6 rule 4. *(spec refinement)*: `process_fields` performs no identifier validation (`sql_parser.rs:502`), so safety rests on the grammar plus `IDENT_RE`; aliases are pushed only when the plan is otherwise empty (§7).
- [x] FFI shape — *Resolved in brainstorm*: keep the reference `_qsurl.parse(src) -> str` (JSON) and `json.loads` in the Python wrapper; no `pythonize`; parity corpus compares the JSON bytes. → §3 M1/M2, AC3.
- [x] HTTP 400 body — *Resolved in brainstorm*: use the existing `AbstractHandler.Error` envelope; the error object travels intact inside the envelope's detail field. → §3 M10, AC7. *(spec refinement)*: production redaction currently drops `detail` entirely (`utils/errors.py:145-147`), so the envelope gains an explicit `detail`/`public_detail` pass-through that is emitted in every mode for caller-asserted client-safe objects; every other error body is unchanged.
- [x] Rust edition / MSRV — *Resolved in brainstorm*: keep the reference `edition = "2024"` (let-chains) and declare `rust-version = "1.88"` in `Cargo.toml`; CI uses `dtolnay/rust-toolchain@stable`. → §3 M1.
- [x] Datetime literals with timezone — *Resolved in brainstorm*: pushdown emits the ISO string as-is and lets the column type cast (PG `timestamptz`); the residual stage compares with `pd.to_datetime(..., utc=True)` when `dtype` is `date`/`datetime`. No new dependency. → §3 M7, AC14.
- [x] GBNF export — *Resolved in brainstorm*: included in this feature: `querysource/qsurl/gbnf.py` converts `grammar.lark` to GBNF, exposed as `querysource.qsurl.to_gbnf()` and shipped/tested alongside the grammar. → §3 M4, AC16.
- [ ] Should the residual evaluator be contributed back to `types/dt/filters.py` (hardening `build_condition` against eval injection) in a follow-up spec, so MultiQuery `Filter` and qsurl share one safe implementation? — *Owner: Jesus Lara* (does not block: M7 is qsurl-local by design).
- [ ] Capability probe cost (M10 `resolve_capabilities` builds a lazy `QS` and `build_provider()` before the real one): acceptable for phase 1, or should `QS` grow a public `provider_class_for(slug)` resolver in a follow-up? — *Owner: Jesus Lara* (implementation proceeds with the probe).

---

## 9. Design Research Cross-Check

> Independent design opinion from the `codex` seat over the **accepted exploration
> doc** (never over this spec). Model: `gpt-5.6-luna` · Status: **skipped (brainstorm `**Status**: exploration`, not `accepted` — §3b precondition not met; `codex` 0.156.1 is installed, so flipping the brainstorm status to `accepted` and re-running `/sdd-spec qsurl-parser` enables this pass)**
> · Transcript: none

| # | Suggestion (kind) | Disposition | Reason | Landed in |
|---|---|---|---|---|
| — | — | — | — | — |

Summary: **0** confirmed · **0** rejected · **0** escalated.

---

## Worktree Strategy

- **Isolation**: ONE feature worktree for FEAT-152
  (`.claude/worktrees/feat-FEAT-152-qsurl-parser`, created from `origin/dev` by
  `python -m scripts.sdd.ensure_worktree --slug qsurl-parser --feature-id FEAT-152`);
  the `sdd-coder` engine gives each task its own sub-worktree inside it.
- **Module dependency graph** (edge = "needs merged first", with evidence):
  - M3 → M2 (`_fallback.py` raises `QSUrlError` and orders `requires` by `capabilities.ALL`).
  - M4 → M3 (`gbnf.py` reads `grammar.lark`).
  - M5 → M2 (`providers/*.py` import `querysource.qsurl.capabilities` constants); M5 → M9 (`pgProvider` may declare `text_match` only once the `ILIKE` dict operator exists).
  - M6 → M2 (`translate.py` imports `QSUrlError`, `capabilities`, `ResidualPlan`).
  - M7 → M2 (`residual.py` imports `ResidualPlan`, `QSUrlError`).
  - M8 → M2, M7 (`qs.py` imports `ResidualPlan` and `residual.apply`).
  - M10 → M2, M5, M6, M8 (handler calls `parse`, reads `capabilities`, calls `split`, builds `QS(residual=...)`).
  - M11 → M1, M2 (Makefile/CI need the manifest and the package directory).
  - M12 → M10.
  - No edges among {M1, M2, M9}: they start concurrently. M3/M6/M7 run concurrently after M2; M4 after M3; M5 after M2+M9; M8 after M7; M10 last before M11/M12.
- **Shared files** (tasks serialised): none — every file in §6 Edit Sites belongs to exactly one module. `querysource/qsurl/__init__.py` is written once by M2 with lazy imports of M3/M4 symbols.
- **Exclusive resources** (`parallel: false`): M1 and M11 (maturin builds into the shared `.venv`, `target/`), M9 (`make build-rust` + `make build-inplace` rebuild both extensions in place), M11 (`uv lock` rewrites `uv.lock`).
- **Cross-feature dependencies**: none. No per-spec index in `sdd/tasks/index/` has pending tasks touching `queries/qs.py`, `providers/abstract.py`, `services.py`, `Makefile` or `release.yml` (checked 2026-09-24).

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-09-24 | Jesus Lara | Initial draft from `qsurl-parser.brainstorm.md` (Option A); FEAT-152 reserved |
