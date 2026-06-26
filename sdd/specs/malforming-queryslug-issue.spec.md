---
type: feature
base_branch: dev
---

# Feature Specification: Malforming Query-Slug Issue — SQL Injection & Datasource Credential Exposure

**Feature ID**: FEAT-103
**Date**: 2026-06-19
**Author**: Jesús / Claude (SDD)
**Status**: approved
**Target version**: 4.4.4

> Input: `sdd/proposals/malforming-queryslug-issue.brainstorm.md` (Recommended Option A,
> sub-shape **A2** — context-aware Rust validating substitution).

---

## 1. Motivation & Business Requirements

### Problem Statement

Two related, actively-exploitable security vulnerabilities were confirmed on the
staging deployment (`api.staging.trocdigital.io`) and exist in the same code paths in
production:

**V1 — SQL Injection via named-query slug conditions.** Raw named-query slugs (e.g.
`troc_client_tenant`) interpolate request-supplied query-string parameters directly
into raw SQL using Python `str.format_map(defaultdict(str, SafeDict(**conditions)))`
(`querysource/providers/sql.py:167`, `default.py:72`), with **no escaping, no
quoting, and no parameterization**. The non-raw path's WHERE-builder
(`filter_conditions`) likewise interpolates the condition **key**, **operator**, and
**dict/list/BETWEEN values** raw (`sql.pyx:127/139/192`, `pgsql.pyx:65/74/86/208/212`).
A `UNION SELECT` payload aligned to the output columns exfiltrated `version()`,
`current_database()`, `current_user`, `pg_database` (DB names),
`information_schema.tables` (table names), `pg_user`/`pg_roles` (roles + `usesuper`),
and `auth_user` rows (emails, **password hashes**, `is_superuser`). The PoC payload
leads with `"` because it breaks out of the `f'"{key}"'` identifier context.

PoC (see `docs/img1.png`–`docs/img4.png`):
```
POST /api/v2/services/queries/troc_client_tenant
  ?client_slug=" IS NULL UNION SELECT version(),null,null,null,null,null--=x
Origin: https://navigator.staging.trocdigital.io
# version() is returned inside the client_slug field of the JSON response.
```

**V2 — Datasource credentials returned in plaintext.** `GET /api/v1/datasource(s)`
returns full datasource records — including `credentials` and `dsn` — without
redaction on the *list* code path (`datasource.py:187` requests those fields,
`:218` returns them unmasked). Only the *single-source* path masks
`credentials['password']` (`:242`). The list PBAC filter (`_pbac_filter`,
`datasource.py:47`) **fails open** on guardian error (`:86-98`) and when the guardian
is absent (`:71`). See `docs/img5.png`.

### Goals
- Eliminate the unsafe lazy-substitution path so request-supplied conditions can
  never be interpolated into SQL without context-aware validation/escaping (V1).
- Reject injection-shaped condition values/keys with HTTP 400 and a generic message
  (no DB error text, no SQL fragment, no schema leak).
- Preserve backward compatibility for legitimate slugs (valid values still filter).
- Stop returning `credentials`/secret-bearing `dsn` from datasource GET responses,
  and gate datasource reads behind a **fail-closed** PBAC check (V2).
- Ship as a single coordinated hotfix off `main`, auto-propagated to `staging`/`dev`.

### Non-Goals (explicitly out of scope)
- **DB-role least-privilege / REVOKE on `pg_catalog`/`information_schema`** — captured
  as an ops follow-up (needs grants/migrations, not app code). See §8.
- **Bound-parameter conversion of all slug templates** — rejected in brainstorm
  (Option C: high regression risk, not hotfix-appropriate; deferred hardening epic).
- **Changing the existing `safe_format_map`** — rejected: its ~10 internal callers
  assemble already-built SQL fragments and would break if it escaped. See
  `proposals/malforming-queryslug-issue.brainstorm.md` Option A.
- **SQL-AST/statement-shape backstop** (Option D) — optional extra layer, deferred
  unless cheap to add; tracked in §8.

---

## 2. Architectural Design

### Overview

Adopt **Option A, sub-shape A2 (context-aware validating substitution)** implemented
in the existing Rust extension (`querysource.qs_parsers._qs_parsers`), plus the V2
credential fix in the datasource handler.

