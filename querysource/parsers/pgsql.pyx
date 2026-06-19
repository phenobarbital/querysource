# cython: language_level=3, embedsignature=True
# Copyright (C) 2018-present Jesus Lara
#
# file: pgsql.pyx
"""
SQL Parser for PostgreSQL.
"""
import asyncio
from string import Template
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from datamodel.typedefs import NullDefault, SafeDict
from datamodel.parsers.json import json_encoder
from ..exceptions import EmptySentence
from ..types.validators import Entity, field_components, is_integer, is_camel_case
from .sql cimport SQLParser

# Try to import Rust extension for accelerated parsing
try:
    from querysource.qs_parsers import _qs_parsers as _rs
    HAS_RUST = True
except ImportError:
    HAS_RUST = False


COMPARISON_TOKENS = ('>=', '<=', '<>', '!=', '<', '>',)


cdef class pgSQLParser(SQLParser):
    """PostgreSQL-specific SQL Parser."""

    def __init__(self, *args, **kwargs):
        super(pgSQLParser, self).__init__(*args, **kwargs)
        self.schema_based = True

    async def filter_conditions(self, sql):
        """Options for Filtering (PostgreSQL-specific, rayon-parallel Rust fast-path)."""
        if HAS_RUST and self.filter and isinstance(self.filter, dict):
            try:
                cond_def = self.cond_definition if self.cond_definition else {}
                return _rs.pgsql_filter_conditions(sql, self.filter, cond_def)
            except Exception:
                pass  # fall through to Cython implementation
        return await self._filter_conditions_cy(sql)

    async def _filter_conditions_cy(self, sql):
        """Cython fallback for filter_conditions with full PG operator support."""
        cdef str _sql = sql
        cdef str key
        cdef str name
        cdef str end
        cdef str _format
        cdef str _and
        cdef str _filter
        cdef str val
        cdef str fval
        cdef str op
        cdef list where_cond

        if self.filter:
            where_cond = []
            for key, value in self.filter.items():
                # SECURITY (FEAT-103): Validate key as a safe SQL identifier.
                try:
                    if isinstance(int(key), (int, float)):
                        key = f'"{key}"'
                    else:
                        stripped = key.rstrip('|!~#@:')
                        if not all(c.isalnum() or c == '_' or c == '.' for c in stripped):
                            continue
                except (ValueError, TypeError):
                    stripped = key.rstrip('|!~#@:')
                    if not all(c.isalnum() or c == '_' or c == '.' for c in stripped):
                        continue
                try:
                    _format = self.cond_definition[key]
                except KeyError:
                    _format = None
                try:
                    if is_integer(key):
                        key = f'"{key}"'
                except ValueError:
                    pass
                try:
                    _, name, end = field_components(str(key))[0]
                except IndexError:
                    name = key
                    end = None
                # if format is not defined, need to be determined
                if isinstance(value, dict):
                    op, v = value.popitem()
                    if op in COMPARISON_TOKENS:
                        # SECURITY: Escape the comparison value
                        safe_v = Entity.quoteString(v) if isinstance(v, str) else str(v)
                        where_cond.append(f"{key} {op} {safe_v}")
                    else:
                        # currently, discard any non-supported comparison token
                        continue
                elif isinstance(value, list):
                    try:
                        fval = value[0]
                        if fval in self.valid_operators:
                            # SECURITY: Escape second operand
                            safe_v = Entity.quoteString(str(value[1])) if value[1] not in ('null', 'NULL') else str(value[1])
                            where_cond.append(f"{key} {fval} {safe_v}")
                        else:
                            if _format in ('date', 'datetime'):
                                # SECURITY: Escape BETWEEN boundary values
                                safe_v0 = str(value[0]).replace("'", "''")
                                safe_v1 = str(value[1]).replace("'", "''")
                                if end == '!':
                                    where_cond.append(f"{name} NOT BETWEEN '{safe_v0}' AND '{safe_v1}'")
                                else:
                                    where_cond.append(f"{name} BETWEEN '{safe_v0}' AND '{safe_v1}'")
                                continue
                            # is a list of values
                            val = ','.join(["{}".format(Entity.quoteString(v)) for v in value])  # pylint: disable=C0209
                            # check for operator
                            if end == '!':
                                where_cond.append(f"{name} NOT IN ({val})")
                            else:
                                if _format == 'array':
                                    if end == '|':
                                        where_cond.append(
                                            "ARRAY[{val}]::character varying[]  && {name}::character varying[]"
                                        )
                                    else:
                                        # I need to build a query based array fields
                                        where_cond.append(
                                            "ARRAY[{val}]::character varying[]  <@ {key}::character varying[]"
                                        )
                                else:
                                    where_cond.append(f"{key} IN ({val})")
                    except (KeyError, IndexError):
                        val = ','.join(["{}".format(Entity.quoteString(v)) for v in value])
                        if not val:
                            where_cond.append(f"{key} IN (NULL)")
                        else:
                            where_cond.append(f"{key} IN {val}")
                elif isinstance(value, (str, int)):
                    str_value = str(value)
                    if end == '~':
                        base = str_value[:-1].replace("'", "''")
                        val = f"'{base}%'"
                        where_cond.append(f"{name} ILIKE {val}")
                    elif end == '!~':
                        base = str_value[:-1].replace("'", "''")
                        val = f"'{base}%'"
                        where_cond.append(f"{name} NOT ILIKE {val}")
                    elif "BETWEEN" in str_value:
                        # SECURITY: Reject BETWEEN clauses with injection markers
                        upper_val = str_value.upper()
                        if ('--' in str_value or '/*' in str_value or ';' in str_value
                                or 'UNION' in upper_val or 'SELECT' in upper_val):
                            continue
                        where_cond.append(f"({key} {str_value})")
                    elif value in ('null', 'NULL'):
                        where_cond.append(
                            f"{key} IS NULL"
                        )
                    elif value in ('!null', '!NULL'):
                        where_cond.append(
                            f"{key} IS NOT NULL"
                        )
                    elif end == '!':
                        # SECURITY: Escape the negated value
                        where_cond.append(
                            f"{name} != {Entity.quoteString(str_value)}"
                        )
                    elif str_value.startswith('!'):
                        where_cond.append(
                            f"{key} != {Entity.quoteString(str_value[1:])}"
                        )
                    else:
                        if _format == 'array':
                            if isinstance(value, int):
                                where_cond.append(
                                    f"{value} = ANY({key})"
                                )
                            else:
                                # SECURITY: Escape value before type cast
                                safe_val = str_value.replace("'", "''")
                                where_cond.append(
                                    f"'{safe_val}'::character varying = ANY({key})"
                                )
                        elif _format == 'numrange':
                            # SECURITY: Validate numeric value
                            try:
                                float(str_value)
                                where_cond.append(f"{str_value}::numeric <@ {key}")
                            except (ValueError, TypeError):
                                continue
                        elif _format in ('int4range', 'int8range'):
                            # SECURITY: Validate integer value
                            try:
                                int(str_value)
                                where_cond.append(f"{str_value}::integer <@ {key}::int4range")
                            except (ValueError, TypeError):
                                continue
                        elif _format in ('tsrange', 'tstzrange'):
                            # SECURITY: Escape timestamp value
                            safe_val = str_value.replace("'", "''")
                            where_cond.append(
                                f"'{safe_val}'::timestamptz <@ {key}::tstzrange"
                            )
                        elif _format == 'daterange':
                            # SECURITY: Escape date value
                            safe_val = str_value.replace("'", "''")
                            where_cond.append(
                                f"'{safe_val}'::date <@ {key}::daterange"
                            )
                        else:
                            if is_camel_case(key):
                                key = '"{}"'.format(key)
                            where_cond.append(
                                f"{key}={Entity.quoteString(value)}"
                            )
                elif isinstance(value, dict):
                    # making a JSONB search:
                    # check first if dictionary have only one key:
                    if len(value) == 1:
                        v = json_encoder(value)
                        where_cond.append(
                            f"{key} @> {v}"
                        )
                    else:
                        op, v = value.popitem()
                        if op in ('->', '->>', '@>', '@>', '<@', '<@'):
                            if isinstance(v, (str, int)):
                                where_cond.append(
                                    f"{key} {op} {Entity.quoteString(v)}"
                                )
                            else:
                                where_cond.append(
                                    f"{key} {op} {v}"
                                )
                elif isinstance(value, bool):
                    where_cond.append(
                        f"{key} = {value}"
                    )
                else:
                    where_cond.append(
                        f"{key}={Entity.escapeString(value)}"
                    )
            # build WHERE
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
                # need to attach the condition
                _and = ' AND '.join(where_cond)
                if 'WHERE' in _sql:
                    _filter = f' AND {_and}'
                else:
                    _filter = f' WHERE {_and}'
                _sql = f'{_sql}{_filter}'
        if '{where_cond}' in _sql:
            _sql = _sql.format_map(SafeDict(where_cond=''))
        if '{and_cond}' in _sql:
            _sql = _sql.format_map(SafeDict(and_cond=''))
        if '{filter}' in _sql:
            _sql = _sql.format_map(SafeDict(filter=''))
        return _sql

    async def build_query(self, querylimit: int = None, offset: int = None):
        """
        build_query.
         Last Step: Build a SQL Query.
        """
        cdef str sql = self.query_raw
        self.logger.notice(
            f"RAW SQL is: {sql}"
        )
        # check table and schema names:
        if '{schema}' in sql:
            sql = sql.format_map(
                SafeDict(schema=self.schema, table=self.tablename)
            )
        elif '{table}' in sql:
            sql = sql.format_map(
                SafeDict(table=self.tablename)
            )
        sql = await self.process_fields(sql)
        # add query options
        ## TODO: Function FILTERS (called in threads)
        for _, func in self.get_query_filters().items():
            fn, args = func
            result = {}
            func = partial(
                fn,
                args,
                where=self.filter,
                program=self.program_slug,
                hierarchy=self._hierarchy
            )
            with ThreadPoolExecutor(max_workers=1) as executor:
                result, ordering = await asyncio.get_event_loop().run_in_executor(
                    executor, func
                )
            self.filter = {**self.filter, **result}
            if ordering:
                self.ordering = self.ordering + ordering
        # add filtering conditions
        sql = self.filtering_options(sql)
        # processing filter options
        sql = await self.filter_conditions(sql)
        # processing conditions
        sql = await self.group_by(sql)
        if self.ordering:
            sql = await self.order_by(sql)
        if querylimit:
            sql = await self.limiting(sql, querylimit, offset)
        elif self.querylimit:
            sql = await self.limiting(sql, self.querylimit, self._offset)
        else:
            sql = await self.limiting(sql, '')
        if isinstance(self._conditions, dict):
            if self._conditions:
                sql = sql.format_map(
                    SafeDict(**self._conditions)
                )
        try:
            if self._safe_substitution is True:
                sql = Template(sql)
                sql = sql.safe_substitute(NullDefault())
            else:
                sql = sql.format_map(NullDefault())
        except ValueError:
            pass
        self.query_parsed = sql
        self.logger.debug(
            f":: SQL : {sql}"
        )
        if self.query_parsed == '' or self.query_parsed is None:
            raise EmptySentence(
                'PG SQL Error, no SQL query to parse.'
            )
        return self.query_parsed
