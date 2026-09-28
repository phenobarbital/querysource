# cython: language_level=3, embedsignature=True
# Copyright (C) 2018-present Jesus Lara
#
# file: jsonb_unnest.pyx
"""JSONB array unnest planner for pgSQLParser (FEAT-153).

Cython fallback of the ``_qs_parsers`` Rust fast path (``pgsql_unnest_plan`` /
``pgsql_unnest_wrap``). Both implementations must produce identical output and
identical ``ValueError`` messages.
"""
import re

ALLOWED_CASTS = (
    'text', 'int', 'integer', 'bigint', 'numeric', 'float',
    'date', 'timestamp', 'timestamptz', 'boolean',
)
AGGREGATES = ('count', 'min', 'max', 'sum', 'avg')
BUCKETS = ('year', 'quarter', 'month', 'week', 'day')
ARRAY_ALIAS = '_qs_e0'
SOURCE_ALIAS = '_qs_src'
CONFIG_KEYS = ('columns', 'aliases', 'strict', 'safe_cast')
COLUMN_KEYS = ('empty', 'safe_cast', 'prefilter')
EMPTY_POLICIES = ('exclude', 'include')

_IDENT = r'[A-Za-z_][A-Za-z0-9_]{0,62}'
_KEY = r'[A-Za-z0-9_-]{1,128}'
IDENT_RE = re.compile(rf'^{_IDENT}$')
REF_RE = re.compile(
    rf'^(?P<col>{_IDENT})(?:\[\]\.(?P<keys>{_KEY}(?:\.{_KEY})*))?(?:\s*::\s*(?P<cast>[A-Za-z]+))?$'
)


cdef str _pg_literal(str value):
    """Quote ``value`` as a PostgreSQL literal (same semantics as pgsql.pyx ``pg_literal``)."""
    cdef str escaped = value.replace("'", "''")
    if '{' in escaped or '}' in escaped or '\\' in escaped:
        escaped = escaped.replace('\\', '\\\\').replace('{', '\\x7b').replace('}', '\\x7d')
        return f"E'{escaped}'"
    return f"'{escaped}'"


def pg_literal(value: str) -> str:
    """Python-visible wrapper of ``_pg_literal`` (used by tests and sibling tasks)."""
    return _pg_literal(value)


class Ref:
    """A row column or an array-element path: ``col`` or ``col[].k1.k2`` with optional cast."""

    __slots__ = ('column', 'keys', 'cast')

    def __init__(self, column: str, keys: tuple, cast: str | None) -> None:
        self.column = column
        self.keys = keys
        self.cast = cast

    @property
    def is_path(self) -> bool:
        """True when the reference points inside a JSONB array element."""
        return bool(self.keys)


class Expr:
    """A parsed expression: kind is 'ref', 'count_star', 'agg' or 'bucket'."""

    __slots__ = ('kind', 'func', 'distinct', 'arg')

    def __init__(self, kind: str, func: str | None = None, distinct: bool = False, arg=None) -> None:
        self.kind = kind
        self.func = func
        self.distinct = distinct
        self.arg = arg  # Ref for 'ref'/'bucket'; Ref or Expr(bucket) for 'agg'; None for 'count_star'

    @property
    def is_aggregate(self) -> bool:
        """True for count(*) and agg(...) expressions."""
        return self.kind in ('count_star', 'agg')


class Item:
    """A select/order entry: an expression or a bare name, plus alias / direction / nulls."""

    __slots__ = ('expr', 'name', 'alias', 'direction', 'nulls', 'text')

    def __init__(self, text: str, expr=None, name=None, alias=None, direction=None, nulls=None) -> None:
        self.text = text
        self.expr = expr
        self.name = name
        self.alias = alias
        self.direction = direction
        self.nulls = nulls


_FUNC_RE = re.compile(r'^(?P<func>[A-Za-z]+)\s*\(\s*(?P<body>.*?)\s*\)$', re.DOTALL)
_DISTINCT_RE = re.compile(r'^distinct\s+(?P<rest>.+)$', re.IGNORECASE | re.DOTALL)
_AS_RE = re.compile(r'\s+as\s+', re.IGNORECASE)
_ORDER_RE = re.compile(
    r'^(?P<body>.+?)(?:\s+(?P<dir>asc|desc))?(?:\s+nulls\s+(?P<nulls>first|last))?$',
    re.IGNORECASE | re.DOTALL,
)


