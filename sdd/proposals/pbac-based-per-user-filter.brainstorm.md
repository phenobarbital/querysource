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
tags: [pbac, user-filtering, row-level-security, parser, program-restriction, session]
---

# Brainstorm: PBAC-based per-user automatic filtering (`user_filtering`)

**Date**: 2026-11-03
**Author**: Jesus Lara
**Status**: exploration
**Recommended Option**: A

---

## Problem Statement

QuerySource slugs are shared definitions: every caller that may execute a slug
sees the same rows. The only per-user narrowing available today is the
*legacy* "hierarchical filter" (`hfilter`): a `bool` column `h_filtering`
on `QueryModel` / `TenantQueryDefinition` that is read by nothing (grep:
only `describe.py` echoes it back), plus the `QUERYSOURCE_FILTERS` registry
(`qry_options`, `grouping_set`, `group_by_child` in
`querysource/libs/functions/__init__.py`) that mutates the WHERE/GROUP BY
from *request conditions*, never from the **user's identity**.

Consequences:

- A slug such as `SELECT * FROM epson.users` is visible to any authenticated
  caller allowed to execute the slug, regardless of whether the user belongs
  to the `epson` program. PBAC (`slug:execute`) is all-or-nothing per slug; it
  cannot say "run it, but only your rows".
- Per-user narrowing is reimplemented ad hoc by clients (passing `user_id`
  as a filter), which is trivially spoofable: the caller chooses the value.
- The semantic layer already has governance row filters
  (`GovernanceBlock.row_filters`, `governance_filters()`), but they are
  role-keyed and semantic-only; plain SQL slugs have nothing equivalent.

**Who is affected**: end users of multi-tenant/multi-program deployments
(data isolation), slug authors (want declarative, non-spoofable per-user
filters), ops/security (need fail-closed guarantees and auditability).

**Why now**: PBAC (FEAT-182, FEAT-091, FEAT-175 M5) landed the identity
plumbing (`QSPrincipal`, `principal_from_session()`, `resolve_principal()`),
so the missing piece is just the *row-level* enforcement that consumes it.
`h_filtering` is dead weight and should be replaced rather than revived.

## Constraints & Requirements

Decisions taken during discovery (Rounds 0–2) — binding for `/sdd-spec`:

- **Flow**: `type: feature`, base `dev`.
- **Enforcement point: inside the parser (`.pyx`)**. The predicate must be
  rendered into the dialect query by `AbstractParser` subclasses
  (SQL/pgSQL/SOSQL/CQL…), not injected into `conditions` upstream.
- **Identity source: `QSPrincipal` first, raw session userinfo as fallback.**
  Rules may reference the typed principal claims (`user_id`, `username`,
  `groups`, `roles`, `programs`, `superuser`, `tenant_id`) *and* any extra
  attribute present in the navigator-auth session userinfo (e.g.
  `attributes.store_id`, `region`).
- **Plumbing**: `QS` / `BaseProvider` resolve the principal + userinfo
  **once** and pass them to the parser constructor as kwargs. The parser
  never touches `aiohttp` or `navigator_auth` and stays testable in isolation.
- **No principal/session → HTTP 403.** Fail-closed everywhere.
- **Rename, not reuse**: `h_filtering` → `user_filtering` (bool, switch) and
  a new `user_filter_options` (jsonb, rules). `h_filtering` is kept as a
  deprecated read alias for one minor release, then dropped. Requires a DB
  migration on the query-definitions table(s) and on the tenant model.
- **Program restriction uses `program_slug` from the query definition**,
  never SQL-schema inference. `program_slug == 'default'` is always
  visible (mirrors `build_program_predicate()`).
- **Bypass path**: queries that never reach a parser (`is_raw=True` slugs,
  `qstype='query'` without placeholders, providers with `__parser__ = None`
  such as REST/HTTP/Mongo-like) that have `user_filtering=True` **must not
  run**: fail-closed with a configuration/access error.
- **Edge cases** (all selected):
  - referenced attribute missing or `None` in the principal/session → 403
    (never `IS NULL`, never silently skipped) unless the rule says
    `required: false`;
  - `superuser=True` bypasses both the column filters and the program
    restriction, with an audit log line;
  - `bypass` is configurable per definition (groups/roles that skip the
    filter), with `["superuser"]` as the implicit default;
  - `'default'` program is always allowed.
