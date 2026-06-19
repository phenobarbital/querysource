---
type: hotfix
base_branch: main
---

# Brainstorm: Malforming Query-Slug Issue — SQL Injection & Datasource Credential Exposure

**Date**: 2026-06-19
**Author**: Jesús / Claude brainstorm
**Status**: exploration
**Recommended Option**: A

---

## Problem Statement

Two related, actively-exploitable security vulnerabilities were confirmed on the
staging deployment (`api.staging.trocdigital.io`) and almost certainly exist in
production with the same code:

### V1 — SQL Injection via named-query slug conditions

The `troc_client_tenant` named query (a **raw** slug) interpolates request-supplied
query-string parameters (e.g. `client_slug`) **directly into raw SQL** using Python
string substitution (`str.format_map` over a `SafeDict`), with **no escaping, no
quoting, and no parameterization**. An attacker can:

- Break out of the intended value context with an unterminated quote / boolean
  expression (error-based probing).
- Use a `UNION SELECT` payload aligned to the output columns to exfiltrate arbitrary
  data — confirmed leaking `version()`, `current_database()`, `current_user`,
  `pg_database` (DB names), `information_schema.tables` (table names),
  `pg_user`/`pg_roles` (role names + `usesuper`), and `auth_user` rows
  (emails, **password hashes**, `is_superuser`).

Reproduced via:
```
POST /api/v2/services/queries/troc_client_tenant
  ?client_slug=" IS NULL UNION SELECT version(),null,null,null,null,null--=x
Origin: https://navigator.staging.trocdigital.io
```
(See `docs/img1.png`–`docs/img4.png`.) The injected payload surfaces in the
`client_slug` field of the JSON response.

### V2 — Datasource credentials returned in plaintext

`GET /api/v1/datasource` (and `/api/v1/datasources`) returns the full datasource
records — **including the `credentials` and `dsn` fields** — without redaction on
the *list* code path. `docs/img5.png` shows hosts/ports/drivers being enumerated;
the same response carries connection secrets in cleartext. Only the *single-source*
path performs partial masking, and even that only masks `credentials['password']`.

### Who is affected
Any deployment exposing the QuerySource query API and datasource API. Impact is
high: read access to authentication material (password hashes, superuser flags)
and infrastructure credentials.

### Why now
Confirmed exploitable on a live (staging) origin with a working PoC. This is a
**hotfix** landing on `main`, auto-propagated to `staging`/`dev` via
`.github/workflows/sync-down.yml`.

---

## Constraints & Requirements

- **Flow**: `type: hotfix`, `base_branch: main` (live, exploitable vuln).
- **Scope**: BOTH vulnerabilities in one coordinated security fix.
- **Remediation posture**: **defense-in-depth** (input validation + hardened
  substitution layer + documented DB-role least-privilege follow-up).
- **Backward compatibility (V1)**: Some legitimate raw slugs rely on `{placeholder}`
  substitution for *dynamic SQL fragments* (tenant/table/column names, `IN`-lists),
  not just literal values. The chosen strategy is **validate + typed escaping**:
  keep substitution working but force every interpolated value through strict
  validation (charset/length/type allowlist) and context-aware quoting/escaping;
  reject anything that looks like injected SQL.
- **Credential exposure (V2)**: **full redaction + PBAC gate** — mask all secrets
  (`credentials`, secret-bearing `dsn`) on *every* datasource GET (list + single),
  AND require a PBAC `datasource:read`/`datasource:list` check that **fails closed**
  (the current list path fails *open*).
- **DB metadata blocking**: out of code scope for the hotfix — **documented as an
  ops follow-up** (REVOKE on `pg_catalog`/`information_schema`, least-privilege app
  role + migration script).
- Must not regress legitimate slugs already in production (`navigator_*`, etc.).
- Async-first; no blocking I/O; follow existing Cython/Rust parser conventions.

---

## Options Explored

### Option A: Rust-based validating substitution (eliminate lazy Python f-strings)