def parse_ref(text: str) -> Ref:
    """Parse ``col``, ``col::cast`` or ``col[].k1.k2[::cast]``.

    Raises:
        ValueError: ``jsonb_unnest: invalid reference '<text>'`` or ``unknown cast``.
    """
    match = REF_RE.match(text.strip())
    if match is None:
        raise ValueError(f"jsonb_unnest: invalid reference '{text}'")
    cast = match.group('cast')
    if cast is not None:
        cast = cast.lower()
        if cast not in ALLOWED_CASTS:
            raise ValueError(f"jsonb_unnest: unknown cast '{cast}'")
    keys = tuple(match.group('keys').split('.')) if match.group('keys') else ()
    return Ref(match.group('col'), keys, cast)


def parse_expr(text: str) -> Expr:
    """Parse an expression per the grammar (count(*), agg(ref|bucket), bucket(ref), ref).

    Raises:
        ValueError: ``jsonb_unnest: invalid expression '<text>'`` (or a nested reference error).
    """
    stripped = text.strip()
    match = _FUNC_RE.match(stripped)
    if match is None:
        return Expr('ref', arg=parse_ref(stripped))
    func = match.group('func').lower()
    body = match.group('body')
    invalid = ValueError(f"jsonb_unnest: invalid expression '{text}'")
    if not body or body.lower() == 'distinct':
        raise invalid
    if func == 'count':
        if body == '*':
            return Expr('count_star', func='count')
        distinct_match = _DISTINCT_RE.match(body)
        rest = distinct_match.group('rest').strip() if distinct_match else body
        if rest == '*':
            raise invalid
        return Expr('agg', func, distinct_match is not None, parse_ref(rest))
    if func in AGGREGATES:
        if _DISTINCT_RE.match(body):
            raise invalid
        inner = _FUNC_RE.match(body)
        if inner is None:
            return Expr('agg', func, False, parse_ref(body))
        unit = inner.group('func').lower()
        inner_body = inner.group('body')
        if unit not in BUCKETS or not inner_body or '(' in inner_body:
            raise invalid
        return Expr('agg', func, False, Expr('bucket', unit, arg=parse_ref(inner_body)))
    if func in BUCKETS:
        if '(' in body:
            raise invalid
        return Expr('bucket', func, arg=parse_ref(body))
    raise invalid


def parse_select_item(text: str) -> Item:
    """Parse ``<expr> [as <ident>]`` (``as`` case-insensitive).

    Raises:
        ValueError: ``jsonb_unnest: invalid select item '<text>'`` for a bad alias;
            expression errors propagate unchanged.
    """
    stripped = text.strip()
    split_at = None
    for match in _AS_RE.finditer(stripped):
        head = stripped[:match.start()]
        if head.count('(') == head.count(')'):
            split_at = match
    if split_at is None:
        return Item(text, expr=parse_expr(stripped))
    alias = stripped[split_at.end():].strip()
    if IDENT_RE.match(alias) is None:
        raise ValueError(f"jsonb_unnest: invalid select item '{text}'")
    return Item(text, expr=parse_expr(stripped[:split_at.start()]), alias=alias)


def parse_order_item(text: str) -> Item:
    """Parse ``(<expr> | <ident>) [asc|desc] [nulls first|last]``.

    A bare identifier is returned as ``Item(name=...)`` with ``expr`` also parsed as a
    ref, because the caller decides whether it names a select alias (TASK-782).

    Raises:
        ValueError: ``jsonb_unnest: invalid order item '<text>'``.
    """
    match = _ORDER_RE.match(text.strip())
    if match is None:
        raise ValueError(f"jsonb_unnest: invalid order item '{text}'")
    body = match.group('body').strip()
    direction = match.group('dir')
    nulls = match.group('nulls')
    try:
        expr = parse_expr(body)
    except ValueError as exc:
        if str(exc).startswith('jsonb_unnest: unknown cast'):
            raise
        raise ValueError(f"jsonb_unnest: invalid order item '{text}'") from None
    name = body if IDENT_RE.match(body) else None
    return Item(
        text, expr=expr, name=name,
        direction=direction.upper() if direction else None,
        nulls=nulls.upper() if nulls else None,
    )


