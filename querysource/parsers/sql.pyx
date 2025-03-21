# cython: language_level=3, embedsignature=True
# Copyright (C) 2018-present Jesus Lara
#
# file: abstract.pyx
"""
Basic SQL Parser.
"""
import asyncio
import re
from typing import Union
from functools import partial
from datamodel.typedefs import NullDefault, SafeDict
from ..exceptions cimport EmptySentence
from ..types.validators import Entity, field_components
from .abstract cimport AbstractParser


COMPARISON_TOKENS = ('>=', '<=', '<>', '!=', '<', '>',)


cdef class SQLParser(AbstractParser):
    """ SQL Parser. """
    def __init__(
        self,
        *args,
        **kwargs
    ):
        super(SQLParser, self).__init__(
            *args,
            **kwargs
        )
        self.valid_operators: tuple = ('<', '>', '>=', '<=', '<>', '!=', 'IS NOT', 'IS')
        self.tablename: str = '{schema}.{table}'
        self._base_sql: str = 'SELECT {fields} FROM {tablename} {filter} {grouping} {offset} {limit}'
        # Schema based:
        if self.schema_based is True:
            self.tablename = '{schema}.{table}'
        else:
            self.tablename = '{table}'
        # Group Pattern:
        self._group_pattern = re.compile(
            r"GROUP\s+BY\s+(.*?)(?=\b(?:FROM|HAVING|ORDER|LIMIT|WHERE)\b|$)"
        )
        # DOTALL to handle multiline SELECT clauses
        self._select_pattern = re.compile(
            r"(SELECT\s+)(.*?)(?=\bFROM\b)",
            re.IGNORECASE | re.DOTALL
        )

    async def get_sql(self):
        return await self.build_query()

    async def filter_conditions(self, sql):
        """
        Options for Filtering.
        """
        _sql = sql
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
                    if op in COMPARISON_TOKENS:
                        where_cond.append(f"{key} {op} {v}")
                    else:
                        # currently, discard any non-supported comparison token
                        continue
                elif isinstance(value, list):
                    fval = value[0]
                    if fval in self.valid_operators:
                        where_cond.append(f"{key} {fval} {value[1]}")
                    else:
                        # TODO: passing for a Function Parser.
                        # is a list of values
                        val = ','.join(
                            [
                                "{}".format(Entity.quoteString(v)) for v in value
                            ]
                        )  # pylint: disable=C0209
                        # check for operator
                        if end == '!':
                            where_cond.append(f"{name} NOT IN ({val})")
                        else:
                            where_cond.append(f"{key} IN ({val})")
                elif isinstance(value, (str, int)):
                    if "BETWEEN" in str(value):
                        if isinstance(value, str) and "'" not in value:
                            where_cond.append(
                                f"({key} {value})"
                            )
                        else:
                            where_cond.append(
                                f"({key} {value})"
                            )
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
                        where_cond.append(
                            f"{key} != {Entity.quoteString(value[1:])}"
                        )
                    else:
                        where_cond.append(
                            f"{key}={Entity.quoteString(value)}"
                        )
                elif isinstance(value, bool):
                    where_cond.append(
                        f"{key} = {value}"
                    )
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

    async def group_by(self, sql: str):
        # TODO: adding GROUP BY GROUPING SETS OR ROLLUP
        if self.grouping:
            match = self._group_pattern.search(sql)
            if match:
                # Extract the current group by columns
                current_columns = [
                    col.strip() for col in match.group(1).split(",")
                ]
                # Add the additional columns to the current columns
                all_columns = current_columns + self.grouping
                # Reconstruct the SQL query with the modified GROUP BY clause
                sql = sql[:match.start(1)] + ", ".join(all_columns) + sql[match.end(1):]
            else:
                if isinstance(self.grouping, str):
                    sql = f"{sql} GROUP BY {self.grouping}"
                else:
                    group = ', '.join(self.grouping)
                    sql = f"{sql} GROUP BY {group}"
        return sql

    async def order_by(self, sql: str):
        _sql = "{sql} ORDER BY {order}"
        if isinstance(self.ordering, list) and len(self.ordering) > 0:
            order = ', '.join(self.ordering)
            sql = _sql.format_map(SafeDict(sql=sql, order=order))
        else:
            sql = _sql.format_map(SafeDict(sql=sql, order=self.ordering))
        return sql

    async def limiting(self, sql: str, limit: Union[str, int] = None, offset: Union[str, int] = None):
        if '{limit}' in sql:
            if limit:
                limit = f"LIMIT {limit}"
            sql = sql.format_map(SafeDict(limit=limit))
        elif limit:
            sql = f"{sql} LIMIT {limit}"
        if '{offset}' in sql:
            if offset:
                offset = f"OFFSET {offset}"
                sql = sql.format_map(SafeDict(offset=offset))
        elif offset:
            sql = f"{sql} OFFSET {offset}"

        return sql

    async def process_fields(self, sql: str):
        if isinstance(self.fields, list) and len(self.fields) > 0:
            if self._add_fields:
                # Only add new fields if requested:
                match = self._select_pattern.search(sql)
                if match:
                    # Extract the current SELECT fields
                    _fields = [field.strip() for field in match.group(2).split(",")]
                    # Add the new fields after the current fields
                    all_fields = _fields + self.fields
                    # Reconstruct the SQL query with the modified SELECT clause
                    sql = sql[:match.start(2)] + ' ' + ", ".join(all_fields) + ' ' + sql[match.end(2):]
                    return sql
            sql = sql.replace(' * FROM', ' {fields} FROM')
            fields = ', '.join(self.fields)
            sql = sql.format_map(SafeDict(fields=fields))
        elif isinstance(self.fields, str):
            sql = sql.replace(' * FROM', ' {fields} FROM')
            fields = ', '.join(self.fields.split(','))
            sql = sql.format_map(SafeDict(fields=fields))
        elif '{fields}' in self.query_raw:
            self._conditions.update({'fields': '*'})
        return sql

    async def build_query(self, querylimit: int = None, offset: int = None):
        """
        build_query.
        Last Step: Build a SQL Query.
        """
        sql = self.query_raw
        # check table and schema names:
        if '{schema}' in sql:
            sql = sql.format_map(SafeDict(schema=self.schema, table=self.tablename))
        elif '{table}' in sql:
            sql = sql.format_map(SafeDict(table=self.tablename))
        sql = await self.process_fields(sql)
        # add query options
        ## TODO: Function FILTERS (called in threads)
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
            try:
                sql = sql.format_map(SafeDict(**self._conditions))
                sql = sql.format_map(NullDefault())
            except ValueError:
                pass
        # default null setters
        self.query_parsed = sql
        self.logger.debug(
            f": SQL :: {sql}"
        )
        if self.query_parsed == '' or self.query_parsed is None:
            raise EmptySentence(
                'QS SQL Error, no SQL query to parse.'
            )
        return self.query_parsed