Route **all** untrusted-value interpolation for raw slugs and parser WHERE-building
through the existing Rust extension (`querysource.qs_parsers._qs_parsers`), adding a
new **validating** substitution primitive that escapes/quotes every value by type
before it touches the SQL string. This is the "validate + typed escaping" decision,
implemented in Rust to remove the unsafe Python `str.format_map(SafeDict(...))` path
entirely.

**Why not just change the existing `safe_format_map`?** Because it is a *structural
template-assembly* helper, not a value-substitution helper. It has ~10 internal
callers (`rust/src/sql_parser.rs`, `pgsql_parser.rs`, `filter_common.rs`,
`bigquery_parser.rs`) that feed it **already-built, already-escaped SQL fragments** —
`{fields}` (a quoted field list), `{filter}`/`{where_cond}`/`{and_cond}` (a fully-
formed WHERE clause whose leaf values were already escaped by `filter_conditions`),
`{limit}` (`LIMIT 10`), `{tablename}` (`schema.table`). If `safe_format_map` escaped
its inputs, it would **double-escape and corrupt** those fragments and break every
PG/MSSQL/BigQuery query in the system. The two operations have opposite contracts:
`safe_format_map` assembles *trusted* fragments and must NOT escape; the fix
sanitizes *untrusted leaf values* and must escape. They stay separate functions so a
caller can never confuse "trusted assembly" with "untrusted input."

Escaping therefore happens at a new, distinct layer at the boundary where untrusted
request conditions first enter. There are two viable sub-shapes:

**Sub-shape A1 — sanitize-then-reuse (reuses `safe_format_map` unchanged):**
- Add a Rust `sanitize_conditions(conditions, cond_definition) -> dict` that runs
  every value (and key) through `validators::is_valid` / `escape_string` /
  `quote_string` and **rejects** (raises → 400) anything failing validation.
- `raw_query()` calls `sanitize_conditions(...)` first, then feeds the sanitized
  dict to the existing `safe_format_map(template, sanitized)` — no change to
  `safe_format_map` or its 10 callers.
- **Limitation:** the sanitizer can't see the text around each `{placeholder}`, so
  it can only produce correct output if templates use a *standardized, unquoted*
  placeholder convention (`WHERE x = {slug}`, never `WHERE x = '{slug}'`). It cannot
  safely cover free-form legacy templates that wrap placeholders in quotes.

**Sub-shape A2 — context-aware validating substitution (new substitution pass):**
- Add a Rust `safe_format_map_validated(template, conditions, cond_definition)` that
  walks the template, and for each `{key}` placeholder inspects the surrounding
  characters to detect its context (bare, inside `'...'` literal, inside `"..."`
  identifier), validates the value for that context, and emits a correctly
  quoted/escaped token — or rejects. `is_valid` already carries a `noquote` flag for
  exactly this.
- **Advantage:** safe for **free-form, DB-stored templates** regardless of whether
  the author quoted the placeholder — which is the real production situation (see
  resolved open question below).

**Resolution / decision:** adopt **A2 as the primary mechanism** because raw-slug
templates are DB-stored, free-form, and author-written (none live in the repo, and
quoting is not uniform — keys flow into `"..."` identifier context, values into
`'...'` literal context). A1 alone cannot protect those. Additionally, **standardize
new raw-slug templates to a typed, unquoted placeholder convention** so A1-style
pre-sanitization is possible going forward and A2 has unambiguous context; the
sanitizer + the context pass then reinforce each other (defense-in-depth).

Wiring (both sub-shapes share this):
- Replace `sqlProvider.raw_query()` / `defaultProvider.raw_query()` Python
  `format_map(SafeDict(...))` with the Rust call above.
- Ensure the parser `filter_conditions` WHERE-builder always takes the `HAS_RUST`
  Rust path (`_rs.filter_conditions`) and that the Rust path escapes **every**
  interpolation site — not only string values but also the raw **key**, **operator**,
  and **dict/list/BETWEEN value** branches (`f"{key} {op} {v}"`, `f"{key} = {value}"`,
  `f"({key} {value})"`) that the Cython fallback leaves unescaped. The PoC breaks out
  via the condition *key* into a `"..."` identifier, so key sanitization is required.
