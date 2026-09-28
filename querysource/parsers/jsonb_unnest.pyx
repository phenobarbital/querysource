# cython: language_level=3, embedsignature=True
# Copyright (C) 2018-present Jesus Lara
#
# file: jsonb_unnest.pyx
"""JSONB array unnest planner for pgSQLParser (FEAT-153).

Cython fallback of the ``_qs_parsers`` Rust fast path (``pgsql_unnest_plan`` /
``pgsql_unnest_wrap``). Both implementations must produce identical output and
identical ``ValueError`` messages.
"""
import math
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


SAFE_CAST_PATTERNS = {
    'date': '^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]',
    'timestamp': '^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]',
    'timestamptz': '^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]',
    'int': '^-?[0-9]+$',
    'integer': '^-?[0-9]+$',
    'bigint': '^-?[0-9]+$',
    'numeric': '^-?[0-9]+([.][0-9]+)?$',
    'float': '^-?[0-9]+([.][0-9]+)?$',
    'boolean': '^(true|false)$',
}
HAVING_OPERATORS = ('=', '>=', '<=', '<>', '!=', '<', '>')


def render_ref(ref: Ref, safe_cast: bool = False, implicit_cast: str | None = None) -> str:
    """Render a reference per the rendering table.

    Args:
        ref: parsed reference.
        safe_cast: guard the cast with SAFE_CAST_PATTERNS (path refs only).
        implicit_cast: cast applied to a path ref that has none (buckets → 'date', sum/avg → 'numeric').

    Returns:
        The SQL text of the reference.
    """
    cdef str text
    cdef str cast
    cdef str pattern
    cdef list literals
    if not ref.keys:
        if ref.cast:
            return f'({SOURCE_ALIAS}.{ref.column}::{ref.cast})'
        return f'{SOURCE_ALIAS}.{ref.column}'
    literals = [_pg_literal(key) for key in ref.keys]
    if len(literals) == 1:
        text = f'({ARRAY_ALIAS}.elem ->> {literals[0]})'
    else:
        inner = ' -> '.join(literals[:-1])
        text = f'({ARRAY_ALIAS}.elem -> {inner} ->> {literals[-1]})'
    cast = ref.cast or implicit_cast
    if not cast:
        return text
    pattern = SAFE_CAST_PATTERNS.get(cast) if safe_cast else None
    if pattern is not None:
        return f'(CASE WHEN {text} ~ {_pg_literal(pattern)} THEN {text}::{cast} END)'
    return f'({text}::{cast})'


def render_expr(expr: Expr, safe_cast_for) -> str:
    """Render an expression; ``safe_cast_for(column) -> bool`` resolves the effective safe_cast."""
    kind = expr.kind
    if kind == 'count_star':
        return 'count(*)'
    if kind == 'ref':
        return render_ref(expr.arg, safe_cast_for(expr.arg.column))
    if kind == 'bucket':
        arg_sql = render_ref(expr.arg, safe_cast_for(expr.arg.column), 'date')
        return f"(date_trunc('{expr.func}', {arg_sql})::date)"
    # aggregate
    arg = expr.arg
    if isinstance(arg, Expr):
        arg_sql = render_expr(arg, safe_cast_for)
    else:
        implicit = 'numeric' if expr.func in ('sum', 'avg') else None
        arg_sql = render_ref(arg, safe_cast_for(arg.column), implicit)
    if expr.distinct:
        return f'{expr.func}(DISTINCT {arg_sql})'
    return f'{expr.func}({arg_sql})'


def _ref_name(ref: Ref) -> str:
    return ref.keys[-1] if ref.keys else ref.column


