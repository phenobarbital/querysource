---
# SDD flow type and base branch (FEAT-145).
# - type: feature  (default)  → base_branch: the base_branch in sdd/config.json (or any non-hotfix branch)
# - type: hotfix              → base_branch MUST be the hotfix_base in sdd/config.json
type: feature
base_branch: dev
# projects: parts of the codebase this doc concerns. Use the project areas of your
#   repository (`sdd/CONVENTIONS.md` § Project areas, or the `projects` list in
#   `sdd/config.json`). Unknown values warn, not fail.
projects: [parsers, providers, queries, auth, models]
# tags: free-form kebab-case keywords for organizing specs (e.g. memory, mcp).
tags: [pbac, user-filtering, row-level-security, parser, program-restriction, session]
---

# Feature Specification: PBAC-based per-user automatic filtering (`user_filtering`)

**Feature ID**: FEAT-184
**Date**: 2026-11-03
**Author**: Jesus Lara / Claude (`/sdd-spec`)
**Status**: approved
**Target version**: 5.2.7 (deprecated `h_filtering` alias removed in 5.3.0)
**Exploration**: `sdd/proposals/pbac-based-per-user-filter.brainstorm.md` (Recommended Option A)

---

## 1. Motivation & Business Requirements

### Problem Statement

QuerySource slugs are shared definitions: every caller that may execute a slug
sees the same rows. The only per-user narrowing available today is the
*legacy* "hierarchical filter" (`hfilter`): a `bool` column `h_filtering` on
`QueryModel` / `TenantQueryDefinition` that is read by nothing (only
`describe.py:267` echoes it back), plus the `QUERYSOURCE_FILTERS` registry
(`qry_options`, `grouping_set`, `group_by_child` in
`querysource/libs/functions/__init__.py`) that mutates the WHERE/GROUP BY from
*request conditions*, never from the **user's identity**.

Consequences:

- A slug such as `SELECT * FROM epson.users` is visible to any authenticated
  caller allowed to execute the slug, regardless of whether the user belongs
  to the `epson` program. PBAC (`slug:execute`) is all-or-nothing per slug; it
  cannot say "run it, but only your rows".
- Per-user narrowing is reimplemented ad hoc by clients (passing `user_id` as
  a filter), which is trivially spoofable: the caller chooses the value.
- The semantic layer already has governance row filters
  (`GovernanceBlock.row_filters`, `governance_filters()`), but they are
  role-keyed and semantic-only; plain SQL slugs have nothing equivalent.

**Who is affected**: end users of multi-tenant/multi-program deployments (data
isolation), slug authors (declarative, non-spoofable per-user filters),
ops/security (fail-closed guarantees and auditability).

**Why now**: PBAC (FEAT-182, FEAT-091, FEAT-150, FEAT-175 M5) landed the
identity plumbing (`QSPrincipal`, `principal_from_session()`), so the missing
piece is the *row-level* enforcement that consumes it. `h_filtering` is dead
weight and is replaced rather than revived.

### Goals

- G1. A query definition can declare, in a typed `user_filter_options`
  document, which query columns are constrained by which identity attributes;
  the predicate is rendered **inside the parser** (`AbstractParser`
  subclasses), through the existing typed/escaped `where_cond` path.
- G2. A definition can be **restricted by program**: execution is denied
  unless `definition.program_slug` is among the caller's programs, with
  `'default'` always allowed.
- G3. Identity comes from `QSPrincipal` first and from the raw navigator-auth
  session userinfo as fallback; `QS` resolves it **once** and passes it to the
  parser through `BaseProvider`. The parser never touches `aiohttp` or
  `navigator_auth`.
- G4. **Fail-closed everywhere**: no principal/session → 403; required
  attribute missing/`None`/empty → 403; program restriction not met → 403;
  `user_filtering=True` on a parser-less or raw execution path → 403 before
  any connection is opened; invalid or empty options with the switch on →
  500 configuration error. `QS_PBAC_ENABLED` does **not** gate this feature.
- G5. `superuser` and a per-definition `bypass` list (groups/roles) skip both
  the column rules and the program check, with an audit log line.
- G6. The user predicate always overrides a caller-supplied filter on the same
  column, and the rendered predicate is part of the result-cache key
  (`BaseProvider.checksum()` hashes the rendered query, so this holds without
  new machinery).
- G7. The rule compiler is a **pure Python module** (`querysource/auth/user_filter.py`)
  with no parser or aiohttp dependency, unit-testable without Cython and
  reusable by the multi-query follow-up and by `describe`.
- G8. `h_filtering` → `user_filtering` rename at the API level, with
  `h_filtering` kept as a deprecated alias for one minor release.

### Non-Goals (explicitly out of scope)

- Applying the rules in `MultiQS` (multi-query). Explicit follow-up feature;
  this spec only guarantees that `compile_user_filter()` is callable from
  `querysource/queries/multi/` without importing the parser.
- Inferring the program from the SQL schema name (`epson.users` → `epson`).
  Program restriction uses `definition.program_slug` only (resolved in
  brainstorm). Tenant stores already derive `program_slug` from the store
  schema (`querysource/repositories/definitions.py:178`), which covers the
  `epson.users` case.
- Validating rule columns against the slug's declared columns at save time
  (open question Q3; the database raises at execution).
- PostgreSQL Row-Level Security push-down, `QS`-level injection into
  `conditions`, and a legacy `QS_FILTERS` plugin were rejected in the
  brainstorm (Options D, B, C) — see `sdd/proposals/pbac-based-per-user-filter.brainstorm.md`.
- Removing the legacy `QUERYSOURCE_FILTERS` registry; it stays untouched.

---

## Governing Decisions

<!-- GENERATED by `parrot sdd spec consequences <spec> --render` — do not edit by hand.
     Lists every accepted decision governing this spec's subsystems and targets (FEAT-784).
     Unconfigured repositories render one line: "decision audit not configured in this repository". -->

---

## 2. Architectural Design

### Overview

**Option A — parser-native `user_filter` step fed by a pure rule-compiler
module.** A new plain-Python module `querysource/auth/user_filter.py` owns the
rules: it validates `user_filter_options` into a typed `UserFilterOptions`
model (`extra="forbid"`), resolves each rule's dotted attribute path against
`QSPrincipal` first and the raw session userinfo second, decides bypass,
performs the program check, and returns a `UserFilterResult` whose
`predicates` dict is ready to be merged into the parser's `filter`. Every
denial is raised as the new 403-class exception `UserFilterDenied`
(`querysource/exceptions.py`); every invalid/empty options document is a
`ConfigError` (500).

`AbstractParser` gains two public attributes (`principal`, `userinfo`) set
from constructor kwargs, a public `user_filter_applied` tuple for logging, and
one new extraction step `_user_filter_sync()` placed **after**
`_qs_filters_sync()` inside `_extract_options()`. When
`definition.user_filtering` is true the step calls the compiler and replaces
`self.filter` with a **new dict** `{**self.filter, **predicates}` (never an
in-place `update`, because `_query_filter_sync` may have aliased
`self.filter` to the cached `definition.filtering` object). The predicate is
then rendered by the untouched `filtering_options()` / `filter_conditions()`
pipeline of every dialect parser (scalars → `=`, lists → `IN (...)`).

`QS.build_provider()` resolves the identity once: the explicit `principal`
kwarg (programmatic callers) or `principal_from_session()` over the cached
`request['user_session']`, plus the raw userinfo dict from the same session.
Both travel in the provider `args` next to the existing `"principal"` entry.
`BaseProvider` stores them as `_user_principal` / `_user_info` (the name
`_principal` is already owned by `semanticProvider`,
`querysource/providers/semantic.py:44`), forwards them to the parser
constructor, and performs the **fail-closed guard**: a definition with
`user_filtering=True` whose provider has `__parser__ = None` raises
`UserFilterDenied(reason_code="parser_required")` in `__init__`;
`sqlProvider.prepare_connection()` raises the same before its two early
returns (`use_parser` false for `qstype='query'`, and `is_raw`).

**Resolved decisions carried from the brainstorm (binding):**

- Enforcement inside the parser (`.pyx`), rules in a pure Python module.
- Identity: `QSPrincipal` first, raw session userinfo as fallback.
- Naming: `user_filtering` (bool) + `user_filter_options` (jsonb);
  `h_filtering` stays as a deprecated alias for one minor release.
- Program determination: `definition.program_slug` only; `'default'` always
  allowed.
- Parser-less / raw paths: fail-closed, never execute unfiltered.
- Missing attribute → 403; superuser bypasses; `bypass` configurable per
  definition with `["superuser"]` as the implicit default.
- Rule syntax: Option B, typed rule list.
- Multi-query: follow-up; compiler designed for reuse.

**Refinements decided in this spec (research-driven, within the resolved
answers):**

- **The alias is a real model field, and the migration is ADD-only.** Both
  `QueryModel` and `TenantQueryDefinition` reject unknown keyword arguments
  (`TypeError`, verified by construction — `querysource/tenant_models.py:30-33`
  and a live probe), and the tenant repository validates every row with
  `TenantQueryDefinition(**data)` (`querysource/repositories/definitions.py:159`).
  Renaming the column while dropping the field would make every row of an
  un-migrated store unreadable. Therefore: `h_filtering` remains declared on
  both models (deprecated, never consulted), the SQL migration only **adds**
  `user_filtering` and `user_filter_options`, and the `h_filtering` column
  is dropped together with the field in 5.3.0.
- `h_filtering=True` on legacy rows does **not** enable user filtering (it
  has no rules; auto-enabling it would 500 every such slug).
- Exception propagation from Cython: with Cython ≥ 3.0 (`pyproject.toml:5`
  pins `Cython==3.0.11`; 3.3.1 installed) `cdef void` functions propagate
  Python exceptions by default. The new step is nevertheless declared
  `cpdef void _user_filter_sync(self) except *` — `cpdef` so parser tests can
  invoke it without `set_options()`/Redis, `except *` to make propagation
  explicit — and the `.pxd` carries the identical declaration.
