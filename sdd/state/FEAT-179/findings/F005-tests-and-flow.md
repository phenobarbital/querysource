# F005 — Test harness and where_cond request flow

- **Queries**: Q009/Q010 (wiki page for tests file), Q011 (grep where_cond upstream)

## Digest

**Tests** — `tests/test_pgsql_jsonb_filters.py`:
- `PATHS = ["rust" (skipif not built), "cython"]`; every case runs through
  both `pgsql._rs.pgsql_filter_conditions` and
  `pgSQLParser._filter_conditions_cy`.
- Helpers: `_make_parser` (bypasses set_options/Redis), `_render`, `_where`.
- Existing cases cover: implicit containment, `@>`/`<@`, `@>|` grouping
  (`test_build_query_any_of_is_grouped`), escaping
  (`test_jsonb_values_are_escaped`), invalid-filter dropping, comparison-token
  dicts, mixing with scalar filters, full `build_query` survival of
  format passes. New `@!`/`@$` cases slot into the same parametrization.

**Request flow** — where_cond reaches the parser unvalidated:
- `querysource/models.py:38` — `QueryObject.where_cond: Optional[dict]`
- `querysource/parsers/abstract.pyx:311` —
  `self.filter = self.conditions.pop('where_cond', {})`
- `querysource/parsers/abstract.pyx:560,607-608` — `where_cond()` setter
  rebuilds `self.filter`.
- No upstream operator whitelist: new operator tokens only need the parser
  layer(s) to learn them. But note (F002) the per-key identifier validation
  means where_cond KEYS must be column names — the ticket's literal example
  (`"@!"` as a top-level where_cond key) would be silently skipped today, and
  supporting that shape would require touching the caller loop in both
  builders, not just `jsonb_condition`.

## Citations
- tests/test_pgsql_jsonb_filters.py (structure via wiki page)
- querysource/models.py:38
- querysource/parsers/abstract.pyx:311, 560, 607-608
