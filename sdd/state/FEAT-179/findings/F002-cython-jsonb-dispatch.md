# F002 — Cython JSONB operator dispatch (the code to extend)

- **Queries**: Q005 (grep `"@` in parsers), Q007 (read pgsql.pyx:110-270)

## Digest

`querysource/parsers/pgsql.pyx`:

- `JSONB_OPERATORS = ('@>', '<@', '@>|', '->', '->>',)` — pgsql.pyx:30.
  Operator tokens accepted as keys of a **dict-typed filter value**
  (i.e. `{"<column>": {"<op>": operand}}`), NOT top-level where_cond keys.
- `JSONB_KEY_SUFFIXES = '|!~#@:'` — pgsql.pyx:33: suffixes stripped from the
  column name.
- `jsonb_any_of_condition(col, operand)` — pgsql.pyx:86-113: renders
  `{"@>|": [a, b]}` as `(col @> 'a'::jsonb OR col @> 'b'::jsonb)`. This is the
  existing **OR** support. Single item → unparenthesized single check;
  non-list/empty operand → None (condition dropped).
- `jsonb_condition(col, value)` — pgsql.pyx:151-199: dispatch. Semantics:
  * no operator keys → implicit containment `col @> '<json>'::jsonb`
    (a dict of several plain keys is **AND** via JSONB containment — this is
    the existing "AND support").
  * `@>` / `<@` → explicit containment (pgsql.pyx:193-194)
  * `@>|` → any-of OR (pgsql.pyx:195-196)
  * `->` / `->>` → path comparisons, AND-ed (pgsql.pyx:116-148)
  * dict mixing operator and plain keys (`operators != len(value)`) → dropped
    (`(True, None)`) — pgsql.pyx:191-192.
  * dispatch uses only the FIRST key (`next(iter(value.items()))`,
    pgsql.pyx:180); a dict with two operator keys passes the
    `operators == len(value)` check but only the first operator is rendered.
- Caller loop `_filter_conditions_cy` (pgsql.pyx:233-268): validates key as a
  safe SQL identifier (FEAT-103, pgsql.pyx:236-247) — a top-level `"@!"` key in
  where_cond would be **rejected** by this validation (not alnum/underscore).
  All rendered conditions are joined with `' AND '` (pgsql.pyx:436).
- `pg_literal()` (pgsql.pyx:42-60): brace/backslash-safe literal quoting
  (E'...' with \x7b/\x7d) so later format_map passes survive.

## Citations
- querysource/parsers/pgsql.pyx:30-33, 86-113, 116-148, 151-199, 233-268, 436