**V1 — secure substitution.** Add a *new* Rust function
`safe_format_map_validated(template, conditions, cond_definition)` that walks the SQL
template and, for each `{key}` placeholder, detects its surrounding context (bare /
inside `'...'` literal / inside `"..."` identifier), validates the value for that
context via the existing `validators::is_valid`/`escape_string`/`quote_string`, and
emits a correctly quoted/escaped token — or **rejects** (raises a Python exception →
400). The existing `safe_format_map` is left **unchanged** (its callers feed trusted,
pre-built fragments). The provider `raw_query()` methods stop calling Python
`format_map(SafeDict(...))` and call the new Rust function. The WHERE-builder
`filter_conditions` always takes the `HAS_RUST` Rust path, and the Rust
`filter_conditions` (+ the Cython fallbacks) must escape **every** interpolation
site — key, operator, and value — not only string values.

A2 is primary because raw-slug templates are **DB-stored, free-form, author-written**
(none in the repo; placeholders appear in both identifier and literal contexts), so a
context-blind pre-sanitize (sub-shape A1) cannot protect legacy templates. New
raw-slug templates SHOULD additionally standardize on typed, unquoted placeholders so
A1-style pre-sanitization can reinforce A2 going forward.

**V2 — credential redaction + fail-closed PBAC.** Add a single redaction helper that
masks all secret keys in `credentials` and strips/masks secrets inside `dsn`, applied
to **both** the list and single-source branches of `DatasourceView.get()`. Replace
the fail-open list filter with a **fail-closed** `datasource:read` PBAC check modeled
on the existing `AbstractHandler._enforce_pbac()` (which already fails closed). Note
`DatasourceView` extends `navigator.views.BaseView`, not the QS `AbstractHandler`, so
the fail-closed check must be extracted into a shared helper or replicated locally.

### Component Diagram
```
                                 V1 — SQL injection
  HTTP request (slug + conditions)
        │
        ▼
  QS.build_provider() ──→ Connection.get_slug() ──→ objquery (query_raw, is_raw)
        │                                                   │
        ▼                                                   ▼
  conditions = {**objquery.conditions, **request_conditions}   (qs.py:201)
        │
        ├─ is_raw=True ─→ sqlProvider.raw_query(query_raw)
        │                     └─→ _rs.safe_format_map_validated(tpl, conds, cond_def)   ← NEW (Rust)
        │                           └─ validators::is_valid / escape_string / quote_string
        │                           └─ reject → ParserError → HTTP 400
        │
        └─ is_raw=False ─→ AbstractParser.build_query() ─→ filter_conditions(sql)
                                 └─→ _rs.filter_conditions(...)   ← HARDEN (escape key/op/value)

                                 V2 — credential exposure
  GET /api/v1/datasource(s) ─→ DatasourceView.get()
        ├─ _enforce_pbac-style check (datasource:read, FAIL-CLOSED)   ← NEW
        └─ _redact_datasource(record)  (mask credentials + dsn)       ← NEW, applied to list + single
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `querysource.qs_parsers._qs_parsers` (Rust) | extends | add `safe_format_map_validated`; reuse `is_valid`/`escape_string`/`quote_string`; harden `filter_conditions` |
| `sqlProvider.raw_query()` / `get_raw_query()` (`providers/sql.py:162/171`) | modifies | replace `format_map(SafeDict)` with the Rust validating call |
| `defaultProvider.raw_query()` (`providers/default.py:67`) | modifies | same replacement |
| `mysqlProvider`, `sqlserverProvider`, `cassandraProvider`, `documentdbProvider` | modifies | same `raw_query`/`get_raw_query` pattern |
| `AbstractParser`/SQL parsers (`sql.pyx`, `pgsql.pyx`, `sqlserver.pyx`, `cql.pyx`, `bigquery.pyx`, `sosql.pyx`) | modifies | escape key/op/dict/BETWEEN/value sites in Cython fallback |
| Rust `sql_parser::filter_conditions`, `pgsql_parser::pgsql_filter_conditions` | modifies | escape every interpolation site |
| `DatasourceView.get()` (`datasources/handlers/datasource.py:148`) | modifies | redaction helper + fail-closed PBAC on list & single |
| `AbstractHandler._enforce_pbac()` (`handlers/abstract.py:259`) | reuses (pattern) | fail-closed single-resource check to model the gate on |
| `ResourceType.DATASOURCE` (`auth/_resource_types.py:43`) | uses | resource type for the gate |
| CI / build | depends on | `maturin` rebuild of `_qs_parsers` wheel; recompile `.pyx` |

### Data Models

No new persistent models. One internal helper return shape (not a Pydantic model;
plain dict mutation/copy):

```python
# Conceptual — redaction helper masks in place on a copy
# credentials: dict  → every secret key replaced with '(hidden)'
# dsn: str           → secrets in the DSN string masked
SECRET_KEYS = {"password", "pwd", "secret", "token", "api_key", "apikey", "key"}
```

### New Public Interfaces

```python
# Rust (exposed via querysource.qs_parsers._qs_parsers), Python signature:
def safe_format_map_validated(
    template: str,
    conditions: dict,            # request-derived; values/keys are untrusted
    cond_definition: dict,       # per-placeholder type hints (may be sparse)
) -> str: ...                    # raises on validation failure (→ ParserError → 400)