def default_alias(expr: Expr) -> str:
    """Default output alias: last key / column, ``<unit>_<name>``, ``count``, ``<func>_<name>``."""
    kind = expr.kind
    if kind == 'count_star':
        return 'count'
    if kind == 'ref':
        return _ref_name(expr.arg)
    if kind == 'bucket':
        return f'{expr.func}_{_ref_name(expr.arg)}'
    arg = expr.arg
    if isinstance(arg, Expr):
        return f'{expr.func}_{arg.func}_{_ref_name(arg.arg)}'
    return f'{expr.func}_{_ref_name(arg)}'


def _path_refs(expr: Expr) -> list:
    """Return every path reference contained in ``expr`` in traversal order."""
    kind = expr.kind
    if kind == 'count_star':
        return []
    arg = expr.arg
    if isinstance(arg, Expr):
        return _path_refs(arg)
    return [arg] if arg.keys else []


class _Planner:
    """Accumulates array-column, alias and strict-mode state while building one plan."""

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.aliases = cfg['aliases']
        self.array_column = None  # first path column seen
        self.select_aliases = {}  # alias -> Expr
        self.select_sql = {}  # alias -> rendered expression

    def resolve(self, expr: Expr, text: str):
        """Expand a bare config alias (precedence: select alias > config alias > parse).

        Returns:
            ``(expr, alias_name)`` where ``alias_name`` is the config alias that was expanded, or None.
        """
        if (
            expr.kind == 'ref' and not expr.arg.keys and expr.arg.cast is None
            and expr.arg.column in self.aliases
        ):
            name = expr.arg.column
            expanded = parse_expr(self.aliases[name])
            self.track(expanded, text, True)
            return expanded, name
        self.track(expr, text, False)
        return expr, None

    def track(self, expr: Expr, text: str, from_alias: bool) -> None:
        """Register every path column in ``expr``; enforce single column, columns allowlist, strict."""
        for ref in _path_refs(expr):
            if self.cfg['strict'] and not from_alias:
                raise ValueError(f"jsonb_unnest: raw path '{text}' not allowed in strict mode")
            column = ref.column
            if self.array_column is None:
                columns = self.cfg['columns']
                if columns is not None and column not in columns:
                    raise ValueError(f"jsonb_unnest: array column '{column}' is not declared in columns")
                self.array_column = column
            elif column != self.array_column:
                raise ValueError(
                    f"jsonb_unnest: more than one array column ('{self.array_column}', '{column}')"
                )

    def safe_cast_for(self, column: str) -> bool:
        """Effective safe_cast for ``column`` (column value, else top-level)."""
        columns = self.cfg['columns']
        if columns is not None and column in columns:
            value = columns[column]['safe_cast']
            if value is not None:
                return value
        return self.cfg['safe_cast']

    def lateral(self) -> str:
        """Render the lateral clause (or '' without an array column)."""
        column = self.array_column
        if column is None:
            return ''
        columns = self.cfg['columns']
        empty = 'exclude'
        if columns is not None and column in columns:
            empty = columns[column]['empty']
        source = f'{SOURCE_ALIAS}.{column}'
        call = (
            f"jsonb_array_elements(CASE jsonb_typeof({source}) WHEN 'array' THEN {source} "
            f"ELSE '[]'::jsonb END) AS {ARRAY_ALIAS}(elem)"
        )
        if empty == 'include':
            return f'LEFT JOIN LATERAL {call} ON true'
        return f'CROSS JOIN LATERAL {call}'

    def render(self, expr: Expr) -> str:
        """Render ``expr`` with this plan's effective safe_cast rules."""
        return render_expr(expr, self.safe_cast_for)

    def add_select(self, select: list, expr: Expr, alias: str) -> None:
        """Append ``<expr> AS "<alias>"`` rejecting duplicate output aliases."""
        if alias in self.select_aliases:
            raise ValueError(f"jsonb_unnest: duplicate output alias '{alias}'")
        sql = self.render(expr)
        self.select_aliases[alias] = expr
        self.select_sql[alias] = sql
        select.append(f'{sql} AS "{alias}"')

    def having_condition(self, key: str, value) -> list:
        """Render the conditions of one ``having`` entry."""
        if key in self.select_aliases and self.select_aliases[key].is_aggregate:
            sql = self.select_sql[key]
        else:
            if key in self.aliases:
                expr = parse_expr(self.aliases[key])
                self.track(expr, key, True)
            else:
                expr = parse_expr(key)
                self.track(expr, key, False)
            if not expr.is_aggregate:
                raise ValueError(f"jsonb_unnest: unknown having key '{key}'")
            sql = self.render(expr)
        if isinstance(value, dict):
            pairs = list(value.items())
        else:
            pairs = [('=', value)]
        conditions = []
        for op, operand in pairs:
            if op not in HAVING_OPERATORS:
                raise ValueError(f"jsonb_unnest: invalid having operator '{op}'")
            conditions.append(f'{sql} {op} {_having_value(key, operand)}')
        return conditions