- **Rule syntax: Option B (typed rule list)** — see Feature Description.
- **Security invariants**: the user filter always *overrides* a caller-
  supplied filter on the same column; values are rendered through the
  parser's existing typed/escaped `where_cond` path (no string concatenation
  of session values); the rendered predicate must be part of the result
  cache key (today `BaseProvider.checksum()` hashes the final query, so
  parser-level rendering satisfies this for free).
- **Out of scope (explicit follow-up)**: applying the same rules in
  `MultiQS` (multi-query). The rule-evaluation module must nevertheless be a
  plain, parser-independent Python module so the follow-up only has to call
  it from `querysource/queries/multi/`.
- Conventions: `aiohttp`/`navconfig`, `self.logger`, Google docstrings,
  strict typing, Cython `.pxd` updated for every new `cdef` attribute,
  Cython rebuild required before tests see parser changes.

---

## Options Explored

### Option A: Parser-native `user_filter` step fed by a pure rule-compiler module

A new plain-Python module (`querysource/auth/user_filter.py`, name
indicative) owns the rules: it validates `user_filter_options`, resolves each
rule's attribute against `QSPrincipal` → raw userinfo, decides bypass,
performs the program check, and returns either a **deny** (reason) or a
`dict[column, value]` ready to be merged into the parser's `filter`.
`AbstractParser` gains two public attributes (`principal`, `userinfo`) set from
constructor kwargs, and one new extraction step (`_user_filter_sync`, placed
after `_qs_filters_sync` inside `_extract_options`) that calls the module
when `definition.user_filtering` is true. The step overwrites any
same-named key in `self.filter`, so the predicate is rendered by the existing
`where_cond` / `filter_conditions` pipeline in every dialect parser (lists →
`IN (...)`, scalars → `=`). `QS` resolves the principal (explicit
`principal` kwarg, else `principal_from_session(request['user_session'])`)
plus the raw userinfo and passes both to the provider; `BaseProvider`
forwards them to the parser. `BaseProvider` also performs the **bypass
guard**: `definition.user_filtering` on a path with no parser (or `is_raw`)
raises before any connection is opened.

✅ **Pros:**
- Matches the discovery decision (enforcement inside the parser); every
  dialect parser gets it through the shared `_extract_options` pipeline.
- Values travel through the existing typed, escaped `where_cond` machinery:
  no new SQL-building surface, no injection vector.
- Cache safety for free: the predicate is in the rendered SQL that
  `checksum()` hashes.
- The compiler module is pure Python → unit-testable without Cython, and
  directly reusable by the multi-query follow-up and by `describe`.
- Minimal Cython delta: two attributes, one step, `.pxd` additions.

❌ **Cons:**
- Requires a Cython rebuild (`make build-cython`) and `.pxd` discipline.
- `cdef void` extractors cannot raise cleanly; the step must be declared
  `except *` or the raise must happen from `set_options()` (async). Needs
  care so a deny is not swallowed.
- Parser-less providers only get the *guard*, never the filter (by design,
  but it limits coverage to parser-backed providers).

📊 **Effort:** Medium

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `cython` (already a build dep) | rebuild `abstract.pyx`/`.pxd` | `make build-cython` / `build-inplace` |
| `datamodel` / Pydantic (already used) | validate `user_filter_options` into a typed `UserFilterOptions` model | follow `semantic/models.py` style (`extra="forbid"`) |
| `navigator-auth` (optional import, already pattern) | `AUTH_SESSION_OBJECT` key for raw userinfo | same try/except as `principal_from_session()` |

🔗 **Existing Code to Reuse:**
- `querysource/parsers/abstract.pyx:159` `_extract_options()` — add the new step after `_qs_filters_sync()` (line 338).
- `querysource/parsers/abstract.pyx:586` `where_cond()` and `querysource/parsers/sql.pyx:143` `filter_conditions()` — render the merged `self.filter`.
- `querysource/semantic/governance.py:17` `principal_from_session()` — build the principal from the cached session.
- `querysource/auth/slug_visibility.py:67` `normalize_programs()` and `:184` `build_program_predicate()` — program normalization and the `'default'` rule to mirror.
- `querysource/queries/qs.py:210` — the cached `request['user_session']` already extracted for PBAC credentials; `:312` already forwards `principal` to the provider.
- `querysource/providers/abstract.py:128` — parser instantiation site to extend with the identity kwargs.
- `querysource/providers/sql.py:147` — `use_parser` decision; the bypass guard lives next to it.