# Python helper in the datasource handler module:
def _redact_datasource(record) -> dict: ...   # masks credentials + dsn on a copy
```

---

## 3. Module Breakdown

### Module 1: Rust validating substitution
- **Path**: `rust/src/safe_dict.rs` (+ `rust/src/lib.rs` registration)
- **Responsibility**: `safe_format_map_validated(template, conditions, cond_definition)`
  — context-aware (bare/`'...'`/`"..."`) per-placeholder validation + escaping;
  reuse `validators::{is_valid, escape_string, quote_string}`. Reject on failure.
  Leave `safe_format_map` untouched.
- **Depends on**: `rust/src/validators.rs` (existing).

### Module 2: Rust WHERE-builder hardening
- **Path**: `rust/src/sql_parser.rs`, `rust/src/pgsql_parser.rs`
- **Responsibility**: ensure `filter_conditions`/`pgsql_filter_conditions` escape
  every interpolation site (key→identifier-quote, operator→allowlist, value→literal
  escape), not only string values.
- **Depends on**: Module 1's validators.

### Module 3: qs_parsers Python re-export
- **Path**: `querysource/qs_parsers/__init__.py`
- **Responsibility**: re-export `safe_format_map_validated`; keep `HAS_RUST` flag.
- **Depends on**: Modules 1–2 (built wheel).

### Module 4: Provider raw_query wiring
- **Path**: `querysource/providers/sql.py`, `default.py`, `mysql.py`, `sqlserver.py`,
  `cassandra.py`, `documentdb.py`
- **Responsibility**: replace `sql.format_map(defaultdict(str, SafeDict(**conditions)))`
  with `safe_format_map_validated(...)`; map rejection → `ParserError`/HTTP 400.
- **Depends on**: Module 3.

### Module 5: Cython parser fallback hardening
- **Path**: `querysource/parsers/sql.pyx`, `pgsql.pyx`, `sqlserver.pyx`, `cql.pyx`,
  `bigquery.pyx`, `sosql.pyx`
- **Responsibility**: escape key/op/dict/list/BETWEEN/value sites in the non-Rust
  fallback so behavior is safe even when `HAS_RUST` is false. Recompile.
- **Depends on**: Module 1 validators (or `Entity.quoteString`/`escapeString`).

### Module 6: Datasource redaction + fail-closed PBAC
- **Path**: `querysource/datasources/handlers/datasource.py`
- **Responsibility**: `_redact_datasource()` helper masking `credentials`+`dsn`;
  apply to list (`:218`) and single (`:235-249`) branches; replace fail-open
  `_pbac_filter` use with a fail-closed `datasource:read` gate modeled on
  `AbstractHandler._enforce_pbac`.
- **Depends on**: `ResourceType.DATASOURCE`; fail-closed check helper.

---

## 4. Test Specification

### Unit Tests
| Test | Module | Description |
|---|---|---|
| `test_validated_subst_rejects_quote_breakout` | M1 | `client_slug=" ...` (identifier breakout) is rejected, not substituted |
| `test_validated_subst_rejects_union_payload` | M1 | `' UNION SELECT version()--` rejected |
| `test_validated_subst_allows_valid_value` | M1 | benign value substitutes identically to legacy output (golden) |
| `test_validated_subst_context_literal_vs_ident` | M1 | value escaped correctly in `'{x}'` vs `"{x}"` vs bare `{x}` contexts |
| `test_safe_format_map_unchanged` | M1 | existing `safe_format_map` still does plain (no-escape) substitution for `{filter}`/`{fields}` |
| `test_filter_conditions_escapes_key` | M2/M5 | malicious condition **key** is identifier-quoted/escaped or rejected |
| `test_filter_conditions_escapes_operator_and_value` | M2/M5 | dict/list/BETWEEN value paths escaped |
| `test_redact_datasource_masks_credentials_and_dsn` | M6 | `credentials` secret keys + `dsn` secrets masked on a copy |
| `test_datasource_get_list_no_plaintext_secrets` | M6 | list response contains no plaintext credential/dsn |
| `test_datasource_pbac_fail_closed` | M6 | guardian error / no session → deny (not unfiltered) |

