---
id: F003
query_id: Q003
type: read
intent: how pgSQLParser (Cython) handles dict values; existing ILIKE support (FEAT-152)
executed_at: 2026-10-07T00:07:00Z
duration_ms: 500
parent_id: null
depth: 0
---

# F003 — `pgsql.pyx` already has a dict-operator extension point: `PG_TEXT_OPERATORS` (ILIKE / NOT ILIKE)

## Summary

`pgsql.pyx` defines `COMPARISON_TOKENS`, `JSONB_OPERATORS` and, since FEAT-152,
`PG_TEXT_OPERATORS = ('ILIKE', 'NOT ILIKE')`. In the Cython dict branch, after the JSONB
check, `op in PG_TEXT_OPERATORS and isinstance(v, str)` renders
`key op pg_literal(unquoted_v)`. The value is first un-quoted (strip outer `'...'`, undo
`''` doubling) because `is_valid()` pre-quotes strings (see F009). The pattern is used
**raw** — wildcard escaping is the caller's job. Any other op is discarded. A legacy
key-suffix form `{"name~": v}` renders `name ILIKE 'v%'` (prefix match) in the str branch.

## Citations

- path: `querysource/parsers/pgsql.pyx`
  lines: 27-36
  symbol: `COMPARISON_TOKENS`, `JSONB_OPERATORS`, `JSONB_KEY_SUFFIXES`, `PG_TEXT_OPERATORS`
  excerpt: |
    COMPARISON_TOKENS = ('>=', '<=', '<>', '!=', '<', '>',)
    JSONB_OPERATORS = ('@>', '<@', '@>|', '@!', '@$', '->', '->>',)
    JSONB_KEY_SUFFIXES = '|!~#@:'
    PG_TEXT_OPERATORS = ('ILIKE', 'NOT ILIKE',)
- path: `querysource/parsers/pgsql.pyx`
  lines: 41-60
  symbol: `pg_literal`
- path: `querysource/parsers/pgsql.pyx`
  lines: 359-400
  symbol: `pgSQLParser._filter_conditions_cy` (dict branch)
  excerpt: |
    if isinstance(value, dict):
        handled, cond = jsonb_condition(key, value)
        if handled: ...; continue
        op, v = next(reversed(value.items()))
        if op in COMPARISON_TOKENS:
            where_cond.append(f"{key} {op} {safe_v}")
        elif op in PG_TEXT_OPERATORS and isinstance(v, str):
            _v = v[1:-1].replace("''", "'") if len(v) >= 2 and v[0] == "'" and v[-1] == "'" else v
            where_cond.append(f"{key} {op} {pg_literal(_v)}")
        else:
            continue
- path: `querysource/parsers/pgsql.pyx`
  lines: 442-451
  symbol: `_filter_conditions_cy` (str branch, `~` / `!~` key suffix → ILIKE prefix)
  excerpt: |
    if end == '~':
        base = str_value[:-1].replace("'", "''")
        val = f"'{base}%'"
        where_cond.append(f"{name} ILIKE {val}")
