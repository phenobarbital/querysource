# cython: language_level=3, embedsignature=True
# Copyright (C) 2018-present Jesus Lara
#
# file: bigquery.pyx
"""
BigQuery SQL Parser.
"""
import re
from typing import Union, Dict, Any, List, Tuple
from datamodel.typedefs import NullDefault, SafeDict
from .sql cimport SQLParser
from ..types.validators import Entity, field_components

# Try to import Rust extension for accelerated parsing
try:
    import qs_parsers as _rs
    HAS_RUST = True
except ImportError:
    HAS_RUST = False

COMPARISON_TOKENS = ('>=', '<=', '<>', '!=', '<', '>',)


cdef str bq_quote_string(object value):
    """Quote a string value for BigQuery using double-quote delimiters.

    BigQuery strings enclosed in double quotes do not require escaping
    internal single quotes, avoiding the PostgreSQL-style '' doubling
    that is incompatible with BigQuery (e.g. "Sam's Club" instead of
    'Sam''s Club').
    """
    cdef str v
    if value is None or value == 'None':
        return '""'
    if value in ('null', 'NULL'):
        return <str>value
    v = str(value)
    # Strip surrounding single quotes if already wrapped by a caller
    if v.startswith("'") and v.endswith("'") and len(v) >= 2:
        v = v[1:-1]
    # Escape any literal double quotes inside the string
    v = v.replace('"', '\\"')
    return f'"{v}"'