### Integration Tests
| Test | Description |
|---|---|
| `test_slug_injection_blocked_e2e` | Replay the PoC against a raw slug; expect 400 + no schema/data leak in body |
| `test_legitimate_slug_still_works` | Known-good slug + valid conditions returns expected rows (regression) |
| `test_datasource_endpoint_redacted_e2e` | `GET /api/v1/datasource(s)` returns records without secrets; gated by PBAC |

### Test Data / Fixtures
```python
# PoC payloads (verbatim attack strings) for regression
@pytest.fixture
def sqli_payloads():
    return [
        '" IS NULL UNION SELECT version(),null,null,null,null,null--',
        "' UNION SELECT string_agg(datname,',') FROM pg_database--",
        "' UNION SELECT usename||':'||passwd FROM pg_shadow--",
    ]

@pytest.fixture
def raw_slug_template():
    # representative free-form raw template (placeholder in literal context)
    return "SELECT client_slug, name FROM troc.clients WHERE client_slug = '{client_slug}'"
```

---

## 5. Acceptance Criteria

> Frontmatter: `type: hotfix`, `base_branch: main` (FEAT-145 — hotfix requires main).

This feature is complete when ALL of the following are true:

- [ ] The PoC payloads (error-based, UNION-based, `pg_database`/`pg_user`/
      `information_schema` enumeration) against `troc_client_tenant` (and any raw
      slug) return **HTTP 400** with a generic message — no DB error text, no SQL
      fragment, no schema/data in the body.
- [ ] Injection-shaped condition **keys** (identifier breakout via `"`) are rejected
      or safely identifier-quoted.
- [ ] Legitimate slugs with valid conditions return identical results to pre-fix
      (golden/regression test over representative production slugs passes).
- [ ] `safe_format_map` behavior is unchanged (no-escape) and all existing parser
      callers still produce correct SQL.
- [ ] The non-Rust Cython fallback (`HAS_RUST=False`) is also safe (key/op/value
      escaped) — verified by forcing the fallback in a test.
- [ ] `GET /api/v1/datasource` and `/api/v1/datasources` return **no** plaintext
      `credentials` or secret-bearing `dsn` on either list or single-source paths.
- [ ] Datasource reads are gated by a `datasource:read` PBAC check that **fails
      closed** (guardian error / no session → deny), unlike the current list path.
- [ ] `maturin` rebuild of `_qs_parsers` and `.pyx` recompile succeed in CI; the
      wheel ships with the hotfix.
- [ ] `pytest` unit + integration suites in §4 pass.
- [ ] No breaking change to legitimate public query behavior (only malformed input
      now 400s; datasource GET no longer returns secrets — documented).

---

## 6. Codebase Contract

> **CRITICAL — Anti-Hallucination Anchor.** All line numbers verified 2026-06-19 on
> branch `dev`. Re-verify before editing if the tree has moved.

### Verified Imports
```python
from querysource.qs_parsers import _qs_parsers as _rs   # querysource/parsers/sql.pyx:* (cql.pyx:18 etc.)
# _rs exposes: escape_string, quote_string, is_valid, to_string, safe_format_map,
#   build_sql, filter_conditions, pgsql_filter_conditions, ...
#   (verified: python -c "import querysource.qs_parsers; dir(querysource.qs_parsers)")
from querysource.auth import ResourceType                # querysource/auth/_resource_types.py
from ...utils.functions import anonymize                 # querysource/datasources/handlers/datasource.py:16 (verified by use)
```

