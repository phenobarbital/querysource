import asyncio
from functools import partial

from querysource.exceptions import EmptySentence
from querysource.models import QueryObject
from querysource.providers import BaseProvider
# from .parser import QueryParser, ParserHolders
from querysource.types.typedefs import NullDefault, SafeDict
from querysource.types.validators import Entity, field_components, is_integer

from .abstract import COMPARISON_TOKENS, QueryParser

valid_operators = ('<', '>', '>=', '<=', '<>', '!=', 'IS NOT', 'IS')

class pgSQLParser(QueryParser):
    schema_based: bool = True
    _tablename:str = '{schema}.{table}'
    _base_sql:str = 'SELECT {fields} FROM {tablename} {filter} {grouping} {offset} {limit}'

    def __init__(
        self,
        query: str = None,
        options: BaseProvider = None,
        conditions: QueryObject = None,
        **kwargs
    ):
        super(pgSQLParser, self).__init__(
            query=query,
            options=options,
            conditions=conditions,
            **kwargs
        )
        if self.schema_based is True:
            self._tablename = '{schema}.{table}'
        else:
            self._tablename = '{table}'

    async def get_sql(self):
        sql = await self.build_query()
        return sql

    def where_cond(self, where):
        self.filter = where
        return self

    async def filtering_options(self, sentence): #pylint: disable=W0221
        """
        Filtering Conditions.
        """
        await super(pgSQLParser, self).filtering_options()
        _sql = sentence
        if self.filter_options:
            if 'where_cond' not in _sql or 'filter' not in _sql:
                    _sql = f'{sentence!s} {{filter}}'
        return _sql

    async def filter_conditions(self, sql):
        """
        Options for Filtering.
        """
        _sql = sql
        if self.filter:
            where_cond = []
            self.logger.debug(f" == WHERE: {self.filter}")
            for key, value in self.filter.items():
                try:
                    if isinstance(int(key), (int, float)):
                        key = f'"{key}"'
                except ValueError:
                    pass
                # print(':::: KEY: ', key, ' VALUE: ', value)
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
                        where_cond.append(f"{key} {op} {v}")
                    else:
                        # currently, discard any non-supported comparison token
                        continue
                elif isinstance(value, list):
                    fval = value[0]
                    if fval in valid_operators:
                        where_cond.append(f"{key} {fval} {value[1]}")
                    else:
                        # is a list of values
                        val = ','.join(["{}".format(Entity.quoteString(v)) for v in value]) #pylint: disable=C0209
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
                        if _format == 'array':
                            if isinstance(value, int):
                                where_cond.append(
                                    f"{value} = ANY({key})"
                                )
                            else:
                                where_cond.append(
                                    f"{value}::character varying = ANY({key})"
                                )
                        elif _format == 'numrange':
                            where_cond.append(
                                f"{value}::numeric <@ {key}"
                            )
                        elif _format in ('int4range', 'int8range'):
                            where_cond.append(
                                f"{value}::integer <@ {key}::int4range"
                            )
                        elif _format in ('tsrange', 'tstzrange'):
                            # sample f.past_quarter @> order_date
                            where_cond.append(
                                f"{value}::timestamptz <@ {key}::tstzrange"
                            )
                        elif _format == 'daterange':
                            where_cond.append(
                                f"{value}::date <@ {key}::daterange"
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
                        f"{key}={Entity.escapeString(value)}"
                    )
            # build WHERE
            if _sql.count('and_cond') > 0:
                _and = ' AND '.join(where_cond)
                self.filter = f' AND {_and}'
                _sql = _sql.format_map(SafeDict(and_cond=self.filter))
            elif _sql.count('where_cond') > 0:
                _and = ' AND '.join(where_cond)
                self.filter = f' WHERE {_and}'
                _sql = _sql.format_map(SafeDict(where_cond=self.filter))
            elif _sql.count('filter') > 0:
                _and = ' AND '.join(where_cond)
                self.filter = f' WHERE {_and}'
                _sql = _sql.format_map(SafeDict(filter=self.filter))
            else:
                # need to attach the condition
                _and = ' AND '.join(where_cond)
                if 'WHERE' in _sql:
                    self.filter = f' AND {_and}'
                else:
                    self.filter = f' WHERE {_and}'
                _sql = f'{_sql}{self.filter}'
        if '{where_cond}' in _sql:
            _sql = _sql.format_map(SafeDict(where_cond=''))
        if '{and_cond}' in _sql:
            _sql = _sql.format_map(SafeDict(and_cond=''))
        if '{filter}' in _sql:
            _sql = _sql.format_map(SafeDict(filter=''))
        return _sql

    async def group_by(self, sql:str):
        # TODO: adding GROUP BY GROUPING SETS OR ROLLUP
        if self.grouping:
            if isinstance(self.grouping, str):
                sql = f"{sql} GROUP BY {self.grouping}"
            else:
                group = ', '.join(self.grouping)
                sql = f"{sql} GROUP BY {group}"
        return sql

    async def order_by(self, sql:str):
        _sql = "{sql} ORDER BY {order}"
        if isinstance(self.ordering, list) and len(self.ordering) > 0:
            order = ', '.join(self.ordering)
            sql = _sql.format_map(SafeDict(sql=sql, order=order))
        else:
            sql = _sql.format_map(SafeDict(sql=sql, order=self.ordering))
        return sql

    async def limiting(self, sql:str, limit:str = None, offset:str = None):
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

    async def process_fields(self, sql:str):
        if isinstance(self.fields, list) and len(self.fields) > 0:
            sql = sql.replace(' * FROM', ' {fields} FROM')
            fields = ', '.join(self.fields)
            sql = sql.format_map(SafeDict(fields=fields))
        elif isinstance(self.fields, str):
            sql = sql.replace(' * FROM', ' {fields} FROM')
            fields = ', '.join(self.fields.split(','))
            sql = sql.format_map(SafeDict(fields=fields))
        elif '{fields}' in self.query_raw:
            self.conditions.update({'fields': '*'})
        return sql

    async def build_query(self, querylimit: int = None, offset: int = None):
        """
        build_query.
         Last Step: Build a SQL Query
        """
        sql = self.query_raw
        # self.logger.debug(f"RAW SQL is: {sql}")
        sql = await self.process_fields(sql)
        # add query options
        ## TODO: Function FILTERS (called in threads)
        for _, func in self._query_filters.items():
            fn, args = func
            func = partial(fn, args, where=self.filter, program=self.program_slug, hierarchy=self._hierarchy)
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
        if self.conditions and len(self.conditions) > 0:
            sql = sql.format_map(SafeDict(**self.conditions))
            # default null setters
            sql = sql.format_map(NullDefault())
        self.query_parsed = sql
        self.logger.debug(f":: SQL : {sql}")
        if self.query_parsed == '' or self.query_parsed is None:
            raise EmptySentence(
                'QuerySource SQL Error, no SQL query to parse.'
            )
        return self.query_parsed