- `compile_user_filter()` **raises** `UserFilterDenied` itself (one deny
  site, fail-closed by default) and returns `UserFilterResult` only for
  allow/bypass; callers never have to remember to raise.
- The parser imports the compiler **lazily** inside the step
  (`from ..auth.user_filter import compile_user_filter`), mirroring
  `qs.py:217-218`, because `querysource.auth.slug_visibility` imports
  `..queries.describe` and `..models` (`slug_visibility.py:18-19`) and a
  module-level import from the parser would close an import cycle. For the
  same reason `user_filter.py` imports only `..auth.principal` and
  `..exceptions` and reimplements the three-line program normalization
  (lower/strip over str, dict `slug`/`name`, or objects with `.slug`)
  instead of importing `normalize_programs`.
- Authz-form principals (`QSPrincipal.is_authz`): their typed claims other
  than `groups`/`roles` are treated as **absent**, so every `required` rule
  denies (`reason_code="authz_principal"`) and the program check denies
  unless `allow_default` applies. This is the brainstorm's intended behavior
  stated precisely.
- Rule `column` must match `^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$`
  (optional `alias.column`); anything else is a `ConfigError`. Definition
  authors are trusted, but the column name is spliced into SQL by
  `filter_conditions()` and a grammar check costs nothing.
- Legacy `QS_FILTERS` plugins (`sql.pyx:445-451`) run **after** the step and
  merge their own result over `self.filter`; they are deployment-trusted
  server code, not caller input, so a plugin overwriting a rule column is a
  deployment bug (Known Risk in §7), not a spoofing vector.

### Component Diagram

```
aiohttp handler ──► QS.build_provider()
                      │  principal  = self._principal or principal_from_session(request['user_session'])
                      │  userinfo   = session_userinfo(request['user_session'])          (M4)
                      ▼
                  BaseProvider.__init__(…, principal=…, userinfo=…)                      (M4)
                      │  guard: definition.user_filtering and __parser__ is None → UserFilterDenied(403)
                      │  self._parser = __parser__(query=…, definition=…, conditions=…, principal=…, userinfo=…)
                      ▼
                  sqlProvider.prepare_connection()                                       (M4)
                      │  guard: user_filtering and (not use_parser or is_raw) → UserFilterDenied(403)
                      ▼
                  AbstractParser.set_options() ──► _extract_options()
                      │     … _query_filter_sync() → _qs_filters_sync() → _user_filter_sync()   (M3)
                      │                                                       │
                      │                                                       ▼
                      │                         querysource.auth.user_filter.compile_user_filter()   (M1)
                      │                             parse options ─► bypass? ─► program check ─► rules
                      │                             raises UserFilterDenied / ConfigError, else UserFilterResult
                      │  self.filter = {**self.filter, **result.predicates}
                      ▼
                  <dialect>.build_query() → filter_conditions()  (unchanged rendering: = / IN)
                      ▼
                  BaseProvider.checksum() = hash(rendered query)   → per-user cache partition (unchanged)

describe_slug() ──► capabilities.user_filtering (+ h_filtering alias); user_filter_options admin-only (M2)
QueryModel / TenantQueryDefinition: + user_filtering, + user_filter_options, h_filtering deprecated (M2)
```

### Integration Points

| Existing Component | Integration Type | Notes |
|---|---|---|
| `querysource/models.py` `QueryModel` | modifies | adds `user_filtering: bool`, `user_filter_options: Optional[dict]` (jsonb); `h_filtering` kept, comment marks it deprecated |
| `querysource/tenant_models.py` `TenantQueryDefinition` | modifies | same two fields; repository projection picks them up automatically (`definitions.py:131-136`) |
| DB schema (`QS_QUERIES_SCHEMA.QS_QUERIES_TABLE`, every tenant `queries` table) | migration (ADD-only) | `docs/sql/user_filtering.sql` (location is open question Q1); un-migrated stores read as `user_filtering=False` |
| `querysource/exceptions.py` | extends | new `UserFilterDenied(QueryException)`, `default_code = 403`, generic message, `reason_code` attribute |
| `querysource/auth/user_filter.py` (new) | adds | typed options model, attribute resolution, bypass, program check, compiler, `session_userinfo()` |
| `querysource/parsers/abstract.pyx` / `abstract.pxd` | modifies | `principal`, `userinfo`, `user_filter_applied` attrs; `_user_filter_sync` step after `_qs_filters_sync` |
| `querysource/parsers/sql.pyx`, `pgsql.pyx`, `sosql.pyx`, `cql.pyx` | depends on (no change) | they render `self.filter`; their `__init__(self, *args, **kwargs)` forward the new kwargs to `AbstractParser.__cinit__` |
| `querysource/providers/abstract.py` `BaseProvider` | modifies | pops `principal`/`userinfo` kwargs, forwards them to the parser, parser-less guard |
| `querysource/providers/sql.py` `sqlProvider` | modifies | guard before the two early `return self` in `prepare_connection()` |
| `querysource/queries/qs.py` `QS` | modifies | resolves principal + userinfo once; adds `"userinfo"` to provider args; adds `UserFilterDenied` to the re-raise tuple |
| `querysource/queries/describe.py` | modifies | `capabilities.user_filtering`, `h_filtering` alias, `user_filter_options` in `ADMIN_FIELDS` |
| `tests/tenants/test_query_move.py` `_FULL_COLUMNS` | modifies | test DDL gains the two columns so the move contract covers them |
| `querysource/queries/multi/__init__.py` `MultiQS` | untouched (follow-up) | will call `compile_user_filter()` later |
| `querysource/libs/functions/__init__.py`, `services.py` `QUERYSOURCE_FILTERS` | untouched | legacy plugin registry stays |
| `docs/USER_FILTERING.md` (new), `docs/DESCRIBE_API.md`, `docs/PBAC_PROGRAMMATIC.md`, `CHANGES.rst` | adds / modifies | user guide, describe payload, programmatic callers, changelog entry |

### Data Models

```python
# querysource/auth/user_filter.py  (new) — Pydantic, same style as querysource/semantic/models.py:116 and queries/describe.py:54
class UserFilterRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    column: str                                 # ^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$
    attribute: str                              # dotted path: principal claim first, then session userinfo
    operator: Literal["eq", "in"] = "eq"        # "in" implied when the resolved value is a list
    required: bool = True                       # True → missing/None/empty denies (403); False → rule skipped


class ProgramRestriction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    restricted: bool = False
    allow_default: bool = True                  # program_slug == 'default' always passes
    attribute: str = "programs"                 # identity attribute holding the program list


class UserFilterOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    rules: tuple[UserFilterRule, ...] = ()
    program: ProgramRestriction = ProgramRestriction()
    bypass: tuple[str, ...] = ("superuser",)    # "superuser" literal, or group/role names (case-insensitive)
    # model validator: `rules` non-empty OR `program.restricted` — otherwise ValueError
    #   ("user_filtering is on but user_filter_options declares nothing"), surfaced as ConfigError.


@dataclass(frozen=True)
class UserFilterResult:
    predicates: dict[str, Any]                  # column → scalar (=) or list (IN); {} when bypassed
    applied: tuple[str, ...]                    # columns actually constrained (for logs)
    bypassed: bool = False
    bypass_reason: str | None = None            # "superuser" | "group:<name>" | "role:<name>"
```

Persisted document (jsonb) example — the brainstorm's user-selected syntax:

```json
{
  "rules": [
    {"column": "user_id", "attribute": "user_id"},
    {"column": "store_id", "attribute": "attributes.store_id", "operator": "in", "required": true}
  ],
  "program": {"restricted": true, "allow_default": true},
  "bypass": ["superuser"]
}
```

| Field | Meaning | Default |
|---|---|---|
| `rules[].column` | Column (optionally `alias.column`) of the query to constrain | required |
| `rules[].attribute` | Dotted path into the identity: typed `QSPrincipal` claims first, then raw session userinfo | required |
| `rules[].operator` | `eq` / `in` (`in` implied when the value is a list; `in` + scalar → one-element list) | `eq` |
| `rules[].required` | `true` → missing/`None`/empty-list attribute denies with 403; `false` → rule skipped | `true` |
| `program.restricted` | Deny unless `definition.program_slug ∈ programs` | `false` |
| `program.allow_default` | `program_slug == 'default'` always passes | `true` |
| `program.attribute` | Identity attribute holding the program list | `programs` |
| `bypass` | Groups/roles (case-insensitive) or the literal `superuser` that skip **all** rules and the program check | `["superuser"]` |

Reason codes (`UserFilterDenied.reason_code`, logs only, never in the HTTP
body): `no_principal`, `authz_principal`, `missing_attribute`,
`program_restricted`, `parser_required`.

### New Public Interfaces

```python
# querysource/exceptions.py
class UserFilterDenied(QueryException):           # default_code = 403; message always "Access denied."
    reason_code: str
    slug: str | None

# querysource/auth/user_filter.py
def parse_user_filter_options(raw: Mapping[str, Any] | UserFilterOptions | None) -> UserFilterOptions
def session_userinfo(session: Any | None) -> dict[str, Any]
def resolve_attribute(path: str, principal: QSPrincipal | None, userinfo: Mapping[str, Any] | None) -> Any
def compile_user_filter(
    options: Mapping[str, Any] | UserFilterOptions | None, *,
    program_slug: str | None, principal: QSPrincipal | None,
    userinfo: Mapping[str, Any] | None, slug: str | None = None,
    logger: logging.Logger | None = None,
) -> UserFilterResult

# querysource/parsers/abstract.pyx (public attributes, readable from Python)
AbstractParser.principal: object | None
AbstractParser.userinfo: object | None
AbstractParser.user_filter_applied: tuple[str, ...]
AbstractParser._user_filter_sync() -> None        # cpdef; raises UserFilterDenied / ConfigError

# Definition columns (QueryModel, TenantQueryDefinition, DB)
user_filtering: bool = False
user_filter_options: dict = {}

# describe payload
derived.capabilities.user_filtering: bool          # h_filtering kept as alias (same value) until 5.3.0
user_filter_options                                # admin grant only (ADMIN_FIELDS)
```