---

### Option B: `QS`-level injection into `conditions` (pre-parser)

`QS.build_provider()` resolves the identity, evaluates the same rule module,
and merges the resulting predicates into the `where_cond`/`filter` key of
`conditions` before the provider instantiates the parser. The parser is
untouched.

✅ **Pros:**
- No Cython changes at all; pure Python, fastest to ship.
- Naturally reusable for `MultiQS`, which also builds `conditions`.

❌ **Cons:**
- Rejected in discovery: the user wants enforcement in the parser.
- Caller-supplied `where_cond` is merged *before* the injection point in
  some paths (`_query_filter_sync` pops `where_cond` then `filter`), so the
  override guarantee is harder to prove; conditions dicts are also mutated
  in several places (`_merge_conditions_and_filters`), widening the audit.
- Does not help parser-less providers either; still needs the guard.

📊 **Effort:** Low

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| none new | — | pure Python |

🔗 **Existing Code to Reuse:**
- `querysource/queries/qs.py:278-314` — conditions merge + provider args.
- `querysource/parsers/abstract.pyx:318` `_query_filter_sync()` — where injected keys would land.

---

### Option C: Register it as a legacy `QS_FILTERS` plugin (like the old `hfilter`)

Implement `user_filter` as a function in `querysource/libs/functions/`
registered through `QUERYSOURCE_FILTERS` (settings) and triggered by a
reserved `conditions` key, exactly like `qry_options` / `grouping_set`.
`QS` would inject the resolved identity into that reserved key.

✅ **Pros:**
- Zero parser changes beyond what exists (`get_query_filters()` loop in
  every dialect at `sql.pyx:445`, `pgsql.pyx:685`, `sosql.pyx:215`,
  `cql.pyx:177`).
- Deployments can turn it on per environment via settings.

❌ **Cons:**
- The plugin signature is `fn(options, where, program)` and runs in a thread
  executor: no principal, no logger context, no clean way to deny (returns
  a dict, cannot raise 403 through `run_in_executor` without wrapping).
- Enforcement becomes *opt-in by settings*: a deployment that forgets the
  registry entry silently runs unfiltered — violates fail-closed.
- The reserved conditions key would be caller-settable unless QS strips it,
  reintroducing the spoofing problem.

📊 **Effort:** Low–Medium

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| none new | — | settings-driven |

🔗 **Existing Code to Reuse:**
- `querysource/services.py:131` — `QUERYSOURCE_FILTERS` loader.
- `querysource/libs/functions/__init__.py:33` `query_options()` — plugin shape.

---

### Option D (unconventional): Push-down to PostgreSQL Row-Level Security

Instead of rewriting the query, `QS` sets per-request session GUCs
(`SET LOCAL app.user_id = ...; SET LOCAL app.programs = ...`) on the
connection before execution, and DBAs define RLS policies on the tables.
`user_filter_options` becomes a list of GUC names to publish.

✅ **Pros:**
- Enforced by the database even for `is_raw` slugs and ad-hoc SQL; the
  strongest possible guarantee.
- No parser or dialect work.

❌ **Cons:**
- PostgreSQL-only; useless for SOSQL/CQL/BigQuery/SQL Server/REST.
- Pooled `asyncdb` connections make `SET LOCAL` require an explicit
  transaction per query; cross-talk risk if a path forgets it.
- Moves the policy out of QuerySource into DBA-managed objects: no
  `describe` visibility, no unit tests in this repo, harder rollout.
- Result cache keyed on the rendered SQL would **leak** across users (same
  SQL, different GUCs) unless the cache key is extended.

📊 **Effort:** High (cross-team)

📦 **Libraries / Tools:**
| Package | Purpose | Notes |
|---|---|---|
| `asyncdb` (existing) | run `SET LOCAL` inside a transaction | needs per-query transaction wrapper |

🔗 **Existing Code to Reuse:**
- `querysource/connections.py` `QueryConnection` — where a GUC hook would live.

---

## Recommendation

**Option A** is recommended because:

- It is the only option that satisfies all three hard constraints at once:
  enforcement in the parser (user decision), fail-closed semantics, and
  cache safety without new machinery.