- Plus V2: full credential redaction helper + PBAC `datasource:read` gate
  (fail-closed) on `DatasourceView.get()`.

✅ **Pros:**
- Removes the unsafe lazy-substitution path (`SafeDict.format_map`) from the hot
  path — the root cause — rather than patching symptoms.
- Reuses an **already-built, already-shipped** Rust crate (`_qs_parsers`) with
  `escape_string`, `quote_string`, `is_valid`, `safe_format_map` exposed today.
- Centralizes escaping in one audited, type-aware, fuzz-testable place (Rust),
  matching the project's existing direction (`rust/src/*_parser.rs`, `qs-parser/`).
- Fast: substitution/escaping stays in native code; no per-value Python overhead.
- Preserves backward compatibility — legitimate slugs keep working, only
  injection-shaped values are rejected.

❌ **Cons:**
- Requires Rust/PyO3 work + `maturin` rebuild of the wheel and CI artifact.
- New Rust function must be written carefully (escaping correctness is security-
  critical) and fuzz-tested.
- The Cython `.pyx` parsers must be recompiled if their Python fallback paths change.

📊 **Effort:** Medium

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `pyo3` (in `rust/Cargo.toml`) | Rust↔Python FFI | already a dependency of the `_qs_parsers` module |
| `maturin` | Build the Rust extension wheel | already used to build `_qs_parsers` |
| `cython` | Recompile `.pyx` parsers if fallback paths change | existing toolchain |
| `proptest`/`cargo test` | Fuzz/property-test the escaping function | recommended for a security fix |

🔗 **Existing Code to Reuse:**
- `rust/src/validators.rs` — `escape_string` (138), `quote_string` (164),
  `is_valid` (257), `is_pgconstant`/`is_pg_function`/`to_string`.
- `rust/src/safe_dict.rs` — `safe_format_map` left UNCHANGED (10 internal callers
  depend on no-escape behavior); add a SIBLING `safe_format_map_validated` (A2) or a
  `sanitize_conditions` step (A1) alongside it.
- `rust/src/sql_parser.rs`, `rust/src/pgsql_parser.rs` — existing `filter_conditions`
  (must escape key/op/value at every interpolation site, not only string values).
- `rust/src/lib.rs` — `#[pymodule]` wiring (add the new function here).
- `querysource/qs_parsers/__init__.py` — Python-facing re-exports + `HAS_RUST`.
- `querysource/providers/sql.py` / `default.py` — `raw_query()` substitution sites.

---

### Option B: Python/Cython-layer validation + typed escaping (no Rust changes)

Harden the unsafe paths entirely in Python/Cython: rewrite `raw_query()` and the
parser WHERE-builder so every substituted value is validated and passed through
`Entity.quoteString`/`escapeString` (the existing `datamodel` helpers already used
in `pgsql.pyx`/`sql.pyx`), with a strict allowlist for structural placeholders.

✅ **Pros:**
- No Rust toolchain / wheel rebuild — fastest to ship as a hotfix.
- Uses escaping helpers already present in the Cython parsers.
- Lower build/CI risk; pure Python paths are easy to unit-test.

❌ **Cons:**
- Leaves the unsafe `str.format_map(SafeDict)` *pattern* in the codebase; future
  raw-slug code could reintroduce the bug.
- Escaping logic duplicated across providers/parsers (`sql.py`, `default.py`,
  `mysql.py`, `sqlserver.py`, `cassandra.py`, `*.pyx`) — DRY risk.
- Cython `.pyx` edits still require recompilation; not as "pure Python" as it looks.
- Slower per-value than native Rust (minor).

📊 **Effort:** Medium (broad surface, many call sites)

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `python-datamodel` (`Entity`) | `quoteString`/`escapeString` | already imported in parsers |
| `cython` | Recompile edited `.pyx` parsers | existing toolchain |

🔗 **Existing Code to Reuse:**
- `querysource/parsers/abstract.pyx:113-224` — `filter_conditions` WHERE-builder.
- `querysource/parsers/pgsql.pyx`, `sql.pyx` — existing `Entity.quoteString` usage.
- `querysource/providers/sql.py:162`, `default.py:67` — `raw_query()`.