---

## 3. Module Breakdown

> Define the discrete modules that will be implemented.
> These directly map to Task Artifacts in Phase 2.

#### Delegation-eligible modules

| Module | Eligible? | Decided patterns / exact contracts | Why not (if no) |
|---|---|---|---|
| M1: Exception + rule compiler | yes | `UserFilterDenied` shape, Pydantic models with `extra="forbid"`, reason codes, resolution order, bypass/program/rule algorithm, column grammar, imports limited to `..auth.principal` and `..exceptions` — all fixed in §2/§3 | — |
| M2: Model fields, migration, describe | yes | field names/defaults/`db_type`, ADD-only DDL at `docs/sql/user_filtering.sql`, `ADMIN_FIELDS` extension, capabilities keys, test DDL line | Q1 (migration location) is assumed, not blocking |
| M3: Parser step (`.pyx`/`.pxd`) | yes | attribute names, `cpdef void … except *` signature, placement after `_qs_filters_sync()`, new-dict merge, lazy import, rebuild via `make build-inplace` | — |
| M4: Provider guard + QS plumbing | yes | attribute names `_user_principal` / `_user_info`, guard sites, kwargs forwarded, re-raise tuple, `session_userinfo()` helper from M1 | — |
| M5: Documentation | yes | file list and sections fixed in §3 | — |

### Module 1: `UserFilterDenied` exception and rule compiler

- **Path**: `querysource/exceptions.py` (modify), `querysource/auth/user_filter.py` (new)
- **Responsibility**: the 403-class denial exception; validation of
  `user_filter_options`; identity attribute resolution; bypass, program check
  and rule compilation into parser-ready predicates; the session → userinfo
  helper used by `QS`. No parser, aiohttp or navigator_auth import.
- **Depends on**: `querysource/auth/principal.py` (`QSPrincipal`),
  `querysource/exceptions.py` (`QueryException`, `ConfigError`).
- **Interface Skeleton** *(signatures + docstrings only — bodies belong to task blueprints, FEAT-545)*:
  ```python
  # querysource/exceptions.py  (modifies querysource/exceptions.py:74 — inserted before `class DriverError`)
  class UserFilterDenied(QueryException):  # QueryException verified: querysource/exceptions.py:6
      """Per-user filtering refused to run the query (HTTP 403).

      The message is always the generic "Access denied." — never the rule,
      attribute or program that failed. ``reason_code`` carries the
      machine-readable cause for logs: ``no_principal``, ``authz_principal``,
      ``missing_attribute``, ``program_restricted`` or ``parser_required``.
      """

      default_code = 403

      def __init__(self, message: str | None = None, *, reason_code: str = "denied", slug: str | None = None) -> None:
          """Store ``reason_code`` and ``slug`` as attributes; pass ``message or "Access denied."`` and ``code=403`` to QueryException."""
  ```
  ```python
  # querysource/auth/user_filter.py  (new)
  """Per-user automatic filtering rules (user_filtering / user_filter_options).

  Pure module: imports only ``querysource.auth.principal`` and
  ``querysource.exceptions`` so the Cython parser can import it lazily
  without a cycle and the multi-query follow-up can reuse it unchanged.
  """
  REASON_NO_PRINCIPAL: Final[str] = "no_principal"
  REASON_AUTHZ_PRINCIPAL: Final[str] = "authz_principal"
  REASON_MISSING_ATTRIBUTE: Final[str] = "missing_attribute"
  REASON_PROGRAM_RESTRICTED: Final[str] = "program_restricted"
  REASON_PARSER_REQUIRED: Final[str] = "parser_required"
  SUPERUSER_BYPASS: Final[str] = "superuser"
  COLUMN_GRAMMAR: Final[re.Pattern[str]]   # ^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$

  class UserFilterRule(BaseModel): ...        # see §2 Data Models; field_validator on `column` (grammar) and `attribute` (non-blank)
  class ProgramRestriction(BaseModel): ...    # see §2 Data Models
  class UserFilterOptions(BaseModel): ...     # see §2 Data Models; model_validator: rules or program.restricted

  @dataclass(frozen=True)
  class UserFilterResult: ...                 # see §2 Data Models

  def parse_user_filter_options(raw: Mapping[str, Any] | UserFilterOptions | None) -> UserFilterOptions:
      """Validate the jsonb document into ``UserFilterOptions``.

      Raises ``ConfigError`` (500) when ``raw`` is None/empty, not a mapping,
      fails validation, or declares neither rules nor a program restriction.
      A ``UserFilterOptions`` instance is returned as-is.
      """

  def session_userinfo(session: Any | None) -> dict[str, Any]:
      """Return the raw navigator-auth userinfo dict of a session, or ``{}``.

      Mirrors ``principal_from_session()`` (querysource/semantic/governance.py:17):
      reads ``session[AUTH_SESSION_OBJECT]`` (fallback key ``"user"``) and
      returns ``{}`` for a missing session or a non-dict value.
      """

  def resolve_attribute(path: str, principal: QSPrincipal | None, userinfo: Mapping[str, Any] | None) -> Any:
      """Resolve a dotted attribute path against the identity.

      The first segment is looked up on the typed principal claims
      (``user_id``, ``username``, ``groups``, ``roles``, ``programs``,
      ``superuser``, ``tenant_id``) first — except for an authz-form principal,
      whose claims other than ``groups``/``roles`` count as absent — then on
      ``userinfo``; remaining segments index into mappings. Returns ``None``
      when any segment is missing. Tuples are returned as lists.
      """

  def bypass_reason(options: UserFilterOptions, principal: QSPrincipal | None) -> str | None:
      """Return ``"superuser"``, ``"group:<name>"`` or ``"role:<name>"`` when the principal matches ``options.bypass``; else ``None``. Never bypasses without a principal."""

  def check_program(options: UserFilterOptions, program_slug: str | None, principal: QSPrincipal | None, userinfo: Mapping[str, Any] | None, *, slug: str | None = None) -> None:
      """Enforce ``options.program``.

      No-op when ``restricted`` is False, or when ``allow_default`` and the
      (lower-cased) ``program_slug`` is ``'default'``. Otherwise resolves
      ``options.program.attribute``, normalizes it to lower-case slugs (str,
      dict ``slug``/``name``, or objects with ``.slug``) and raises
      ``UserFilterDenied(reason_code="program_restricted")`` unless
      ``program_slug`` is among them. A ``None`` program_slug is denied.
      """

  def compile_user_filter(
      options: Mapping[str, Any] | UserFilterOptions | None,
      *,
      program_slug: str | None,
      principal: QSPrincipal | None,
      userinfo: Mapping[str, Any] | None,
      slug: str | None = None,
      logger: logging.Logger | None = None,
  ) -> UserFilterResult:
      """Compile the definition's rules into parser predicates for one caller.

      Order: parse options (ConfigError) → no principal and empty userinfo →
      ``UserFilterDenied(no_principal)`` → bypass (INFO audit line, returns
      ``bypassed=True`` with no predicates) → ``check_program()`` → each rule:
      resolve the attribute; ``None`` or an empty list is *missing* → required
      rule raises ``UserFilterDenied(missing_attribute)`` (WARNING with slug,
      reason and ``principal.log_fields()``), optional rule is skipped; a list
      value (or ``operator="in"``) yields ``column → list`` (rendered IN), a
      scalar yields ``column → scalar`` (rendered =). Returns the predicates
      and the applied column names. Never mutates its inputs.
      """
  ```

### Module 2: Definition fields, migration and describe exposure

- **Path**: `querysource/models.py`, `querysource/tenant_models.py`,
  `docs/sql/user_filtering.sql` (new), `querysource/queries/describe.py`,
  `tests/tenants/test_query_move.py` (DDL fixture)
- **Responsibility**: declare `user_filtering` and `user_filter_options` on
  both definition models, keep `h_filtering` as a deprecated field, ship the
  ADD-only migration, and expose the switch (plus the alias) and the
  admin-only rules in the describe payload.
- **Depends on**: nothing from this spec (M1 is not imported here).
- **Interface Skeleton**:
  ```python
  # querysource/models.py  (modifies querysource/models.py:76)
  class QueryModel(Model):  # verified: querysource/models.py:49
      h_filtering: bool = Field(required=False, default=False, comment="DEPRECATED alias of user_filtering; column dropped in 5.3.0. Never consulted.")
      user_filtering: bool = Field(required=False, default=False, comment="Per-user automatic filtering compiled from user_filter_options (FEAT-184).")
      user_filter_options: Optional[dict] = Field(required=False, db_type='jsonb', default_factory=dict, comment="Typed rule list: {rules, program, bypass}.")

  # querysource/tenant_models.py  (modifies querysource/tenant_models.py:58) — identical three fields on TenantQueryDefinition  # verified: tenant_models.py:24
  ```
  ```sql
  -- docs/sql/user_filtering.sql  (new) — run against QS_QUERIES_SCHEMA.QS_QUERIES_TABLE (conf.py:372-373, default public.queries)
  -- and against every tenant `<schema>.queries` table. Idempotent, ADD-only.
  ALTER TABLE {schema}.{table} ADD COLUMN IF NOT EXISTS user_filtering boolean NOT NULL DEFAULT false;
  ALTER TABLE {schema}.{table} ADD COLUMN IF NOT EXISTS user_filter_options jsonb NOT NULL DEFAULT '{}'::jsonb;
  -- 5.3.0 (not part of this feature): ALTER TABLE {schema}.{table} DROP COLUMN IF EXISTS h_filtering;
  ```
  ```python
  # querysource/queries/describe.py  (modifies querysource/queries/describe.py:35 and :267)
  ADMIN_FIELDS: tuple[str, ...] = ("dwh_info", "dwh_scheduler", "cache_options", "created_by", "updated_by", "user_filter_options")  # verified: describe.py:35
  # inside describe_slug() capabilities (verified: describe.py:262-270):
  #   "user_filtering": bool(base.get("user_filtering") or False),
  #   "h_filtering": bool(base.get("user_filtering") or False),   # deprecated alias — same value, removed in 5.3.0
  ```
  ```python
  # tests/tenants/test_query_move.py  (modifies tests/tenants/test_query_move.py:40 — `_FULL_COLUMNS` gains
  #   `user_filtering boolean DEFAULT false, user_filter_options jsonb DEFAULT '{{}}',` after `h_filtering boolean DEFAULT false,`)
  ```