### Existing Class Signatures
```python
# querysource/providers/sql.py
class sqlProvider(...):
    # line 130: when is_raw → self._query = self.raw_query(self._query)
    def raw_query(self, query: str):                       # line 162
        conditions = {**self.replacement}                  # line 164
        if self._conditions:
            conditions = {**conditions, **self._conditions} # line 166 (request-derived)
        return sql.format_map(defaultdict(str, SafeDict(**conditions)))  # line 167  <-- UNSAFE
    def get_raw_query(self, query: str): ...               # line 171 (same pattern)

# querysource/providers/default.py
class defaultProvider(...):
    def raw_query(self, query: str): ...                   # line 67  <-- UNSAFE format_map (line 72)

# querysource/providers/abstract.py
class BaseProvider(...):
    replacement: dict = { ... }                            # line 28
    def __init__(self, ..., conditions=None, request=None, **kwargs):  # line 38
        self._conditions: dict = conditions                # line 53
        self._conditions = copy.deepcopy(conditions)       # line 82 (request-derived)

# querysource/parsers/sql.pyx (base SQL parser; pg uses pgsql.pyx)
class SQLParser(AbstractParser):
    async def filter_conditions(self, sql):                # sql.pyx:113 / pgsql.pyx:36
        if HAS_RUST and self.filter:                       # sql.pyx:118 — Rust path RUNS IN PROD
            return _rs.filter_conditions(sql, dict(self.filter), dict(self.cond_definition))
        key = f'"{key}"'                                   # sql.pyx:127 (key → identifier ctx) <-- UNSAFE
        where_cond.append(f"{key} {op} {v}")               # sql.pyx:139 <-- raw op + value
        where_cond.append(f"({key} {value})")              # sql.pyx:162-168 (BETWEEN) <-- raw
        where_cond.append(f"{key}={Entity.quoteString(value)}")  # sql.pyx:188/192 (escaped)
        where_cond.append(f"{key} = {value}")              # sql.pyx:192 (bool) <-- raw
# pgsql.pyx mirrors: key-quote :65/:74, raw op :86/:208, raw value/BETWEEN :212

# querysource/queries/qs.py
class QS(BaseQuery):
    async def build_provider(self):                        # line 135
        objquery = await self.connection.get_slug(self._query, program=self._program)  # line 167
        conditions = {**objquery.conditions, **self._conditions}  # line 201 (request merge)

# querysource/datasources/handlers/datasource.py
class DatasourceView(BaseView):                            # line 43 (navigator.views.BaseView)
    async def _pbac_filter(self, request, items, name_key, resource_type, action):  # line 47
        # FAILS OPEN: guardian None → return items (line 71); guardian error → return items (86-98)
    async def get(self) -> web.Response:                   # line 148
        fields = [...,"credentials","dsn",...]             # line 187
        return self.json_response(response=result, ...)    # line 218 (list — UNREDACTED)
        result.credentials['password'] = '(hidden)'        # line 242 (single — only password)

# querysource/handlers/abstract.py — fail-closed single-resource gate to MODEL the V2 check on
class AbstractHandler(...):
    async def _enforce_pbac(self, request, resource_type, resource_name: str, action: str) -> None:  # line 259
        # guardian None → no-op (284); no session → HTTPNotFound (300-308); deny → HTTPNotFound (352+)

# querysource/datasources/models.py
class DataSource(Model):                                   # line 20
    params: dict        # line 34
    credentials: dict   # line 35
    dsn: str            # line 36

# querysource/models.py
class QueryModel(...):
    query_raw: str = Field(required=False)                 # line 71
    is_raw: bool = Field(required=False, default=False)    # line 72
```

