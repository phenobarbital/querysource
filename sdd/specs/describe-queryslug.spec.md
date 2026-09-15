---
type: feature
base_branch: dev
---

# Feature Specification: Describe Query-Slug REST Endpoints

**Feature ID**: FEAT-148
**Date**: 2026-09-15
**Author**: Jesús Lara / Claude (`/sdd-spec`)
**Status**: draft
**Target version**: 4.6.0
**Source**: `sdd/proposals/describe-queryslug.brainstorm.md` (Recommended Option A; all open questions resolved in commit `0e27ef2`)

---

## 1. Motivation & Business Requirements

### Problem Statement

Today a consumer can learn what a query slug is in only two ways, and neither is safe.

1. **Hand-written SQL against the definitions table through the query executor.** For example:
   `SELECT query_slug, provider, description, program_slug, updated_at FROM public.queries ORDER BY updated_at DESC NULLS LAST`
   sent to `POST /api/v1/queries/run`.
   - Anything richer needs another raw query: `cond_definition`, default `conditions`, `attributes`, `fields`, `filtering`, `ordering`, `grouping`, or `query_raw` itself.
   - This path requires `raw_query:execute` + `datasource:use` + `driver:use`, far more power than "tell me what this slug is".
   - Every client must know the physical table, and nothing filters the result by what the user may see.
2. **`QueryManager` at `GET /api/v1/management/queries[/{slug}]`.** This is an admin CRUD view that enforces **no PBAC at all**. It returns every column, including `query_raw`, `dwh_info` and `cache_options`, to anyone who reaches the route.

The people affected:
- **Frontend and dashboard developers** building query pickers and parameter forms.
- **Integrators and agents** that must know a slug's variables and conditions before executing it.

Three downstream consumers in `ai-parrot` are waiting on this contract:
- **FEAT-567 `QuerySourceToolkit.qs_describe`.** It replaces `QSourceTool`, which can only *execute* a slug.
- **A2UI linked surfaces.** A widget carries a data-source descriptor (`slug` + `conditions` + declared parameters) and re-fetches through `POST /api/v2/services/queries/{slug}`.
- **`DatasetManager.QuerySlugSource.prefetch_schema()`.** Today it executes the slug with `querylimit=1` only to learn column names and types.

Two more gaps surfaced during research:
- **Column types are discarded.** `providers/pg.py:44-56` prepares the statement and reads `stmt.get_attributes()`, but keeps only `a.name`.
- **The relative-date keyword vocabulary is not discoverable.** `TODAY`, `YESTERDAY`, `FDOM`, `LDOM`, `CURRENT_YEAR`, `CURRENT_MONTH` and `LAST_YEAR` are accepted as condition **values**. They are resolved inconsistently, and there is nothing to read them from.
  - The inconsistency was reproduced during spec research on 2026-09-15:
    - Cython `is_valid('firstdate', 'FDOM', 'date')` → `'2026-09-01'`
    - Rust `_rs.safe_format_map_validated(..., {'firstdate': 'FDOM'}, {'firstdate': 'date'})` → `'FDOM'` (a quoted literal)
  - Raw-query slugs execute through the Rust path, so **keywords never resolve there**, typed or untyped.

The platform already has PBAC (`slug:execute`, policies under `policies/`), and slugs are owned by programs (`program_slug`). The describe API must respect both:
- an **SQL pre-filter** by the caller's programs;
- an **ABAC post-filter** per slug.

### Goals

- **G1 — List.** `GET /api/v1/queries/describe`: a paginated list of the slugs visible to the caller.
  - Pipeline: program pre-filter in SQL → ABAC `slug:list OR slug:execute` batch filter → in-memory pagination with exact totals.
- **G2 — Detail.** `GET /api/v1/queries/{slug}/describe`: every `QueryModel` attribute plus derived metadata.
  - Derived metadata: typed variables, structural placeholders, effective `cond_definition`, capabilities, links.
  - Redaction is by omission, reported in `redacted`.
- **G3 — Columns.** `GET /api/v1/queries/{slug}/columns`: typed output columns.
  - Sources: the prepared statement, else the declared `attributes.columns`, else unavailable.
  - The query is never executed.
- **G4 — Vocabulary.** `GET /api/v1/queries/vocabulary`: the effective relative-date keyword vocabulary, with PG constants and an informational function block.
- **G5 — Per-tenant variants.** `GET /api/v1/{tenant}/queries/describe`, `/{tenant}/queries/{slug}/describe` and `/{tenant}/queries/{slug}/columns`.
  - Served by the same handler over the FEAT-176 `(schema, table)` data access.
  - Program context is derived from the tenant.
- **G6 — UDF keyword fix, shipped first.** Keywords must resolve identically on the Rust (raw-query) and Cython (parser) paths for untyped and `date`/`datetime`/`timestamp`-typed conditions, so `accepts_keywords` is truthful.
- **G7 — Fail closed.** No session and no sessionless authz → `401`. Evaluator errors → deny.
- **G8 — Zero regressions.** `QueryManager`, `QueryService.get_columns`/`columns`, `_enforce_pbac` and `_pagination` defaults behave exactly as before.

### Non-Goals (explicitly out of scope)

- Modifying or hardening `QueryManager` (`/api/v1/management/queries`). It stays the admin CRUD tool. Extending it was rejected in the brainstorm (Option B).
- A "system slug" that runs describe through the QS pipeline. Rejected in the brainstorm (Option C): authorization would sit in the output layer.
- Caching slug definitions or describe responses (Redis or memory).
- Sampling rows (`querylimit=1`) to infer column types on providers without `prepare`.
- Invoking `utils/functions.pyx` helpers as HTTP condition values. The vocabulary lists them with `invocable: false`.
- Columns for MultiQuery (v3) slugs. `QueryHandler.columns` keeps answering 204.
- Generated OpenAPI/Swagger documents.
- Implementing FEAT-176 itself: tenant discovery, the registry, tenant CRUD or execution routes. This feature only **consumes** FEAT-176's data-access layer.

---

## 2. Architectural Design

### Overview

The recommended option (A) adds a dedicated read-only handler, a slug-visibility service and a pure describer. It also adds three smaller pieces: an additive provider hook for typed columns, a curated vocabulary registry, and a validator fix that lands first.

**List — `GET /api/v1/queries/describe`**

- **Query parameters:**
  - `page` (default 1), `page_size` (default 50, max 200);
  - `sort=<field>[:asc|desc]` (default `updated_at:desc`, **NULLS LAST**), where the field must be in `SORTABLE_COLUMNS`;
  - `q`/`search` (ILIKE over `SEARCHABLE_COLUMNS`);
  - equality filters (`provider`, `program_slug`, … from `FILTERABLE_COLUMNS`).
  - A `program_slug` filter narrows **within** the caller's programs and never widens them.
- **`200` response:** `{"data": [{query_slug, provider, description, program_slug, updated_at}], "meta": {page, page_size, total, total_pages}}`.
  - Headers: `X-Total-Count`, `X-Page`, `X-Page-Size`, `X-Total-Pages`.
  - `X-Truncated: true` is added when the scan cap was hit.
- **Other responses:**
  - `204` when nothing is visible;
  - `400` for invalid pagination, sort or filter parameters (same payload style as `QueryManager`);
  - `401` when there is no principal.
  - `HEAD` returns the headers only.

**Detail — `GET /api/v1/queries/{slug}/describe`**

- **`200` response.** Fields, grouped:
  - **Identity:** `query_slug`, `description`, `program_slug`, `program_id`, `provider`, `parser`, `source`.
  - **Definition:** `query_raw` (only with `slug:describe_raw`), `is_raw`, `params`, `attributes`, `conditions`, `cond_definition`.
  - **Query options:** `fields`, `filtering`, `ordering`, `grouping`, `h_filtering`, `qry_options`.
  - **Cache and DWH:** `is_cached`, `cache_timeout`, `cache_refresh`, `dwh`, `dwh_driver`. The admin-only fields `dwh_info`, `dwh_scheduler` and `cache_options` appear only for admins.
  - **Timestamps:** `created_at`, `updated_at`. The admin-only fields `created_by` and `updated_by` appear only for admins.
- **`derived` block:**
  - `variables`: list of `DescribeVariable`, or `null`.
  - `variables_supported`: `false` for JSON `query_raw`.
  - `variables_error`: present only for malformed braces.
  - `structural_placeholders`, `effective_cond_definition`.
  - `capabilities`: the query options above plus `refresh_param: "refresh"`.
  - `links`: `{"columns": ..., "vocabulary": "/api/v1/queries/vocabulary"}`.
  - `warnings`.
- **`redacted`:** a sorted list of the field names withheld.
- **Other responses:**
  - `404` when the slug is missing, outside the caller's programs, or ABAC denies `slug:describe OR slug:execute`. The three cases are indistinguishable.
  - `401` when there is no principal.
- Describe never connects to the slug's datasource.

**Columns — `GET /api/v1/queries/{slug}/columns`**

- Visibility rules are identical to detail.
- Query-string conditions are merged exactly like `QueryService.get_columns`: `{**json_body, **query_params}`.
- **`200` response:** `{"slug", "columns": [{"name", "type"}], "columns_source": "prepare"|"declared"|"unavailable", "warnings": [...]}`.
- The statement is prepared, never executed. No rows are read and the rendered SQL is never returned.
- A datasource that is down still yields `200` with `columns_source: "unavailable"`.

**Vocabulary — `GET /api/v1/queries/vocabulary`**

- Requires a principal (session or sessionless authz). There is no PBAC action.
- **`200` response:** `{"version": "1.0", "case_insensitive": true, "keywords": [...], "constants": [...], "pg_functions": [...], "functions": [...], "usage": "..."}`.
  - `keywords` is exactly the process's effective `UDF_LIST`, environment override included.
  - Each keyword `example` is computed at request time with `to_udf`, so it is the real resolved value.
  - `functions` entries carry `invocable: false`.

**Tenant variants**

- The same three slug routes live under `/api/v1/{tenant}/queries/...` and behave identically.
- They resolve the tenant's `QueryStore` (schema, table, loader) through FEAT-176.
- Instead of the `program_slug` predicate, the tenant pre-filter requires the caller's normalized programs to contain the lowercase tenant name. Superuser and sessionless authz are unfiltered.

**Principal & pre-filter rules** (from the brainstorm, binding)

| Principal kind | Condition | SQL pre-filter | Notes |
|---|---|---|---|
| `superuser` | `userinfo["superuser"] is True` | none | ABAC still applies |
| `programs` | non-empty normalized `userinfo["programs"]` | `lower(program_slug) = ANY($1::text[])` with `programs ∪ {'default'}` | lowercase both sides |
| `authz` | no session, `QS_PBAC_ALLOW_SESSIONLESS_AUTHZ` and `request[AUTHZ_BACKEND_KEY]` | none | synthetic identity `{'username': 'authz:<backend>', 'groups': ['authorized', backend]}` |
| `no_programs` | session present, `programs` missing or empty, not superuser | deny all (list `204`, detail/columns `404`) | `default` is **not** granted |
| `none` | no session, no sessionless authz | — | `401` on every describe route, before any DB access |

**Program normalization:**
- `Program` objects map to `.slug`, falling back to `.name`, then `str()`.
- Values are lowercased, stripped, deduplicated, and empty entries dropped.

**ABAC rules:**
- List visibility is `slug:list` OR `slug:execute`. `slug:execute` is evaluated only over the `slug:list`-denied remainder, and SQL order is preserved.
- Detail and columns require `slug:describe` OR `slug:execute`; denial is `404`.
- `query_raw` requires `slug:describe_raw`. `raw_query:execute` does **not** imply it.
- **Admin** fields are gated by `userinfo.superuser`, or a session group in `QS_DESCRIBE_ADMIN_GROUPS` (default `admin,superuser`). There is no PBAC action for this.
- Evaluator exceptions **fail closed**: slugs are excluded from the list, and detail/columns return `404`.
- **PBAC disabled** (`app['security']` absent): ABAC checks are no-ops that allow, mirroring `_enforce_pbac`. The program pre-filter and the `401` rule still apply.