cdef class BigQueryParser(SQLParser):
    """ BigQuery SQL Parser with support for JSON columns. """

    def __cinit__(self, *args, **kwargs):
        """Initialize the critical attributes in __cinit__ to ensure they exist."""
        self._json_pattern = re.compile(r"^([a-zA-Z0-9_]+)\.([a-zA-Z0-9_\.]+)$")

    def __init__(
        self,
        *args,
        **kwargs
    ):
        super(BigQueryParser, self).__init__(
            *args,
            **kwargs
        )

    async def get_json_path(self, field: str) -> Tuple[str, str, bool]:
        """Parse a field with dot notation into column and JSON path."""
        if match := self._json_pattern.match(field):
            column_name, json_path = match.groups()
            json_path_parts = json_path.split('.')
            json_path = f"$.{'.'.join(json_path_parts)}"
            return column_name, json_path, True
        return field, None, False

    async def process_json_field(self, field: str) -> str:
        """Process a field that might be a JSON field."""
        column_name, json_path, is_json_field = await self.get_json_path(field)

        # Check if this field is defined as JSON in cond_definition
        cdef bint is_json_defined = False
        try:
            is_json_defined = self.cond_definition.get(field) == "json"
        except (AttributeError, KeyError):
            pass

        if is_json_field or is_json_defined:
            if is_json_field and json_path:
                return f"JSON_VALUE({column_name}, '{json_path}')"
            else:
                return f"JSON_VALUE({field}, '$')"
        return field

    async def filter_conditions(self, sql):
        """Options for Filtering (BigQuery-specific, rayon-parallel Rust fast-path)."""
        if HAS_RUST and self.filter and isinstance(self.filter, dict):
            try:
                cond_def = self.cond_definition if self.cond_definition else {}
                return _rs.bq_filter_conditions(sql, self.filter, cond_def)
            except Exception:
                pass  # fall through to Cython implementation
        return await self._filter_conditions_cy(sql)

    async def _filter_conditions_cy(self, sql):
        """Cython fallback for filter_conditions with BigQuery JSON support."""
        cdef str _sql = sql
        cdef str key
        cdef str name
        cdef str end
        cdef str _and
        cdef str _filter
        cdef str val
        cdef str fval
        cdef str op
        cdef list where_cond
        cdef bint is_json_field

        if self._conditions:
            for key, value in self._conditions.items():
                if f"{{{key}}}" not in _sql:
                    if self.filter:
                         self.filter[key] = value
                    else:
                        self.filter = {key: value}

        if self.filter:
            where_cond = []
            for key, value in self.filter.items():
                try:
                    if isinstance(int(key), (int, float)):
                        key = f'"{key}"'
                except ValueError:
                    pass

                # Check if this is a JSON field in cond_definition
                is_json_field = False
                try:
                    is_json_field = self.cond_definition.get(key) == "json"
                except (AttributeError, KeyError):
                    pass

                # Parse field components safely
                try:
                    comps = field_components(key)
                    if comps:
                        _, name, end = comps[0]
                    else:
                        name = key
                        end = ''
                except (IndexError, ValueError):
                    name = key
                    end = ''

                # Process potential JSON field
                json_field_name, json_path, has_json_path = await self.get_json_path(key)

                # Determine if we need to use JSON_VALUE
                if is_json_field or has_json_path:
                    if has_json_path:
                        field_expr = f"JSON_VALUE({json_field_name}, '{json_path}')"
                    else:
                        field_expr = f"JSON_VALUE({key}, '$')"
                else:
                    field_expr = key

                # Handle various value types
                if isinstance(value, dict):
                    op, v = value.popitem()
                    if op in COMPARISON_TOKENS:
                        where_cond.append(f"{field_expr} {op} {v}")
                    else:
                        # BigQuery: JSON extraction via dict key
                        json_expr = f"JSON_VALUE({field_expr}, '$.{op}')"
                        where_cond.append(
                            f"{json_expr} = {bq_quote_string(str(v))}"
                        )

                elif isinstance(value, list):
                    try:
                        fval = value[0]
                        if fval in self.valid_operators:
                            where_cond.append(f"{field_expr} {fval} {value[1]}")
                        else:
                            val = ','.join(
                                [f"{bq_quote_string(v)}" for v in value]
                            )
                            if end == '!':
                                where_cond.append(f"{name} NOT IN ({val})")
                            else:
                                where_cond.append(f"{field_expr} IN ({val})")
                    except (KeyError, IndexError):
                        val = ','.join(
                            [f"{bq_quote_string(v)}" for v in value]
                        )
                        if not val:
                            where_cond.append(f"{field_expr} IN (NULL)")
                        else:
                            where_cond.append(f"{field_expr} IN ({val})")

                elif isinstance(value, (str, int)):
                    if "BETWEEN" in str(value):
                        where_cond.append(f"({field_expr} {value})")
                    elif value in ('null', 'NULL'):
                        where_cond.append(f"{field_expr} IS NULL")
                    elif value in ('!null', '!NULL'):
                        where_cond.append(f"{field_expr} IS NOT NULL")
                    elif end == '!':
                        where_cond.append(f"{name} != {value}")
                    elif str(value).startswith('!'):
                        where_cond.append(
                            f"{field_expr} != {bq_quote_string(value[1:])}"
                        )
                    else:
                        where_cond.append(
                            f"{field_expr}={bq_quote_string(value)}"
                        )

                elif isinstance(value, bool):
                    where_cond.append(f"{field_expr} = {value}")

                else:
                    where_cond.append(
                        f"{field_expr}={bq_quote_string(value)}"
                    )

            # Build WHERE clause
            if _sql.count('and_cond') > 0:
                _and = ' AND '.join(where_cond)
                _filter = f' AND {_and}'
                _sql = _sql.format_map(SafeDict(and_cond=_filter))
            elif _sql.count('where_cond') > 0:
                _and = ' AND '.join(where_cond)
                _filter = f' WHERE {_and}'
                _sql = _sql.format_map(SafeDict(where_cond=_filter))
            elif _sql.count('filter') > 0:
                _and = ' AND '.join(where_cond)
                _filter = f' WHERE {_and}'
                _sql = _sql.format_map(SafeDict(filter=_filter))
            else:
                _and = ' AND '.join(where_cond)
                if 'WHERE' in _sql:
                    _filter = f' AND {_and}'
                else:
                    _filter = f' WHERE {_and}'
                _sql = f'{_sql}{_filter}'

        # Clean up any remaining placeholders
        if '{where_cond}' in _sql:
            _sql = _sql.format_map(SafeDict(where_cond=''))
        if '{and_cond}' in _sql:
            _sql = _sql.format_map(SafeDict(and_cond=''))
        if '{filter}' in _sql:
            _sql = _sql.format_map(SafeDict(filter=''))

        return _sql

    async def process_fields(self, sql: str):
        """Override process_fields to handle JSON fields in the SELECT clause."""
        # Rust fast-path for field processing
        if HAS_RUST and isinstance(self.fields, list) and len(self.fields) > 0:
            try:
                cond_def = self.cond_definition if self.cond_definition else {}
                return _rs.bq_process_fields(
                    sql, self.fields, bool(self._add_fields),
                    self.query_raw, cond_def
                )
            except Exception:
                pass  # fall through to Cython implementation

        if isinstance(self.fields, list) and len(self.fields) > 0:
            if self._add_fields:
                if match := self._select_pattern.search(sql):
                    _fields = [field.strip() for field in match.group(2).split(",")]
                    processed_fields = []

                    for field in self.fields:
                        processed_field = await self.process_json_field(field)
                        if processed_field != field and "AS" not in processed_field:
                            alias = field.split('.')[-1]
                            processed_field = f"{processed_field} AS {alias}"
                        processed_fields.append(processed_field)

                    all_fields = _fields + processed_fields
                    sql = sql[:match.start(2)] + ' ' + ", ".join(all_fields) + ' ' + sql[match.end(2):]
                    return sql

            processed_fields = []
            for field in self.fields:
                processed_field = await self.process_json_field(field)
                if processed_field != field and "AS" not in processed_field:
                    alias = field.split('.')[-1]
                    processed_field = f"{processed_field} AS {alias}"
                processed_fields.append(processed_field)

            sql = sql.replace(' * FROM', ' {fields} FROM')
            fields = ', '.join(processed_fields)
            sql = sql.format_map(SafeDict(fields=fields))

        elif isinstance(self.fields, str):
            sql = sql.replace(' * FROM', ' {fields} FROM')
            field_list = self.fields.split(',')
            processed_fields = []

            for field in field_list:
                field = field.strip()
                processed_field = await self.process_json_field(field)
                if processed_field != field and "AS" not in processed_field:
                    alias = field.split('.')[-1]
                    processed_field = f"{processed_field} AS {alias}"
                processed_fields.append(processed_field)

            fields = ', '.join(processed_fields)
            sql = sql.format_map(SafeDict(fields=fields))

        elif '{fields}' in self.query_raw:
            self._conditions.update({'fields': '*'})

        return sql
