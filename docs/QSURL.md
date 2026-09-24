# qsurl — URL query dialect

`qsurl` is a compact, closed, HTSQL-style URL dialect for shaping a QuerySource
stored-query slug from a single URL — designed to be easy for an LLM agent to
generate (and self-correct against, via [`to_gbnf()`](#python-api)) and for a
human to write by hand:

```text
/queries/hisense_stores{store_id,name,city}?state_code='CA'&opened>=2024-01-01:sort(name):top(50)
```

It compiles to an engine-neutral JSON [IR](#intermediate-representation-ir),
which QuerySource then partially pushes down to the executing provider
(PostgreSQL, Cassandra, ...) and partially evaluates in memory — see
[Pushdown and residual evaluation](#pushdown-and-residual-evaluation).

## Quick start

Path form — the whole query, percent-encoded, in the URL path:

```text
GET /api/v1/services/qsurl/hisense_stores%7Bstore_id%2Cname%7D%3Fstate_code%3D%27CA%27
```

`?q=` form — a bare slug in the path, with everything after it in `q` (the
handler joins `path + q` and parses that; `q` must carry everything after the
slug, including its own leading `{`/`?`/`:`):

```text
GET /api/v1/services/qsurl/hisense_stores?q=%7Bstore_id%2Cname%7D%3Fstate_code%3D%27CA%27
```

Both forms are decoded **exactly once** by aiohttp before qsurl ever sees the
string — never percent-decode again in a client or in custom middleware, or a
literal `%27` in a quoted string would turn into a stray `'`.

```bash
curl 'https://host/api/v1/services/qsurl/hisense_stores%7Bstore_id%2Cname%7D%3Fstate_code%3D%27CA%27'
```

## Grammar

Phase 1 grammar (identical between the Rust parser, `rust/qsurl/src/parser.rs`,
and the pure-Python Lark fallback, `querysource/qsurl/grammar.lark`):

```text
query      := prefix? slug selection? filter? pipe*
prefix     := '/' | '/queries/' | 'queries/'
slug       := [A-Za-z0-9_-]+
selection  := '{' field (',' field)* ','? '}'
field      := path (':as(' ident ')')?
path       := ident ('.' ident)*
filter     := '?' expr
expr       := expr '|' expr | expr '&' expr | '!' expr | '(' expr ')' | operand (cmp operand)?
cmp        := '==' | '=' | '!=' | '<=' | '>=' | '<' | '>' | '~' | '!~' | '^=' | '$=' | '=~'
operand    := list | literal | call | path
list       := '(' literal (',' literal)* ')'
call       := ident '(' (operand (',' operand)*)? ')'
literal    := null | true | false | datetime | date | number | string
date       := DDDD-DD-DD
datetime   := date 'T' DD:DD (':' DD)? ('Z' | ('+'|'-') DD:DD)?
number     := '-'? int ('.' digits)?
string     := '\'' ( '\'\'' | [^'] )* '\''  |  '"' ( '\"' | [^"] )* '"'
pipe       := ':sort(' sortkey (',' sortkey)* ')' | ':top(' int ')' | ':limit(' int ')'
            | ':skip(' int ')' | ':offset(' int ')' | ':distinct'
sortkey    := ('+' | '-')? path
```

Whitespace (a decoded `%20`) is tolerated around every token: `?state = 'CA' &
city ~ 'san'` is equivalent to `?state='CA'&city~'san'`. `!` binds tighter than
`&`, which binds tighter than `|`; parentheses group explicitly. Keywords
(`null`, `true`, `false`, `sort`, `top`, `limit`, `skip`, `offset`, `distinct`)
require a word boundary, so `nullable=1` and `topic=1` are ordinary column
comparisons, not keyword collisions.

```qsurl
s?a=1
s?nullable=1&topic=1&distinctive=1
```

## Operators

| URL operator | `expression` | Notes |
|---|---|---|
| `=` / `==` | `==` | synonyms |
| `!=` | `!=` | |
| `<` `<=` `>` `>=` | `<` `<=` `>` `>=` | comparison with the column on either side is flipped to put the column first |
| `~` | `contains` | case-insensitive on every engine |
| `!~` | `not_contains` | case-insensitive |
| `^=` | `startswith` | case-insensitive |
| `$=` | `endswith` | case-insensitive |
| `=~` | `regex` | always evaluated in the residual stage, never pushed down |
| `col=null` | `is_null` | sugar |
| `col!=null` | `not_null` | sugar |
| bare `col` | `not_null` | `?active` means "active is not null/truthy" |
| `!col` | `is_null` | sugar for `col=null`, not a `not` node |
| `col=(a,b,...)` | `==` with a list value | membership; only `=`/`!=` accept a list |

```qsurl
s?email=null&!fax
s?state=('CA','NV')
s?city~'san'
```

## Pipeline operators

Six closed operators, applied after the filter, each taking exactly the shape
below; giving `:top`/`:limit` (or `:skip`/`:offset`) twice is a `parse` error
(`":top/:limit given more than once"`), and any other `:name` is a `parse`
error listing the six valid ones:

| Pipe | Effect |
|---|---|
| `:sort(+a,-b,...)` | sort ascending (`+`, default) or descending (`-`) by one or more columns |
| `:top(n)` / `:limit(n)` | synonyms, row limit |
| `:skip(n)` / `:offset(n)` | synonyms, row offset |
| `:distinct` | drop duplicate rows (always evaluated in the residual stage — no dialect renders `_distinct`) |

```qsurl
s:sort(+a,-b):top(10):skip(5):distinct
```

## Intermediate representation (IR)

`parse()` returns this IR for the Quick start example
(`hisense_stores{store_id,name,city}?state_code='CA'&opened>=2024-01-01:sort(name):top(50)`
— real output of `querysource.qsurl.parse()`):

```json
{
  "slug": "hisense_stores",
  "fields": ["store_id", "name", "city"],
  "filter": {
    "and": [
      {"column": "state_code", "expression": "==", "value": "CA"},
      {"column": "opened", "expression": ">=", "value": "2024-01-01", "dtype": "date"}
    ]
  },
  "sort": [{"column": "name", "order": "asc"}],
  "limit": 50,
  "offset": null,
  "distinct": false,
  "requires": ["select", "filter", "sort", "limit"]
}
```

The filter root is always `null`, `{"and": [...]}` or `{"or": [...]}`; a leaf
is `{"column", "expression", "value"?}` (`value` is absent for `is_null`/
`not_null`), plus `"dtype": "date"|"datetime"` when the value came from an
*unquoted* date/datetime literal (a quoted `'2024-01-01'` stays a plain
string). `requires` lists exactly the capability tokens the query needs, in
this fixed declaration order — **not** alphabetical:

```text
select, alias, filter, or, not, in_list, null_check, text_match, regex,
functions, navigation, sort, limit, offset, distinct
```

## Pushdown and residual evaluation

Each provider declares a `capabilities` set and a `residual_scan` flag
(`querysource/providers/abstract.py`, `sql.py`, `pg.py`, `cassandra.py`).
`querysource.qsurl.translate.split(ir, capabilities, residual_scan=...)`
pushes every leaf whose `requires` are a subset of `capabilities` into the
`conditions` dict the existing dialect parsers already consume (`fields`,
`filter`, `ordering`, `_limit`, `_offset`); everything else — a root `or`, an
unsupported operator, a second condition on an already-used column, `regex`,
`distinct` — becomes a `ResidualPlan` applied in memory, in the fixed order
filter → sort → project → distinct → offset → limit → rename.

| Provider | `capabilities` | `residual_scan` |
|---|---|---|
| `BaseProvider` (default; unaudited providers) | `select, filter, in_list, null_check` | `True` |
| `sqlProvider` (MySQL, MSSQL, Oracle, SQLite...) | base + `alias, sort, limit, offset` | `True` |
| `pgProvider` | sql + `text_match` | `True` |
| `cassandraProvider` | `select, filter, in_list, null_check, limit` | `False` |

Text operators (`~ !~ ^= $=`) are case-insensitive on **every** engine:
PostgreSQL pushes them down as `ILIKE`/`NOT ILIKE` (with `%`/`_`/`\` escaped
before reaching the pattern); every other provider evaluates them in the
residual stage with a lower-cased comparison. `=~` (`regex`) is always
residual — no provider declares the `regex` capability.

```python
>>> from querysource.qsurl import parse
>>> from querysource.qsurl.translate import split
>>> from querysource.providers.pg import pgProvider
>>> conditions, plan = split(parse("s?city~'san'"), pgProvider.capabilities, residual_scan=True)
>>> conditions
{'filter': {'city': {'ILIKE': '%san%'}}}
>>> plan.is_empty()
True
```

`functions`/`navigation` (dotted paths like `store.region.name`, function
calls like `lower(name)`) are accepted by the grammar but not yet supported by
any provider — phase 1 answers `400 unsupported` naming the capability:

```json
{"kind": "unsupported", "offset": 0, "message": "capability `functions` is not supported by any provider in this release", "found": null, "expected": [], "pointer": ""}
```

## Cost guard

Two independent guards keep a qsurl query from becoming an unbounded scan:

- **`QSURL_MAX_RESIDUAL_ROWS`** (`navconfig`, default `50000`): when a pushdown
  result has more rows than this before an in-memory `ResidualPlan` is
  applied, `QS._apply_residual` raises `QSUrlError(kind="cost")` **before**
  building any `DataFrame`.
- **`residual_scan=False`**: a provider (only `cassandraProvider` today) that
  forbids a residual-only scan rejects a query whose filter cannot be pushed
  down at all, with `kind="cost"`, before any query executes:

```json
{"kind": "cost", "offset": 0, "message": "residual-only filter on a provider that forbids scans (residual_scan=False); push down at least one condition", "found": null, "expected": [], "pointer": ""}
```

## Errors

Every qsurl failure is a `QSUrlError` (`querysource.qsurl.QSUrlError`, a
`QueryException` subclass, `code=400`) with one of four `kind`s:

| `kind` | Meaning |
|---|---|
| `parse` | grammar error, or a pipeline-operator error (unknown op, duplicate `:top`/`:skip`) |
| `lower` | the parse tree is syntactically valid but semantically wrong (list with a non-membership operator, a comparison with no column, an unknown result column at residual time) |
| `unsupported` | the query needs a phase-2 capability (`functions`, `navigation`) |
| `cost` | the [cost guard](#cost-guard) rejected the query before it ran |

`QSUrlError.to_dict()` — `{kind, offset, message, found, expected, pointer}`
(`offset`/`found`/`expected`/`pointer` are only meaningful for `parse`
errors) — is what reaches the client, in **every** mode (production included:
this is an explicit, caller-asserted client-safe `detail`, unlike the
redacted default 400 body every other error gets). Real 400 body for
`stores?state='CA':order(name)` (from `QSUrlService`, `debug=False`):

```json
{
  "error": "unknown pipeline operator `:order`; expected one of: :sort, :top, :limit, :skip, :offset, :distinct",
  "status": 400,
  "error_id": "c66130cde027",
  "detail": {
    "kind": "parse",
    "offset": 18,
    "message": "unknown pipeline operator `:order`; expected one of: :sort, :top, :limit, :skip, :offset, :distinct",
    "found": null,
    "expected": [],
    "pointer": "stores?state='CA':order(name)\n                  ^"
  }
}
```

## Python API

```python
from querysource.qsurl import parse, requires, to_gbnf, HAS_RUST, QSUrlError

parse(src: str) -> dict          # IR dict; raises QSUrlError
requires(src: str) -> list[str]  # parse(src)["requires"]
to_gbnf() -> str                 # the grammar as GBNF, for constrained decoding
HAS_RUST: bool                   # True when the Rust extension (_qsurl) is importable
```

`parse()`/`requires()` use the Rust extension when it is importable, and fall
back to the pure-Python Lark parser (`querysource/qsurl/grammar.lark` +
`querysource/qsurl/_fallback.py`) otherwise — both produce byte-identical IR
for every case in the shared parity corpus (`tests/qsurl/corpus.json`). Set
`QSURL_FORCE_FALLBACK=1` to force the Lark path even when the extension is
installed (useful for testing the fallback specifically).

`to_gbnf()` renders the grammar as a [GBNF](https://github.com/ggerganov/llama.cpp/blob/master/grammars/README.md)
grammar for constrained decoding — hand it to an LLM inference backend that
supports grammar-constrained sampling (e.g. llama.cpp, or any backend
accepting a GBNF `grammar` parameter) so the model can only ever generate a
syntactically valid qsurl string:

```python
>>> from querysource.qsurl import to_gbnf
>>> to_gbnf().splitlines()[0]
'root ::= ws query ws'
```

## Limits

- Practical URL size ceiling is **~8 KB**; parsing an 8 KB URL is
  sub-millisecond on the Rust path and comfortably under 100 ms on the Lark
  fallback (`tests/qsurl/test_perf.py`, opt-in via `pytest -m perf`).
- A quoted literal can contain a percent-encoded sequence (e.g. `%2F` for a
  literal `/` inside a string) — since decoding happens exactly once, write
  it pre-encoded in the URL; qsurl itself never decodes anything.
- Phase-2 features are recognised by the grammar but rejected at translation
  time: `functions` (`lower(name)`, `year(opened)`, ...) and `navigation`
  (`store.region.name`) always answer `400 unsupported` today — see
  [Pushdown and residual evaluation](#pushdown-and-residual-evaluation).
- The legacy route (`/api/v2/services/queries/{slug}`, `slug:format` suffix,
  stored-query placeholders) is untouched; qsurl is a separate, additive
  route (`/api/v1/services/qsurl/{path:.*}`).
