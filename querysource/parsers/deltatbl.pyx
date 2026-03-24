# cython: language_level=3, embedsignature=True, boundscheck=False, wraparound=False
# Copyright (C) 2018-present Jesus Lara
#
# file: deltatbl.pyx
"""
DeltaTable SQL Parser.

Build SQL queries for DeltaTable sources using DuckDB SQL syntax.
DeltaTable data is queried via asyncdb's delta driver which uses DuckDB under the hood.
"""
from datamodel.typedefs import NullDefault, SafeDict
from ..exceptions import EmptySentence
from .sql cimport SQLParser


cdef class DeltaTableParser(SQLParser):
    """DeltaTable SQL Parser.

    Extends SQLParser to handle DeltaTable-specific query options like
    delta_path, delta_tablename (alias for DuckDB), factory format, and write mode.
    Queries are standard SQL executed via DuckDB on DeltaTable data.
    """

    def __init__(self, *args, **kwargs):
        self.delta_path = kwargs.pop('delta_path', None)
        self.delta_tablename = kwargs.pop('delta_tablename', None)
        self._factory = kwargs.pop('factory', 'pandas')
        self._mode = kwargs.pop('mode', 'append')
        super(DeltaTableParser, self).__init__(*args, **kwargs)
        # Default SQL template uses the delta tablename alias
        self._base_sql = 'SELECT {fields} FROM {delta_tablename} {filter} {grouping} {offset} {limit}'

    async def build_query(self, querylimit: int = None, offset: int = None):
        """Build SQL query for DeltaTable.

        DeltaTable queries use standard SQL syntax. The asyncdb delta driver
        handles the DuckDB integration and table resolution via tablename alias.

        Args:
            querylimit: Maximum number of rows to return.
            offset: Number of rows to skip.

        Returns:
            str: The parsed SQL query string.

        Raises:
            EmptySentence: When no query can be built.
        """
        cdef str sql = self.query_raw

        # Resolve delta-specific options from conditions
        if not self.delta_path:
            if self.conditions:
                self.delta_path = self.conditions.pop('delta_path', None)
            if not self.delta_path and self.definition:
                try:
                    self.delta_path = self.definition.source
                except AttributeError:
                    pass

        if not self.delta_tablename:
            if self.conditions:
                self.delta_tablename = self.conditions.pop('delta_tablename', None)
                if not self.delta_tablename:
                    self.delta_tablename = self.conditions.pop('tablename', None)

        # Resolve factory and mode
        if self.conditions:
            self._factory = self.conditions.pop('factory', self._factory)
            self._mode = self.conditions.pop('mode', self._mode)

        # Replace delta-specific placeholders
        if '{delta_tablename}' in sql:
            sql = sql.format_map(
                SafeDict(delta_tablename=self.delta_tablename or 'arrow_dataset')
            )
        if '{delta_path}' in sql:
            sql = sql.format_map(SafeDict(delta_path=self.delta_path or ''))

        # Check schema/table names
        if '{schema}' in sql:
            sql = sql.format_map(SafeDict(schema=self.schema, table=self.tablename))
        elif '{table}' in sql:
            sql = sql.format_map(SafeDict(table=self.tablename))

        # Process fields
        sql = await self.process_fields(sql)

        # Add filtering conditions
        sql = self.filtering_options(sql)
        sql = await self.filter_conditions(sql)

        # GROUP BY / ORDER BY
        sql = await self.group_by(sql)
        if self.ordering:
            sql = await self.order_by(sql)

        # LIMIT / OFFSET
        if querylimit:
            sql = await self.limiting(sql, querylimit, offset)
        elif self.querylimit:
            sql = await self.limiting(sql, self.querylimit, self._offset)
        else:
            sql = await self.limiting(sql, '')

        # Apply remaining conditions as substitutions
        if isinstance(self._conditions, dict):
            try:
                sql = sql.format_map(SafeDict(**self._conditions))
                sql = sql.format_map(NullDefault())
            except ValueError:
                pass

        self.query_parsed = sql
        self.logger.debug(f": DeltaTable SQL :: {sql}")
        if not self.query_parsed:
            raise EmptySentence(
                'QS DeltaTable: no SQL query to parse.'
            )
        return self.query_parsed