### Module 3: Parser step `_user_filter_sync`

- **Path**: `querysource/parsers/abstract.pyx`, `querysource/parsers/abstract.pxd`
- **Responsibility**: accept the identity kwargs, run the compiler after the
  legacy filter hook, merge predicates into `self.filter` as a new dict, and
  record the applied columns. Requires `make build-inplace` (Makefile:156)
  before tests see it.
- **Depends on**: Module 1 (`compile_user_filter`, `UserFilterDenied`,
  `ConfigError`), Module 2 (`definition.user_filtering`,
  `definition.user_filter_options` attribute names).
- **Interface Skeleton**:
  ```cython
  # querysource/parsers/abstract.pxd  (modifies abstract.pxd:41 and :81)
  cdef class AbstractParser:
      cdef public object principal              # QSPrincipal | None            (after `cdef public dict _query_filters`, pxd:41)
      cdef public object userinfo               # dict | None
      cdef public tuple user_filter_applied     # columns constrained by the last _user_filter_sync()
      cpdef void _user_filter_sync(self) except *   # after `cdef void _qs_filters_sync(self)`, pxd:81
  ```
  ```cython
  # querysource/parsers/abstract.pyx
  cdef class AbstractParser:                    # verified: abstract.pyx:36
      def __cinit__(self, *args, definition: object, conditions: object, query: str = None, **kwargs):  # verified: abstract.pyx:39
          # after `self.definition = definition if definition else None` (abstract.pyx:54):
          #   self.principal = kwargs.pop('principal', None)
          #   self.userinfo = kwargs.pop('userinfo', None)
          #   self.user_filter_applied = ()

      cdef void _extract_options(self):         # verified: abstract.pyx:159 — add `self._user_filter_sync()` right after `self._qs_filters_sync()` (abstract.pyx:174)

      cpdef void _user_filter_sync(self) except *:   # new, placed before `cdef void _col_definition_sync(self):` (abstract.pyx:345)
          """Merge the caller's compiled predicates into ``self.filter``.

          No-op unless ``self.definition.user_filtering`` is truthy. Lazily
          imports ``compile_user_filter`` (import-cycle guard, see §2) and
          calls it with ``self.definition.user_filter_options``,
          ``self.program_slug``, ``self.principal``, ``self.userinfo`` and
          ``self._slug``. On allow: ``self.filter = {**self.filter, **predicates}``
          (new dict — ``self.filter`` may alias ``definition.filtering``) and
          ``self.user_filter_applied = result.applied``; caller keys on the
          same columns are overwritten and logged at DEBUG. Propagates
          ``UserFilterDenied`` (403) and ``ConfigError`` (500) unchanged.
          """
  ```

### Module 4: Provider guard and `QS` identity plumbing

- **Path**: `querysource/providers/abstract.py`, `querysource/providers/sql.py`,
  `querysource/queries/qs.py`
- **Responsibility**: resolve principal + userinfo once per `QS` call, pass
  them to the provider and on to the parser, fail closed on parser-less and
  raw paths, and let `UserFilterDenied` surface with its own 403 code.
- **Depends on**: Module 1 (`UserFilterDenied`, `session_userinfo`),
  Module 3 (parser accepts the kwargs).
- **Interface Skeleton**:
  ```python
  # querysource/providers/abstract.py  (modifies providers/abstract.py:124-140)
  class BaseProvider(SourceHooksMixin, ABC):  # verified: providers/abstract.py:35
      def __init__(self, slug: str = '', query: Any = None, qstype: str = '', connection: Callable = None,
                   definition: Union[QueryModel, dict] = None, conditions: dict = None,
                   request: web.Request = None, **kwargs):  # verified: providers/abstract.py:55-64
          # before `## Parser Logic:` (providers/abstract.py:124):
          #   self._user_principal = kwargs.pop('principal', None)   # NOT `_principal`: owned by semanticProvider (providers/semantic.py:44)
          #   self._user_info = kwargs.pop('userinfo', None)
          # parser call (providers/abstract.py:128-133) gains `principal=self._user_principal, userinfo=self._user_info`
          # after the parser try/except (providers/abstract.py:140): `self._assert_user_filter_capable()`

      def user_filtering_required(self) -> bool:
          """True when ``self._definition`` declares ``user_filtering`` truthy (``getattr`` with False default; dict definitions use ``.get``)."""

      def _assert_user_filter_capable(self) -> None:
          """Raise ``UserFilterDenied(reason_code="parser_required", slug=self._slug)`` when filtering is required but ``self._parser`` is None; log at WARNING."""
  ```
  ```python
  # querysource/providers/sql.py  (modifies providers/sql.py:147-152)
  class sqlProvider(BaseProvider):
      async def prepare_connection(self) -> Callable:  # verified: providers/sql.py:138
          # before `return self` of the `not use_parser` branch (sql.py:148-151) and before `return self` of
          # `if self.is_raw is True:` (sql.py:152-153):
          #   if self.user_filtering_required(): raise UserFilterDenied(reason_code="parser_required", slug=self._slug)
  ```
  ```python
  # querysource/queries/qs.py  (modifies queries/qs.py:24-32 import block, :210-213, :303-313, :317)
  class QS(BaseQuery):  # verified: queries/qs.py:51
      async def build_provider(self):
          # after the `_pbac_session` extraction (qs.py:210-213):
          #   principal = self._principal
          #   if principal is None and _pbac_session is not None:
          #       from ..semantic.governance import principal_from_session   # lazy, like qs.py:217
          #       principal = principal_from_session(_pbac_session)
          #   from ..auth.user_filter import session_userinfo
          #   userinfo = session_userinfo(_pbac_session)
          # provider args (qs.py:303-313): "principal": principal, "userinfo": userinfo
          # re-raise tuple (qs.py:317): (RawQueryPlaceholderError, SemanticError, QueryAccessDenied, UserFilterDenied)
  ```

### Module 5: Documentation

- **Path**: `docs/USER_FILTERING.md` (new), `docs/DESCRIBE_API.md`,
  `docs/PBAC_PROGRAMMATIC.md`, `CHANGES.rst`
- **Responsibility**: user guide for `user_filtering` / `user_filter_options`
  (syntax table from §2, identity resolution order, bypass, program
  restriction, reason codes, migration instructions, deprecation of
  `h_filtering`, the "no session → 403 even with PBAC off" rule, and the
  parser-less/raw refusal); describe payload update (`user_filtering`,
  `h_filtering` alias, admin-only `user_filter_options`); a short section in
  `PBAC_PROGRAMMATIC.md` telling library callers that `QS(principal=...)` is
  required for `user_filtering` slugs; `CHANGES.rst` "Unreleased" entry
  (behaviour change + deprecation notice).
- **Depends on**: Modules 1–4 (documents their behaviour).
- **Interface Skeleton**: documentation only — section list:
  `docs/USER_FILTERING.md`: *Overview · Enabling a slug · Rule syntax ·
  Identity resolution · Program restriction · Bypass · Errors and reason
  codes · Caching · Migration (docs/sql/user_filtering.sql) · Deprecation of
  h_filtering · Limitations (multi-query follow-up, parser-less providers)*.

---

## 4. Test Specification

### Unit Tests

| Test | Module | Description |
|---|---|---|
| `test_options_rejects_unknown_keys` | M1 | `extra="forbid"` → `ConfigError` on an unknown key |
| `test_options_rejects_empty_document` | M1 | `{}` / `None` / `{"rules": []}` with no program restriction → `ConfigError` |
| `test_options_rejects_bad_column_grammar` | M1 | `"user_id; DROP"` → `ConfigError` |
| `test_resolve_attribute_principal_first_then_userinfo` | M1 | `user_id` from principal wins over userinfo; `attributes.store_id` comes from userinfo |
| `test_resolve_attribute_authz_principal_claims_absent` | M1 | `QSPrincipal.for_authz` → `user_id` resolves to `None` |
| `test_compile_no_principal_denies` | M1 | principal None and empty userinfo → `UserFilterDenied` with `reason_code="no_principal"`, `code == 403`, message `"Access denied."` |
| `test_compile_missing_required_attribute_denies` | M1 | absent / `None` / `[]` → `missing_attribute`; `required: false` → rule skipped |
| `test_compile_scalar_and_list_predicates` | M1 | scalar → `{"user_id": "7"}`; list / `operator="in"` → list value |
| `test_compile_superuser_bypass` | M1 | `superuser=True` → `bypassed=True`, no predicates, program check skipped |
| `test_compile_group_role_bypass_case_insensitive` | M1 | `bypass: ["Admins"]` matches group `admins`; role match reported as `role:<name>` |
| `test_check_program_default_allowed` | M1 | `program_slug='default'` passes with `allow_default`; denied when `allow_default=False` and not in programs |
| `test_check_program_restricted_denies` | M1 | `program_slug='epson'`, programs `['walmart']` → `program_restricted`; programs as dicts/objects normalized |
| `test_session_userinfo_fallback_key` | M1 | `AUTH_SESSION_OBJECT` missing → `"user"` key; non-dict → `{}` |
| `test_models_declare_user_filtering_fields` | M2 | `QueryModel` and `TenantQueryDefinition` accept both new fields with defaults `False` / `{}`; `h_filtering` still accepted |
| `test_describe_capabilities_user_filtering` | M2 | `capabilities.user_filtering` and alias `h_filtering` carry the same value |
| `test_describe_user_filter_options_admin_only` | M2 | `user_filter_options` redacted without `grants.admin`, present with it |
| `test_parser_accepts_identity_kwargs` | M3 | `SQLParser(definition=…, conditions=…, query=…, principal=p, userinfo=u)` exposes `.principal` / `.userinfo` |
| `test_parser_user_filter_merges_new_dict` | M3 | after `_user_filter_sync()` `parser.filter is not definition.filtering` and the definition dict is unchanged |
| `test_parser_user_filter_overrides_caller_key` | M3 | caller `where_cond={"user_id": "999"}` → rendered SQL contains the principal's value only |
| `test_parser_renders_in_for_lists` | M3 | list predicate renders `IN (...)` via `build_query()` |
| `test_parser_denial_propagates` | M3 | `_user_filter_sync()` raises `UserFilterDenied` (not swallowed, not `ParserError`) |
| `test_parser_noop_without_flag` | M3 | `user_filtering=False` → filter untouched, `user_filter_applied == ()` |
| `test_provider_parserless_guard` | M4 | provider subclass with `__parser__ = None` + `user_filtering=True` → `UserFilterDenied(parser_required)` in `__init__` |
| `test_sql_provider_raw_guard` | M4 | `is_raw=True` + `user_filtering=True` → `UserFilterDenied` from `prepare_connection()` before any parse |
| `test_sql_provider_query_without_placeholders_guard` | M4 | `qstype='query'`, `use_parser` false, flag on → `UserFilterDenied` |
| `test_provider_forwards_identity_to_parser` | M4 | `BaseProvider(..., principal=p, userinfo=u)` → `provider._parser.principal is p` |
| `test_qs_resolves_principal_from_session` | M4 | `QS(request=…)` with cached `user_session` → provider args carry a `QSPrincipal` and the userinfo dict |
| `test_qs_reraises_user_filter_denied` | M4 | `UserFilterDenied` from `prepare_connection()` leaves `build_provider()` unchanged (403), not wrapped in `QueryError` |
| `test_qs_user_filtering_ignores_pbac_flag` | M4 | `QS_PBAC_ENABLED=False` + no session + flag on → 403 |

### Integration Tests

| Test | Description |
|---|---|
| `test_user_filter_end_to_end_sql` | Definition with two rules + program restriction, session with matching programs: rendered SQL carries both predicates; `checksum()` differs between two users |
| `test_user_filter_program_denied_end_to_end` | Same definition, session without the program → HTTP 403 through `QS.build_provider()` |
| `test_describe_roundtrip_user_filtering` | `describe_slug()` on a definition with the flag reports it; non-admin payload lists `user_filter_options` under `redacted` |

### Validation Contract

tests:
- tests/auth/test_user_filter_options.py
- tests/auth/test_user_filter.py
- tests/test_models_user_filtering.py
- tests/handlers/test_describe_user_filtering.py
- tests/test_sql_parser_user_filter.py
- tests/providers/test_user_filter_guard.py
- tests/test_qs_user_filtering.py
- tests/handlers/test_queryservice_pbac_smoke.py
- tests/test_sql_parser_combinations.py
- tests/auth/test_principal.py
- tests/semantic/test_governance.py
- tests/handlers/test_describe_detail.py
- tests/tenants/test_tenant_repository_reads.py
- tests/tenants/test_tenant_repository_writes.py
- tests/tenants/test_columns_definition.py
- tests/providers/test_semantic_provider.py
markers: none

### Test Data / Fixtures

```python
# tests/auth/test_user_filter.py and tests/test_sql_parser_user_filter.py
import pytest
from querysource.auth.principal import QSPrincipal