**Scan cap:**
- The list projection is fetched with `LIMIT QS_DESCRIBE_MAX_SCAN + 1` (default `10000`).
- When more rows exist, only the first `QS_DESCRIBE_MAX_SCAN` rows in SQL order are processed. The response sets `X-Truncated: true` and a warning is logged. It is never a `400`.

**UDF keyword fix (Module 1, first task):**
- A single Cython function `resolve_udf_conditions(conditions, cond_definition)` resolves keyword values through `to_udf`. It covers untyped values and `date`/`datetime`/`timestamp` hints (case-insensitive), matching what Cython `is_valid` already does.
- `BaseProvider._udf_resolved_conditions()` applies it. Every provider call site of `_rs.safe_format_map_validated` passes the resolved dict instead of `self._conditions`.
- Parity tests assert that the Rust path renders the same value as Cython `is_valid` for every keyword × hint combination.
- The same module exposes Python-visible accessors for the effective `UDF_LIST`, `PG_CONSTANTS` and `PG_UDF`. It also fixes their environment override, which today assigns a raw `str` to a `cdef list`.

### Component Diagram

```
aiohttp router (QuerySource.setup, services.py)
   │
   ├─ GET /api/v1/queries/describe ───────────┐
   ├─ GET /api/v1/queries/vocabulary ─────────┤
   ├─ GET /api/v1/queries/{slug}/describe ────┤
   ├─ GET /api/v1/queries/{slug}/columns ─────┤
   └─ GET /api/v1/{tenant}/queries/... (x3) ──┤
                                              ▼
                         QueryDescribe(AbstractHandler)   querysource/handlers/describe.py
                          │        │            │             │
          resolve_principal│        │            │             │ build_vocabulary()
          + store/predicate▼        │            │             ▼
   querysource/auth/slug_visibility.py           │      querysource/utils/vocabulary.py
     Principal / QueryStore / ProgramPredicate   │             │
     filter_visible() / can_access()             │             ▼
          │ PolicyEvaluator (app['policy_evaluator'])   types/validators.pyx
          │                                      │      udf_keywords()/pg_constants()/pg_udfs()/to_udf
          ▼                                      │
   handlers/_pagination.py                       │ describe_slug()
     PaginationParams, build_where_clause,       ▼
     build_order_by(nulls_last=True),     querysource/queries/describe.py (pure)
     compose_where(), build_scan_sql()      extract_placeholders / build_variables /
          │                                  redact_payload
          ▼
   app['qs_connection'].acquire() → conn.fetch_all(sql, *args) / conn.fetch_one(sql, *args)
          │                                     ▲
          └── QueryStore.loader (legacy: QueryModel.get; tenant: FEAT-176) ┘

   columns route:  QS(slug, conditions, request).build_provider()
                     → provider.describe_columns()   (BaseProvider default / pgProvider override)
                     → fallback definition.attributes.columns

   Module 1 (first):  BaseProvider._udf_resolved_conditions()
                        → validators.resolve_udf_conditions() → to_udf
                        → _rs.safe_format_map_validated(sql, resolved, cond_definition)
                      call sites: providers/sql.py:177,202 · mysql.py:112 · sqlserver.py:91 ·
                                  cassandra.py:78 · default.py:77
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `querysource/services.py` `QuerySource.setup` (routes at L160-167) | modifies | register 4 legacy GET routes right after the `QueryExecutor` block, plus 3 tenant routes (Module 8) |
| `querysource/handlers/__init__.py` | modifies | export `QueryDescribe` |
| `querysource/handlers/abstract.py` `AbstractHandler` (L27) | extends (subclass) / uses | `_get_user_session` (L289) reused; `_enforce_pbac` (L317) **not modified** (its EvalContext construction is mirrored in the visibility service) |
| `querysource/handlers/_pagination.py` | extends (backward compatible) | `build_order_by(params, nulls_last=False)`; new `compose_where`, `build_scan_sql` |
| `querysource/auth/_resource_types.py` `ResourceType.SLUG` | uses | resource type for every slug check |
| `navigator_auth` `PolicyEvaluator.check_access` / `.filter_resources` | uses | called directly with an `EvalContext` (Guardian's `filter_resources` cannot serve sessionless authz) |
| `querysource/models.py` `QueryModel` | uses | legacy loader `QueryModel.get(query_slug=, _connection=)`; `Meta.schema`/`Meta.name` for the legacy store |
| `querysource/queries/qs.py` `QS` | uses | columns route only: `build_provider()` + `get_source()` |
| `querysource/providers/abstract.py` `BaseProvider` | extends | additive `describe_columns()` and `_udf_resolved_conditions()` |
| `querysource/providers/pg.py` `pgProvider` | extends | `describe_columns()` override keeping `attribute.type.name`; `columns()` untouched |
| `querysource/providers/{sql,mysql,sqlserver,cassandra,default}.py` | modifies (Module 1) | pass `self._udf_resolved_conditions()` to `_rs.safe_format_map_validated` |
| `querysource/types/validators.pyx` | modifies (Module 1) | accessors, env-override fix, `resolve_udf_conditions` — **requires `make build-inplace`** |
| `querysource/conf.py` | extends | `QS_DESCRIBE_MAX_SCAN`, `QS_DESCRIBE_ADMIN_GROUPS`, `QS_DESCRIBE_COLUMNS_TIMEOUT` |
| `policies/defaults.yaml` `admin_full_access` | modifies | add `slug:describe`, `slug:describe_raw` actions (admin/superuser only) |
| `querysource/handlers/manager.py` `QueryManager` | none | explicitly untouched |
| FEAT-176 per-tenant data access (not yet specified) | depends on | Module 8 only; see §7 and Worktree Strategy |
| `CHANGES.rst`, `querysource/version.py` | modifies | changelog entry; version `4.6.0` |

### Data Models

```python
# querysource/queries/describe.py
from typing import Any, Literal, Optional
from pydantic import BaseModel, ConfigDict

VariableSource = Literal["cond_definition", "conditions", "placeholder"]

class DescribeVariable(BaseModel):
    """One user-facing input of a slug."""
    model_config = ConfigDict(frozen=True)
    name: str
    type: Optional[str] = None          # canonical lowercase type, see CANONICAL_TYPES
    raw_type: Optional[str] = None      # cond_definition value verbatim
    default: Any = None                 # from conditions, else provider replacement default
    required: bool
    source: VariableSource
    accepts_keywords: bool              # True for date/datetime/timestamp and untyped

class DescribeGrants(BaseModel):
    """What the caller may see beyond the base payload."""
    model_config = ConfigDict(frozen=True)
    raw: bool = False                   # slug:describe_raw
    admin: bool = False                 # superuser OR QS_DESCRIBE_ADMIN_GROUPS

# querysource/handlers/describe.py
class ColumnInfo(BaseModel):
    name: str
    type: Optional[str] = None

class ColumnsResponse(BaseModel):
    slug: str
    columns: list[ColumnInfo]
    columns_source: Literal["prepare", "declared", "unavailable"]
    warnings: list[str] = []

# querysource/auth/slug_visibility.py
from dataclasses import dataclass, field
from enum import Enum

class PrincipalKind(str, Enum):
    SUPERUSER = "superuser"
    PROGRAMS = "programs"
    AUTHZ = "authz"
    NO_PROGRAMS = "no_programs"
    NONE = "none"

@dataclass(frozen=True)
class Principal:
    kind: PrincipalKind
    userinfo: dict = field(default_factory=dict)   # synthetic identity for AUTHZ
    groups: tuple[str, ...] = ()
    programs: tuple[str, ...] = ()                 # normalized lowercase slugs
    session: Any = None                            # navigator_session SessionData or None

@dataclass(frozen=True)
class QueryStore:
    schema: str
    table: str
    has_program_slug: bool = True
    tenant: Optional[str] = None
    loader: Optional[Callable[[Any, str], Awaitable["QueryModel"]]] = None  # (conn, slug) -> QueryModel

@dataclass(frozen=True)
class ProgramPredicate:
    deny_all: bool = False
    sql: str = ""                       # e.g. 'lower("program_slug") = ANY($1::text[])'
    args: tuple = ()

# querysource/utils/vocabulary.py
class KeywordEntry(BaseModel):
    name: str
    category: Literal["date"] = "date"
    returns: str                        # "date-string" | "integer" | ...
    description: Optional[str] = None

class FunctionArg(BaseModel):
    name: str
    default: Any = None

class FunctionEntry(BaseModel):
    name: str
    args: list[FunctionArg]
    description: str
    invocable: bool = False
```

### New Public Interfaces

```python
# HTTP (aiohttp routes, all GET)
/api/v1/queries/describe                      -> QueryDescribe.describe_list
/api/v1/queries/vocabulary                    -> QueryDescribe.vocabulary
/api/v1/queries/{slug}/describe               -> QueryDescribe.describe
/api/v1/queries/{slug}/columns                -> QueryDescribe.columns
/api/v1/{tenant}/queries/describe             -> QueryDescribe.describe_list   (Module 8)
/api/v1/{tenant}/queries/{slug}/describe      -> QueryDescribe.describe        (Module 8)
/api/v1/{tenant}/queries/{slug}/columns       -> QueryDescribe.columns         (Module 8)

