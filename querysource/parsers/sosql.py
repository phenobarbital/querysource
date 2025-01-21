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
from ..exceptions import EmptySentence, ParserError
from .sql import SQLParser


class SOQLParser(SQLParser):
    schema_based: bool = False
    _schema: str = None  # default internal schema
    _tablename: str = '{table}'
    _base_sql: str = 'SELECT {fields} FROM {tablename} {filter} {offset} {limit}'

    def __init__(
        self,
        *args,
        **kwargs
    ):
        super(SOQLParser, self).__init__(
            *args,
            **kwargs
        )
        ## connection:
        self._connection: Callable = None

    def get_sf_fields(self):
        _table = re.search(r"FROM\s+(\S+)", self.query_raw).group(1)
        try:
            obj = getattr(self._connection, _table)
            desc = obj.describe()
            fields = []
            for f in desc['fields']:
                fields.append(f['name'])
            return fields
        except Exception as ex:
            raise ParserError(
                f"SF: Invalid Object {_table}: {ex}"
            ) from ex

    async def process_fields(self, sql: str):
        # adding option if not exists:
        if '*' in self.query_raw:
            sql = sql.replace('SELECT * FROM', 'SELECT {fields} FROM')
        if not self.fields:
            self.fields = self.get_sf_fields()
        # verify FIELDS:
        if isinstance(self.fields, list) and len(self.fields) > 0:
            fields = ', '.join(self.fields)
            sql = sql.format_map(SafeDict(fields=fields))
        elif isinstance(self.fields, str):
            fields = ', '.join(self.fields.split(','))
            sql = sql.format_map(SafeDict(fields=fields))
        elif self.fields is None:
            sql = sql.format_map(SafeDict(fields='*'))
        elif '{fields}' in self.query_raw:
            self.conditions.update({'fields': '*'})
        return sql

    async def filter_conditions(self, sql):
        """
        WHERE Conditions.
        """
        _sql = sql
        if self.filter:
            if self._procedure is True:
                # _filter = ','.join([f"{k}={v}" for k,v in self.filter.items()])
                _sql = f'{_sql}'
            else:
                where_cond = []
                print(f" == WHERE: {self.filter}")
                # processing where
                for key, value in self.filter.items():
                    _format = None
                    _, name, end = field_components(key)[0]
                    if key in self.cond_definition:
                        _format = self.cond_definition[key]
                        # print('HERE ', format, value)
                        # TODO: it format = epoch, convert from date
                    # if format is not defined, need to be determined
                    if isinstance(value, list):
                        # is a list of values
                        val = ','.join(
                            [
                                "{}".format(Entity.quoteString(v)) for v in value
                            ]
                        )  # pylint: disable=C0209
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

    async def build_query(self, connection: Callable):  # pylint: disable=W0221
        """
        build_query.
         Last Step: Build a SQL Query
        """
        sql = self.query_raw
        self._connection = connection
        self.logger.debug(f"RAW SQL is: {sql}")
        # self.logger.debug(f"FIELDS ARE {self.fields}")
        self.logger.debug(f'Conditions ARE: {self.filter}')
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
        # self.filter = self.query_options(self.qry_options)
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
            # default null setters
        self.query_parsed = sql
        # self.logger.debug(f": SOQL :: {sql}")
        if self.query_parsed == '' or self.query_parsed is None:
            raise EmptySentence(
                'SalesForce SOQL Error, no SQL query to parse.'
            )
        return self.query_parsed
