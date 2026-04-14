# cython: language_level=3, embedsignature=True
# Copyright (C) 2018-present Jesus Lara
#
# file: sqlserver.pyx
"""
MS SQL Server Parser.

Build SQL-Queries for MS SQL Server, validation and parsing.
"""
from datamodel.typedefs import SafeDict
from ..types.validators import Entity, field_components
from ..exceptions import EmptySentence
from .sql cimport SQLParser

# Try to import Rust extension for accelerated parsing
try:
    from querysource.qs_parsers import _qs_parsers as _rs
    HAS_RUST = True
except ImportError:
    HAS_RUST = False


cdef class msSQLParser(SQLParser):
    """MS SQL Server Parser."""

    def __init__(self, *args, bint is_procedure=False, **kwargs):
        self._procedure = is_procedure
        super(msSQLParser, self).__init__(*args, **kwargs)
        self.schema_based = True
        self.schema = 'dbo'
        self._base_sql = 'SELECT {limit} {fields} FROM {tablename} {filter} {grouping} {offset} {limit}'
        self.tablename = '{schema}.{table}'

    async def process_fields(self, sql):
        """Process fields with MS SQL Server TOP syntax."""
        cdef str _sql = sql

        # adding option if not exists:
        if '{fields}' in self.query_raw:
            _sql = _sql.replace('SELECT {fields} FROM', 'SELECT {limit} {fields} FROM')
        else:
            _sql = _sql.replace('SELECT * FROM', 'SELECT {limit} {fields} FROM')
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

    async def limiting(self, sql, limit=None, offset=None):
        """MS SQL Server uses TOP N instead of LIMIT."""
        cdef str _sql = sql

        if self._procedure is True:
            return _sql
        if limit:
            limit = f"TOP {limit} {{fields}}"
            _sql = _sql.format_map(SafeDict(fields=limit))
        else:
            _sql = _sql.format_map(SafeDict(limit=''))
        return _sql

    async def filter_conditions(self, sql):
        """WHERE conditions (rayon-parallel Rust fast-path)."""
        if self._procedure is True:
            return sql
        if HAS_RUST and self.filter and isinstance(self.filter, dict):
            try:
                cond_def = self.cond_definition if self.cond_definition else {}
                return _rs.mssql_filter_conditions(sql, self.filter, cond_def)
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
                _format = None
                _, name, end = field_components(key)[0]
                if key in self.cond_definition:
                    _format = self.cond_definition[key]
                # if format is not defined, need to be determined
                if isinstance(value, list):
                    # is a list of values
                    val = ','.join(["{}".format(Entity.quoteString(v)) for v in value])  # pylint: disable=C0209
                    # check for operator
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