OPTIONS = {
    "rules": [
        {"column": "user_id", "attribute": "user_id"},
        {"column": "store_id", "attribute": "attributes.store_id", "operator": "in"},
    ],
    "program": {"restricted": True, "allow_default": True},
    "bypass": ["superuser", "admins"],
}

@pytest.fixture
def principal() -> QSPrincipal:
    return QSPrincipal(user_id="7", username="ana", groups=("sales",), programs=("epson",), channel="http")

@pytest.fixture
def userinfo() -> dict:
    return {"user_id": "7", "programs": ["epson"], "attributes": {"store_id": [11, 12]}}

@pytest.fixture
def definition():
    """QueryModel with user_filtering=True (parser tests follow tests/test_sql_parser_combinations.py::_make_parser)."""
    from querysource.models import QueryModel
    return QueryModel(query_slug="s", program_slug="epson", query_raw="SELECT * FROM epson.users {where_cond}",
                      filtering={"active": True}, user_filtering=True, user_filter_options=OPTIONS)
```

Parser tests call `parser._user_filter_sync()` directly (it is `cpdef`), so
`set_options()` and Redis are not needed; the rendered SQL is checked through
`await parser.build_query()` and parsed with `sqlglot` like the existing
combinatorial tests.

---

## 5. Acceptance Criteria

> This feature is complete when ALL of the following are true:

- [ ] AC-1. `QueryModel` and `TenantQueryDefinition` declare `user_filtering: bool = False` and `user_filter_options: dict = {}` (jsonb); `h_filtering` remains declared, documented as deprecated, and is never consulted by any code path.
- [ ] AC-2. `docs/sql/user_filtering.sql` adds the two columns idempotently (`ADD COLUMN IF NOT EXISTS`) and only adds — no rename, no drop; the `tests/tenants/test_query_move.py` `_FULL_COLUMNS` DDL carries both columns.
- [ ] AC-3. `UserFilterDenied` exists in `querysource/exceptions.py` with `code == 403`, a generic message that never names the rule/attribute/program, and a `reason_code` attribute limited to the five codes in §2.
- [ ] AC-4. `compile_user_filter()` raises `UserFilterDenied(no_principal)` when there is no principal and no userinfo, regardless of `QS_PBAC_ENABLED`.
- [ ] AC-5. A `required` rule whose attribute is missing, `None` or an empty list raises `UserFilterDenied(missing_attribute)`; `required: false` skips the rule; an empty `IN ()` is never rendered.
- [ ] AC-6. `program.restricted` denies with `program_restricted` unless `definition.program_slug` (lower-cased) is among the caller's normalized programs; `'default'` passes when `allow_default` is true; `program_slug` is never inferred from the SQL text.
- [ ] AC-7. `superuser=True` and any case-insensitive match of `bypass` against the principal's groups/roles skip all rules and the program check, emitting one INFO audit line with `principal.log_fields()`; every denial emits one WARNING with slug and reason code.
- [ ] AC-8. Invalid, unknown-key, non-mapping or empty `user_filter_options` with `user_filtering=True` raises `ConfigError` (500); the slug never runs unfiltered.
- [ ] AC-9. `AbstractParser` exposes `principal`, `userinfo`, `user_filter_applied`; `_user_filter_sync()` runs after `_qs_filters_sync()` in `_extract_options()`, is declared identically in `abstract.pxd`, and assigns a new dict to `self.filter` (the definition's `filtering` object is never mutated).
- [ ] AC-10. A caller-supplied `where_cond` / `filter` key on a rule column is overwritten by the rule value in the rendered SQL.
- [ ] AC-11. List predicates render as `IN (...)` and scalars as `=` through the existing dialect `filter_conditions()`; no new SQL-building code is added to any `.pyx`.
- [ ] AC-12. `BaseProvider.__init__` raises `UserFilterDenied(parser_required)` for a `user_filtering` definition when `__parser__ is None`; `sqlProvider.prepare_connection()` raises it before both early returns (`qstype='query'` without parser need, and `is_raw`); no connection is opened first.
- [ ] AC-13. `QS.build_provider()` passes the explicit `principal` (or the session-derived one) and the raw session userinfo to the provider, and `UserFilterDenied` leaves `build_provider()` with code 403 instead of being wrapped into `QueryError`/`ParserError`.
- [ ] AC-14. `BaseProvider.checksum()` differs for two principals whose predicates differ (cache partitioned per user) with no change to the cache code.
- [ ] AC-15. `describe_slug()` reports `capabilities.user_filtering` and the alias `capabilities.h_filtering` with the same value; `user_filter_options` is in `ADMIN_FIELDS` and appears under `redacted` for non-admin grants.
- [ ] AC-16. `querysource.auth.user_filter` imports nothing from `querysource.parsers`, `querysource.providers`, `querysource.queries`, `aiohttp` or `navigator_auth` at module level (`python -c "import querysource.auth.user_filter"` succeeds in isolation).
- [ ] AC-17. `semanticProvider` behaviour is unchanged: its `self._principal` (providers/semantic.py:44) is not overwritten by `BaseProvider`.
- [ ] AC-18. All tests in §4 pass after `make build-inplace`; the Validation Contract files pass unchanged.
- [ ] AC-19. `docs/USER_FILTERING.md`, `docs/DESCRIBE_API.md`, `docs/PBAC_PROGRAMMATIC.md` and `CHANGES.rst` are updated as in M5.
- [ ] AC-20. No new third-party dependency.

---

## 6. Codebase Contract

> **CRITICAL — Anti-Hallucination Anchor**
> Verified against HEAD `f3b6bf6` (anchors re-checked after merges #689–#691; the only code change since `7289f4b` is `repositories/definitions.py:479`, below every cited line). Every path/line below was re-read for this spec.

### Verified Imports
```python
from querysource.auth.principal import QSPrincipal                    # verified: querysource/auth/principal.py:21 (also re-exported by querysource/auth/__init__.py:19)
from querysource.semantic.governance import principal_from_session    # verified: querysource/semantic/governance.py:17
from querysource.exceptions import QueryException, ConfigError, QueryAccessDenied, ParserError  # verified: exceptions.py:6 / :33 / :63 / :86
from querysource.models import QueryModel, QueryObject               # verified: querysource/models.py:49 / :24
from querysource.tenant_models import TenantQueryDefinition          # verified: querysource/tenant_models.py:24
from querysource.providers import BaseProvider                        # verified: querysource/providers/__init__.py:4
from querysource.parsers.sql import SQLParser                         # verified: querysource/providers/sql.py:17 (import site), tests/test_sql_parser_combinations.py:20
from querysource.parsers import QS_FILTERS                            # verified: querysource/parsers/__init__.py:9 (module-level dict); imported by abstract.pyx:14
from querysource.auth.enforcement import enforce_principal            # verified: querysource/auth/enforcement.py:151 (lazy-imported at qs.py:218)
from querysource.conf import QS_PBAC_ENABLED, QS_QUERIES_SCHEMA, QS_QUERIES_TABLE  # verified: conf.py:466 / :372 / :373
from navigator_auth.conf import AUTH_SESSION_OBJECT                   # optional import, try/except ImportError → "user" (pattern: governance.py:22-25)
```

### Existing Class Signatures
```python
# querysource/models.py
class QueryObject(ClassDict):                                                # line 24
class QueryModel(Model):                                                     # line 49
    qry_options: Optional[dict] = Field(required=False, db_type='jsonb', default_factory=dict)   # line 75
    h_filtering: bool = Field(required=False, default=False, comment="filtering based on Hierarchical rules.")  # line 76
    is_raw: bool = Field(required=False, default=False)                      # line 79
    program_slug: str = Field(required=True, default='default')              # line 88
    class Meta: name = QS_QUERIES_TABLE; schema = QS_QUERIES_SCHEMA; strict = True   # lines 108-112