```rust
// rust/src/validators.rs
pub fn escape_string(value: &str) -> String { }                 // line 138
pub fn quote_string(value: &str, no_dblquoting: bool) -> String { } // line 164
pub fn to_string(value: &str) -> String { }                     // line 221
pub fn is_valid(key: &str, value: &str, type_hint: Option<&str>, noquote: bool) -> String { } // line 257

// rust/src/safe_dict.rs — DO NOT change behavior; add a sibling fn
#[pyfunction]
pub fn safe_format_map(template: &str, replacements: &Bound<'_, PyDict>) -> String { } // plain, no-escape
pub fn safe_format_map_rust(template: &str, replacements: &HashMap<String,String>) -> String { } // line 35

// rust/src/lib.rs (#[pymodule], line 33) — register new fn alongside:
m.add_function(wrap_pyfunction!(validators::escape_string, m)?)?;   // line 44
m.add_function(wrap_pyfunction!(validators::quote_string, m)?)?;    // line 45
m.add_function(wrap_pyfunction!(validators::is_valid, m)?)?;        // line 47
m.add_function(wrap_pyfunction!(safe_dict::safe_format_map, m)?)?;  // line 55
m.add_function(wrap_pyfunction!(sql_parser::filter_conditions, m)?)?; // line 58

// safe_format_map_rust internal callers (must keep no-escape contract):
// sql_parser.rs:270/275/280/297/370/381/424/494, pgsql_parser.rs:404-430,
// filter_common.rs:216-242, bigquery_parser.rs:354/400
```

### Integration Points
| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `safe_format_map_validated` | `sqlProvider.raw_query()` | replaces `format_map` | `providers/sql.py:167` |
| `safe_format_map_validated` | `defaultProvider.raw_query()` | replaces `format_map` | `providers/default.py:72` |
| hardened `filter_conditions` | `SQLParser.filter_conditions` | `_rs.filter_conditions` | `parsers/sql.pyx:118-119` |
| `_redact_datasource` | `DatasourceView.get()` | mask before `json_response` | `datasource.py:218`, `:242` |
| fail-closed `datasource:read` gate | `DatasourceView.get()` | modeled on `_enforce_pbac` | `handlers/abstract.py:259` |
| `ResourceType.DATASOURCE` | the gate | resource type arg | `auth/_resource_types.py:43` |

### Does NOT Exist (Anti-Hallucination)
- ~~`_qs_parsers.safe_format_map_validated`~~ / ~~`_qs_parsers.sanitize_conditions`~~ —
  must be ADDED. The shipped `safe_format_map` does **plain, no-escape** substitution
  and must NOT be changed (10 internal callers assemble trusted fragments; escaping
  would double-escape `{filter}`/`{fields}`/`{limit}`/`{tablename}` and break parsers).
- ~~`querysource/providers/db.py` / `pg.py` `raw_query`~~ — not defined there;
  `pgProvider` inherits `raw_query` from `sqlProvider` (`providers/sql.py`).
- ~~`DatasourceView._enforce_pbac`~~ — `_enforce_pbac` exists on
  `AbstractHandler` (`handlers/abstract.py:259`), NOT on `BaseView`/`DatasourceView`.
  The fail-closed check must be extracted to a shared helper or replicated.
- ~~A `datasource:read` `ResourceType`/action constant~~ — actions are free strings
  (e.g. `"datasource:list"`); `datasource:read` is a new action string. Existing
  `ResourceType` members: `DATASOURCE`, `DRIVER`, `RAW_QUERY` (`_resource_types.py:43-45`).
- ~~A uniform/quoted placeholder convention in raw-slug templates~~ — does NOT exist;
  templates are DB-stored, free-form; placeholders appear in both `'...'` and `"..."`
  contexts. This is why A2 (context-aware) is required.
- ~~`scripts.sdd.sdd_meta`~~ — module not present in this repo; frontmatter parsed
  directly.

---

## 7. Implementation Notes & Constraints

### Patterns to Follow
- Cython: prefer `cimport`, `cdef`/`cpdef`, static typing (`.claude/rules/cython-development.md`).
- Rust/PyO3: `Bound<'py, T>` API, return `PyResult<T>`, map errors via
  `.map_err(|e| PyValueError::new_err(...))`; keep the FFI boundary thin
  (`.claude/rules/rust-development.md`). Build via `maturin`.
- Async-first; no blocking I/O; `self.logger`/`self._logger` not print.
- Never echo DB driver errors or injected input to the client; log details
  server-side only.

### Known Risks / Gotchas
- **Escaping correctness is security-critical** — fuzz/property-test the Rust
  substitution (`proptest`) against the PoC corpus and identifier/literal contexts.
