# Partial-matching filter operators

`where_cond` / `filter` accept a dict value whose single key is a partial-matching operator:

```json
{
  "where_cond": {"full_name": {"startswith": "andre"}}
}
```

## Operators

| Operator | Meaning | Case | Operand handling |
|---|---|---|---|
| `like` | Match a ready LIKE pattern. | Sensitive | Raw pattern. |
| `not_like` | Do not match a ready LIKE pattern. | Sensitive | Raw pattern. |
| `ilike` | Match a ready LIKE pattern. | Insensitive | Raw pattern. |
| `not_ilike` | Do not match a ready LIKE pattern. | Insensitive | Raw pattern. |
| `startswith` | Match a value at the start of a field. | Sensitive | Raw value, escaped, then suffixed with `%`. |
| `not_startswith` | Do not match a value at the start of a field. | Sensitive | Raw value, escaped, then suffixed with `%`. |
| `istartswith` | Match a value at the start of a field. | Insensitive | Raw value, escaped, then suffixed with `%`. |
| `not_istartswith` | Do not match a value at the start of a field. | Insensitive | Raw value, escaped, then suffixed with `%`. |
| `endswith` | Match a value at the end of a field. | Sensitive | Raw value, escaped, then prefixed with `%`. |
| `not_endswith` | Do not match a value at the end of a field. | Sensitive | Raw value, escaped, then prefixed with `%`. |
| `iendswith` | Match a value at the end of a field. | Insensitive | Raw value, escaped, then prefixed with `%`. |
| `not_iendswith` | Do not match a value at the end of a field. | Insensitive | Raw value, escaped, then prefixed with `%`. |
| `contains` | Match a value anywhere in a field. | Sensitive | Raw value, at least 3 characters, escaped, then wrapped in `%`. |
| `not_contains` | Do not match a value anywhere in a field. | Sensitive | Raw value, at least 3 characters, escaped, then wrapped in `%`. |
| `icontains` | Match a value anywhere in a field. | Insensitive | Raw value, at least 3 characters, escaped, then wrapped in `%`. |
| `not_icontains` | Do not match a value anywhere in a field. | Insensitive | Raw value, at least 3 characters, escaped, then wrapped in `%`. |
| `regex` | Match a regular expression. | Sensitive | Ready regular-expression pattern; PostgreSQL only. |
| `not_regex` | Do not match a regular expression. | Sensitive | Ready regular-expression pattern; PostgreSQL only. |
| `iregex` | Match a regular expression. | Insensitive | Ready regular-expression pattern; PostgreSQL only. |
| `not_iregex` | Do not match a regular expression. | Insensitive | Ready regular-expression pattern; PostgreSQL only. |

For example, a `where_cond` template can use the same placeholder convention as other filters:

```sql
SELECT * FROM students {where_cond}
```

## Rendering per dialect

`E(v)` is the backslash LIKE escape (`\\` to `\\\\`, `%` to `\\%`, `_` to `\\_`), `B(v)` is
the bang LIKE escape (`!` to `!!`, `%` to `!%`, `_` to `!_`), and `Q` is the dialect's
literal quoter.

| Operator | PostgreSQL (`Q` = `pg_literal`) | Generic SQL / SQL Server (`Q` = `Entity.quoteString` / `quote_string(escape_string())`) | BigQuery (`Q` = `bq_quote_string`) |
|---|---|---|---|
| `like` | `col LIKE Q(v)` | `col LIKE Q(v)` | `f LIKE Q(v)` |
| `ilike` | `col ILIKE Q(v)` | `LOWER(col) LIKE LOWER(Q(v))` | `LOWER(f) LIKE LOWER(Q(v))` |
| `startswith` | `col LIKE Q(E(v)+'%')` | `col LIKE Q(B(v)+'%') ESCAPE '!'` | `f LIKE Q(E(v)+'%')` |
| `istartswith` | `col ILIKE Q(E(v)+'%')` | `LOWER(col) LIKE LOWER(Q(B(v)+'%')) ESCAPE '!'` | `LOWER(f) LIKE LOWER(Q(E(v)+'%'))` |
| `endswith` / `iendswith` | as above with `'%'+E(v)` | as above with `'%'+B(v)` | as above |
| `contains` / `icontains` | as above with `'%'+E(v)+'%'`; operand at least 3 characters | as above | as above |
| `regex` | `col ~ Q(v)` | `ParserError` | `ParserError` |
| `iregex` | `col ~* Q(v)` | `ParserError` | `ParserError` |
| `not_regex` / `not_iregex` | `col !~ Q(v)` / `col !~* Q(v)` | `ParserError` | `ParserError` |
| `not_<like-op>` | `NOT LIKE` / `NOT ILIKE` in place of `LIKE` / `ILIKE` | `NOT LIKE` in place of `LIKE` (inside the `LOWER()` form too) | same |

PostgreSQL needs no `ESCAPE` clause because `pg_literal` emits `E'...'` with doubled
backslashes when the pattern contains `\\`. Generic SQL and SQL Server use `ESCAPE '!'`;
backslash is a string-literal escape in MySQL. BigQuery has no `ESCAPE` clause and uses
backslash to escape `%` and `_` natively, so that backslash must survive `bq_quote_string`.

## Reserved names

The operator names are reserved, lowercase names. On PostgreSQL, a dict whose single key is
one of these names is a partial-matching operator, never implicit JSONB containment. To query
a JSON key with a colliding name, use PostgreSQL's `->>` extraction or `@>` containment
explicitly. On BigQuery, a reserved name is never `JSON_VALUE(f, '$.<key>')` extraction; use
an explicit `JSON_VALUE` field expression when that JSON key is intended.

## Errors

Invalid partial matches raise `ParserError` (HTTP 400). The validator reports these messages:

- `unknown partial-matching operator '<op>' on '<key>'`
- `<op> on '<key>' requires a string operand`
- `<op> on '<key>' requires at least 3 characters (got <n>)`
- `<op> on '<key>': regex operators are not supported by this query parser`
- `<op> on '<key>': empty regex pattern`
- `<op> on '<key>': regex pattern too long (<n> > 200 chars)`
- `<op> on '<key>': regex pattern has a nested quantifier`
- `one operator per field: '<key>' combines a partial-matching operator with other keys`

The `contains`, `not_contains`, `icontains`, and `not_icontains` operators require at least
three characters. A partial-matching operator cannot be combined with another key for the
same field: one operator per field is required.

## qsurl

The [qsurl reference](QSURL.md) uses the same vocabulary: `~`, `~*`, `^=`, `^=*`, `$=`,
`$=*`, `=~`, and `=~*` map to partial-matching expressions. In particular, the documented
operator spellings are `~ ~* ^= ^=* $= $=* =~ =~*`.