# Unknown kwargs → TypeError (verified live: QueryModel(query_slug="x", program_slug="d", bogus_col=1))

# querysource/tenant_models.py
class TenantQueryDefinition(BaseModel):                                      # line 24 (no program_slug field; rejects unknown kwargs, lines 30-33)
    h_filtering: bool = Field(required=False, default=False, comment='filtering based on Hierarchical rules.')  # line 58

# querysource/repositories/definitions.py
    def _row_to_persisted(self, row, store) -> tuple[dict, str | None]:     # line 140 → TenantQueryDefinition(**data) at line 159
    def _runtime_model(self, persisted, store, legacy_program_slug) -> QueryModel:  # line 172
        program_slug = legacy_program_slug if store.has_program_slug else store.schema   # line 178 (tenant store → program = schema)

# querysource/auth/principal.py (frozen dataclass)
class QSPrincipal:                                                           # line 21
    user_id: str; username: str | None; groups: tuple[str, ...]; roles: tuple[str, ...]; programs: tuple[str, ...]
    superuser: bool = False; tenant_id: str | None = None; channel: str = "library"; authz_backend: str | None = None
    @property is_authz -> bool                                               # line 93
    def to_userinfo(self) -> dict[str, Any]                                  # line 97
    def log_fields(self) -> dict[str, Any]                                   # line 120

# querysource/semantic/governance.py
def principal_from_session(session: Any | None) -> QSPrincipal | None:      # line 17 (AUTH_SESSION_OBJECT try/except at 22-25; userinfo = session.get(..., session.get("user", {})) line 27)
def governance_filters(resource, principal) -> tuple[list, list[str]]:     # line 179

# querysource/exceptions.py
class QueryException(Exception): default_code = 500; __init__(self, message: str, code: int | None = None, **kwargs)  # lines 6-20
class ConfigError(QueryException): __init__(message=None) → code=500        # line 33
class QueryAccessDenied(QueryException): → code=404 BY DESIGN                # line 63
class DriverError(QueryException)                                            # line 74  ← new UserFilterDenied is inserted before this line
class ParserError(QueryException): default_code = 400                        # line 86

# querysource/parsers/abstract.pyx (Cython)
cdef class AbstractParser:                                                   # line 36
    def __cinit__(self, *args, definition: object, conditions: object, query: str = None, **kwargs)   # line 39
        self.definition = definition if definition else None                 # line 54
        self.set_attributes(); self.define_conditions(conditions)            # lines 56-57
    cdef void _extract_options(self)                                         # line 159 — calls … _query_filter_sync() (173), _qs_filters_sync() (174), _col_definition_sync() (175)
    cdef void _program_slug_sync(self)                                       # line 183 → self.program_slug = self.definition.program_slug
    cdef void _query_filter_sync(self)                                       # line 318 → self.filter = conditions.pop('where_cond') | conditions.pop('filter') | definition.filtering (ALIASED, line 331)
    cdef void _qs_filters_sync(self)                                         # line 338
    cdef void _col_definition_sync(self)                                     # line 345
    cpdef dict get_query_filters(self)                                       # line 410
    async def set_options(self)                                              # line 417 → self._extract_options() at line 428
    cpdef object where_cond(self, dict where)                                # line 586

# querysource/parsers/abstract.pxd
    cdef public dict filter                                                  # line 17
    cdef public str program_slug                                             # line 23
    cdef public dict _query_filters                                          # line 41
    cdef void _extract_options(self)                                         # line 68
    cdef void _qs_filters_sync(self)                                         # line 81
    cdef void _col_definition_sync(self)                                     # line 82

# querysource/parsers/sql.pyx
class SQLParser: def __init__(self, *args, **kwargs) → super().__init__(*args, **kwargs)   # line 115 (kwargs reach AbstractParser.__cinit__)
    async def filter_conditions(self, sql)                                   # line 143 (Rust fast path when HAS_RUST and self.filter)
    # list values render "{key} IN ({val})"                                  # line 233
    async def build_query(self, querylimit=None, offset=None)                # line 431; QS_FILTERS loop lines 445-451 merges plugin result over self.filter
# pgsql.pyx:329, sosql.pyx:31, cql.pyx:27 — all `def __init__(self, *args, **kwargs)`

# querysource/providers/abstract.py
class BaseProvider(SourceHooksMixin, ABC):                                   # line 35
    __parser__: AbstractParser = None                                        # line 37
    _parser_options: dict = {}                                               # line 38
    def __init__(self, slug='', query=None, qstype='', connection=None, definition=None, conditions=None, request=None, **kwargs)  # lines 55-64
        self._definition = definition                                        # line 69
        self.is_raw: bool = False                                            # line 76
        self._program = self._definition.program_slug                        # line 94
        ## Parser Logic:                                                     # line 124
        self._parser = self.__parser__(query=self._query, definition=definition, conditions=conditions, **self._parser_options)  # lines 128-133 (inside try → ParserError, lines 127-140)
    async def prepare_connection(self)                                       # line 259 → await self._parser.set_options() (line 264)
    def checksum(self): return get_hash(self._query)                         # line 330 (hashes the RENDERED query)

# querysource/providers/sql.py
class sqlProvider(BaseProvider): __parser__ = SQLParser                      # lines 26 / 43
    async def prepare_connection(self) -> Callable                           # line 138
        use_parser = self._needs_parser(self._query) or self._has_parser_conditions(self._conditions)   # line 147 → `return self` at 151
        if self.is_raw is True: return self                                  # lines 152-153
        await super().prepare_connection()                                   # line 154 (set_options runs here, OUTSIDE the ParserError try at 156-160)

# querysource/providers/semantic.py
class semanticProvider: self._principal = kwargs.pop("principal", None)      # line 44 (before super().__init__ at 45) — BaseProvider must not reuse `_principal`

# querysource/queries/qs.py
class QS(BaseQuery):                                                         # line 51
    async def build_provider(self):
        _cached = self._request.get('user_session', _SENTINEL)              # line 210 (memoized navigator session; _pbac_session set at 212)
        from ..auth.enforcement import enforce_principal                     # line 218 (lazy-import precedent)
        args = {"slug", "query", "qstype", "connection", "definition", "conditions", "request", "loop", "principal": self._principal}  # lines 303-313
        await self._qs.prepare_connection()                                  # line 315
        except (RawQueryPlaceholderError, SemanticError, QueryAccessDenied): raise   # line 317
        except Exception as err: raise QueryError(..., code=400 if isinstance(err, ParserError) else 500)   # lines 319-326
# querysource/interfaces/queries.py: AbstractQuery.__init__ stores self._principal = principal   # line 129
# querysource/queries/base.py:35 — BaseQuery.__init__ kwarg `principal: "QSPrincipal | None" = None`

# querysource/queries/describe.py
RAW_FIELDS = ("query_raw",); ADMIN_FIELDS = ("dwh_info", "dwh_scheduler", "cache_options", "created_by", "updated_by")  # lines 34-35
class DescribeGrants(BaseModel): raw: bool = False; admin: bool = False     # line 54
def redact_payload(payload, grants) -> tuple[dict, list[str]]               # line 185
def describe_slug(...)                                                       # line 230; capabilities dict lines 262-270 ("h_filtering" at 267)

# querysource/auth/request_gate.py
async def get_request_session(request, *, logger, session_getter=None) -> SessionData | None   # line 27 (memoizes request['user_session'])
# querysource/handlers/abstract.py:313 — AbstractHandler._get_user_session() delegates to it

# querysource/semantic/models.py
class GovernanceBlock(BaseModel): model_config = ConfigDict(extra="forbid")  # line 116 (Pydantic style precedent)

