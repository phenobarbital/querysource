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
from .sql import SQLParser


COMPARISON_TOKENS = ('>=', '<=', '<>', '!=', '<', '>',)


class pgSQLParser(SQLParser):
    schema_based: bool = True

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
                    try:
                        fval = value[0]
                        if fval in self.valid_operators:
                            where_cond.append(f"{key} {fval} {value[1]}")
                        else:
                            if _format in ('date', 'datetime'):
                                if end == '!':
                                    where_cond.append(f"{name} NOT BETWEEN '{value[0]}' AND '{value[1]}'")
                                else:
                                    where_cond.append(f"{name} BETWEEN '{value[0]}' AND '{value[1]}'")
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
                    if end == '~':
                        val = value[:-1] + "%'"
                        where_cond.append(f"{name} ILIKE {val}")
                    elif end == '!~':
                        val = value[:-1] + "%'"
                        where_cond.append(f"{name} NOT ILIKE {val}")
                    elif "BETWEEN" in str(value):
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
         Last Step: Build a SQL Query
        """
        sql = self.query_raw
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