- **`cond_definition` may be sparse** for raw slugs (see §8). A2 must fall back to a
  safe default: treat unknown placeholders as quoted literals and reject SQL-shaped
  tokens (keywords, comment markers, stacked statements).
- **Rust path is the prod path**: `_rs.filter_conditions` runs when `HAS_RUST` (true —
  the `.so` is built). Hardening only the Cython fallback would NOT fix prod; the Rust
  function is the primary fix surface for the non-raw path.
- **Backward-compat regression risk**: run representative production slugs through a
  golden test to confirm identical rendered SQL for valid inputs before merge.
- **V2 fail-closed flip**: the current list path fails open; switching to fail-closed
  could deny previously-allowed callers if policies are missing — verify policy
  coverage for `datasource:read` before rollout.
- **`maturin`/`.pyx` build in CI**: the hotfix must rebuild the wheel and recompile
  Cython; confirm the release pipeline does this for the `main` hotfix branch.

### External Dependencies
| Package | Version | Reason |
|---|---|---|
| `pyo3` | (existing in `rust/Cargo.toml`) | Rust↔Python FFI for `_qs_parsers` |
| `maturin` | `>=1.0` | build the Rust extension wheel |
| `cython` | (existing) | recompile `.pyx` parsers |
| `python-datamodel` (`Entity`) | (existing) | `quoteString`/`escapeString` in Cython fallback |
| `navigator-auth` | (existing) | PBAC `ResourceType`, evaluator (V2 gate) |
| `proptest` (Rust, dev) | (add) | fuzz the escaping/substitution function |

---

## 8. Open Questions

- [x] Raw-slug placeholder-quoting convention & substitution sub-shape — *Resolved in
  brainstorm*: templates are DB-stored, free-form, mixed-context; adopt **A2
  (context-aware `safe_format_map_validated`)** as primary, do NOT change
  `safe_format_map`, and standardize new templates to typed unquoted placeholders as a
  reinforcing layer. Escape keys/operators/values, not only string values.
- [x] Why not change `safe_format_map`? — *Resolved in brainstorm*: ~10 internal Rust
  callers assemble already-built SQL fragments; escaping there double-escapes/corrupts
  them. Add a sibling function instead.
- [x] Is `cond_definition` populated for raw slugs (it drives per-placeholder type
  hints for `is_valid`)? If sparse, A2 uses safe defaults (quoted-literal + reject
  SQL-shaped tokens). — *Owner: Jesús*: cond_definition populated by a dictionary
- [x] Does any production consumer legitimately read `credentials`/`dsn` from
  `GET /api/v1/datasource`? If so, design a separate PBAC-gated reveal endpoint
  before full redaction. — *Owner: Jesús*: let's design a separate PBAC-gated reveal endpoint.
- [ ] Is `maturin` wheel rebuild + `.pyx` recompile already part of the hotfix release
  pipeline for `main`, or must it be added for this branch? — *Owner: Ops*
- [ ] Ops follow-up (separate ticket): dedicated least-privilege DB role + `REVOKE` on
  `pg_catalog`/`information_schema` + migration script. — *Owner: Ops*
- [ ] Ship Option D (SQL-AST/statement-shape backstop) in this hotfix as a cheap extra
  layer, or defer? — *Owner: Jesús*

---

## Worktree Strategy

- **Default isolation unit**: `mixed`.
- **V2 track (Module 6)** — pure-Python change in `datasources/handlers/datasource.py`;
  small, self-contained, no build step. Can land first in its own worktree to stop the
  credential bleed immediately.
- **V1 track (Modules 1–5)** — Rust + Cython + provider wiring; requires `maturin`
  rebuild and `.pyx` recompile; sequence Modules 1→2→3→4→5 in one worktree
  (build artifacts are shared dependencies).
- **Cross-feature dependencies**: watch for overlap with in-flight Rust/parser work
  (e.g. `feat/group-by-cte-fix`, `rust-options`). No dependency on other unmerged
  specs.
- Both tracks merge into the single hotfix branch off `main`
  (`feat-FEAT-103-malforming-queryslug-issue`), per `base_branch: main`.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-06-19 | Jesús / Claude | Initial draft from brainstorm (Option A2) |