def _having_value(key: str, value) -> str:
    if isinstance(value, bool):
        raise ValueError(f"jsonb_unnest: invalid having value for '{key}'")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"jsonb_unnest: invalid having value for '{key}'")
        return repr(value)
    if isinstance(value, str):
        return _pg_literal(value)
    raise ValueError(f"jsonb_unnest: invalid having value for '{key}'")


def unnest_plan(fields, grouping, ordering, filter, having, config):
    """Build the UnnestPlan dict, or return None when ``is_plan_candidate`` is False.

    Raises:
        ValueError: any rule in Implementation Notes (exact messages).
    """
    if not is_plan_candidate(fields, grouping, ordering, filter, having, config):
        return None
    planner = _Planner(validate_config(config))
    select = []
    for text in (fields or []):
        item = parse_select_item(text)
        expr, alias_name = planner.resolve(item.expr, text)
        planner.add_select(select, expr, item.alias or alias_name or default_alias(expr))
    group_by = []
    for text in (grouping or []):
        stripped = text.strip()
        if IDENT_RE.match(stripped) and stripped in planner.select_sql:
            group_by.append(planner.select_sql[stripped])
            continue
        expr, alias_name = planner.resolve(parse_expr(stripped), text)
        group_by.append(planner.render(expr))
        if not fields:
            planner.add_select(select, expr, alias_name or default_alias(expr))
    if not fields:
        planner.add_select(select, Expr('count_star', func='count'), 'count')
    order_by = []
    for text in (ordering or []):
        item = parse_order_item(text)
        if item.name is not None and item.name in planner.select_sql:
            rendered = f'"{item.name}"'
        else:
            expr, _ = planner.resolve(item.expr, text)
            rendered = planner.render(expr)
        if item.direction:
            rendered = f'{rendered} {item.direction}'
        if item.nulls:
            rendered = f'{rendered} NULLS {item.nulls}'
        order_by.append(rendered)
    having_sql = []
    if having:
        if not isinstance(having, dict):
            raise ValueError('jsonb_unnest: having must be a mapping')
        if not any(expr.is_aggregate for expr in planner.select_aliases.values()):
            raise ValueError('jsonb_unnest: having requires an aggregate')
        for key, value in having.items():
            having_sql.extend(planner.having_condition(key, value))
    element_where, row_filter = split_filters(planner, filter or {})
    return {
        'select': select,
        'group_by': group_by,
        'order_by': order_by,
        'having': having_sql,
        'element_where': element_where,
        'lateral': planner.lateral(),
        'row_filter': row_filter,
    }


def unnest_wrap(inner_sql: str, plan: dict) -> str:
    """Wrap ``inner_sql`` (placeholders already blanked by the caller) per Implementation Notes."""
    sql = f"SELECT {', '.join(plan['select'])} FROM ({inner_sql.strip()}) AS {SOURCE_ALIAS}"
    if plan['lateral']:
        sql = f"{sql} {plan['lateral']}"
    if plan['element_where']:
        sql = f"{sql} WHERE {' AND '.join(plan['element_where'])}"
    return sql