def _is_bool(value) -> bool:
    return isinstance(value, bool)


def validate_config(config) -> dict:
    """Validate and normalise ``attributes['jsonb_unnest']``.

    Returns:
        ``{'columns': {col: {'empty': str, 'safe_cast': bool|None, 'prefilter': bool}},
        'aliases': {name: str}, 'strict': bool, 'safe_cast': bool}``. ``columns`` is ``None``
        when not declared (any grammar-valid array column allowed). A column's ``safe_cast``
        stays ``None`` when unset (inherit the top-level value).

    Raises:
        ValueError: messages from the table in Implementation Notes.
    """
    result = {'columns': None, 'aliases': {}, 'strict': False, 'safe_cast': False}
    if config is None:
        return result
    if not isinstance(config, dict):
        raise ValueError('jsonb_unnest: invalid config: must be a mapping')
    for key in config:
        if key not in CONFIG_KEYS:
            raise ValueError(f"jsonb_unnest: invalid config: unknown key '{key}'")
    if 'columns' in config:
        columns_error = 'jsonb_unnest: invalid config: columns must map identifiers to mappings'
        columns = config['columns']
        if not isinstance(columns, dict):
            raise ValueError(columns_error)
        normalized = {}
        for name, options in columns.items():
            if not isinstance(name, str) or IDENT_RE.match(name) is None:
                raise ValueError(columns_error)
            if not isinstance(options, dict):
                raise ValueError(columns_error)
            entry = {'empty': 'exclude', 'safe_cast': None, 'prefilter': False}
            for option, value in options.items():
                if option not in COLUMN_KEYS:
                    raise ValueError(f"jsonb_unnest: invalid config: unknown column option '{option}'")
                if option == 'empty':
                    if value not in EMPTY_POLICIES:
                        raise ValueError(
                            "jsonb_unnest: invalid config: empty must be 'exclude' or 'include'"
                        )
                elif not _is_bool(value):
                    raise ValueError(f'jsonb_unnest: invalid config: {option} must be a boolean')
                entry[option] = value
            normalized[name] = entry
        result['columns'] = normalized
    if 'aliases' in config:
        aliases_error = 'jsonb_unnest: invalid config: aliases must map identifiers to expressions'
        aliases = config['aliases']
        if not isinstance(aliases, dict):
            raise ValueError(aliases_error)
        for name, expression in aliases.items():
            if not isinstance(name, str) or IDENT_RE.match(name) is None:
                raise ValueError(aliases_error)
            if not isinstance(expression, str):
                raise ValueError(aliases_error)
            parse_expr(expression)
        result['aliases'] = dict(aliases)
    for flag in ('strict', 'safe_cast'):
        if flag in config:
            if not _is_bool(config[flag]):
                raise ValueError(f'jsonb_unnest: invalid config: {flag} must be a boolean')
            result[flag] = config[flag]
    return result


def _first_token(text) -> str:
    stripped = text.strip()
    return stripped.split(None, 1)[0] if stripped else ''


def is_plan_candidate(fields, grouping, ordering, filter, having, config) -> bool:
    """Cheap activation check; never parses, never raises.

    True when any string in fields/grouping/ordering or any filter key contains ``'[].'``,
    when ``having`` is truthy, or when ``config`` is a dict whose ``aliases`` dict contains
    a bare name used in fields/grouping, the first token of an ordering entry, a filter key
    (trailing ``'!'`` stripped) or a having key.
    """
    try:
        if having:
            return True
        names = ()
        if isinstance(config, dict):
            aliases = config.get('aliases')
            if isinstance(aliases, dict):
                names = aliases
        for entries in (fields, grouping, ordering):
            if not isinstance(entries, (list, tuple)):
                continue
            for entry in entries:
                if not isinstance(entry, str):
                    continue
                if '[].' in entry:
                    return True
                if names and _first_token(entry) in names:
                    return True
        if isinstance(filter, dict):
            for key in filter:
                if not isinstance(key, str):
                    continue
                if '[].' in key:
                    return True
                if names and key.rstrip('!') in names:
                    return True
    except Exception:  # noqa: BLE001 - detection must never raise
        return False
    return False