---

### Option C: Bound-parameter conversion + structural allowlist

Stop string-substituting values altogether for the common case: rewrite raw-slug
templates so `{placeholder}` literals become real driver bind parameters (`$1`/`%s`),
passed separately to `conn.query(sql, params)`. Only an explicit per-slug allowlist
of *structural* placeholders (table/column/tenant) may be string-substituted, and
only from an enum of known-safe values.

✅ **Pros:**
- The gold standard against SQLi — values never become SQL text.
- Eliminates whole classes of escaping bugs.

❌ **Cons:**
- Large migration: every raw slug template + the provider execution layer
  (`conn.query`) must learn to carry parameter lists; mixed literal/structural
  placeholders make automatic conversion hard.
- High regression risk across all in-production slugs — not appropriate for a fast
  hotfix; better as a follow-up hardening epic.
- Some providers (REST/ES/Mongo) don't have positional bind params — inconsistent.

📊 **Effort:** High

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `asyncpg` (via `asyncdb`) | Native bound parameters for PostgreSQL | already the driver |

🔗 **Existing Code to Reuse:**
- `querysource/providers/sql.py` `query()` — execution path to extend with params.
- `querysource/models.py` — slug `query_raw`/`is_raw` definition.

---

### Option D (unconventional): SQL-AST validation guard

Parse the **rendered** SQL with a SQL grammar (e.g. `sqlglot`/`sqlparse`) before
execution and reject statements that contain multiple top-level statements,
`UNION`, comments, or references to `pg_catalog`/`information_schema`/`pg_*` when the
slug's expected shape doesn't include them. Acts as a backstop independent of how
substitution is done.

✅ **Pros:**
- Defense-in-depth backstop that catches injection regardless of the substitution
  bug; complements A/B.
- Can also satisfy the "app-layer metadata blocklist" idea without DB changes.

❌ **Cons:**
- Not sufficient alone (false negatives; legitimate slugs may legitimately use
  `UNION`); must layer on top of A or B.
- Adds a parser dependency + per-request parse latency.

📊 **Effort:** Medium

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `sqlglot` | Parse/validate rendered SQL | optional backstop layer |

🔗 **Existing Code to Reuse:**
- `rust/src/sql_parser.rs` — could host a Rust-side statement-shape check instead.

---

## Recommendation

**Option A** (Rust-based validating substitution) is recommended, with the
**V2 credential fix bundled in**, and **Option D's metadata blocklist captured as a
follow-up** within the ops hardening note.

Reasoning:
- It directly answers the user's question: yes, we can migrate the escape/substitution
  out of fragile Python f-strings/`format_map` into the existing robust Rust layer
  (`_qs_parsers`), the same pattern already used by `qs-parser`/`rust/src/*_parser.rs`.
- It fixes the **root cause** (unsafe lazy substitution) rather than masking symptoms,
  while honoring the "validate + typed escaping" compatibility decision — legitimate
  slugs keep working; only injection-shaped values are rejected.
- It reuses code that is **already built and shipped** (`escape_string`,
  `quote_string`, `is_valid`, `safe_format_map` are all live in the `.so`), so the
  net-new Rust surface is small and security-focused.
- Trade-off accepted: a `maturin` rebuild + CI wheel step and careful, fuzz-tested
  Rust escaping. That cost buys a single audited choke point for all SQL value
  interpolation — worth it for a security hotfix.

Option B is the fallback if the Rust build cannot be turned around fast enough for
the hotfix window; the Python `Entity.quoteString` path can land first and be
superseded by the Rust path. Option C is deferred as a larger hardening epic.

---

## Feature Description

### User-Facing Behavior
- Legitimate queries and slugs behave exactly as before. Valid `client_slug` values
  (and other conditions) continue to filter results normally.
- Malicious or malformed condition values (unterminated quotes, `UNION`, comment
  sequences, control characters, oversized payloads) are **rejected with HTTP 400**
  and a generic error message — no DB error text, no SQL fragment, no leaked schema.