- Keeping the rule compiler as a **pure Python module** neutralizes the main
  cost of parser-level work: the logic is unit-tested without Cython and
  the multi-query follow-up reuses it as-is; the `.pyx` delta is a thin
  adapter (two attributes, one step).
- Option B is cheaper but was explicitly declined and has a weaker override
  story; Option C makes security opt-in via settings; Option D is PG-only
  and cross-team.
- What we trade off: a Cython rebuild in the dev loop, and the need to be
  precise about exception propagation from `cdef` code (mitigated by raising
  from `set_options()` or declaring `except *`).

---

## Feature Description

### User-Facing Behavior

- **Slug authors** set two columns on a query definition:
  - `user_filtering: bool` — switch (replaces `h_filtering`).
  - `user_filter_options: jsonb` — typed rule list:

    ```json
    {
      "rules": [
        {"column": "user_id", "attribute": "user_id"},
        {"column": "store_id", "attribute": "attributes.store_id",
         "operator": "in", "required": true}
      ],
      "program": {"restricted": true, "allow_default": true},
      "bypass": ["superuser"]
    }
    ```

    | Field | Meaning | Default |
    |---|---|---|
    | `rules[].column` | Column (optionally `alias.column`) of the query to constrain | required |
    | `rules[].attribute` | Dotted path into the identity: typed `QSPrincipal` claims first, then raw session userinfo | required |
    | `rules[].operator` | `eq` / `in` (`in` is implied when the attribute value is a list) | `eq` |
    | `rules[].required` | `true` → missing/`None` attribute denies with 403; `false` → rule skipped | `true` |
    | `program.restricted` | Deny unless `definition.program_slug ∈ session.programs` | `false` |
    | `program.allow_default` | `program_slug == 'default'` always passes | `true` |
    | `program.attribute` | Identity attribute holding the program list | `programs` |
    | `bypass` | Groups/roles (case-insensitive) or the literal `superuser` that skip **all** rules and the program check | `["superuser"]` |

- **Callers** see nothing new on success: the result set is narrowed. On
  failure they receive **HTTP 403** with a generic message (never the rule
  that failed), in these cases: no session/principal; required attribute
  missing; program restriction not met; definition flagged
  `user_filtering` but executed on a parser-less/raw path.
- **`describe`** (`/api/v1/queries/{slug}` HEAD/OPTIONS) reports
  `user_filtering` (and keeps `h_filtering` as an alias for one minor
  release); rules are exposed only to admin principals (same redaction
  policy as governance roles).
- **Audit**: one `INFO` log line per bypass (who, why) and one `WARNING`
  per deny (slug, reason code, principal `log_fields()`).

### Internal Behavior

1. **Identity resolution (once, in `QS`)**: `principal = self._principal`
   if given (programmatic callers), else `principal_from_session()` over
   the cached `request['user_session']`; `userinfo` is the raw
   `session[AUTH_SESSION_OBJECT]` dict (may be empty for library callers).
   Both are added to the provider `args` next to the existing `principal`.
2. **Provider guard (`BaseProvider`/`SQLProvider`)**: if
   `definition.user_filtering` is true and the execution path will not run a
   parser (`__parser__ is None`, `is_raw`, or `use_parser` false), raise the
   403-class error before connecting. Otherwise forward `principal` and
   `userinfo` to the parser constructor.
3. **Parser step (`AbstractParser._user_filter_sync`)**, after
   `_qs_filters_sync()`: if `definition.user_filtering`, call the rule
   compiler with `(options, program_slug, principal, userinfo)`.
   - bypass match → log, skip;
   - deny → raise (403) — propagated from `set_options()`;
   - allow → `self.filter.update(predicates)` (user filter wins over
     caller-supplied keys) and record applied columns for logging.
4. **Rendering**: unchanged — `filtering_options()` / `filter_conditions()`
   turn `self.filter` into dialect predicates; list values become `IN`.
5. **Caching**: unchanged — the rendered query differs per user, so
   `checksum()` already partitions the cache.
6. **Model/migration**: `QueryModel` and `TenantQueryDefinition` gain
   `user_filtering` + `user_filter_options`; `h_filtering` stays as a
   deprecated alias populated from `user_filtering` on read. SQL migration
   renames the column and adds the jsonb column with default `'{}'`.
7. **Validation**: `user_filter_options` is parsed into a typed model at
   definition load (or lazily on first use) with `extra="forbid"`; an
   invalid document is a configuration error (500) — never silently
   ignored.