# querysource/conf.py
QS_QUERIES_SCHEMA = config.get('QS_QUERIES_SCHEMA', fallback='public')      # line 372
QS_QUERIES_TABLE = config.get('QS_QUERIES_TABLE', fallback='queries')        # line 373
QS_PBAC_ENABLED = config.getboolean('QS_PBAC_ENABLED', fallback=False)       # line 466 — must NOT gate user filtering
```

### Integration Points
| New Component | Connects To | Via | Verified At |
|---|---|---|---|
| `UserFilterDenied` | `QueryException.__init__(message, code=403)` | subclass | `querysource/exceptions.py:6-20` |
| `compile_user_filter()` | `QSPrincipal` claims, `principal.log_fields()` | attribute access | `querysource/auth/principal.py:32-40, 120` |
| `session_userinfo()` | navigator session `.get(AUTH_SESSION_OBJECT)` | same pattern as `principal_from_session` | `querysource/semantic/governance.py:22-27` |
| `AbstractParser._user_filter_sync()` | `_extract_options()` step order | call after `_qs_filters_sync()` | `querysource/parsers/abstract.pyx:174` |
| `AbstractParser._user_filter_sync()` | `self.filter` rendering | `filter_conditions()` / `IN` rendering | `querysource/parsers/sql.pyx:143, 233` |
| `AbstractParser.principal/userinfo` | `__cinit__(**kwargs)` | `kwargs.pop` | `querysource/parsers/abstract.pyx:39-54` |
| `BaseProvider._user_principal/_user_info` | parser constructor | explicit kwargs next to `**self._parser_options` | `querysource/providers/abstract.py:128-133` |
| `BaseProvider._assert_user_filter_capable()` | `self._parser is None` | call after parser block | `querysource/providers/abstract.py:140` |
| `sqlProvider.prepare_connection()` guard | early returns | before `return self` | `querysource/providers/sql.py:151, 153` |
| `QS.build_provider()` | `principal_from_session(_pbac_session)` | lazy import | `querysource/queries/qs.py:210-213, 217-218` |
| `QS.build_provider()` | provider `args` | `"userinfo"` entry next to `"principal"` | `querysource/queries/qs.py:303-313` |
| `QS.build_provider()` | re-raise tuple | add `UserFilterDenied` | `querysource/queries/qs.py:317` |
| `describe_slug()` | `ADMIN_FIELDS`, `capabilities` | tuple extension, dict keys | `querysource/queries/describe.py:35, 262-270` |
| Tenant stores | `program_slug = store.schema` | existing derivation | `querysource/repositories/definitions.py:178` |
| Result cache | `checksum()` of rendered query | unchanged | `querysource/providers/abstract.py:330` |

### Does NOT Exist (Anti-Hallucination)
- ~~`user_filtering`~~, ~~`user_filter_options`~~, ~~`h_filter_options`~~ — no such columns/attributes anywhere in `querysource/` today (new in this feature).
- ~~`AbstractParser._user_filter_sync`~~, ~~`AbstractParser.principal`~~, ~~`AbstractParser.userinfo`~~, ~~`AbstractParser.user_filter_applied`~~, ~~`AbstractParser.request`~~ — the parser receives **no** identity or request today (`__cinit__` only takes `definition`, `conditions`, `query`, `**kwargs`).
- ~~`UserFilterDenied`~~ / ~~`UserFilterError`~~ — no 403-class exception exists; `QueryAccessDenied` is 404 by design (`exceptions.py:63-71`).
- ~~`querysource.auth.user_filter`~~ — module to be created; `querysource/auth/__init__.py` does not export it and must not need to.
- ~~`BaseProvider._principal`~~, ~~`BaseProvider._user_principal`~~, ~~`BaseProvider._user_info`~~, ~~`BaseProvider.user_filtering_required()`~~ — `BaseProvider` ignores the `principal` kwarg today (it lands in `**kwargs` unused); only `semanticProvider` defines `_principal`.
- ~~`QS` passing `userinfo` to providers~~ — `args` at `qs.py:303-313` has no such key.
- ~~`cdef void _extract_options(self) except *`~~ — the declaration has no exception clause (`abstract.pxd:68`); Cython ≥ 3 propagates anyway.
- ~~an `hfilter` function in `querysource/libs/functions/`~~ — only `query_options`, `grouping_set`, `group_by_child`, `first_day`, `last_day` exist; `h_filtering` is read only by `describe.py:267`.
- ~~DDL for the definitions table in `docs/sql/`~~ — only `datasources_logic.sql` and `useful_pg_extensions.sql`; the only DDL in the repo is the test fixture `tests/tenants/test_query_move.py:36-47`.
- ~~`tests/queries/`~~ — no such directory; provider/QS tests go under `tests/providers/` (exists) and handler tests under `tests/handlers/`.
- ~~`QueryModel.__post_init__` alias logic~~ — not planned; `h_filtering` stays a plain field.
- ~~`sdd/CONVENTIONS.md` project areas~~ — the section is an empty template; `projects` in the frontmatter are derived from code paths.

### Edit Sites (Blueprint Anchors)

Verified against: `f3b6bf6` (`grep -c` counts taken 2026-11-03).

| File | Action | Verbatim anchor line | Verified at | Occurrences |
|---|---|---|---|---|
| `querysource/exceptions.py` | MODIFY | `class DriverError(QueryException):` (insert `UserFilterDenied` before it) | `exceptions.py:74` | 1 |
| `querysource/auth/user_filter.py` | CREATE | — | — | — |
| `querysource/models.py` | MODIFY | `    h_filtering: bool = Field(required=False, default=False, comment="filtering based on Hierarchical rules.")` | `models.py:76` | 1 |
| `querysource/tenant_models.py` | MODIFY | `    h_filtering: bool = Field(required=False, default=False, comment='filtering based on Hierarchical rules.')` | `tenant_models.py:58` | 1 |
| `docs/sql/user_filtering.sql` | CREATE | — | — | — |
| `querysource/queries/describe.py` | MODIFY | `ADMIN_FIELDS: tuple[str, ...] = ("dwh_info", "dwh_scheduler", "cache_options", "created_by", "updated_by")` | `describe.py:35` | 1 |
| `querysource/queries/describe.py` | MODIFY | `        "h_filtering": bool(base.get("h_filtering") or False),` | `describe.py:267` | 1 |
| `tests/tenants/test_query_move.py` | MODIFY | `  qry_options jsonb DEFAULT '{{}}', h_filtering boolean DEFAULT false, query_raw text, is_raw boolean DEFAULT false,` | `test_query_move.py:40` | 1 |
| `querysource/parsers/abstract.pxd` | MODIFY | `    cdef public dict _query_filters` (append the three attributes after it) | `abstract.pxd:41` | 1 |
| `querysource/parsers/abstract.pxd` | MODIFY | `    cdef void _qs_filters_sync(self)` (append `cpdef void _user_filter_sync(self) except *` after it) | `abstract.pxd:81` | 1 |
| `querysource/parsers/abstract.pyx` | MODIFY | `        self.definition = definition if definition else None` (append the three attribute inits) | `abstract.pyx:54` | 1 |
| `querysource/parsers/abstract.pyx` | MODIFY | `        self._qs_filters_sync()` — ambiguous (also at :405); use the occurrence inside `cdef void _extract_options(self):` that is immediately followed by `        self._col_definition_sync()` | `abstract.pyx:174` | 2 |
| `querysource/parsers/abstract.pyx` | MODIFY | `    cdef void _col_definition_sync(self):` (insert the new `cpdef` method before it) | `abstract.pyx:345` | 1 |
| `querysource/providers/abstract.py` | MODIFY | `        ## Parser Logic:` (insert the two `kwargs.pop` lines before it) | `providers/abstract.py:124` | 1 |
| `querysource/providers/abstract.py` | MODIFY | `                self._parser = self.__parser__(  # pylint: disable=E1102` (add `principal=`/`userinfo=` kwargs to this call; add the guard call after the enclosing try/except) | `providers/abstract.py:128` | 1 |
| `querysource/providers/sql.py` | MODIFY | `            use_parser = self._needs_parser(self._query) or self._has_parser_conditions(self._conditions)` | `providers/sql.py:147` | 1 |
| `querysource/providers/sql.py` | MODIFY | `        if self.is_raw is True:` | `providers/sql.py:152` | 1 |
| `querysource/queries/qs.py` | MODIFY | `    QueryAccessDenied,` (import block; add `UserFilterDenied,`) | `queries/qs.py:28` | 1 |
| `querysource/queries/qs.py` | MODIFY | `                _cached = self._request.get('user_session', _SENTINEL)` (identity resolution goes after the enclosing try/except) | `queries/qs.py:210` | 1 |
| `querysource/queries/qs.py` | MODIFY | `                    "principal": self._principal,` | `queries/qs.py:312` | 1 |
| `querysource/queries/qs.py` | MODIFY | `            except (RawQueryPlaceholderError, SemanticError, QueryAccessDenied):` | `queries/qs.py:317` | 1 |
| `docs/USER_FILTERING.md` | CREATE | — | — | — |
| `docs/DESCRIBE_API.md` | MODIFY | `  "h_filtering": false,` — ambiguous (lines 89 and 115); update both payload examples | `DESCRIBE_API.md:89` | 2 |
| `docs/PBAC_PROGRAMMATIC.md` | MODIFY | append a section; no anchor needed | — | — |
| `CHANGES.rst` | MODIFY | `Unreleased` (add the entry under the existing heading) | `CHANGES.rst:1` | 1 |
| `tests/auth/test_user_filter.py` | CREATE | — | — | — |
| `tests/test_sql_parser_user_filter.py` | CREATE | — | — | — |
| `tests/providers/test_user_filter_guard.py` | CREATE | — | — | — |
| `tests/handlers/test_describe_user_filtering.py` | CREATE | — | — | — |
| `tests/test_models_user_filtering.py` | CREATE | — | — | — |

---

## 7. Implementation Notes & Constraints

> Architecture decisions stay with the thinking model. A delegated
> implementation may only express a decision already recorded here and in
> the TASK's implementation blocks — it must never invent an API, choose a
> file, or resolve an open design question.

### Patterns to Follow
- **Identity from session**: copy the `AUTH_SESSION_OBJECT` try/except and
  the `session.get(..., session.get("user", {}))` fallback from
  `querysource/semantic/governance.py:17-45`; never import `navigator_auth`
  unguarded.