- `GET /api/v1/datasource(s)` no longer returns `credentials` or secret-bearing
  `dsn` content. Secrets are masked everywhere; an authorized reveal requires the
  PBAC `datasource:read` permission. Callers without permission get a filtered/empty
  view or 403, and the check **fails closed** if the guardian is unavailable.

### Internal Behavior
- **V1 substitution**: `sqlProvider.raw_query()` / `defaultProvider.raw_query()`
  stop calling Python `str.format_map(defaultdict(str, SafeDict(**conditions)))`.
  Instead they call a new Rust `safe_format_map_validated(template, conditions,
  cond_definition)` that, per `{key}`:
  - looks up the expected type/role (literal vs identifier) from the slug's
    `cond_definition`/conditions metadata,
  - runs the value through `validators::is_valid` + `escape_string`/`quote_string`,
  - rejects (raises) on validation failure; substitutes a safely-quoted/escaped
    token on success.
- **V1 WHERE-builder**: the parser's `filter_conditions` always uses the `HAS_RUST`
  Rust path; the unescaped Python fallthrough (`f"{key} = {value}"` at
  `abstract.pyx:191`) is removed/escaped so the non-Rust fallback is also safe.
- **V2 redaction**: a single redaction helper masks `credentials` (all secret keys,
  not just `password`) and strips/masks secrets inside `dsn`, applied to BOTH the
  list and single-source branches of `DatasourceView.get()`. A PBAC
  `datasource:read` gate wraps the read, replacing the current fail-open list filter
  with fail-closed behavior.

### Edge Cases & Error Handling
- **Structural placeholders** (table/column/tenant names) that legitimately need
  identifier substitution: accepted only when matching a strict identifier regex
  (`^[A-Za-z_][A-Za-z0-9_.]*$`) and/or a per-slug allowlist; otherwise rejected.
- **`IN`-list values**: validated element-by-element and quoted/escaped individually.
- **Unmatched placeholders**: preserved as today (`SafeDict` semantics) so partial
  templates don't break — but never filled from untrusted input without validation.
- **Guardian/PBAC outage** (V2): fail **closed** (deny) for datasource reads — the
  opposite of the current list path which logs and returns unfiltered.
- **Backward-compat regressions**: a slug-replay test harness should run known
  production slugs through the new path to confirm identical rendered SQL for valid
  inputs.
- **Error messages**: never echo DB driver errors or injected input back to the
  client; log details server-side only.

---

## Capabilities

### New Capabilities
- `secure-query-substitution`: Rust-backed, validating, type-aware substitution of
  request conditions into raw-slug SQL, replacing the unsafe `format_map(SafeDict)`
  path. Includes the safe WHERE-builder fallback.
- `datasource-secret-redaction`: full masking of datasource credentials/dsn on all
  GET responses plus a fail-closed PBAC `datasource:read` gate.

### Modified Capabilities
- (none formally specced yet) — touches existing `providers`, `parsers`, and the
  `datasources` API handler.

---

## Impact & Integration

| Affected Component | Impact Type | Notes |
|---|---|---|
| `rust/src/safe_dict.rs` | extends | add SIBLING `safe_format_map_validated` (A2) / `sanitize_conditions` (A1); existing `safe_format_map` UNCHANGED |
| `rust/src/validators.rs` | depends on | reuse `escape_string`/`quote_string`/`is_valid` |
| `rust/src/lib.rs` | modifies | register new `#[pyfunction]` in the `#[pymodule]` |
| `querysource/qs_parsers/__init__.py` | modifies | re-export new function; `HAS_RUST` |
| `querysource/providers/sql.py` | modifies | `raw_query()` → Rust validating call |
| `querysource/providers/default.py` | modifies | `raw_query()` → Rust validating call |
| `querysource/providers/mysql.py`, `sqlserver.py`, `cassandra.py`, `documentdb.py` | modifies | same `raw_query`/`get_raw_query` pattern |
| `querysource/parsers/sql.pyx`, `pgsql.pyx` (+ `sqlserver.pyx`, `cql.pyx`, `bigquery.pyx`, `sosql.pyx`) | modifies | always take Rust filter path; escape key/op/dict/BETWEEN/value sites in the Cython fallback |
| `rust/src/sql_parser.rs`, `pgsql_parser.rs` | modifies | ensure `filter_conditions` escapes every interpolation site (key/op/value) |
| `querysource/datasources/handlers/datasource.py` | modifies | redaction helper + fail-closed PBAC on `get()` |
| CI / build | depends on | `maturin` rebuild of `_qs_parsers` wheel; recompile `.pyx` |
| Ops / DB (follow-up) | depends on | least-privilege role + REVOKE on `pg_catalog`/`information_schema` |