### Edge Cases & Error Handling

- **No session and no principal** → 403, even when PBAC is globally
  disabled (`QS_PBAC_ENABLED=False` does not turn off user filtering: the
  switch is per definition).
- **Authz-form principal** (`QSPrincipal.for_authz`, sessionless backend):
  has no `user_id`/programs → every rule with `required: true` denies, and
  the program check denies unless `allow_default` applies. Documented as
  intended.
- **Attribute present but empty list** (`programs: []`, `in` operator) →
  treated as missing → 403 when required (an empty `IN ()` is never
  rendered).
- **Caller passes the same column in `where_cond`** → overwritten by the
  rule value; logged at DEBUG.
- **Rule column not in the query** → rendered as a plain WHERE predicate;
  the DB raises (column does not exist). Not validated at parse time
  (open question: lint via `describe` columns).
- **Multiple schemas / joins** → irrelevant: program restriction is by
  `program_slug`, filters are by explicit column name (aliases allowed).
- **`user_filtering=True` on REST/HTTP/non-parser providers or `is_raw`
  slugs** → 403-class config error at provider init; never executes.
- **`user_filter_options` empty or missing while `user_filtering=True`** →
  configuration error (500): a switch without rules is a mistake, not
  "filter nothing".
- **Superuser** → bypass everything, `INFO` audit line.
- **Tenant stores** (`TenantQueryDefinition`): same columns; the program
  attribute still comes from the session, `program_slug` from the row.
- **Exception propagation from Cython**: the deny must surface as the
  403-class exception, not be swallowed into `ParserError` (400) by
  `prepare_connection()`'s broad `except Exception` at
  `providers/sql.py:157-160`; the spec must add it to the re-raise list the
  way `QueryAccessDenied` is already special-cased in `qs.py:316`.

---

## Capabilities

### New Capabilities
- `user-filtering`: declarative per-user row filtering compiled from
  `user_filter_options` and the caller's identity, enforced in the parser.
- `program-restriction`: deny execution when the definition's
  `program_slug` is not among the caller's programs (`'default'` exempt).
- `user-filter-guard`: fail-closed refusal of `user_filtering` definitions
  on parser-less or raw execution paths.

### Modified Capabilities
- `describe-queryslug` (`sdd/specs/describe-queryslug.spec.md`): expose
  `user_filtering`, alias `h_filtering`, admin-only rule visibility.
- `tenant-definition-model` (TASK-717): new columns on
  `TenantQueryDefinition`.
- `pbac-request-credentials` (`sdd/specs/pbac-request-credentials.spec.md`):
  programmatic callers must pass `principal` (and optionally `userinfo`) to
  satisfy `user_filtering` slugs.

---

## Impact & Integration

| Affected Component | Impact Type | Notes |
|---|---|---|
| `querysource/models.py` `QueryModel` | modifies | `h_filtering` → `user_filtering` (+ deprecated alias), add `user_filter_options` jsonb |
| `querysource/tenant_models.py` `TenantQueryDefinition` | modifies | same two fields |
| DB schema (query definitions table, tenant tables) | migration | rename + add column; `docs/sql/` has no DDL for this table — migration location is an open question |
| `querysource/auth/user_filter.py` (new) | adds | typed options model + rule compiler + deny reasons |
| `querysource/exceptions.py` | extends | new 403-class exception (`QueryAccessDenied` is deliberately 404) |
| `querysource/parsers/abstract.pyx` / `.pxd` | modifies | `principal`, `userinfo` attrs; `_user_filter_sync` step; raise from `set_options()` |
| `querysource/parsers/sql.pyx`, `pgsql.pyx`, `sosql.pyx`, `cql.pyx` | depends on | no change expected; they already render `self.filter` |
| `querysource/providers/abstract.py` `BaseProvider` | modifies | forward identity kwargs to parser; guard for `__parser__ is None` |
| `querysource/providers/sql.py` `SQLProvider` | modifies | guard on `is_raw` / `use_parser=False`; re-raise the 403 class |
| `querysource/queries/qs.py` `QS` | modifies | resolve principal/userinfo once; pass to provider; add new exception to the re-raise tuple |
| `querysource/queries/describe.py` | modifies | `user_filtering` in capabilities, alias, redaction |
| `querysource/queries/multi/__init__.py` `MultiQS` | follow-up | consumes the same compiler later (out of scope) |
| `querysource/libs/functions/__init__.py`, `services.py` `QUERYSOURCE_FILTERS` | untouched | legacy plugin registry stays |
| `docs/` | adds | `USER_FILTERING.md` + changelog entry for the rename |
| `tests/auth/`, `tests/providers/`, parser tests (`tests/test_pgsql_*.py` style) | adds | compiler unit tests, parser rendering tests, guard tests, describe tests |