FILTER_OPERATORS = ('=', '>=', '<=', '<>', '!=', '<', '>')


def _unquote(value: str) -> str:
    """Undo ``is_valid``/``quoteString`` quoting (mirrors pgsql.pyx:282-285)."""
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def _filter_literal(value, key: str) -> str:
    """Render one element-filter value as a text literal (see Implementation Notes)."""
    if isinstance(value, str):
        return _pg_literal(_unquote(value))
    if isinstance(value, bool):
        return _pg_literal('true' if value else 'false')
    if isinstance(value, (int, float)):
        return _pg_literal(str(value))
    raise ValueError(f"jsonb_unnest: invalid filter value for '{key}'")


def _prefilter_document(keys: tuple, value: str) -> dict:
    """Build the nested JSON object for ``keys`` ending in ``value``."""
    document = _unquote(value)
    for key in reversed(keys):
        document = {key: document}
    return document


def _element_condition(sql: str, key: str, negated: bool, value) -> list:
    """Render the outer-WHERE conditions for one element filter entry."""
    if value is None or (isinstance(value, str) and value in ('null', 'NULL')):
        return [f'{sql} IS NOT NULL' if negated else f'{sql} IS NULL']
    if isinstance(value, dict):
        if not value:
            raise ValueError(f"jsonb_unnest: invalid filter value for '{key}'")
        conditions = []
        for op, operand in value.items():
            if op not in FILTER_OPERATORS:
                raise ValueError(f"jsonb_unnest: invalid filter operator '{op}' for '{key}'")
            conditions.append(f'{sql} {op} {_filter_literal(operand, key)}')
        return conditions
    if isinstance(value, list):
        if not value:
            raise ValueError(f"jsonb_unnest: invalid filter value for '{key}'")
        literals = ', '.join(_filter_literal(item, key) for item in value)
        return [f'{sql} NOT IN ({literals})' if negated else f'{sql} IN ({literals})']
    literal = _filter_literal(value, key)
    return [f'{sql} <> {literal}' if negated else f'{sql} = {literal}']


def split_filters(planner, filter) -> tuple:
    """Split ``filter`` into ``(element_where: list[str], row_filter: dict)``.

    Raises:
        ValueError: reference/strict/array errors via ``planner.track``; invalid value/operator.
    """
    element_where = []
    row_filter = {}
    prefilters = []
    columns = planner.cfg['columns']
    for key, value in filter.items():
        if not isinstance(key, str):
            row_filter[key] = value
            continue
        negated = key.endswith('!')
        base = key[:-1] if negated else key
        ref = None
        from_alias = False
        if '[].' in base:
            ref = parse_ref(base)
        elif base in planner.aliases:
            aliased = parse_expr(planner.aliases[base])
            if aliased.kind == 'ref' and aliased.arg.keys:
                ref = aliased.arg
                from_alias = True
        if ref is None:
            row_filter[key] = value
            continue
        planner.track(Expr('ref', arg=ref), key, from_alias)
        sql = render_ref(ref, planner.safe_cast_for(ref.column))
        element_where.extend(_element_condition(sql, key, negated, value))
        if columns is None or ref.column not in columns or not columns[ref.column]['prefilter']:
            continue
        if ref.cast is not None or negated:
            continue
        if isinstance(value, str) and value not in ('null', 'NULL'):
            prefilters.append((ref, {'@>': [_prefilter_document(ref.keys, value)]}))
        elif isinstance(value, list) and value and all(isinstance(item, str) for item in value):
            prefilters.append((ref, {'@>|': [[_prefilter_document(ref.keys, item)] for item in value]}))
    for ref, condition in prefilters:
        suffix = '|'
        while f'{ref.column}{suffix}' in row_filter:
            suffix += '|'
        row_filter[f'{ref.column}{suffix}'] = condition
    return element_where, row_filter
