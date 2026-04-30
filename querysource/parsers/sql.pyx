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
from ..exceptions import EmptySentence
from ..types.validators import Entity, field_components
from .abstract cimport AbstractParser

# Try to import Rust extension for accelerated parsing
try:
    from querysource.qs_parsers import _qs_parsers as _rs
    HAS_RUST = True
except ImportError:
    HAS_RUST = False


COMPARISON_TOKENS = ('>=', '<=', '<>', '!=', '<', '>',)


cdef Py_ssize_t _find_kw_at_depth(str sql, str keyword, int target_depth):
    """Locate ``keyword`` at the given parenthesis depth.

    Skips content inside single-quoted string literals (with `''` escapes).
    Mirrors `find_keyword_at_depth` in rust/src/sql_parser.rs and is used to
    pick the *outer* GROUP BY in queries that contain CTEs or subqueries
    with their own GROUP BY clause. Returns -1 if no match is found.
    """
    cdef Py_ssize_t i = 0
    cdef Py_ssize_t n = len(sql)
    cdef Py_ssize_t kw_len = len(keyword)
    cdef int depth = 0
    cdef bint in_string = False
    cdef str keyword_upper = keyword.upper()
    cdef str c
    while i < n:
        c = sql[i]
        if in_string:
            if c == "'":
                if i + 1 < n and sql[i + 1] == "'":
                    i += 2
                    continue
                in_string = False
            i += 1
            continue
        if c == "'":
            in_string = True
            i += 1
            continue
        if c == "(":
            depth += 1
            i += 1
            continue
        if c == ")":
            depth -= 1
            i += 1
            continue
        if depth == target_depth and i + kw_len <= n:
            if sql[i:i + kw_len].upper() == keyword_upper:
                if (i == 0 or not sql[i - 1].isalnum()) and \
                   (i + kw_len >= n or not sql[i + kw_len].isalnum()):
                    return i
        i += 1
    return -1


cdef Py_ssize_t _find_first_kw_at_depth(str sql, tuple keywords, int target_depth):
    """Earliest occurrence of any keyword in ``keywords`` at ``target_depth``."""
    cdef Py_ssize_t best = -1
    cdef Py_ssize_t pos
    for kw in keywords:
        pos = _find_kw_at_depth(sql, kw, target_depth)
        if pos != -1 and (best == -1 or pos < best):
            best = pos
    return best


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
        # Rust fast path: delegate entire WHERE-building to Rust
        if HAS_RUST and self.filter:
            return _rs.filter_conditions(sql, dict(self.filter), dict(self.cond_definition))
        # --- Cython fallback ---
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
        # Rust fast path
        if HAS_RUST and self.grouping:
            return _rs.group_by(sql, list(self.grouping))
        # --- Cython fallback ---
        # TODO: adding GROUP BY GROUPING SETS OR ROLLUP
        cdef Py_ssize_t gb_pos
        cdef Py_ssize_t after_gb
        cdef Py_ssize_t end_pos
        cdef Py_ssize_t suffix_match
        if self.grouping:
            # Only consider the *outer* GROUP BY (depth 0). A GROUP BY nested
            # inside a CTE or subquery must not be modified — appending columns
            # to it would splice the inner GROUP BY into the outer FROM clause.
            gb_pos = _find_kw_at_depth(sql, "GROUP BY", 0)
            if gb_pos != -1:
                after_gb = gb_pos + 8  # len("GROUP BY")
                suffix_match = _find_first_kw_at_depth(
                    sql[after_gb:],
                    ("HAVING", "ORDER", "LIMIT", "WHERE"),
                    0,
                )
                end_pos = after_gb + suffix_match if suffix_match != -1 else len(sql)
                current_columns = [
                    col.strip() for col in sql[after_gb:end_pos].split(",")
                ]
                all_columns = current_columns + list(self.grouping)
                sql = (
                    sql[:gb_pos].rstrip()
                    + " GROUP BY "
                    + ", ".join(all_columns)
                    + " "
                    + sql[end_pos:].lstrip()
                ).strip()
            else:
                if isinstance(self.grouping, str):
                    sql = f"{sql} GROUP BY {self.grouping}"
                else:
                    group = ', '.join(self.grouping)
                    sql = f"{sql} GROUP BY {group}"
        return sql

    async def order_by(self, sql: str):
        # Rust fast path
        if HAS_RUST and self.ordering:
            if isinstance(self.ordering, list):
                return _rs.order_by(sql, self.ordering)
            else:
                return _rs.order_by(sql, [self.ordering])
        # --- Cython fallback ---
        _sql = "{sql} ORDER BY {order}"
        if isinstance(self.ordering, list) and len(self.ordering) > 0:
            order = ', '.join(self.ordering)
            sql = _sql.format_map(SafeDict(sql=sql, order=order))
        else:
            sql = _sql.format_map(SafeDict(sql=sql, order=self.ordering))
        return sql

    async def limiting(self, sql: str, limit: Union[str, int] = None, offset: Union[str, int] = None):
        # Rust fast path
        if HAS_RUST:
            return _rs.limiting(sql, str(limit) if limit else '', str(offset) if offset else '')
        # --- Cython fallback ---
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
        # Rust fast path
        if HAS_RUST and isinstance(self.fields, list) and len(self.fields) > 0:
            return _rs.process_fields(sql, self.fields, bool(self._add_fields), self.query_raw)
        # --- Cython fallback ---
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