Breaking changes: column rename (mitigated by alias + migration). No new
third-party dependencies.

---

## Code Context

### User-Provided Code

```python
# Source: user-provided (legacy settings excerpt, QUERYSOURCE_FILTERS)
"qry_options": "querysource.libs.functions.query_options",
"grouping_set": "querysource.libs.functions.grouping_set",
"group_by_child": "querysource.libs.functions.group_by_child",
```

```json
// Source: user-provided (original syntax idea for the new options column)
{"user_id": "user_id"}
```

```json
// Source: user-selected during discovery (Round 2, syntax Option B)
{
  "rules": [
    {"column": "user_id", "attribute": "user_id"},
    {"column": "store_id", "attribute": "attributes.store_id",
     "operator": "in", "required": true}
  ],
  "program": {"restricted": true},
  "bypass": ["superuser"]
}
```

### Verified Codebase References

#### Classes & Signatures
```python
# From querysource/models.py:49
class QueryModel(Model):
    qry_options: Optional[dict] = Field(required=False, db_type='jsonb', default_factory=dict)  # line 75
    h_filtering: bool = Field(required=False, default=False, comment="filtering based on Hierarchical rules.")  # line 76
    program_slug: str = Field(required=True, default='default')  # line 88

# From querysource/tenant_models.py:24
class TenantQueryDefinition(BaseModel):
    h_filtering: bool = Field(required=False, default=False, comment='filtering based on Hierarchical rules.')  # line 58

# From querysource/auth/principal.py:21 (frozen dataclass)
class QSPrincipal:
    user_id: str
    username: str | None = None
    groups: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    programs: tuple[str, ...] = ()
    superuser: bool = False
    tenant_id: str | None = None
    channel: str = "library"
    authz_backend: str | None = None
    def to_userinfo(self) -> dict[str, Any]: ...  # line 97
    def log_fields(self) -> dict[str, Any]: ...   # line 120

# From querysource/semantic/governance.py:17
def principal_from_session(session: Any | None) -> QSPrincipal | None: ...
# From querysource/semantic/governance.py:179
def governance_filters(resource: SemanticResource, principal: QSPrincipal | None) -> tuple[list[SemanticFilter | str], list[str]]: ...

# From querysource/auth/slug_visibility.py:67
def normalize_programs(raw: Any) -> tuple[str, ...]: ...
# From querysource/auth/slug_visibility.py:184
def build_program_predicate(...)  # adds 'default' to the programs list (line 200)

# From querysource/auth/enforcement.py:151
async def enforce_principal(principal: QSPrincipal, resource_type: Any, resource_name: str,
                            action: str, *, tenant: str | None = None,
                            logger: logging.Logger | None = None) -> AccessDecision: ...

# From querysource/exceptions.py
class ConfigError(QueryException):          # line 33, code=500
class QueryAccessDenied(QueryException):    # line 63, code 404 BY DESIGN (not-found semantics)
class ParserError(QueryException):          # line 86, default_code = 400

# From querysource/parsers/abstract.pyx
cdef class AbstractParser:
    def __cinit__(self, *args, definition: object, conditions: object, query: str = None, **kwargs)  # line 39
    cdef void _extract_options(self)                 # line 159 (calls the *_sync steps in order)
    cdef void _program_slug_sync(self)               # line 183 → self.program_slug = definition.program_slug
    cdef void _query_filter_sync(self)               # line 318 → self.filter from where_cond/filter/definition.filtering
    cdef void _qs_filters_sync(self)                 # line 338 → QS_FILTERS hook (legacy hfilter)
    cpdef dict get_query_filters(self)               # line 410
    async def set_options(self)                      # line 417 (async; safe place to raise)
    cdef object _merge_conditions_and_filters(self, dict conditions)  # line 485
    cpdef object where_cond(self, dict where)        # line 586

# From querysource/parsers/abstract.pxd
    cdef public str program_slug      # line 23
    cdef public dict _query_filters   # line 41
    cdef void _qs_filters_sync(self)  # line 81

# From querysource/parsers/sql.pyx
    async def filter_conditions(self, sql)                                          # line 143
    async def build_query(self, querylimit: int = None, offset: int = None)         # line 431
        for _, func in self.get_query_filters().items():                            # line 445 (QS_FILTERS loop)
    # list values render as "{key} IN ({val})"                                       # line 233
# Same QS_FILTERS loop: pgsql.pyx:685, sosql.pyx:215, cql.pyx:177

# From querysource/providers/abstract.py
class BaseProvider:
    _parser_options: dict = {}                       # line 38
    self._parser = self.__parser__(query=..., definition=..., conditions=..., **self._parser_options)  # line 128
    def checksum(self): return get_hash(self._query) # line 330 (hashes the RENDERED query)

# From querysource/providers/sql.py
    __parser__ = SQLParser                           # line 43
    use_parser = self._needs_parser(self._query) or self._has_parser_conditions(self._conditions)  # line 147

# From querysource/queries/qs.py
class QS(BaseQuery):                                  # line 51
    _cached = self._request.get('user_session', _SENTINEL)   # line 210 (cached navigator session)
    "principal": self._principal,                            # line 312 (already forwarded to provider)
# From querysource/queries/base.py:35
    principal: "QSPrincipal | None" = None,   # BaseQuery.__init__ kwarg

# From querysource/queries/describe.py:267
    "h_filtering": bool(base.get("h_filtering") or False),

# From querysource/queries/multi/__init__.py:225
class MultiQS(BaseQuery):   # __init__ already accepts user_session: object | None

# From querysource/handlers/abstract.py:313
    async def _get_user_session(...)   # memoizes request['user_session']

# From querysource/services.py:131
        for name, fn in QUERYSOURCE_FILTERS.items():   # legacy plugin loader → QS_FILTERS
# From querysource/libs/functions/__init__.py:33
def query_options(options: dict, where: dict = None, program: str = 'default', hierarchy: list = None): ...

# From querysource/semantic/models.py:116
class GovernanceBlock(BaseModel):   # role-keyed row_filters precedent (extra="forbid")

# From querysource/conf.py:466
QS_PBAC_ENABLED = config.getboolean('QS_PBAC_ENABLED', fallback=False)
```