# Python
BaseProvider.describe_columns() -> list[dict]
querysource.queries.describe.describe_slug(definition, grants, *, columns_link, vocabulary_link) -> dict
querysource.types.validators.resolve_udf_conditions(conditions, cond_definition) -> dict
querysource.types.validators.udf_keywords() / pg_constants() / pg_udfs() -> list
```

---

## 3. Module Breakdown

#### Delegation-eligible modules

| Module | Eligible? | Decided patterns / exact contracts | Why not (if no) |
|---|---|---|---|
| M1: UDF keyword resolution fix | no | — | security-critical substitution path (FEAT-103); Cython rebuild; parity semantics need judgement |
| M2: Slug describer (pure) | yes | signatures, `RESERVED_PLACEHOLDERS`, `CANONICAL_TYPES`, `ADMIN_FIELDS`, `RAW_FIELDS`, variable rules all fixed below | — |
| M3: Slug visibility service | no | — | fail-closed authz logic; EvalContext mirroring of `_enforce_pbac` |
| M4: Pagination helpers + config + policies | yes | `build_order_by(..., nulls_last)`, `compose_where`, `build_scan_sql`, 3 config keys, 2 policy actions fixed | — |
| M5: Typed columns provider hook | yes | `describe_columns()` default + pg override fixed; `columns()` untouched | — |
| M6: Vocabulary registry | yes | models, registry contents list, `build_vocabulary` contract fixed | — |
| M7: Describe handler + routes | no | — | orchestration of M2–M6, error mapping, 401/404/204 semantics, route order |
| M8: Per-tenant routes | no | — | depends on FEAT-176 interfaces that do not exist yet |
| M9: Docs, changelog, version | yes | `CHANGES.rst` Unreleased entry, `version.py` → `4.6.0` | — |

### Module 1: UDF keyword resolution fix (first task)

- **Paths**:
  - `querysource/types/validators.pyx` (modifies)
  - `querysource/providers/abstract.py` (modifies)
  - `querysource/providers/sql.py`, `mysql.py`, `sqlserver.py`, `cassandra.py`, `default.py` (modify call sites)
  - `tests/unit/test_udf_keyword_resolution.py` (new)
- **Responsibility**:
  - Make relative-date keywords resolve identically on the Rust raw-query path and the Cython parser path.
  - Expose the effective keyword/constant lists to Python.
  - Fix the environment override parsing.
- **Depends on**: none
- **Rules**:
  - Resolve when the value is a `str`, `value.strip().upper()` is in the effective `UDF_LIST`, and the hint is `None`, `date`, `datetime` or `timestamp` (case-insensitive).
  - Any other hint (`string`, `literal`, `integer`, …) is left untouched, matching Cython `is_valid`, where typed validators run first.
  - The resolved value is `str(to_udf(value))`. The Rust substitution then quotes or validates it as usual.
  - Non-string values, lists and dicts pass through unchanged. The input dict is never mutated.
  - Environment override: a comma-separated string, whitespace-stripped and uppercased; an empty string means "use the default".
- **Rust (`rust/src/validators.rs`) is NOT modified.** Resolution happens before the Rust call, so no Rust rebuild is needed. The Rust `UDF_LIST` static stays as-is (§7 risk).
- **Interface Skeleton**:
  ```python
  # querysource/types/validators.pyx  (modifies querysource/types/validators.pyx:27-33, adds functions)
  cdef list _env_list(str name, list default)
      """Parse a comma-separated env override into an uppercased list; `default` when unset/empty."""

  cpdef list udf_keywords()   # effective UDF_LIST (copy)      verified cdef list at :28
  cpdef list pg_constants()   # effective PG_CONSTANTS (copy)  verified cdef list at :29-32
  cpdef list pg_udfs()        # effective PG_UDF (copy)        verified cdef list at :33

  cpdef dict resolve_udf_conditions(dict conditions, dict cond_definition = None):
      """Return a new dict where relative-date keyword values are resolved via to_udf.

      Resolves only str values whose uppercase form is in the effective UDF_LIST and whose
      cond_definition hint is absent or one of date/datetime/timestamp (case-insensitive).
      Never mutates `conditions`.
      """
      # uses is_udf (verified :149) and to_udf (verified querysource/utils/functions.pyx:821)

  # querysource/providers/abstract.py  (modifies; BaseProvider verified :23)
  class BaseProvider(ABC):
      def _udf_resolved_conditions(self) -> dict:
          """Conditions with keyword values resolved for the validating Rust substitution.

          Returns resolve_udf_conditions(self._conditions or {}, self._get_cond_definition()).
          """  # _get_cond_definition verified :133

  # call sites (each: replace `self._conditions` with `self._udf_resolved_conditions()` as the 2nd arg)
  #   querysource/providers/sql.py:177      verified
  #   querysource/providers/sql.py:202      verified
  #   querysource/providers/mysql.py:112    verified
  #   querysource/providers/sqlserver.py:91 verified
  #   querysource/providers/cassandra.py:78 verified
  #   querysource/providers/default.py:77   verified
  ```

### Module 2: Slug describer (pure)

- **Path**: `querysource/queries/describe.py` (new)
- **Responsibility**: Transform a `QueryModel` (or its dict) plus `DescribeGrants` into the detail payload. No I/O and no request access.
- **Depends on**: Module 1 is not needed. Uses the `sqlProvider.replacement` defaults as a constant copy, so `providers` is not imported at runtime.
- **Rules**:
  - **`variables_supported`** is `False` when `query_raw`, stripped, starts with `{` or `[` **and** `json.loads` succeeds (a JSON dialect).
    - In that case: `variables = None`, `structural_placeholders = []`.
  - **Extraction:**
    - `string.Formatter().parse(query_raw)` collects field names.
    - Keep only names matching `^[A-Za-z0-9_.]+$`, the Rust key grammar.
    - Deduplicate, preserving first-seen order.
    - A `ValueError` gives `variables = None` plus `variables_error = "<message>"`, never an exception.
  - **Reserved set (`RESERVED_PLACEHOLDERS`):** `schema, table, tablename, fields, filter, where_cond, and_cond, grouping, group_by, ordering, order_by, offset, _offset, limit, _limit, querylimit`.
    - Reserved names found go to `structural_placeholders`.
    - A reserved name also present in `cond_definition` stays structural and adds a warning `"reserved placeholder '<n>' declared in cond_definition"`.
  - **Variable set:** non-reserved placeholder names (in order), then `cond_definition` keys not already listed (in key order).
  - **Default:** `conditions[name]` if present (`source` stays as declared), else `IMPLICIT_DEFAULTS[name]`, where `IMPLICIT_DEFAULTS = {"firstdate": "current_date", "lastdate": "current_date", "filterdate": "current_date"}` (from `providers/sql.py:37-45`).
    - `required = default is None and name not in conditions`.
  - **`source`:** `"cond_definition"` if `name in cond_definition`, else `"conditions"` if `name in conditions`, else `"placeholder"`.
  - **Types:**
    - `raw_type = cond_definition.get(name)`.
    - `type = CANONICAL_ALIASES.get(raw_type.lower(), raw_type.lower())` if that lowercased value is in `CANONICAL_TYPES`, else `None`.
    - `CANONICAL_TYPES = {literal, integer, float, numeric, decimal, epoch, boolean, string, field, date, datetime, timestamp, uuid, array, json, numrange, int4range, int8range}`.
    - `CANONICAL_ALIASES = {"int": "integer", "varchar": "string"}`.
    - An unknown `raw_type` gives `type=None` and a warning.
  - **`accepts_keywords`** = `type in {date, datetime, timestamp}` or `raw_type is None`.
  - **`effective_cond_definition`** = `{**(conditions.get("cond_definition") or {}), **(cond_definition or {})}`, mirroring `AbstractParser._col_definition_sync` (conditions level first, then definition level).
  - **`capabilities`** = `{fields, filtering, ordering, grouping, h_filtering, qry_options, "refresh_param": "refresh"}`, with `None` normalized to `[]`/`{}`/`False`.
  - **Redaction:**
    - `RAW_FIELDS = ("query_raw",)` is removed unless `grants.raw`.
    - `ADMIN_FIELDS = ("dwh_info", "dwh_scheduler", "cache_options", "created_by", "updated_by")` are removed unless `grants.admin`.
    - `redacted` is the sorted list of removed names, including names whose value was `None`.
  - Datetimes are left as Python objects; the handler's `json_response` encoder serializes them.
- **Interface Skeleton**:
  ```python
  # querysource/queries/describe.py  (new)
  RESERVED_PLACEHOLDERS: frozenset[str]
  CANONICAL_TYPES: frozenset[str]
  CANONICAL_ALIASES: dict[str, str]
  IMPLICIT_DEFAULTS: dict[str, str]      # mirrors querysource/providers/sql.py:37-45
  RAW_FIELDS: tuple[str, ...]
  ADMIN_FIELDS: tuple[str, ...]

  def extract_placeholders(query_raw: Optional[str]) -> tuple[Optional[list[str]], Optional[str]]:
      """Return (ordered unique placeholder names, error). ([], None) for empty; (None, msg) on ValueError."""

  def is_json_dialect(query_raw: Optional[str]) -> bool:
      """True when query_raw is a JSON object/array (Mongo/Elastic/Arango style)."""

  def normalize_type(raw_type: Optional[str]) -> Optional[str]:
      """Canonical lowercase type or None."""

  def build_variables(
      query_raw: Optional[str], conditions: Optional[dict], cond_definition: Optional[dict]
  ) -> dict:
      """Return {'variables': list[DescribeVariable] | None, 'variables_supported': bool,
      'structural_placeholders': list[str], 'warnings': list[str], ['variables_error': str]}."""

  def redact_payload(payload: dict, grants: DescribeGrants) -> tuple[dict, list[str]]:
      """Return (new payload without restricted fields, sorted redacted names)."""

  def describe_slug(
      definition: Union[QueryModel, dict],   # QueryModel verified querysource/models.py:48
      grants: DescribeGrants,
      *,
      columns_link: str,
      vocabulary_link: str,
  ) -> dict:
      """Full detail payload: model fields (redacted) + 'derived' + 'redacted'."""
  ```

### Module 3: Slug visibility service

- **Path**: `querysource/auth/slug_visibility.py` (new)
- **Responsibility**:
  - Resolve the principal.
  - Build the store-aware pre-filter predicate.
  - Run fail-closed ABAC batch and single checks.
  - Compute `DescribeGrants`.
- **Depends on**: `querysource/auth/_resource_types.py`, `querysource/conf.py` (Module 4 keys), `navigator_auth` (lazy imports, as `_enforce_pbac` does)
- **Rules**:
  - `resolve_principal` reads `session[AUTH_SESSION_OBJECT]`. It never raises; a malformed `userinfo` (not a dict) is treated as `{}`.
  - `evaluator = request.app.get('policy_evaluator')`; `guardian = request.app.get('security')`.
    - If `guardian is None`, PBAC is disabled: batch checks return every input and single checks return `True`.
    - If `guardian` is set but `evaluator is None`, log an error and deny everything (fail closed).
  - The `EvalContext` mirrors `_enforce_pbac` (abstract.py:420-435): `EvalContext(request=, user=, userinfo=, session=)` with `env=Environment()`.
    - For `AUTHZ`: `user=None`, `userinfo=synthetic`, `session=None`.
  - Every `check_access` / `filter_resources` result is awaited if it is a coroutine (the `inspect.iscoroutine` guard, as at abstract.py:437-445).
  - Any exception in evaluation gives deny for the affected names, plus `logger.error` (fail closed).
  - `build_program_predicate(principal, store, param_index)`:
    - `NONE`/`NO_PROGRAMS` → `deny_all=True`.
    - `SUPERUSER`/`AUTHZ` → empty predicate.
    - `PROGRAMS` with `store.has_program_slug` → `lower("program_slug") = ANY(${param_index}::text[])`, args `(sorted(set(programs) | {"default"}),)`.
    - `PROGRAMS` with a tenant store → `deny_all = store.tenant.lower() not in programs`, else an empty predicate.
  - `is_admin`: `principal.kind is SUPERUSER`, or `set(g.lower() for g in groups) & set(QS_DESCRIBE_ADMIN_GROUPS)`.
  - `legacy_store()` returns `QueryStore(schema=QueryModel.Meta.schema, table=QueryModel.Meta.name, has_program_slug=True, loader=<QueryModel.get wrapper>)`. `QueryModel.Meta` is never mutated.
- **Interface Skeleton**:
  ```python
  # querysource/auth/slug_visibility.py  (new)
  logger = logging.getLogger(__name__)

  def normalize_programs(raw: Any) -> tuple[str, ...]:
      """Program objects -> .slug | .name | str(); lowercase, strip, dedupe, drop empties; sorted."""

  async def resolve_principal(request: web.Request, session: Optional[SessionData]) -> Principal:
      """Classify the caller (SUPERUSER, PROGRAMS, AUTHZ, NO_PROGRAMS, NONE). Never raises.

      AUTHZ only when session is None, QS_PBAC_ALLOW_SESSIONLESS_AUTHZ (conf.py:441) is on and
      request[AUTHZ_BACKEND_KEY] is set (same rule as handlers/abstract.py:357-390).
      """

  def legacy_store() -> QueryStore:
      """Store over QueryModel.Meta.schema/.name (models.py:101-107) with QueryModel.get loader."""

  def build_program_predicate(principal: Principal, store: QueryStore, param_index: int = 1) -> ProgramPredicate:
      """SQL fragment + bound args implementing the program pre-filter table in spec §2."""

  def is_admin(principal: Principal) -> bool:
      """superuser OR a group in QS_DESCRIBE_ADMIN_GROUPS."""

  async def filter_visible(
      request: web.Request, principal: Principal, slugs: list[str],
      primary_action: str, fallback_action: Optional[str] = None,
  ) -> list[str]:
      """Order-preserving subset allowed by primary OR fallback (fallback only on the denied remainder).
      Fail-closed on errors; allow-all when PBAC disabled."""

  async def can_access(
      request: web.Request, principal: Principal, slug: str,
      primary_action: str, fallback_action: Optional[str] = None,
  ) -> bool:
      """Non-raising single-slug check with the same semantics as filter_visible."""

  async def describe_grants(request: web.Request, principal: Principal, slug: str) -> DescribeGrants:
      """raw = can_access(slug:describe_raw) (no fallback); admin = is_admin(principal)."""
  ```

### Module 4: Pagination helpers, configuration, policies

- **Paths**:
  - `querysource/handlers/_pagination.py` (modifies)
  - `querysource/conf.py` (modifies)
  - `policies/defaults.yaml` (modifies)
  - `tests/handlers/test_pagination_describe_helpers.py` (new)
  - `tests/policies/test_default_policies_load.py` (extend)
- **Responsibility**: Provide the backward-compatible SQL helpers, configuration keys and policy grants.
- **Depends on**: none
- **Interface Skeleton**:
  ```python
  # querysource/handlers/_pagination.py  (modifies build_order_by at :309)
  def build_order_by(params: PaginationParams, nulls_last: bool = False) -> str:
      """ORDER BY "<col>" <DIR>[ NULLS LAST]; default output unchanged (QueryManager unaffected)."""

  def compose_where(where: str, extra_clause: str) -> str:
      """Combine a build_where_clause() result ('' or 'WHERE ...') with an extra predicate."""

  def build_scan_sql(
      schema: str, table: str, fields: list[str], where: str, order_by: str, limit: int
  ) -> str:
      """SELECT <allowlisted quoted fields> FROM "<schema>"."<table>" <where> <order_by> LIMIT <int>.
      Validates identifiers like build_page_sql (:352) / _validate_bare_identifier (:404)."""
  ```
  ```python
  # querysource/conf.py  (add after QS_QUERIES_TABLE at :354)
  QS_DESCRIBE_MAX_SCAN: int            # config.getint('QS_DESCRIBE_MAX_SCAN', fallback=10000)
  QS_DESCRIBE_ADMIN_GROUPS: list[str]  # lowercase, comma-split of config.get(..., fallback='admin,superuser')
  QS_DESCRIBE_COLUMNS_TIMEOUT: float   # config.get(..., fallback=5) seconds
  ```
  ```yaml
  # policies/defaults.yaml  admin_full_access.actions (verified :17-26): append
  - "slug:describe"
  - "slug:describe_raw"
  ```

### Module 5: Typed columns provider hook

- **Paths**:
  - `querysource/providers/abstract.py` (modifies)
  - `querysource/providers/pg.py` (modifies)
  - `tests/unit/test_provider_describe_columns.py` (new)
- **Responsibility**: Provide an additive typed-column discovery API. `columns()` is untouched, so `HEAD` and `PATCH /api/v2/services/queries/{slug}` keep their payloads.
- **Depends on**: none
- **Interface Skeleton**:
  ```python
  # querysource/providers/abstract.py  (BaseProvider verified :23; columns() verified :190)
  class BaseProvider(ABC):
      async def describe_columns(self) -> list[dict]:
          """[{'name': str, 'type': None}] derived from await self.columns().

          Accepts list[str] or list[dict-with-'name']; returns [] when columns() raises
          AttributeError/NotImplementedError or yields nothing. Never executes the query.
          """

  # querysource/providers/pg.py  (pgProvider.columns verified :44-56)
  class pgProvider(sqlProvider):
      async def describe_columns(self) -> list[dict]:
          """Prepare self._query (no execution) and return
          [{'name': a.name, 'type': a.type.name} for a in stmt.get_attributes()].
          Raises ParserError on AttributeError (same as columns()); [] when self._query is empty."""
  ```

### Module 6: Vocabulary registry

- **Path**: `querysource/utils/vocabulary.py` (new)
- **Responsibility**: Curate a registry over the effective keyword and constant lists, and build the vocabulary response.
- **Depends on**: Module 1 (`udf_keywords`, `pg_constants`, `pg_udfs` accessors)
- **Registry contents (v1)**:
  - **Keywords.** Each keyword resolves through `to_udf` to the lowercase function in `utils/functions.pyx`:
    - `TODAY` → `today()` (:94): date string, mask `%m/%d/%Y`.
    - `YESTERDAY` → `yesterday()` (:217): date string, `%Y-%m-%d`.
    - `FDOM` → `fdom()` (:133): first day of the current month, `%Y-%m-%d`.
    - `LDOM` → `ldom()` (:183): last day of the current month, `%Y-%m-%d`.
    - `CURRENT_YEAR` → `current_year()` (:104): integer.
    - `CURRENT_MONTH` → `current_month()` (:229): integer.
    - `LAST_YEAR` → `last_year()` (:162): date string one year ago, `%Y-%m-%d`.
  - **Functions** (`invocable: false`):
    - `date_diff(value, diff=1, mode='days', mask='%Y-%m-%d', tz=None)` (:454)
    - `date_sum(value, diff=1, mode='days', mask='%Y-%m-%d', tz=None)` (:503)
    - `days_ago(mask='%m/%d/%Y', offset=1)` (:264)
    - `previous_month(mask='%m/%d/%Y', months=1)` (:111)
    - `fdow(value=None, mask='%Y-%m-%d', zone=None)` (:147)
    - `ldow(value=None, mask='%Y-%m-%d', zone=None)` (:150)
  - `previous_year` (:107) is excluded: its signature says `cpdef int`, but it returns a datetime.
- **Interface Skeleton**:
  ```python
  # querysource/utils/vocabulary.py  (new)
  VOCABULARY_VERSION: str = "1.0"
  KEYWORD_REGISTRY: dict[str, KeywordEntry]
  FUNCTION_REGISTRY: tuple[FunctionEntry, ...]

  def keyword_example(name: str) -> Any:
      """str/int result of to_udf(name) at call time; None (and a debug log) on any exception."""

  def build_vocabulary(
      udf_list: list[str], pg_constants: list[str], pg_udfs: list[str]
  ) -> dict:
      """{'version', 'case_insensitive': True,
          'keywords': [{name, category, returns, description, example}]  # exactly udf_list order;
                                                                         # unknown → description None
          'constants': pg_constants, 'pg_functions': pg_udfs,
          'functions': [FunctionEntry.model_dump()], 'usage': str}"""
  ```

### Module 7: Describe handler and legacy routes

- **Paths**:
  - `querysource/handlers/describe.py` (new)
  - `querysource/handlers/__init__.py` (modifies)
  - `querysource/services.py` (modifies)
  - `tests/handlers/test_describe_list.py`, `test_describe_detail.py`, `test_describe_columns.py`, `test_describe_vocabulary.py` (new)
  - `tests/test_route_registration.py` (extend)
- **Responsibility**: HTTP orchestration of Modules 2–6, error mapping and route registration.
- **Depends on**: Modules 2, 3, 4, 5, 6
- **Flow (list)**:
  1. `_get_user_session` → `resolve_principal`. `NONE` → `401`.
  2. `PaginationParams.from_query_string`. A `ValueError` or validation error → `400`.
  3. `build_program_predicate`. `deny_all` → `204` (with zero-count headers).
  4. Build the SQL, all via `_pagination` helpers:
     - `extra_filters` = query params minus `{page, page_size, sort, search, q, fields}`;
     - `where = compose_where(build_where_clause(params, extra_filters), predicate.sql)`;
     - `order_by = build_order_by(params, nulls_last=True)`;
     - `sql = build_scan_sql(store.schema, store.table, LIST_FIELDS, where, order_by, QS_DESCRIBE_MAX_SCAN + 1)`.
  5. Execute: `async with await app['qs_connection'].acquire() as conn: rows = await conn.fetch_all(sql, *predicate.args)`.
  6. Truncate: more than `QS_DESCRIBE_MAX_SCAN` rows → keep the first N, set `X-Truncated: true`, log a warning.
  7. Filter: `filter_visible(slugs, "slug:list", "slug:execute")`, then keep only rows whose slug is allowed, in order.
  8. Paginate in memory. `total == 0` → `204` with headers; otherwise `PaginatedResponse(...).model_dump()` and headers.
  - `LIST_FIELDS = ["query_slug", "provider", "description", "program_slug", "updated_at"]`.
  - `fields=` in the query string may narrow the projection, but `query_slug` is always included.
- **Flow (detail)**:
  1. Principal. `NONE` → `401`.
  2. Validate the slug with `^[A-Za-z0-9_.\-:]{1,255}$`; an invalid slug → `404`.
  3. Predicate. `deny_all` → `404`.
  4. `conn.fetch_one('SELECT 1 FROM "<schema>"."<table>" WHERE "query_slug" = $1 [AND <predicate with $2>]', slug, *args)`. `None` → `404`.
  5. `store.loader(conn, slug)`. `NoDataFound`, `ValidationError` or `SlugNotFound` → warning log + `404`.
  6. `can_access(slug, "slug:describe", "slug:execute")`. `False` → `404`.
  7. `describe_grants`.
  8. `describe_slug(model, grants, columns_link=..., vocabulary_link="/api/v1/queries/vocabulary")` → `json_response`.
  - For the detail predicate, `build_program_predicate(..., param_index=2)` places the program array at `$2`.
- **Flow (columns)**:
  1. Run the same visibility steps as detail, steps 1–6 (a `404` on any failure).
  2. Conditions: `{**json_body, **query_params}`, as `QueryService.get_columns` merges them (service.py:363-379).
  3. `qs = QS(slug=slug, conditions=conditions, request=request)`, then `await qs.build_provider()`.
     - `SlugNotFound` → `404`.
     - `ParserError`, `ProviderError` or `DriverError` → `200` with the declared fallback and a warning.
  4. `provider = qs.get_source()`.
  5. Before preparing, check for unresolved placeholders. If `extract_placeholders(provider.get_query())` finds non-reserved names, skip `prepare` and add the warning `"prepare_skipped: unresolved placeholders [..]"`.
  6. Otherwise `await asyncio.wait_for(provider.describe_columns(), QS_DESCRIBE_COLUMNS_TIMEOUT)`.
     - `ParserError`, `ProviderError`, `DriverError` or `asyncio.TimeoutError` → fallback plus a warning.
  7. Fallback:
     - `provider.get_definition().attributes.get('columns')`, using dict or attribute access, gives `declared` (types `None`);
     - otherwise `unavailable`.
     - `columns_source` is `"prepare"` only when `describe_columns()` returned a non-empty list.
  8. `ColumnsResponse(...).model_dump()`.
  9. Always `await qs.close()` in `finally`, suppressing exceptions.
  - The rendered SQL is never included in any response or warning.
- **Flow (vocabulary)**: Principal `NONE` → `401`; otherwise `build_vocabulary(udf_keywords(), pg_constants(), pg_udfs())` → `json_response`.
- **Route registration** (services.py, immediately after the `QueryExecutor` routes at L160-167, before `LoggingService`):
  ```python
  dh = QueryDescribe()
  add_get('/api/v1/queries/describe', dh.describe_list, allow_head=True)
  add_get('/api/v1/queries/vocabulary', dh.vocabulary)
  add_get('/api/v1/queries/{slug}/describe', dh.describe)
  add_get('/api/v1/queries/{slug}/columns', dh.columns)
  ```
- **Interface Skeleton**:
  ```python
  # querysource/handlers/describe.py  (new)
  class QueryDescribe(AbstractHandler):   # AbstractHandler verified querysource/handlers/abstract.py:27
      """Read-only describe/columns/vocabulary endpoints for query slugs (FEAT-148)."""

      LIST_FIELDS: tuple[str, ...] = ("query_slug", "provider", "description", "program_slug", "updated_at")

      async def _principal(self, request: web.Request) -> Principal:
          """_get_user_session (abstract.py:289) + resolve_principal; raises web.HTTPUnauthorized for NONE."""

      async def _store(self, request: web.Request) -> QueryStore:
          """legacy_store() for legacy routes; tenant store (Module 8) when match_info has 'tenant'."""

      async def _load_visible(
          self, request: web.Request, principal: Principal, store: QueryStore, slug: str
      ) -> QueryModel:
          """Detail/columns steps 2-6; raises web.HTTPNotFound on any visibility failure."""

      async def describe_list(self, request: web.Request) -> web.Response:
          """GET .../queries/describe — 200 | 204 | 400 | 401."""

      async def describe(self, request: web.Request) -> web.Response:
          """GET .../queries/{slug}/describe — 200 | 401 | 404."""

      async def columns(self, request: web.Request) -> web.Response:
          """GET .../queries/{slug}/columns — 200 | 401 | 404."""

      async def vocabulary(self, request: web.Request) -> web.Response:
          """GET /api/v1/queries/vocabulary — 200 | 401."""
  ```

### Module 8: Per-tenant describe routes (last task; blocked on FEAT-176)

- **Paths**:
  - `querysource/handlers/describe.py` (modifies `_store`)
  - `querysource/auth/slug_visibility.py` (adds `tenant_store`)
  - `querysource/services.py` (routes)
  - `tests/handlers/test_describe_tenant.py` (new)
- **Responsibility**: Serve the three slug routes over a tenant's `(schema, table)`, with tenant-derived program context.
- **Depends on**: Module 7, and **FEAT-176's tenant registry / data-access layer (does not exist yet)**
- **Contract this module requires from FEAT-176** (to be matched to FEAT-176's spec when it exists; unverified):
  - resolve a tenant name to a canonical `(schema, table)`, or "unknown tenant";
  - load a tenant `QueryModel`-compatible definition by `(conn, slug)`;
  - build a `QS` for a tenant-owned slug, for `/columns`.
- **Rules**:
  - An unknown or unregistered tenant gives `404`, the same as a hidden slug.
  - Tenant rows have no `program_slug`, so `QueryStore(has_program_slug=False, tenant=<name>)`.
  - Tenant routes are registered **after** all legacy routes. The `{tenant}` pattern is `{tenant:[a-z][a-z0-9_]*}`.
  - Reserved first-segment names (`queries`, `management`, `qs`, `datasources`, `datasource`, `audit_log`) are rejected by FEAT-176's reserved-name policy. This module adds a test asserting `/api/v1/queries/queries/describe` routes to the legacy detail handler.
- **Interface Skeleton**:
  ```python
  # querysource/auth/slug_visibility.py
  async def tenant_store(request: web.Request, tenant: str) -> Optional[QueryStore]:
      """QueryStore(has_program_slug=False, tenant=tenant.lower(), schema/table/loader from FEAT-176);
      None when the tenant is not registered. (unverified — FEAT-176 contract)"""

  # querysource/services.py
  add_get('/api/v1/{tenant:[a-z][a-z0-9_]*}/queries/describe', dh.describe_list, allow_head=True)
  add_get('/api/v1/{tenant:[a-z][a-z0-9_]*}/queries/{slug}/describe', dh.describe)
  add_get('/api/v1/{tenant:[a-z][a-z0-9_]*}/queries/{slug}/columns', dh.columns)
  ```

### Module 9: Documentation, changelog, version

- **Paths**:
  - `CHANGES.rst` (Unreleased section)
  - `querysource/version.py` (`__version__ = '4.6.0'`, currently `'4.5.16'` at :9)
  - `docs/` (an endpoint page, if a handlers doc exists; otherwise the changelog only)
- **Responsibility**:
  - Document the endpoints, the principal and redaction rules, and the new config keys and policy actions.
  - Record the UDF resolution fix as a behaviour change (raw-query keywords now resolve).
- **Depends on**: Modules 1–8

---

## 4. Test Specification

### Unit Tests

| Test | Module | Description |
|---|---|---|
| `test_resolve_udf_untyped_and_date_hints` | M1 | `FDOM`/`TODAY`/`fdom` resolve for hint `None`, `date`, `DATE`, `datetime`, `timestamp` |
| `test_resolve_udf_leaves_string_literal_integer_hints` | M1 | hints `string`/`literal`/`integer` keep the raw value |
| `test_resolve_udf_does_not_mutate_input` | M1 | input dict identity/content unchanged |
| `test_rust_cython_parity_all_keywords` | M1 | for each keyword × hint in {None, date, datetime, timestamp}: `sqlProvider.raw_query` output equals the `is_valid` rendering |
| `test_udf_env_override_comma_separated` | M1 | `UDF_LIST="TODAY, FDOM"` → `udf_keywords() == ['TODAY','FDOM']` (subprocess/import reload) |
| `test_raw_query_validated_still_rejects_injection` | M1 | existing FEAT-103 rejection cases still raise `ParserError` (extends `tests/unit/test_provider_raw_query_validated.py`) |
| `test_extract_placeholders_order_and_grammar` | M2 | dedupe, first-seen order, drops `{a b}`/format specs; `{}` escape `{{x}}` ignored |
| `test_extract_placeholders_malformed` | M2 | unbalanced brace → `(None, msg)` |
| `test_json_dialect_unsupported` | M2 | Mongo-style `query_raw` → `variables_supported False`, `variables None` |
| `test_structural_placeholders_reported` | M2 | `{fields}`, `{and_cond}`, `{querylimit}` go to `structural_placeholders`, not variables |
| `test_reserved_in_cond_definition_warns` | M2 | reserved name in `cond_definition` stays structural + warning |
| `test_variable_types_and_defaults` | M2 | `STRING`→`string`, `int`→`integer`, `varchar`→`string`, unknown → `None` + warning; `firstdate` implicit default `current_date`, `required False` |
| `test_variable_required_and_source` | M2 | placeholder without default → `required True`, `source placeholder`; cond_definition-only keys appended |
| `test_effective_cond_definition_merge_order` | M2 | definition level overrides conditions level |
| `test_redaction_matrix` | M2 | grants (raw, admin) × 4 combinations → exact `redacted` lists; fields omitted, not nulled |
| `test_normalize_programs` | M3 | objects with `.slug`/`.name`, mixed case, duplicates, empties |
| `test_resolve_principal_kinds` | M3 | superuser, programs, no_programs, authz (flag on + backend), none (flag off / no backend) |
| `test_program_predicate_legacy_and_tenant` | M3 | SQL fragment + args include `default`; deny_all for none/no_programs; tenant membership |
| `test_filter_visible_fallback_only_on_remainder` | M3 | `filter_resources` called with `slug:list` on all, `slug:execute` only on denied; order preserved |
| `test_filter_visible_fail_closed_on_error` | M3 | evaluator raises → `[]` + error log |
| `test_can_access_pbac_disabled_allows` | M3 | no `app['security']` → `True` |
| `test_guardian_without_evaluator_denies` | M3 | `security` set, `policy_evaluator` missing → deny |
| `test_is_admin_groups_config` | M3 | group match case-insensitive against `QS_DESCRIBE_ADMIN_GROUPS` |
| `test_build_order_by_default_unchanged` | M4 | `build_order_by(params)` output identical to before |
| `test_build_order_by_nulls_last` | M4 | `nulls_last=True` appends `NULLS LAST` |
| `test_compose_where_and_scan_sql` | M4 | combinations of empty/non-empty where + extra clause; identifiers validated |
| `test_default_policies_grant_describe_actions` | M4 | `admin_full_access` contains `slug:describe`, `slug:describe_raw` |
| `test_base_describe_columns_from_names` | M5 | `columns()` → `['a','b']` gives untyped dicts; AttributeError → `[]` |
| `test_pg_describe_columns_types` | M5 | mocked prepared statement attributes → `[{'name','type'}]`; `columns()` output unchanged |
| `test_build_vocabulary_matches_udf_list` | M6 | keywords exactly equal input order; unknown keyword → `description None`; functions `invocable False`; `previous_year` absent |
| `test_keyword_example_resolves` | M6 | `keyword_example('FDOM')` equals `fdom()`; exception → `None` |

### Integration Tests

Integration tests use the aiohttp `TestClient` with the `FakeQSConnection` pattern from `tests/handlers/conftest.py`. That pattern is extended so `fetch_all`/`fetch_one` accept `*args`, and a mock `policy_evaluator` is injected.

| Test | Description |
|---|---|
| `test_list_401_without_principal` | no session, no authz → 401 before any SQL call |
| `test_list_204_no_programs` | session without programs → 204, no SQL executed |
| `test_list_program_prefilter_bound_args` | SQL contains `ANY($1::text[])`, args include user programs + `default`, lowercase |
| `test_list_superuser_no_predicate` | no program predicate in SQL |
| `test_list_abac_union_and_exact_totals` | 10 rows, list allows 3, execute allows 2 more → total 5, pagination math, headers |
| `test_list_truncation_header` | `QS_DESCRIBE_MAX_SCAN=3`, 4 rows → `X-Truncated: true` |
| `test_list_invalid_sort_400` | unknown sort field → 400 |
| `test_list_head_headers_only` | HEAD returns headers, empty body |
| `test_detail_404_indistinguishable` | missing slug, other program, ABAC deny → identical 404 |
| `test_detail_execute_implies_describe` | describe denied, execute allowed → 200 |
| `test_detail_redaction_by_grants` | no describe_raw → no `query_raw` and `redacted` lists it; admin group → admin fields present |
| `test_detail_pbac_disabled_shows_raw` | PBAC off + session with programs → 200 incl. `query_raw` (documented behaviour) |
| `test_columns_prepare_typed` | stub provider `describe_columns` → `columns_source prepare` |
| `test_columns_unresolved_placeholders_declared_fallback` | placeholder without default, no condition → `declared` + `prepare_skipped` warning, no prepare call |
| `test_columns_datasource_down_unavailable` | `DriverError` + no declared columns → 200 `unavailable` |
| `test_columns_never_returns_sql` | response body contains no `SELECT`/`query_raw` text |
| `test_vocabulary_requires_principal` | 401 without principal; 200 with session or authz |
| `test_route_registration_describe` | `tests/test_route_registration.py`: 4 legacy GET routes (+HEAD on list) registered; existing routes unchanged |
| `test_tenant_routes_*` (M8) | unknown tenant 404; tenant membership pre-filter; `/api/v1/queries/queries/describe` resolves to legacy detail |

### Test Data / Fixtures

```python
@pytest.fixture
def describe_rows():
    """Projection rows for list tests (query_slug, provider, description, program_slug, updated_at)."""
    return [{"query_slug": f"slug_{i}", "provider": "pg", "description": f"d{i}",
             "program_slug": "walmart" if i % 2 else "default", "updated_at": None} for i in range(10)]

@pytest.fixture
def sample_definition():
    """QueryModel-compatible dict with placeholders, cond_definition and admin/raw fields."""
    return {
        "query_slug": "epson_field_activity", "provider": "pg", "parser": "SQLParser",
        "program_slug": "epson", "query_raw":
            "SELECT {fields} FROM t WHERE visit_date BETWEEN {firstdate} AND {lastdate} "
            "AND store_id = {store_id} {and_cond}",
        "conditions": {"store_id": 10}, "cond_definition": {"firstdate": "date", "lastdate": "DATE",
                                                           "store_id": "integer"},
        "dwh_info": {"x": 1}, "cache_options": {"y": 2}, "created_by": 1, "updated_by": 2,
    }

@pytest.fixture
def mock_evaluator():
    """Evaluator whose check_access / filter_resources answer from {(slug, action): bool}."""
```

---

## 5. Acceptance Criteria

This feature is complete when **all** of the following are true.

**Validators and providers**
- [ ] **AC1 (M1).** For every keyword in the effective `UDF_LIST` and each hint in {none, `date`, `datetime`, `timestamp`} in any letter case, the raw-query (Rust) rendering equals the Cython `is_valid` rendering. Verified by `pytest tests/unit/test_udf_keyword_resolution.py`, after `make build-inplace`.
- [ ] **AC2 (M1).** Existing FEAT-103 injection tests pass unchanged: `pytest tests/unit/test_provider_raw_query_validated.py tests/unit/test_qs_parsers_validated.py`.
- [ ] **AC3 (M1).** `udf_keywords()`, `pg_constants()` and `pg_udfs()` are importable from `querysource.types.validators`. A comma-separated `UDF_LIST` environment override is honoured; today it would be a `str` assigned to a `cdef list`.

**Access rules (all routes)**
- [ ] **AC4.** With no session and no sessionless authz, list, detail, columns and vocabulary answer `401` without executing any SQL.
- [ ] **AC5.** Pre-filter by principal:
  - a session **with** programs gets `lower(program_slug) = ANY($n::text[])`, bound with the lowercase programs ∪ `default`;
  - a session **without** programs gets `204` on the list and `404` on detail/columns (`default` not granted);
  - superuser and sessionless-authz principals get no program predicate.
- [ ] **AC6.** List visibility is `slug:list OR slug:execute`, with `slug:execute` evaluated only on the denied remainder. Detail and columns require `slug:describe OR slug:execute`.
  - Denial, a slug outside the caller's programs, and a missing slug all give byte-identical `404` responses.
  - Evaluator errors deny (fail closed).
- [ ] **AC7.** The program pre-filter applies to detail and columns as well as the list.

**List**
- [ ] **AC8.** Pagination happens after the ABAC filter:
  - `X-Total-Count`/`meta.total` equal the number of visible rows;
  - the default order is `updated_at DESC NULLS LAST`;
  - `page`, `page_size` (max 200), `sort`, `search`/`q` and equality filters work; invalid parameters give `400`.
- [ ] **AC9.** With more than `QS_DESCRIBE_MAX_SCAN` (default 10000) pre-filtered rows, only the first N in SQL order are processed, `X-Truncated: true` is set, and a warning is logged. It is never a `400`.
- [ ] **AC10.** Every SQL value derived from the request or session is either a bound parameter or produced by the existing `_pagination` allowlisted builders. No other string interpolation.

**Detail payload**
- [ ] **AC11.** The detail payload contains all non-restricted `QueryModel` fields plus a `derived` block: `variables` (`name`, `type`, `raw_type`, `default`, `required`, `source`, `accepts_keywords`), `variables_supported`, `structural_placeholders`, `effective_cond_definition`, `capabilities` (incl. `refresh_param`), `links` and `warnings`.
- [ ] **AC12.** Restricted fields are omitted, never nulled, and listed in `redacted`:
  - `query_raw` is present only with `slug:describe_raw`; `raw_query:execute` does not grant it;
  - `dwh_info`, `dwh_scheduler`, `cache_options`, `created_by` and `updated_by` are present only for superusers or members of `QS_DESCRIBE_ADMIN_GROUPS` (default `admin,superuser`).
- [ ] **AC13.** Parameter extraction:
  - structural placeholders (the reserved set in §3 Module 2) never appear in `variables`;
  - `firstdate`/`lastdate`/`filterdate` report the implicit default `current_date` with `required: false`;
  - JSON-dialect `query_raw` gives `variables: null, variables_supported: false`;
  - malformed braces produce `variables_error`, never a `500`.
- [ ] **AC14.** List and detail never open a connection to the slug's datasource, and no response on any route caches definitions.

**Columns**
- [ ] **AC15.** `/columns` prepares and never executes. It returns:
  - `columns_source: "prepare"` with types on pg;
  - `"declared"` from `attributes.columns` when prepare fails, times out (`QS_DESCRIBE_COLUMNS_TIMEOUT`) or has unresolved placeholders;
  - `"unavailable"` otherwise, still `200`.

  Query-string and JSON conditions merge as `{**body, **query}`, and the rendered SQL never appears in the response.
- [ ] **AC16.** `pgProvider.columns()` and `BaseProvider.columns()` return exactly what they returned before. `HEAD` and `PATCH /api/v2/services/queries/{slug}` responses are unchanged.

**Vocabulary**
- [ ] **AC17.** `/vocabulary` lists exactly the effective `UDF_LIST` in order, with request-time examples from `to_udf`. Keywords missing from the registry get `description: null`. It includes `constants`, `pg_functions` and the informational `functions` block (`date_diff`, `date_sum`, `days_ago`, `previous_month`, `fdow`, `ldow`, each `invocable: false`).

**Tenant routes**
- [ ] **AC18 (M8).** Behaviour:
  - the three tenant routes serve the tenant's `(schema, table)`;
  - an unknown tenant gives `404`;
  - non-superuser, non-authz callers need the tenant in their programs;
  - `/api/v1/queries/queries/describe` still resolves to the legacy detail route.

  Tested against FEAT-176's data-access layer once merged.

**Regressions and release**
- [ ] **AC19.** `QueryManager` code and tests are unchanged and pass (`pytest tests/handlers/test_querymanager_pagination.py`). `build_order_by(params)` output is unchanged.
- [ ] **AC20.** `policies/defaults.yaml` grants `slug:describe` and `slug:describe_raw` to the admin/superuser groups only, and `tests/policies/` pass.
- [ ] **AC21.** `pytest tests/handlers tests/unit tests/policies tests/test_route_registration.py -q` is green, and `ruff check` over the changed paths is clean.
- [ ] **AC22.** `CHANGES.rst` documents the endpoints, config keys, policy actions and the keyword-resolution behaviour change. `querysource/version.py` is `4.6.0`.
- [ ] **AC23.** No breaking change to any existing public route or Python API.

---

## 6. Codebase Contract

> **CRITICAL — Anti-Hallucination Anchor.** Every reference below was re-verified on 2026-09-15 against `dev` @ `0e27ef2`.

### Verified Imports

```python
from querysource.models import QueryModel                         # verified: querysource/models.py:48
from querysource.handlers.abstract import AbstractHandler         # verified: querysource/handlers/abstract.py:27
from querysource.handlers._pagination import (                    # verified: querysource/handlers/_pagination.py
    PaginationParams,        # :73
    PaginationMeta,          # :188
    PaginatedResponse,       # :197
    build_where_clause,      # :251
    build_order_by,          # :309
    build_count_sql,         # :332
    build_page_sql,          # :352
    SORTABLE_COLUMNS,        # :52
    SEARCHABLE_COLUMNS,      # :65
    FILTERABLE_COLUMNS,      # :48
    DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE,  # :40-41
)
from querysource.auth import ResourceType                         # verified: querysource/auth/__init__.py:19 (used by handlers/service.py:28)
from querysource.queries.qs import QS                             # verified: querysource/queries/qs.py:36
from querysource.exceptions import SlugNotFound, ParserError, DriverError, QueryException  # verified: querysource/exceptions.py:34,70,58 (import style handlers/service.py:18-23)
from asyncdb.exceptions import ProviderError                      # verified: handlers/service.py:11-14
from querysource.conf import (                                    # verified: querysource/conf.py
    QS_QUERIES_SCHEMA, QS_QUERIES_TABLE,                          # :353-354
    QS_PBAC_ENABLED, QS_POLICY_PATH, QS_PBAC_CACHE_TTL,            # :429-431
    QS_PBAC_ALLOW_SESSIONLESS_AUTHZ,                              # :441-443
)
from querysource.types.validators import is_valid, is_udf, to_udf # verified: runtime import 2026-09-15 (is_udf :149, is_valid :523)
from querysource.qs_parsers import HAS_RUST                       # verified: querysource/providers/sql.py:25-27
from querysource.qs_parsers import _qs_parsers as _rs             # verified: querysource/providers/sql.py:27
from navigator.views import BaseHandler                           # verified: querysource/handlers/abstract.py:7
from navigator_session import get_session, SessionData            # verified: querysource/handlers/abstract.py:8
# Lazy (inside functions only, as in abstract.py:370,410-412):
from navigator_auth.conf import AUTHZ_BACKEND_KEY, AUTH_SESSION_OBJECT
from navigator_auth.abac.context import EvalContext
from navigator_auth.abac.policies.environment import Environment
```

### Existing Class Signatures

```python
# querysource/models.py:48-107
class QueryModel(asyncdb.models.Model):
    query_slug: str; description: str; source: Optional[str]
    params: dict; attributes: dict; conditions: dict; cond_definition: dict       # jsonb
    fields: List[str]; filtering: dict; ordering: List[str]; grouping: List[str]; qry_options: dict
    h_filtering: bool; query_raw: str; is_raw: bool; is_cached: bool
    provider: str = 'db'; parser: str = 'SQLParser'
    cache_timeout: int; cache_refresh: int; cache_options: dict
    program_id: int; program_slug: str = 'default'
    dwh: bool; dwh_driver: str; dwh_info: dict; dwh_scheduler: dict
    created_at: datetime; created_by: int; updated_at: datetime; updated_by: int
    class Meta:  # :101-107
        driver = 'pg'; name = QS_QUERIES_TABLE; schema = QS_QUERIES_SCHEMA; strict = True

# querysource/handlers/abstract.py
_SENTINEL = object()                                                         # :24
class AbstractHandler(BaseHandler):                                          # :27
    async def _get_user_session(self, request: web.Request) -> Optional[SessionData]:   # :289 (memoized on request['user_session'])
    async def _enforce_pbac(self, request: web.Request, resource_type, resource_name: str, action: str) -> None:  # :317
        # guardian = request.app.get('security') -> None => return (no-op)            # :339-341
        # sessionless authz synthetic userinfo {'username','groups':['authorized',backend],'roles':[]}  # :357-390
        # evaluator = request.app.get('policy_evaluator'); None => 404                # :392-398
        # EvalContext(request=, user=, userinfo=, session=)                           # :420-425
        # evaluator.check_access(ctx=, resource_type=, resource_name=, action=, env=Environment())  # :431-437
        # inspect.iscoroutine(result) guard; not result.allowed => HTTPNotFound       # :438-449

# navigator BaseHandler (.venv/.../navigator/views/base.py)
    def no_content(self, headers=None, content_type=...)                         # :111
    def json_response(self, response=None, reason=None, headers=None, status=200, state=None, cls=None)  # :144
    def error(self, response=None, exception=None, status=400, state=None, headers=None, ...)  # :201
    def query_parameters(self, request: web.Request) -> dict                     # :309
    def match_parameters(self, request: web.Request = None) -> dict              # :345

# querysource/handlers/service.py
class QueryService(AbstractHandler):                                         # :32
    async def get_columns(self, request):                                    # :359
        # conditions = {**options, **params}                                   # :379
        # await query.build_provider(); SlugNotFound→404; ParserError/ProviderError/DriverError→Error
        # columns = await query.columns(); fallback `query.get_definition()` # :408 (QS has no get_definition — see Does NOT Exist)

# querysource/handlers/manager.py
class QueryManager(QueryView):                                               # :32
    async def get(self):                                                     # :71
    async def _paginate_list(self, qp: dict, default_args: dict):            # :145
        # db = self.request.app['qs_connection']; async with await db.acquire() as conn:
        # conn.fetchval(count_sql); conn.fetch_all(page_sql)                   # :211-230

# querysource/handlers/_pagination.py
def build_where_clause(params: PaginationParams, extra_filters: dict) -> str   # :251 literal-quoted equality/ILIKE; ValueError on unknown keys
def build_order_by(params: PaginationParams) -> str                          # :309 'ORDER BY "<col>" <DIR>'
def build_page_sql(schema: str, table: str, fields: list[str], where: str, order_by: str, limit: int, offset: int) -> str  # :352
def _validate_bare_identifier(value: str, kind: str) -> None                  # :404

# asyncdb pg connection (.venv/.../asyncdb/drivers/pg.py)
    async def fetch_all(self, sentence: str, *args, **kwargs)                  # :1013
    async def fetch_one(self, sentence: str, *args, **kwargs)                  # :1036
    async def fetchval(self, sentence: str, *args, column: int = 0, **kwargs)  # :1052
# querysource/connections.py
    async def acquire(self): return await self._postgres.acquire()             # :123-128

# querysource/queries/qs.py
class QS(BaseQuery):                                                         # :36
    def __init__(self, slug: str = '', conditions: dict = None, request: web.Request = None,
                 loop: asyncio.AbstractEventLoop = None, **kwargs)           # :42-49
    def get_source(self): return self._qs                                    # :123
    async def columns(self)                                                  # :126
    async def build_provider(self)                                           # :135 (get_slug → provider → prepare_connection at :225-226/:268-269/:318-319)
    async def close(self)                                                    # :519

# querysource/providers/abstract.py
class BaseProvider(ABC):                                                     # :23
    # self._refresh; 'refresh' popped from self._conditions                  # :71, :83-85
    def get_definition(self) -> Union[QueryModel, dict]                      # :128
    def _get_cond_definition(self) -> dict                                   # :133 (None → {})
    async def columns(self)                                                  # :190 — `if self._qs: self._columns = await self._qs.columns(); return self._columns` (does NOT raise NotImplementedError)
    def refresh(self)                                                        # :220
    def get_query(self)                                                      # :235

# querysource/providers/sql.py
class sqlProvider(BaseProvider):                                             # :32
    replacement: dict = {"fields": "*", "filterdate": "current_date", "firstdate": "current_date",
                         "lastdate": "current_date", "where_cond": "", "and_cond": "", "filter": ""}  # :37-45
    _PARSER_PLACEHOLDERS = ("{where_cond}", "{and_cond}", "{filter}", "{fields}", "{group_by}", "{grouping}",
                            "{order_by}", "{ordering}", "{querylimit}", "{_limit}", "{_offset}", "{schema}", "{table}")  # :48-62
    async def prepare_connection(self) -> Callable                           # :140 (self._query = await self._parser.build_query() at :159)
    def raw_query(self, query: str)                                          # :165 (_rs.safe_format_map_validated at :177)
    def get_raw_query(self, query: str)                                      # :196 (:202)

# querysource/providers/pg.py
class pgProvider(sqlProvider):                                               # :14
    async def columns(self):                                                 # :44
        # async with await self._connection.connection() as conn:
        #     stmt, _ = await conn.prepare(self._query)                        # :50
        #     self._columns = [a.name for a in stmt.get_attributes()]          # :51
        # except AttributeError → ParserError                                  # :52-55

# querysource/types/validators.pyx
cdef list udf = ["CURRENT_YEAR", "CURRENT_MONTH", "TODAY", "YESTERDAY", "LAST_YEAR", "FDOM", "LDOM"]  # :27
cdef list UDF_LIST = os.environ.get('UDF_LIST', udf)                         # :28  (str on override → TypeError; not Python-visible)
cdef list PG_CONSTANTS = os.environ.get('PG_CONSTANTS', ["CURRENT_DATE", "CURRENT_TIMESTAMP"])  # :29-32
cdef list PG_UDF = os.environ.get('PG_UDF', ["now()"])                       # :33
cpdef bool_t is_udf(object value): return value in UDF_LIST                  # :149 (case-sensitive; callers uppercase)
cdef dict type_validators = {...}  # "date": [is_date, to_date], "datetime"/"timestamp": [is_datetime, to_date], ...  # :361-384
cpdef object is_valid(object key, object value, str T = None, bint noquote = False)  # :523
    # typed validator first (:536-545); then generic: ... elif is_udf(str(value).upper()): return quoteString(to_udf(value))  # :552-553

# querysource/utils/functions.pyx
cpdef str today(str mask = "%m/%d/%Y", tz: str = None)                       # :94
cpdef int current_year()                                                     # :104
cpdef int previous_year()                                                    # :107 (returns datetime despite signature)
cpdef str previous_month(str mask = "%m/%d/%Y", int months = 1)              # :111
cpdef str fdom(object value = None, str mask = "%Y-%m-%d", str zone = None)  # :133
cpdef str fdow(object value=None, str mask="%Y-%m-%d", str zone=None)        # :147
cpdef str ldow(object value=None, str mask="%Y-%m-%d", str zone=None)        # :150
cpdef str last_year(object value=None, str mask="%Y-%m-%d", str zone=None)   # :162
cpdef str ldom(object value = None, str mask = "%Y-%m-%d", str zone = None)  # :183
cpdef str yesterday(str mask = '%Y-%m-%d')                                   # :217
cpdef int current_month()                                                    # :229
cpdef str days_ago(str mask = "%m/%d/%Y", int offset = 1)                    # :264
cpdef str date_diff(object value, int diff = 1, str mode = 'days', str mask = "%Y-%m-%d", str tz = None)  # :454
cpdef str date_sum(object value, int diff = 1, str mode = 'days', str mask = "%Y-%m-%d", str tz = None)   # :503
def to_udf(str value, *args, **kwargs)                                       # :821 (globals()[value.lower()](*args, **kwargs))

# rust/src/validators.rs
static UDF_LIST: Lazy<Vec<&str>>   # :21-31 (static; no env override)
static PG_CONSTANTS: Lazy<Vec<&str>>  # :34-35
pub fn is_udf(value: &str) -> bool # :78
pub fn is_valid(key, value, type_hint: Option<&str>, noquote: bool) -> String  # :269
    # "date" | "datetime" | "timestamp" => return quote_string(value, true)  # :296-298 (keyword quoted verbatim)
    # generic UDF branch returns quoted keyword "for the Python layer to resolve"  # :320-328
# rust/src/safe_dict.rs
pub fn safe_format_map_validated_rust(...)  # :100 (calls is_valid(key, value, type_hint, false))
pub fn safe_format_map_validated(...)       # :203
# placeholder key grammar: non-empty, no '{', chars [A-Za-z0-9_.]  # :116-120

# navigator_auth (.venv/.../navigator_auth/abac/policies/evaluator.py)
class EvaluationResult: allowed: bool; effect; matched_policy: Optional[str]; reason: str  # :60
class FilteredResources: allowed: List[str]; denied: List[str]; policies_applied: List[str]  # :74
def check_access(self, ctx: EvalContext, resource_type: ResourceType, resource_name: str, action: str,
                 env: Environment = None, owner_reports_to: str = None, org_id: int = 1, ...)  # :405
def filter_resources(self, ctx: EvalContext, resource_type: ResourceType, resource_names: List[str],
                     action: str, env: Environment = None, org_id: int = 1, client_id: int = 1)  # :516
# navigator_auth/abac/guardian.py:209  Guardian.filter_resources(...) calls is_authenticated → unusable for sessionless authz
# navigator_auth/decorators.py:462 allowed_programs → reads session[AUTH_SESSION_OBJECT]["programs"]
# navigator_auth/conf.py: DEFAULT_MAPPING "superuser": "is_superuser" (:271); DJANGO_USER_MAPPING "programs" (:345)
```

### Integration Points

| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `BaseProvider._udf_resolved_conditions` | `validators.resolve_udf_conditions` → `to_udf` | function call | `utils/functions.pyx:821`, `types/validators.pyx:149` |
| providers raw substitution | `_rs.safe_format_map_validated(sql, <resolved>, self._get_cond_definition())` | argument swap | `providers/sql.py:177,202`, `mysql.py:112`, `sqlserver.py:91`, `cassandra.py:78`, `default.py:77` |
| `QueryDescribe` | `AbstractHandler._get_user_session` | method call | `handlers/abstract.py:289` |
| `slug_visibility.can_access` / `filter_visible` | `app['policy_evaluator'].check_access` / `.filter_resources` | method call with `EvalContext` | `evaluator.py:405,516`; `abstract.py:420-449` |
| `QueryDescribe.describe_list` | `app['qs_connection'].acquire()` → `conn.fetch_all(sql, *args)` | async ctx + bound args | `connections.py:123`, `asyncdb/drivers/pg.py:1013` |
| `QueryDescribe._load_visible` | `conn.fetch_one(sql, *args)`; `QueryModel.get(query_slug=, _connection=conn)` | bound args; model loader | `asyncdb/drivers/pg.py:1036`; `interfaces/connections.py:463` |
| `QueryDescribe.columns` | `QS(slug=, conditions=, request=)` → `build_provider()` → `get_source().describe_columns()` | call chain | `queries/qs.py:42,135,123` |
| `pgProvider.describe_columns` | `conn.prepare(self._query)` → `stmt.get_attributes()` | asyncpg | `providers/pg.py:48-51` |
| `build_vocabulary` | `udf_keywords()` / `pg_constants()` / `pg_udfs()` / `to_udf` | function call | new in M1; `functions.pyx:821` |
| routes | `QuerySource.setup` router | `add_get` after QueryExecutor block | `services.py:160-167` |
| policy grants | `admin_full_access.actions` | YAML list | `policies/defaults.yaml:17-26` |

### Does NOT Exist (Anti-Hallucination)

**Routes, handlers and models**
- ~~`GET /api/v1/queries/describe`, `/api/v1/queries/{slug}/describe`, `/{slug}/columns`, `/vocabulary`~~: there is no GET route under `/api/v1/queries` at all (only POST `test|run|schema`, `services.py:161-166`).
- ~~`QueryDescribe`, `DescribeVariable`, any describe response model~~: none exists.
- ~~`outputs/writers/describe.py` `DescribeWriter`~~: commented out and unrelated.
- ~~`queries/multi/_introspect.py` `describe_class`~~: introspects components, not slugs.
- ~~`QueryHandler.columns` (v3) returning data~~: `handlers/multi.py:101-107` always raises 204 "No Columns available".
- ~~A "system slug" over the queries table~~: none; `troc.queries` is referenced nowhere.
- ~~`aiohttp_swagger` / generated OpenAPI~~: absent.

**Access control and caching**
- ~~SQL pre-filtering by `program_slug`, session programs or groups~~: none in any handler.
- ~~PBAC in `QueryManager`~~: it performs no auth or PBAC checks.
- ~~`slug:describe`, `slug:describe_raw` actions~~: not in policies or code (added by M4). `slug:read` is not granted in `defaults.yaml`.
- ~~A cache of slug definitions~~: only results are cached. `get_query_slug` hits the DB every call (`interfaces/connections.py:444-490`).
- ~~Guardian sessionless support~~: `Guardian.filter_resources` requires `is_authenticated` (`guardian.py:209-268`).
- ~~`QS_DESCRIBE_MAX_SCAN`, `QS_DESCRIBE_ADMIN_GROUPS`, `QS_DESCRIBE_COLUMNS_TIMEOUT`~~: not in `conf.py` (added by M4).

**Pagination helpers**
- ~~Bound parameters (`$n`) in `build_where_clause`~~: it emits literal-quoted predicates only.
- ~~`NULLS LAST` in `build_order_by`~~: not emitted.
- ~~`compose_where`, `build_scan_sql`~~: not in `_pagination.py` (added by M4).

**QS and providers**
- ~~`QS.get_definition()`~~: only `BaseProvider.get_definition()` (`providers/abstract.py:128`) exists; use `qs.get_source().get_definition()`.
  - Note: `handlers/service.py:408` calls `query.get_definition()` on a `QS`. That is a latent `AttributeError` in the declared-columns fallback; it is out of scope, and the new handler must not copy it.
- ~~`BaseProvider.columns()` raising `NotImplementedError`~~: it delegates to `self._qs` and returns `self._columns`. The brainstorm statement was inaccurate; corrected here.
- ~~`BaseProvider.describe_columns()`, `BaseProvider._udf_resolved_conditions()`~~: do not exist yet.

**Validators and keywords**
- ~~Python access to `UDF_LIST` / `PG_CONSTANTS` / `PG_UDF`~~: they are `cdef list` module globals, invisible from Python. `import querysource.types.validators as v; hasattr(v, 'UDF_LIST')` → `False` (verified at runtime).
- ~~A working environment override of `UDF_LIST`~~: `os.environ.get` returns `str`, which is assigned to a `cdef list`.
- ~~`querysource/types/validators.pxd`~~: does not exist. New `cpdef` functions are reached through Python import, not `cimport`.
- ~~Keyword resolution in Rust~~: Rust `is_valid` never calls a UDF; `rust/src/validators.rs` is **not** modified by this feature.
- ~~`{today}` / `{today-7}` / `{fdom}` placeholder expressions~~: date keywords are condition **values** only.
- ~~Docstrings on `utils/functions.pyx` date helpers~~: none exist, so the vocabulary is a curated registry.
- ~~`:describe` / `:meta` suffix on `/api/v2/services/queries/{slug}`~~: the `:` suffix there is the output format.

**FEAT-176**
- ~~FEAT-176 tenant registry, `(schema, table)` resolver, tenant loader or tenant `QS`~~: none exists yet. The proposal is in review, and no spec is in `sdd/specs/`. Module 8's contract is **unverified**.

---

## 7. Implementation Notes & Constraints

> Architecture decisions stay with the thinking model. A delegated implementation may only express decisions already recorded here and in the task's implementation blocks.

### Patterns to Follow

- **Handler style.** Follow the function-style `AbstractHandler` pattern (`QueryExecutor`, `QueryService`): one instance, bound methods registered with `add_get`. Pass `request` explicitly, because `AbstractHandler.post_init` does not set `self.request`.
- **EvalContext.** Mirror `_enforce_pbac` (abstract.py:317-449) for `EvalContext` construction, lazy navigator-auth imports and the coroutine guard. Do **not** modify `_enforce_pbac`.
- **List filtering.** Use the listing pattern of `DatasourceView._pbac_filter` (`datasources/handlers/datasource.py:190`), with the opposite failure policy: **fail closed**.
- **Redaction.** Use the pattern of `_redact_datasource` (`datasource.py:48`), but omit fields and report them in `redacted`.
- **Store addressing.** `QueryModel.Meta` is never mutated; the concurrency regression is covered by `tests/test_queryslug_concurrency.py`. Always address the table through `QueryStore(schema, table)` and quoted identifiers validated by `_validate_bare_identifier`.
- **Placeholder grammar.** Keep extraction consistent with the Rust substitution grammar (`safe_dict.rs:116-120`).
- **Logging and conventions.** Use `self.logger` / `logging.getLogger(__name__)`, Google docstrings, strict type hints and Pydantic v2 models.
- **Cython rebuild.** After editing `types/validators.pyx`, run `make build-inplace` before tests. Rust needs no rebuild.
- **Dependency order.** Tests for M1 must run against the rebuilt extension; the task's verification commands must include the rebuild.

### Known Risks / Gotchas

**UDF resolution (M1)**
- **Resolution moves from "never" to "always" on the raw-query path.** Slugs that relied on a literal `'TODAY'` string reaching the database will now receive a date. The brainstorm decision accepts this; it goes in the changelog.
- **Keyword formats differ.** `TODAY` resolves to `%m/%d/%Y` (e.g. `09/15/2026`), while `YESTERDAY`/`FDOM`/`LDOM`/`LAST_YEAR` resolve to `%Y-%m-%d`.
  - The vocabulary publishes the real example so clients can see it.
  - Changing masks is out of scope, because it would break existing parser-path behaviour.
- **Brainstorm corrections.** The brainstorm listed `LAST_YEAR` as integer and `CURRENT_MONTH` as string. The code returns a date string and an integer respectively (`functions.pyx:162,229`); the registry follows the code.
- **Rust `UDF_LIST` is static.** Under an environment override, Rust `is_udf` diverges from Cython's. The pre-resolution uses Cython's effective list, so resolved values are correct. A keyword *removed* by the override is simply not resolved and falls to Rust's default quoting, which matches Cython.

**Access control**
- **PBAC disabled means `query_raw` is visible** to any caller with a session and programs, because `slug:describe_raw` is a no-op allow. This matches `_enforce_pbac` semantics and the brainstorm's "ABAC steps are no-ops" decision. Document it. Admin fields stay gated, because that rule does not depend on PBAC.
- **In-memory pagination cost** scales with the pre-filtered set. The cap bounds it, but superuser/authz totals are truncated beyond 10000, and `X-Truncated` signals this.
- **Route shadowing.**
  - `/api/v1/{tenant}/queries/describe` (M8) versus FEAT-176's future `GET /api/v1/{tenant}/queries/{slug}`: a tenant slug literally named `describe` cannot be *executed* via GET on that path. FEAT-176 must register its execution route after these, or reserve the name.
  - A tenant named `queries` would be shadowed by the legacy detail route. Reserved-name policy belongs to FEAT-176, and M8 tests the resolution.

**Columns route**
- **`/columns` does touch the datasource.** `QS.build_provider()` loads the definition again and prepares on the slug's datasource. The timeout bounds the wait, and failures degrade to `declared`/`unavailable`, never a 5xx, except when the definition vanishes (`404`).
- **`prepare` needs a complete statement.** Variables without defaults block it; callers may pass them as query-string conditions.
- **Non-pg providers** return untyped names from `columns()` where implemented. `columns_source` is still `"prepare"` when the provider yields names, and `type` is `null`.

**Integration and existing code**
- **FEAT-176 dependency (M8).** Its interfaces are not specified yet. M8 must be re-planned against FEAT-176's spec before implementation and must not invent a registry API. The non-tenant routes (M1–M7, M9) do not wait.
- **Test fixtures.** `FakeConn.fetch_all(sql)` in `tests/handlers/conftest.py` takes no `*args`. Extend the fake so it accepts and records `*args`, keeping existing `QueryManager` tests working.
- **Latent bug at `handlers/service.py:408`.** `QS.get_definition` is missing. Do not replicate it in the new handler; the fix itself is out of scope.

### External Dependencies

| Package | Version | Reason |
|---|---|---|
| `aiohttp` | existing | routes / `web.Response` |
| `navigator-api` | existing | `BaseHandler` helpers (`json_response`, `no_content`, `error`, `query_parameters`) |
| `navigator-auth` | existing (with `ResourceType.SLUG`) | `EvalContext`, `Environment`, `PolicyEvaluator.check_access` / `filter_resources`, `AUTHZ_BACKEND_KEY`, `AUTH_SESSION_OBJECT` |
| `navigator-session` | existing | `get_session` via `_get_user_session` |
| `asyncdb` (pg / asyncpg) | existing | `fetch_all` / `fetch_one` with bound args; `prepare` → `get_attributes()` |
| `pydantic` | v2, existing | response / grant / registry models |
| stdlib `string.Formatter`, `json`, `re`, `asyncio` | — | placeholder extraction, JSON dialect detection, timeout |

No new third-party dependency.

---

## Worktree Strategy

- **Default isolation unit:** `per-spec`. All tasks run sequentially in one worktree, `.claude/worktrees/feat-FEAT-148-describe-queryslug`.
- **Task order:**
  1. M1: UDF fix (first, per brainstorm decision)
  2. M4: pagination helpers, config, policies
  3. M2: describer
  4. M3: visibility
  5. M5: typed columns
  6. M6: vocabulary
  7. M7: handler and routes
  8. M9: docs, changelog, version
  9. M8: tenant routes (last)
- **Parallelizable in principle:** M2, M4, M5 and M6 touch disjoint files, and M3 only reads M4's config keys. Parallel worktrees are **not** recommended: the strands are small, and M7 integrates all of them.
- **Cross-feature dependencies:**
  - **FEAT-176 `per-tenant-queries`** is a hard dependency for **M8 only**. Its data-access layer must be merged to `dev` first, and M8 must be re-planned against FEAT-176's spec. If FEAT-176 is not ready when M1–M7 and M9 are done, M8 may be split into a follow-up so the non-tenant endpoints can ship. That call belongs to the author (§8).
  - FEAT-176 will also edit `services.py` and `_pagination.py`. Expect rebase conflicts; keep M4's `_pagination.py` edits additive.
  - No pending tasks in `sdd/tasks/index/` touch `types/validators.pyx` or `providers/*`.

---

## 8. Open Questions

### Resolved in brainstorm

- [x] Flow type and base branch — *Resolved in brainstorm*: `feature`, base `dev`.
- [x] Detail URL shape — *Resolved in brainstorm*: `GET /api/v1/queries/{slug}/describe` (list stays at `GET /api/v1/queries/describe`).
- [x] New handler vs extending `QueryManager` — *Resolved in brainstorm*: new read-only handler; `QueryManager` untouched.
- [x] Meaning of "pre-filtering based on permissions" — *Resolved in brainstorm*: SQL pre-filter by session `programs`; superuser sees all; `program_slug='default'` always included for users with programs; user without programs sees nothing (fail-closed); sessionless authz skips the pre-filter and relies on ABAC.
- [x] Detail payload scope — *Resolved in brainstorm*: full `QueryModel` + derived metadata (query_raw variables, effective cond_definition, query capabilities); no source-schema introspection, no cache/DWH derived block.
- [x] Redaction — *Resolved in brainstorm*: `query_raw` gated by extra permission; `dwh_info`/`cache_options` and `created_by`/`updated_by` hidden from non-admins; fields omitted, with a `redacted` list.
- [x] List pagination strategy — *Resolved in brainstorm*: pre-filter SQL → ABAC batch → paginate in memory (exact totals).
- [x] PBAC actions and denial semantics — *Resolved in brainstorm*: `slug:list` (list), `slug:describe` (detail), `slug:describe_raw` (query_raw); detail deny → 404.
- [x] Does execute imply visibility? — *Resolved in brainstorm*: yes, list = `slug:list OR slug:execute`, detail = `slug:describe OR slug:execute`.
- [x] List parameters — *Resolved in brainstorm*: page/page_size/sort/search + equality filters (provider, program_slug) reusing `_pagination`.
- [x] Caching — *Resolved in brainstorm*: no cache; always read from DB.
- [x] Typed columns: inside describe, or never touching the datasource? — *Resolved in brainstorm*: neither; a sibling sub-resource `GET /api/v1/queries/{slug}/columns` (prepared statement + declared fallback, never executes). Describe stays pure over `QueryModel`.
- [x] Where the relative-date keyword vocabulary lives — *Resolved in brainstorm*: its own endpoint `GET /api/v1/queries/vocabulary`, referenced from describe via `derived.links`.
- [x] Inconsistent UDF resolution for `date`-typed conditions — *Resolved in brainstorm*: initially "separate hotfix on `main`"; revised the same day to ship inside `4.6.0` as the first task of this feature.
- [x] Sampling rows (`querylimit=1`) to infer column types on providers without `prepare` — *Resolved in brainstorm*: out of scope for v1.
- [x] PBAC for the new sub-resources — *Resolved in brainstorm*: `/columns` under `slug:describe OR slug:execute` exactly like detail; `/vocabulary` requires authentication only (no slug resource, no PBAC action).
- [x] Should `/columns` accept query-string conditions? — *Resolved in brainstorm*: yes, same merge rules as `get_columns` (`{**json_body, **query_params}`), validated through `cond_definition` by the Rust substitution.
- [x] Which `functions.pyx` helpers enter the curated `functions` block in v1? — *Resolved in brainstorm*: keywords plus a minimal, informational set (`date_diff`, `date_sum`, `days_ago`, `previous_month`, `fdow`, `ldow`) with documented args; each entry carries `invocable: false`.
- [x] Does the UDF fix ship as `4.5.17` before `4.6.0`, or inside `4.6.0`? — *Resolved in brainstorm*: inside `4.6.0`, as the first task of this feature's branch.
- [x] Does the program pre-filter also apply to the detail endpoint? — *Resolved in brainstorm*: yes, same as the list; a slug outside the caller's programs is 404 even if ABAC would allow it. Superuser and sessionless authz stay unfiltered.
- [x] What defines "admin" for `dwh_info`/`cache_options`/`created_by`/`updated_by`? — *Resolved in brainstorm*: `userinfo.superuser` OR membership in an admin group (`QS_DESCRIBE_ADMIN_GROUPS`, default `admin,superuser`); no new PBAC action.
- [x] Behaviour with no session and no sessionless authz — *Resolved in brainstorm*: deny, fail-closed: `401` on list, detail and columns. `QueryManager` remains the unauthenticated admin path.
- [x] Should `query_raw` also be visible to callers holding `raw_query:execute`? — *Resolved in brainstorm*: no; only `slug:describe_raw` reveals `query_raw`.
- [x] Cap for in-memory pagination scans — *Resolved in brainstorm*: `QS_DESCRIBE_MAX_SCAN`, default `10000`; when exceeded, process the first N rows in SQL order, set `X-Truncated: true`, log a warning. Never `400`.
- [x] Program matching case-sensitivity and `Program` object normalization — *Resolved in brainstorm*: normalise both sides to lowercase; Token-backend `Program` objects map to `.slug`, falling back to `.name` / `str()`.
- [x] Coordination with FEAT-176 — *Resolved in brainstorm*: include the tenant variant now (`/{tenant}/queries/describe`, `/{tenant}/queries/{slug}/describe`, `/{tenant}/queries/{slug}/columns`), served by the same handler over FEAT-176's `(schema, table)` data access; FEAT-176's data-access layer is a hard dependency.
- [x] `policies/defaults.yaml` grant for `slug:describe` — *Resolved in brainstorm*: admins/superuser only; regular users describe exactly the slugs they may execute (the `execute` fallback).

### Still open (non-blocking)

The spec adopts the stated default for each of these until the author overrides it.

- [ ] **Vocabulary for sessionless authz.** Does "authenticated only" admit sessionless authz principals? *Spec default:* yes. Any principal other than `NONE` gets `200`, consistent with the other describe routes admitting authz. — *Owner: Jesús Lara*
- [ ] **UDF fix mechanism.** Is pre-resolution in Python/Cython before the Rust substitution acceptable, instead of making Rust call back into Python? *Spec default:* pre-resolution (M1), with no Rust change or rebuild and one Cython resolution function shared by all providers. — *Owner: Jesús Lara*
- [ ] **Splitting M8.** If FEAT-176's data-access layer is not merged when M1–M7 and M9 are complete, does M8 split into a follow-up so `4.6.0` ships the non-tenant endpoints? *Spec default:* M8 stays in this feature as the last task, blocked until FEAT-176 lands. — *Owner: Jesús Lara*
- [ ] **FEAT-176 reserved tenant names.** Must FEAT-176's reserved-name list include `queries` and `describe` (as a slug on GET) because of the route shadowing described in §7? — *Owner: FEAT-176 author (Jesús Lara)*

---

## 9. Design Research Cross-Check

> Model: `gpt-5.6-luna` · Status: skipped (exploration document status is `exploration`, not `accepted`) · Transcript: none

| # | Suggestion (kind) | Disposition | Reason | Landed in |
|---|---|---|---|---|

Summary: **0** confirmed · **0** rejected · **0** escalated.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-09-15 | Jesús Lara / Claude | Initial draft from `describe-queryslug.brainstorm.md` (Option A, all brainstorm questions resolved); codebase contract re-verified; UDF divergence reproduced at runtime |
