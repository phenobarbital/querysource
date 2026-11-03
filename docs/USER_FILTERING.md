# Per-user automatic filtering (`user_filtering`, FEAT-184)

## Overview

Per-user automatic filtering lets a query definition declare that its rows are
constrained by the caller's identity. When `user_filtering` is enabled, the
parser automatically injects predicates into the query's `WHERE` clause based
on rules defined in `user_filter_options`. This happens **inside the parser**,
before any database connection is opened, and cannot be spoofed by the caller.

The feature replaces the legacy `h_filtering` column (deprecated, removed in
5.3.0). Unlike the old column, `user_filtering` is actually enforced.

## Enabling a slug

Set `user_filtering = true` and provide a `user_filter_options` document:

```sql
UPDATE public.queries
SET user_filtering = true,
    user_filter_options = '{
      "rules": [
        {"column": "user_id", "attribute": "user_id"},
        {"column": "store_id", "attribute": "attributes.store_id", "operator": "in"}
      ],
      "program": {"restricted": true},
      "bypass": ["superuser"]
    }'::jsonb
WHERE query_slug = 'my_slug';
```

Both columns must exist on the query store. Run the migration script first:

```bash
psql -f docs/sql/user_filtering.sql
```

## Rule syntax

`user_filter_options` is a JSON document with three top-level keys:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `rules` | array of `UserFilterRule` | `[]` | List of column constraints |
| `program` | `ProgramRestriction` | `{}` | Optional program restriction |
| `bypass` | array of strings | `["superuser"]` | Groups/roles that skip filtering |

### UserFilterRule

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `column` | string | **required** | Column to constrain (optionally `alias.column`) |
| `attribute` | string | **required** | Dotted path into identity (e.g., `user_id`, `attributes.store_id`) |
| `operator` | `"eq"` or `"in"` | `"eq"` | Comparison operator; `"in"` is implied when the value is a list |
| `required` | boolean | `true` | `true` → missing attribute denies (403); `false` → rule skipped |

Column names must match `^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$`.

### ProgramRestriction

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `restricted` | boolean | `false` | Deny unless caller's programs include `definition.program_slug` |
| `allow_default` | boolean | `true` | `program_slug == 'default'` always passes |
| `attribute` | string | `"programs"` | Identity attribute holding the program list |

### Example document

```json
{
  "rules": [
    {"column": "user_id", "attribute": "user_id"},
    {"column": "store_id", "attribute": "attributes.store_id", "operator": "in", "required": true}
  ],
  "program": {"restricted": true, "allow_default": true},
  "bypass": ["superuser", "data-admins"]
}
```

## Identity resolution

Rules are resolved against the caller's identity in this order:

1. **Typed `QSPrincipal` claims** — the principal passed to `QS(principal=...)`
   or extracted from the HTTP session. Checked first for every attribute.
2. **Raw session userinfo** — the navigator-auth userinfo dict, used as a
   fallback for attributes not present on the principal.

For example, with attribute `attributes.store_id`:
- First, look for `principal.attributes["store_id"]`
- If absent or `None`, look for `userinfo["attributes"]["store_id"]`

**Library callers** must pass `QS(slug=..., principal=QSPrincipal(...))`.
Without a principal, the call fails with 403 (`UserFilterDenied`), even when
`QS_PBAC_ENABLED` is off.

**Authz-form principals** (`QSPrincipal.for_authz(...)`): typed claims other
than `groups`/`roles` are treated as absent, so every `required` rule denies
(`reason_code="authz_principal"`) and the program check denies unless
`allow_default` applies.

## Program restriction

When `program.restricted` is `true`, execution is denied unless the caller's
programs include `definition.program_slug`. The `'default'` program always
passes when `allow_default` is `true`.

For tenant stores, `program_slug` is derived from the schema name
(`epson.users` → `epson`), so the restriction works automatically.

## Bypass

The `bypass` list grants exemption from all rules and the program check. The
literal `"superuser"` matches `principal.superuser == true`. Other strings
are matched case-insensitively against `principal.groups` and `principal.roles`.

When bypassed, the query runs unfiltered and an INFO-level audit line is logged
with the bypass reason (`"superuser"`, `"group:<name>"`, or `"role:<name>"`).

## Errors and reason codes

All denials answer **403 Forbidden** with a generic body. The `reason_code`
appears only in logs:

| Reason code | Meaning |
|-------------|---------|
| `no_principal` | No principal and no session userinfo |
| `authz_principal` | Authz-form principal with a required rule |
| `missing_attribute` | Required attribute is `None`, empty, or missing |
| `program_restricted` | Program restriction not satisfied |
| `parser_required` | `user_filtering=True` on a parser-less or raw path |

Invalid or empty `user_filter_options` (when `user_filtering=True`) answers
**500** (`ConfigError`).

No session → 403 even with `QS_PBAC_ENABLED=false`. The feature is independent
of the PBAC runtime.

## Caching

The rendered predicate is part of the query string hashed by `checksum()`, so
results are cached per-user automatically. Two callers with different
identities get different cache keys.

## Migration

Run `docs/sql/user_filtering.sql` on every query store:

```bash
# Legacy store
psql -c "\i docs/sql/user_filtering.sql"

# Tenant stores (repeat for each schema)
psql -c "ALTER TABLE epson.queries ADD COLUMN IF NOT EXISTS user_filtering boolean NOT NULL DEFAULT false;"
psql -c "ALTER TABLE epson.queries ADD COLUMN IF NOT EXISTS user_filter_options jsonb NOT NULL DEFAULT '{}'::jsonb;"
```

The migration is **ADD-only**; `h_filtering` is kept until 5.3.0 because the
models reject unknown columns. Un-migrated stores read `user_filtering=False`.

## Deprecation of `h_filtering`

`h_filtering` is a deprecated alias of `user_filtering`. It is still accepted
in API writes, never consulted at runtime, and reported in describe responses
as an alias. The column will be dropped in 5.3.0.

## Limitations

- **Multi-query**: `MultiQS` does not yet apply user filtering. The
  `compile_user_filter()` function is designed for reuse in a follow-up
  feature.
- **Parser-less providers**: Slugs with `user_filtering=True` whose provider
  has `__parser__ = None` are refused with 403 (`parser_required`).
- **Raw slugs**: Raw execution (`is_raw=True`) is similarly refused.
- **Legacy `QS_FILTERS` plugins**: These run after the user-filter step and
  can overwrite rule columns. This is a deployment-trusted code path, not a
  caller-spoofing vector.
