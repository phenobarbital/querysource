# cython: language_level=3, embedsignature=True, boundscheck=False, wraparound=False
# Copyright (C) 2018-present Jesus Lara
#
# file: cql.pyx
"""Cassandra CQL Parser.

Build CQL-Queries for Apache Cassandra, validation and parsing.
"""
import asyncio
from functools import partial
from datamodel.typedefs import NullDefault, SafeDict
from ..exceptions import EmptySentence
from ..types.validators import Entity, field_components
from .sql cimport SQLParser

# Try to import Rust extension for accelerated parsing
try:
    import qs_parsers as _rs
    HAS_RUST = True
except ImportError:
    HAS_RUST = False


cdef class CQLParser(SQLParser):
    """Cassandra CQL Parser."""

    def __init__(self, *args, **kwargs):
        super(CQLParser, self).__init__(*args, **kwargs)
        self._tablename = '{schema}.{table}'
        self.schema_based = True

    def set_cql(self, str cql):
        """Set a raw CQL query string."""
        self.query_raw = cql
        return self

    cpdef object where_cond(self, dict where):
        """Set filter conditions."""
        self.filter = where
        return self

    async def filter_conditions(self, sql):
        """WHERE conditions (rayon-parallel Rust fast-path)."""
        if HAS_RUST and self.filter and isinstance(self.filter, dict):
            try:
                cond_def = self.cond_definition if self.cond_definition else {}
                return _rs.cql_filter_conditions(sql, self.filter, cond_def)
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
        cdef str fval
        cdef list where_cond

        if self.filter:
            where_cond = []
            for key, value in self.filter.items():
                try:
                    if isinstance(int(key), (int, float)):
                        key = f'"{key}"'
                except ValueError:
                    pass
                try:
                    _format = self.cond_definition[key]
                except KeyError:
                    _format = None
                _, name, end = field_components(key)[0]
                # if format is not defined, need to be determined
                if isinstance(value, dict):
                    op, v = value.popitem()
                    if op in ('>=', '<=', '<>', '!=', '<', '>'):
                        where_cond.append(f"{key} {op} {v}")
                    else:
                        continue
                elif isinstance(value, list):
                    fval = value[0]
                    if fval in self.valid_operators:
                        where_cond.append(f"{key} {fval} {value[1]}")
                    else:
                        val = ','.join(
                            [
                                "{}".format(Entity.quoteString(v))
                                for v in value
                            ]
                        )
                        if end == '!':
                            where_cond.append(f"{name} NOT IN ({val})")
                        else:
                            where_cond.append(f"{key} IN ({val})")
                elif isinstance(value, (str, int)):
                    if "BETWEEN" in str(value):
                        if isinstance(value, str) and "'" not in value:
                            where_cond.append(f"({key} {value})")
                        else:
                            where_cond.append(f"({key} {value})")
                    elif value in ('null', 'NULL'):
                        where_cond.append(f"{key} IS NULL")
                    elif value in ('!null', '!NULL'):
                        where_cond.append(f"{key} IS NOT NULL")
                    elif end == '!':
                        where_cond.append(f"{name} != {value}")
                    elif str(value).startswith('!'):
                        where_cond.append(
                            f"{key} != {Entity.quoteString(value[1:])}"
                        )
                    else:
                        where_cond.append(
                            f"{key}={Entity.quoteString(value)}"
                        )
                elif isinstance(value, bool):
                    where_cond.append(f"{key} = {value}")
                else:
                    where_cond.append(
                        f"{key}={Entity.quoteString(value)}"
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
        """Build a CQL Query."""
        cdef str cql = self.query_raw
        self.logger.debug(f":: RAW CQL: {cql}")
        cql = await self.process_fields(cql)
        # add query options
        for _, func in self.get_query_filters().items():
            fn, args = func
            func = partial(
                fn, args, where=self.filter, program=self.program_slug
            )
            result, ordering = await asyncio.get_event_loop().run_in_executor(
                self._executor, func
            )
            self.filter = {**self.filter, **result}
            if ordering:
                self.ordering = self.ordering + ordering
        # add filtering conditions
        cql = self.filtering_options(cql)
        # processing filter options
        cql = await self.filter_conditions(cql)
        # processing conditions
        cql = await self.group_by(cql)
        if self.ordering:
            cql = await self.order_by(cql)
        if querylimit:
            cql = await self.limiting(cql, querylimit, offset)
        elif self.querylimit:
            cql = await self.limiting(cql, self.querylimit, self._offset)
        else:
            cql = await self.limiting(cql, '')
        if self.conditions and len(self.conditions) > 0:
            cql = cql.format_map(SafeDict(**self.conditions))
            # default null setters
            cql = cql.format_map(NullDefault())
        self.query_parsed = cql
        self.logger.debug(f": CQL :: {cql}")
        if self.query_parsed == '' or self.query_parsed is None:
            raise EmptySentence(
                'QS Cassandra: no CQL query to parse.'
            )
        return self.query_parsed
