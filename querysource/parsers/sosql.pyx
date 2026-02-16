# cython: language_level=3, embedsignature=True, boundscheck=False, wraparound=False
# Copyright (C) 2018-present Jesus Lara
#
# file: sosql.pyx
"""
SalesForce SOQL Parser.

Build SOQL-Queries for SalesForce, validation and parsing.
"""
import asyncio
from collections.abc import Callable
import re
from functools import partial
from datamodel.typedefs import NullDefault, SafeDict
from ..types.validators import Entity, field_components
from ..exceptions cimport EmptySentence
from ..exceptions import ParserError
from .sql cimport SQLParser

# Try to import Rust extension for accelerated parsing
try:
    import qs_parsers as _rs
    HAS_RUST = True
except ImportError:
    HAS_RUST = False


cdef class SOQLParser(SQLParser):
    """SalesForce SOQL Parser."""

    def __init__(self, *args, **kwargs):
        super(SOQLParser, self).__init__(*args, **kwargs)
        self.schema_based = False
        self.schema = None
        self.tablename = '{table}'
        self._base_sql = 'SELECT {fields} FROM {tablename} {filter} {offset} {limit}'
        self._connection = None

    def get_sf_fields(self):
        """Discover fields from a Salesforce object via connection."""
        cdef str _table
        _table = re.search(r"FROM\s+(\S+)", self.query_raw).group(1)
        try:
            obj = getattr(self._connection, _table)
            desc = obj.describe()
            fields = [f['name'] for f in desc['fields']]
            return fields
        except Exception as ex:
            raise ParserError(
                f"SF: Invalid Object {_table}: {ex}"
            ) from ex

    async def process_fields(self, sql):
        """Process SOQL fields, discovering from SF connection if needed."""
        cdef str _sql = sql
        # adding option if not exists:
        if '*' in self.query_raw:
            _sql = _sql.replace('SELECT * FROM', 'SELECT {fields} FROM')
        if not self.fields:
            self.fields = self.get_sf_fields()
        # verify FIELDS:
        if isinstance(self.fields, list) and len(self.fields) > 0:
            fields = ', '.join(self.fields)
            _sql = _sql.format_map(SafeDict(fields=fields))
        elif isinstance(self.fields, str):
            fields = ', '.join(self.fields.split(','))
            _sql = _sql.format_map(SafeDict(fields=fields))
        elif self.fields is None:
            _sql = _sql.format_map(SafeDict(fields='*'))
        elif '{fields}' in self.query_raw:
            self.conditions.update({'fields': '*'})
        return _sql

    async def filter_conditions(self, sql):
        """WHERE conditions (rayon-parallel Rust fast-path)."""
        if HAS_RUST and self.filter and isinstance(self.filter, dict):
            try:
                cond_def = self.cond_definition if self.cond_definition else {}
                return _rs.soql_filter_conditions(sql, self.filter, cond_def)
            except Exception:
                pass  # fall through to Cython implementation
        return await self._filter_conditions_cy(sql)

    async def _filter_conditions_cy(self, sql):
        """Cython fallback for filter_conditions."""
        cdef str _sql = sql
        cdef str key
        cdef str name
        cdef str end
        cdef str _format
        cdef str _and
        cdef str _filter
        cdef str val
        cdef list where_cond

        if self.filter:
            where_cond = []
            for key, value in self.filter.items():
                _format = None
                _, name, end = field_components(key)[0]
                if key in self.cond_definition:
                    _format = self.cond_definition[key]
                if isinstance(value, list):
                    val = ','.join(
                        ["{}".format(Entity.quoteString(v)) for v in value]
                    )
                    if end == '!':
                        where_cond.append(
                            f"{name} NOT IN ({val})"
                        )
                    else:
                        if _format == 'date':
                            where_cond.append(
                                f"{key} BETWEEN '{value[0]}' AND '{value[1]}'"
                            )
                        else:
                            where_cond.append(
                                f"{key} IN ({val})"
                            )
                elif isinstance(value, (str, int)):
                    if "BETWEEN" in str(value):
                        if isinstance(value, str) and "'" not in value:
                            where_cond.append(
                                f"({key} {Entity.quoteString(value)})"
                            )
                        else:
                            where_cond.append(f"({key} {value})")
                    elif value in ('null', 'NULL'):
                        where_cond.append(
                            f"{key} IS NULL"
                        )
                    elif value in ('!null', '!NULL'):
                        where_cond.append(
                            f"{key} IS NOT NULL"
                        )
                    elif end == '!':
                        where_cond.append(
                            f"{name} != {value}"
                        )
                    elif str(value).startswith('!'):
                        _val = Entity.escapeString(value[1:])
                        where_cond.append(
                            f"{key} != {_val}"
                        )
                    else:
                        if isinstance(value, (int, bool)):
                            where_cond.append(
                                f"{key} = {value}"
                            )
                        else:
                            where_cond.append(
                                f"{key} = {Entity.quoteString(value)}"
                            )
                elif isinstance(value, (int, bool)):
                    where_cond.append(
                        f"{key} = {value}"
                    )
                else:
                    where_cond.append(
                        f"{key} = {Entity.escapeString(value)}"
                    )
            # build WHERE
            if 'and_cond' in _sql:
                _and = ' AND '.join(where_cond)
                _filter = f' AND {_and}'
                _sql = _sql.format_map(SafeDict(and_cond=_filter))
            elif 'where_cond' in _sql:
                _and = ' AND '.join(where_cond)
                _filter = f' WHERE {_and}'
                _sql = _sql.format_map(SafeDict(where_cond=_filter))
            elif 'filter' in _sql:
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
        if '{where_cond}' in _sql:
            _sql = _sql.format_map(SafeDict(where_cond=''))
        if '{and_cond}' in _sql:
            _sql = _sql.format_map(SafeDict(and_cond=''))
        if '{filter}' in _sql:
            _sql = _sql.format_map(SafeDict(filter=''))
        return _sql

    async def build_query(self, connection: Callable):
        """Build a SOQL Query."""
        cdef str sql = self.query_raw
        self._connection = connection
        self.logger.debug(f"RAW SQL is: {sql}")
        self.logger.debug(f'Conditions ARE: {self.filter}')
        sql = await self.process_fields(sql)
        # add query options — run QS_FILTERS in executor
        for _, func in self.get_query_filters().items():
            fn, args = func
            func = partial(fn, args, where=self.filter, program=self.program_slug)
            result, ordering = await asyncio.get_event_loop().run_in_executor(
                self._executor, func
            )
            self.filter = {**self.filter, **result}
            if ordering:
                self.ordering = self.ordering + ordering
        # add filtering conditions
        sql = await self.filtering_options(sql)
        # processing filter options
        sql = await self.filter_conditions(sql)
        if self.conditions and len(self.conditions) > 0:
            try:
                sql = sql.format_map(SafeDict(**self.conditions))
                sql = sql.format_map(NullDefault())
            except ValueError:
                pass
        self.query_parsed = sql
        if self.query_parsed == '' or self.query_parsed is None:
            raise EmptySentence(
                'SalesForce SOQL Error, no SQL query to parse.'
            )
        return self.query_parsed