#### Verified Imports
```python
from querysource.auth.principal import QSPrincipal                    # querysource/auth/principal.py:21
from querysource.semantic.governance import principal_from_session    # querysource/semantic/governance.py:17
from querysource.auth.slug_visibility import normalize_programs       # querysource/auth/slug_visibility.py:67
from querysource.auth.enforcement import enforce_principal            # querysource/auth/enforcement.py:151
from querysource.exceptions import QueryAccessDenied, ConfigError, ParserError  # querysource/exceptions.py:63/33/86
from querysource.parsers import QS_FILTERS                            # querysource/parsers/__init__.py:9 (module-level dict)
```

#### Key Attributes & Constants
- `QueryModel.h_filtering` → `bool` (querysource/models.py:76) — to be renamed.
- `QueryModel.program_slug` → `str`, default `'default'` (querysource/models.py:88).
- `AbstractParser.program_slug` → `str` (abstract.pxd:23), set at abstract.pyx:183.
- `AbstractParser.filter` → `dict` (public) — merged predicates rendered by every dialect.
- `BaseProvider._parser_options` → `dict` (providers/abstract.py:38) — per-provider parser kwargs.
- `request['user_session']` — memoized navigator session (handlers/abstract.py:313; read at qs.py:210).
- `QS_PBAC_ENABLED` (conf.py:466) — must **not** gate user filtering.
- Tests live under `tests/auth/` (e.g. `tests/auth/test_principal.py`), `tests/semantic/test_governance.py`, parser tests at `tests/test_pgsql_*.py`.