**Breaking changes**: Malformed/injection-shaped condition values now 400 instead of
executing. `GET /api/v1/datasource(s)` no longer returns secrets — consumers that
relied on reading credentials from this endpoint must use the gated reveal.

---

## Code Context

### User-Provided Code
The user provided the exploit PoCs as reference images (no source snippets):
```
# Source: user-provided (docs/img1.png – docs/img4.png) — SQLi PoCs
POST /api/v2/services/queries/troc_client_tenant
  ?client_slug=" IS NULL UNION SELECT version(),null,null,null,null,null--=x
Origin: https://navigator.staging.trocdigital.io
# Response leaks version()/current_database()/pg_database/information_schema/pg_user
# into the client_slug field of the JSON body.

# Source: user-provided (docs/img5.png) — credential exposure
GET https://api.trocdigital.io/api/v1/datasource
# Returns datasource records incl. host/port/driver (and credentials/dsn) in cleartext.
```
User suggestion: migrate the escape/substitution from Python f-strings/`format_map`
to the existing Rust string-manipulation layer (as in `qs-parser`).

### Verified Codebase References

#### Classes & Signatures
```python
# From querysource/providers/sql.py
class sqlProvider(...):
    # line 130: when is_raw, raw substitution is applied to the slug SQL
    #   self._query = self.raw_query(self._query)
    def raw_query(self, query: str):                       # line 162
        sql = query
        conditions = {**self.replacement}                  # line 164
        if self._conditions:                               # request-supplied conditions
            conditions = {**conditions, **self._conditions} # line 166
        return sql.format_map(                             # line 167  <-- UNSAFE
            defaultdict(str, SafeDict(**conditions))
        )
    def get_raw_query(self, query: str):                   # line 171 (same pattern)

# From querysource/providers/default.py
class defaultProvider(...):
    def raw_query(self, query: str):                       # line 67  <-- UNSAFE (same)
        ...
        return sql.format_map(defaultdict(str, SafeDict(**conditions)))  # line 72

# From querysource/providers/abstract.py
class BaseProvider(...):
    replacement: dict = { ... }                            # line 28 (class-level defaults)
    def __init__(self, ..., conditions: dict = None, request=None, **kwargs):  # line 38
        self._conditions: dict = conditions                # line 53
        ...
        self._conditions = copy.deepcopy(conditions)       # line 82 (request-derived)

# From querysource/parsers/sql.pyx  (Cython base SQL parser; pgProvider uses pgsql.pyx)
class SQLParser(AbstractParser):
    async def filter_conditions(self, sql):                # sql.pyx:113 / pgsql.pyx:36
        if HAS_RUST and self.filter:                       # sql.pyx:118 — Rust path (RUNS IN PROD)
            return _rs.filter_conditions(sql, dict(self.filter), dict(self.cond_definition))
        # Cython fallback builds WHERE via f-strings. UNSAFE interpolation is NOT
        # limited to string values — keys, operators and dict/list/BETWEEN values
        # are interpolated raw:
        key = f'"{key}"'                                   # sql.pyx:127 (key → "..." identifier ctx)
        where_cond.append(f"{key} {op} {v}")               # sql.pyx:139  <-- raw op + value
        where_cond.append(f"({key} {value})")              # sql.pyx:162-168 (BETWEEN) <-- raw
        where_cond.append(f"{key}={Entity.quoteString(value)}")  # sql.pyx:188/192 (escaped path)
        where_cond.append(f"{key} = {value}")              # sql.pyx:192 (bool) <-- raw fallthrough
# pgsql.pyx mirrors this: key-quoting at :65/:74, raw op at :86/:208, raw BETWEEN/value paths.
# The PoC payload leads with `"` because it breaks out of the `f'"{key}"'` identifier context.

