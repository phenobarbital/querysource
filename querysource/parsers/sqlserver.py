"""
MS SQL Server Parser.

Build SQL-Queries for MS SQL Server, validation and parsing.
"""
from datamodel.typedefs import SafeDict
from ..types.validators import Entity, field_components
from .sql import SQLParser


class msSQLParser(SQLParser):
    schema_based: bool = True
    schema: str = 'dbo'  # default internal schema

    def __init__(
        self,
        *args,
        is_procedure: bool = False,
        **kwargs
    ):
        self._procedure: bool = is_procedure
        super(msSQLParser, self).__init__(
            *args,
            **kwargs
        )
        self._base_sql: str = 'SELECT {limit} {fields} FROM {tablename} {filter} {grouping} {offset} {limit}'
        self.tablename: str = '{schema}.{table}'

    async def process_fields(self, sql: str):
        # adding option if not exists:
        if '{fields}' in self.query_raw:
            sql = sql.replace('SELECT {fields} FROM', 'SELECT {limit} {fields} FROM')
        else:
            sql = sql.replace('SELECT * FROM', 'SELECT {limit} {fields} FROM')
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

    async def limiting(self, sql: str, limit: str = None, offset: str = None):
        if self._procedure is True:
            return sql
        if limit:
            limit = f"TOP {limit} {{fields}}"
            sql = sql.format_map(SafeDict(fields=limit))
        else:
            sql = sql.format_map(SafeDict(limit=''))
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