### Does NOT Exist (Anti-Hallucination)
- ~~`user_filtering`~~, ~~`user_filter_options`~~, ~~`h_filter_options`~~ — no such columns/attributes anywhere in `querysource/` (new in this feature).
- ~~`AbstractParser._user_filter_sync`~~ — not a real step yet.
- ~~`AbstractParser.principal` / `AbstractParser.userinfo` / `AbstractParser.request`~~ — the parser receives **no** identity or request today (`__cinit__` only takes `definition`, `conditions`, `query`, `**kwargs`).
- ~~`UserFilterError` / `UserFilterDenied`~~ — no 403-class exception exists; `QueryAccessDenied` is 404 by design.
- ~~`querysource.auth.user_filter`~~ — module to be created.
- ~~an `hfilter` function in `querysource/libs/functions/`~~ — only `query_options`, `grouping_set`, `group_by_child`, `first_day`, `last_day` exist; `h_filtering` is read by nothing except `describe.py:267`.
- ~~`QUERYSOURCE_FILTERS` in `docs/settings.py.example`~~ — not documented there; it is imported from `settings.settings` at `services.py:37` with `{}` fallback.
- ~~DDL for the query-definitions table in `docs/sql/`~~ — only `datasources_logic.sql` and `useful_pg_extensions.sql` exist.
- ~~`sdd/CONVENTIONS.md` project areas~~ — the section is an empty template; `projects` above are derived from code paths.

---

## Parallelism Assessment

- **Internal parallelism**: moderate. Three independent seams:
  (1) models + migration + describe (`models.py`, `tenant_models.py`,
  `describe.py`, docs); (2) the pure rule-compiler module + exception +
  unit tests (`auth/user_filter.py`, `exceptions.py`); (3) parser/provider
  wiring (`abstract.pyx/.pxd`, `providers/abstract.py`, `providers/sql.py`,
  `qs.py`) which depends on (2)'s interface and (1)'s field names.
- **Cross-feature independence**: `abstract.pyx` and `qs.py` are hot files.
  In-flight specs that touch them: `filter-with-partial-matching`,
  `filter-eval-hardening`, `jsonb-not-or-operators` (parser), and the
  multiquery family (`qs.py`/`multi`). Active tasks (TASK-674…694:
  airtable, scheduler, remote executor) do not overlap.
- **Recommended isolation**: `per-spec` — one worktree, tasks sequential,
  because the Cython rebuild and the `.pxd` edits serialize naturally and
  seam (3) consumes both others.
- **Rationale**: the compiler module is where parallel work would pay off,
  but it is small; the cost of two agents rebuilding Cython in separate
  worktrees outweighs the gain.

---

## Open Questions

- [ ] Which SQL migration mechanism/location is canonical for the query-definitions table (`troc.query_definitions` and tenant tables)? `docs/sql/` has no DDL for it. — *Owner: Jesus Lara*
- [ ] Exact HTTP code: the user asked for **403**, but `QueryAccessDenied` is intentionally 404 (hides existence). Introduce a new 403-class exception for user-filter denials, or collapse to 404 for consistency? Brainstorm assumes a new 403 class. — *Owner: Jesus Lara*
- [ ] Should rule columns be validated against the slug's `columns_definition` / describe columns at definition-save time (lint) or only fail at DB execution? — *Owner: Jesus Lara*
- [ ] Should `bypass` also accept a PBAC action (e.g. `"user_filter:bypass"`) evaluated through `enforce_principal`, in addition to group/role names? — *Owner: Jesus Lara*
- [ ] Deprecation window for the `h_filtering` alias: one minor (5.3.0) as assumed, matching the `QuerySource(loop=...)` DeprecationWarning precedent? — *Owner: Jesus Lara*
- [ ] Programmatic callers (`QS(principal=...)` without a session): should they be able to pass extra `userinfo` attributes, or are typed `QSPrincipal` claims the only identity source for the library channel? — *Owner: Jesus Lara*
- [x] Enforcement layer — *Owner: Jesus Lara*: inside the parser (`.pyx`), fed by a pure Python rule module.
- [x] Identity source — *Owner: Jesus Lara*: `QSPrincipal` first, raw session userinfo as fallback.
- [x] Column naming — *Owner: Jesus Lara*: rename `h_filtering` → `user_filtering`, add `user_filter_options`, keep deprecated alias.
- [x] Program determination — *Owner: Jesus Lara*: `program_slug` of the query definition only; `'default'` always allowed.
- [x] Parser-less / raw paths — *Owner: Jesus Lara*: fail-closed, never execute unfiltered.
- [x] Missing attribute / superuser / bypass — *Owner: Jesus Lara*: missing → 403; superuser bypasses; `bypass` list configurable per definition.
- [x] Rule syntax — *Owner: Jesus Lara*: Option B, typed rule list.
- [x] Multi-query — *Owner: Jesus Lara*: follow-up feature; compiler module designed for reuse.