# From querysource/queries/qs.py
class QS(BaseQuery):
    async def build_provider(self):                        # line 135
        # slug path: objquery = await self.connection.get_slug(self._query, ...)  # line 167
        # conditions = {**objquery.conditions, **self._conditions}                 # line 201 (request merge)

# From querysource/datasources/handlers/datasource.py
class DatasourceView(BaseView):
    async def _pbac_filter(self, request, items, name_key, resource_type, action):  # line 47
        # FAILS OPEN on guardian error (lines 86-98) and when guardian is None (line 71)
    async def get(self) -> web.Response:                   # line 148
        fields = ["uid","driver","name","description","params","credentials","dsn","program_slug"]  # line 187
        # LIST path returns `result` (incl. credentials+dsn) UNREDACTED via json_response  # line 218
        # SINGLE path masks only password:  result.credentials['password'] = '(hidden)'   # line 242

# From querysource/models.py
class QueryModel(...):
    query_raw: str = Field(required=False)                 # line 71
    is_raw: bool = Field(required=False, default=False)    # line 72
```

```rust
// From rust/src/validators.rs
pub fn escape_string(value: &str) -> String { ... }                 // line 138
pub fn quote_string(value: &str, no_dblquoting: bool) -> String { } // line 164
pub fn to_string(value: &str) -> String { ... }                     // line 221
pub fn is_valid(key: &str, value: &str, type_hint: Option<&str>, noquote: bool) -> String { } // line 257