- **Lazy imports in hot paths**: `qs.py:217-218` imports `enforce_principal`
  inside the method; the parser step and `QS` import `user_filter` the same
  way.
- **Pydantic validation style**: `ConfigDict(extra="forbid")` as in
  `querysource/semantic/models.py:116` and `querysource/queries/describe.py:54`;
  `frozen=True` so compiled options can be cached per definition later.
- **Exception shape**: subclass `QueryException` and set `default_code`, like
  `ParserError` (`exceptions.py:86`); generic message like `QueryAccessDenied`
  (`exceptions.py:63-71`).
- **Logging**: `self.logger` / `self._logger` (parser: `QS.Parser.<Name>`,
  provider: `QS.<Name>`); audit lines include `principal.log_fields()`.
- **Cython**: `cimport`/`cdef` discipline; every new attribute declared in
  `abstract.pxd`; rebuild with `make build-inplace` (Makefile:156) before
  running parser tests; tests call the `cpdef` step directly as
  `tests/test_sql_parser_combinations.py::_make_parser` bypasses
  `set_options()`.
- **Cache**: no change — `BaseProvider.checksum()` (`providers/abstract.py:330`)
  already hashes the rendered query.
- **Tests**: pytest + `asyncio_mode = auto`; parser SQL validated with
  `sqlglot` as the existing combinatorial tests do; run only the tests named
  by the task.

### Known Risks / Gotchas
- **Aliased `self.filter` (critical)**: `_query_filter_sync` assigns
  `self.filter = self.definition.filtering` (`abstract.pyx:331`) — the same
  dict object held by the cached `LoadedDefinition.runtime`. An in-place
  `self.filter.update(...)` would leak user A's predicates into the shared
  definition for user B. The step must assign a new dict (AC-9, test
  `test_parser_user_filter_merges_new_dict`).
- **Unknown columns break model construction**: both definition models raise
  `TypeError` on unexpected kwargs and the tenant repository validates whole
  rows; this is why the migration is ADD-only and `h_filtering` stays
  declared until 5.3.0. Un-migrated stores read as `user_filtering=False`;
  writing `user_filtering=True` into an un-migrated store fails at the
  database (documented in `docs/USER_FILTERING.md`).
- **`semanticProvider` owns `_principal`** (`providers/semantic.py:44`, set
  before `super().__init__`); `BaseProvider` must use `_user_principal` /
  `_user_info` and `kwargs.pop(..., None)` so the semantic provider's
  already-popped `principal` yields `None` without error (AC-17).
- **Legacy `QS_FILTERS` plugins run after the step** (`sql.pyx:445-451`,
  `pgsql.pyx:685`, `sosql.pyx:215`, `cql.pyx:177`) and merge their result
  over `self.filter`. They are server-side registered code, not caller
  input; a plugin writing a rule column is a deployment bug. Document it;
  do not try to re-apply predicates in every dialect.
- **Exception propagation from Cython**: Cython ≥ 3 propagates exceptions
  from `cdef void`; declare `except *` explicitly on the new step anyway and
  keep the `.pxd` in sync, or the build fails with a signature mismatch.
- **`prepare_connection()` wrapping**: `set_options()` runs at
  `providers/sql.py:154`, outside the `ParserError` try at 156-160, but
  `QS.build_provider()` wraps everything not in the tuple at `qs.py:317`
  into `QueryError`; forgetting `UserFilterDenied` there turns 403 into 500.
- **Authz principals** (`QSPrincipal.for_authz`) have no user attributes:
  every `required` rule denies. Intended; documented.
- **`'default'` program**: `program_slug` compare is case-insensitive;
  tenant stores derive `program_slug` from the schema name
  (`definitions.py:178`), so a tenant table in schema `epson` yields the
  program `epson`.
- **Empty lists**: an empty `IN ()` is invalid SQL; the compiler treats an
  empty list as a missing attribute (deny when required).
- **Rule column not in the query**: rendered as a plain predicate; the
  database raises. Lint at save time is open question Q3.
- **Breaking surface**: none for readers. Writers that still send
  `h_filtering` keep working (field retained). The 5.3.0 removal is the
  breaking step and is out of this feature.

### External Dependencies
| Package | Version | Reason |
|---|---|---|
| `cython` (existing build dep) | `==3.0.11` (pyproject.toml:5) | rebuild `abstract.pyx` / `.pxd` |
| `pydantic` (existing) | as installed | `UserFilterOptions` validation (same style as `semantic/models.py`) |
| `navigator-auth` (existing, optional import) | as installed | `AUTH_SESSION_OBJECT` key for raw userinfo |
| `sqlglot` (existing test dep) | as installed | validate rendered SQL in parser tests |

No new third-party dependency.

---

## Consequences

<!-- GENERATED by `parrot sdd spec consequences <spec> --render` — do not edit by hand. -->

---

## 8. Open Questions

> Questions that must be resolved before or during implementation.

- [ ] Q1. Which SQL migration mechanism/location is canonical for the definitions table (`QS_QUERIES_SCHEMA.QS_QUERIES_TABLE`, default `public.queries`) and the tenant `queries` tables? `docs/sql/` has no DDL for it. This spec assumes `docs/sql/user_filtering.sql` (ADD-only, idempotent). — *Owner: Jesus Lara*
- [ ] Q2. HTTP code: the user asked for **403**, but `QueryAccessDenied` is intentionally 404 (hides existence). This spec introduces `UserFilterDenied` (403). Confirm, or collapse to 404 for consistency with PBAC denials. — *Owner: Jesus Lara*
- [ ] Q3. Should rule columns be validated against the slug's `columns_definition` / describe columns at definition-save time (lint) or only fail at DB execution? — *Owner: Jesus Lara*
- [ ] Q4. Should `bypass` also accept a PBAC action (e.g. `"user_filter:bypass"`) evaluated through `enforce_principal`, in addition to group/role names? — *Owner: Jesus Lara*
- [ ] Q5. Deprecation window for `h_filtering`: field and column removed in 5.3.0 as assumed (matching the `QuerySource(loop=...)` DeprecationWarning precedent, commit `a76422c`)? — *Owner: Jesus Lara*
- [ ] Q6. Programmatic callers (`QS(principal=...)` without a session): should they be able to pass extra `userinfo` attributes, or are typed `QSPrincipal` claims the only identity source for the library channel? This spec passes `userinfo={}` for them (rules on non-claim attributes deny when required). — *Owner: Jesus Lara*
- [x] Enforcement layer — *Resolved in brainstorm*: inside the parser (`.pyx`), fed by a pure Python rule module.
- [x] Identity source — *Resolved in brainstorm*: `QSPrincipal` first, raw session userinfo as fallback.
- [x] Column naming — *Resolved in brainstorm*: rename `h_filtering` → `user_filtering`, add `user_filter_options`, keep deprecated alias.
- [x] Program determination — *Resolved in brainstorm*: `program_slug` of the query definition only; `'default'` always allowed.
- [x] Parser-less / raw paths — *Resolved in brainstorm*: fail-closed, never execute unfiltered.
- [x] Missing attribute / superuser / bypass — *Resolved in brainstorm*: missing → 403; superuser bypasses; `bypass` list configurable per definition.
- [x] Rule syntax — *Resolved in brainstorm*: Option B, typed rule list.
- [x] Multi-query — *Resolved in brainstorm*: follow-up feature; compiler module designed for reuse.

---

## 9. Design Research Cross-Check

> Independent design opinion from the `codex` seat over the **accepted exploration
> doc** (never over this spec). Model: — · Status: skipped (`reviewer` is `none` in `sdd/config.json` and the brainstorm status is `exploration`, not `accepted`)
> · Transcript: —

| # | Suggestion (kind) | Disposition | Reason | Landed in |
|---|---|---|---|---|

Summary: **0** confirmed · **0** rejected · **0** escalated.

---

## Worktree Strategy

- **Isolation**: one feature worktree (`feat-FEAT-184-pbac-based-per-user-filter`);
  the `sdd-coder` engine gives each task its own sub-worktree inside it.
- **Module dependency graph** (edges = imports/uses):
  - M3 → M1 (lazy-imports `compile_user_filter`; propagates `UserFilterDenied`, `ConfigError`)
  - M3 → M2 (reads `definition.user_filtering` / `definition.user_filter_options`)
  - M4 → M1 (imports `UserFilterDenied`, `session_userinfo`)
  - M4 → M3 (passes `principal=` / `userinfo=` kwargs the parser must accept)
  - M5 → M1, M2, M3, M4 (documents their behaviour)
  - M1 ∥ M2 run concurrently; M3 after both; M4 after M3; M5 last.
- **Shared files**: none between modules — `exceptions.py` (M1 only),
  `describe.py`/`models.py`/`tenant_models.py` (M2 only), `abstract.pyx/.pxd`
  (M3 only), `providers/*.py` + `qs.py` (M4 only), docs (M5 only).
- **Exclusive resources**: M3 rebuilds the Cython extension
  (`make build-inplace`, writes `querysource/parsers/*.so` in the shared
  tree) → its tasks are `parallel: false`. M2's `docs/sql/user_filtering.sql`
  is a migration artifact but touches no shared environment.
- **Cross-feature dependencies**: none to merge first. Hot files
  `abstract.pyx` and `qs.py` are also touched by the in-flight parser specs
  `filter-with-partial-matching`, `filter-eval-hardening`,
  `jsonb-not-or-operators` and the multiquery family; no task in
  `sdd/tasks/active/` currently references either file (grep 2026-11-03).
  Rebase on `dev` before M3 and M4.

---

## Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.2 | 2026-11-03 | Claude (`/sdd-task`) | Validation Contract lists the feature's new test files and the QS PBAC smoke test |
| 0.1 | 2026-11-03 | Jesus Lara / Claude | Initial draft from `pbac-based-per-user-filter.brainstorm.md` (Option A); ADD-only migration and `_user_principal` naming decided from codebase research |