// From rust/src/safe_dict.rs
#[pyfunction]
pub fn safe_format_map(template: &str, replacements: &Bound<'_, PyDict>) -> String { }
// NOTE: plain substitution, NO escaping — extend with a validating variant.

// From rust/src/lib.rs  (#[pymodule], line 33)
m.add_function(wrap_pyfunction!(validators::escape_string, m)?)?;   // line 44
m.add_function(wrap_pyfunction!(validators::quote_string, m)?)?;    // line 45
m.add_function(wrap_pyfunction!(validators::is_valid, m)?)?;        // line 47
m.add_function(wrap_pyfunction!(safe_dict::safe_format_map, m)?)?;  // line 55
m.add_function(wrap_pyfunction!(sql_parser::filter_conditions, m)?)?; // line 58
```

#### Verified Imports
```python
# Confirmed to work (module is built and importable):
from querysource.qs_parsers import _qs_parsers as _rs   # querysource/parsers/cql.pyx:18 et al.
# Exposed names include: escape_string, quote_string, is_valid, to_string,
# safe_format_map, build_sql, filter_conditions, pgsql_filter_conditions, ...
# (verified via: python -c "import querysource.qs_parsers; dir(...)")
```

#### Key Attributes & Constants
- `QueryModel.is_raw` → `bool` (querysource/models.py:72) — selects the raw
  substitution path in providers.
- `QueryModel.query_raw` → `str` (querysource/models.py:71) — the raw SQL template
  with `{placeholder}` slots.
- `BaseProvider.replacement` → `dict` (querysource/providers/abstract.py:28).
- `BaseProvider._conditions` → `dict` (request-derived; abstract.py:53, 82).
- `_qs_parsers.HAS_RUST` flag gates the Rust path in the `.pyx` parsers.
- Slug routes: `POST/GET /api/v2/services/queries/{slug}` (querysource/services.py:148-150).
- Datasource routes: `add_view('/api/v1/datasource', DatasourceView)` (services.py:282)
  and `/api/v1/datasources` (services.py:277).

### Does NOT Exist (Anti-Hallucination)
- ~~`_qs_parsers.safe_format_map_validated`~~ / ~~`sanitize_conditions`~~ — do **not**
  exist yet; one must be added (A2 / A1). The shipped `safe_format_map` does **plain**
  substitution **without escaping** and has ~10 internal Rust callers that rely on
  that (it assembles already-built SQL fragments). It must NOT be changed to escape —
  doing so would double-escape `{filter}`/`{fields}`/`{limit}`/`{tablename}` and break
  every parser. Add a sibling function instead.
- ~~A uniform/quoted placeholder convention in raw-slug templates~~ — does NOT exist.
  Templates are DB-stored, free-form, author-written; placeholders appear in both
  `'...'` literal and `"..."` identifier contexts. This is why A2 (context-aware) is
  required and A1 alone is insufficient for legacy slugs.
- ~~`querysource/providers/db.py` / `pg.py` `raw_query`~~ — these do not define their
  own `raw_query`; `pgProvider` inherits from `sqlProvider` (sql.py).
- ~~A `datasource:read` PBAC action on the GET path~~ — currently only
  `datasource:list` / `driver:list` are used, and only on the list branch
  (datasource.py:209-216); the single-source branch has no PBAC check.
- ~~Any existing redaction of the `dsn` field~~ — `dsn` is returned verbatim; only
  `credentials['password']` is masked, and only on the single-source path.

---

## Resolved Decisions

- **[RESOLVED] Raw-slug placeholder-quoting convention & substitution sub-shape.**
  Raw-slug templates are DB-stored, free-form, and author-written — none exist in the
  repo, and placeholders appear in mixed contexts (`"..."` identifier for keys,
  `'...'` literal for values; the PoC breaks out via the *key* into identifier
  context). There is therefore no uniform/controllable quoting convention to rely on.
  **Decision:** adopt **sub-shape A2 (context-aware `safe_format_map_validated`)** as
  the primary mechanism so legacy free-form templates are protected; do **not** change
  the existing `safe_format_map` (its 10 callers assemble trusted fragments and would
  break). Additionally **standardize new raw-slug templates to typed, unquoted
  placeholders**, enabling A1-style pre-sanitization as a reinforcing second layer.
  Escaping must cover **keys, operators, and dict/list/BETWEEN values**, not only
  string values.

## Open Questions

- [ ] Is `cond_definition` populated for raw slugs (it drives per-placeholder type
      hints for `is_valid`)? If sparse, A2 must fall back to safe defaults
      (treat unknown placeholders as quoted literals; reject SQL-shaped tokens). —
      *Owner: Jesús*
- [ ] Does any production consumer legitimately read `credentials`/`dsn` from
      `GET /api/v1/datasource`? If so, design the gated reveal endpoint/permission
      before redacting. — *Owner: Jesús*
- [ ] CI: is `maturin` wheel rebuild + `.pyx` recompile already part of the
      hotfix release pipeline, or does it need to be added for this branch? — *Owner: Ops*
- [ ] Ops follow-up scope: dedicated least-privilege DB role + `REVOKE` on
      `pg_catalog`/`information_schema` — separate ticket + migration script. — *Owner: Ops*
- [ ] Should Option D (SQL-AST/statement-shape backstop) ship in this hotfix as a
      cheap extra layer, or be deferred? — *Owner: Jesús*

---

## Parallelism Assessment

- **Internal parallelism**: Moderate. The fix splits cleanly into two largely
  independent tracks:
  1. **V1 (SQLi)** — Rust `safe_format_map_validated` + provider/parser wiring +
     wheel/`.pyx` rebuild.
  2. **V2 (credential redaction + PBAC)** — pure-Python change in
     `datasources/handlers/datasource.py`.
  These touch disjoint files and can proceed in separate worktrees.
- **Cross-feature independence**: Low conflict risk. V1 touches `rust/`,
  `querysource/providers/*`, `querysource/parsers/*.pyx`, `qs_parsers/__init__.py`;
  V2 touches only the datasource handler. Watch for overlap with any in-flight
  parser/Rust work.
- **Recommended isolation**: **mixed** — V2 can land first as a fast, low-risk
  Python-only commit; V1 proceeds in its own worktree with the Rust build. Both
  merge into the single hotfix branch off `main`.
- **Rationale**: V2 is small, self-contained, and stops the credential bleed
  immediately; V1 is the larger Rust-backed change. Decoupling lets the cheaper fix
  ship without waiting on the wheel rebuild, while keeping one coordinated hotfix PR.
