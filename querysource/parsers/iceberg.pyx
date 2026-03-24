# cython: language_level=3, embedsignature=True, boundscheck=False, wraparound=False
# Copyright (C) 2018-present Jesus Lara
#
# file: iceberg.pyx
"""
Apache Iceberg SQL Parser.

Build SQL queries for Apache Iceberg tables using DuckDB SQL syntax.
Iceberg tables are queried via asyncdb's iceberg driver which uses DuckDB under the hood.
"""
from datamodel.typedefs import NullDefault, SafeDict
from ..exceptions import EmptySentence
from .sql cimport SQLParser


cdef class IcebergParser(SQLParser):
    """Apache Iceberg SQL Parser.

    Extends SQLParser to handle Iceberg-specific query options like
    table_id (namespace.table), namespace, and factory format.
    Queries are standard SQL executed via DuckDB on Iceberg tables.
    """

    def __init__(self, *args, **kwargs):
        self.table_id = kwargs.pop('table_id', None)
        self.namespace = kwargs.pop('namespace', None)
        self._factory = kwargs.pop('factory', 'pandas')
        super(IcebergParser, self).__init__(*args, **kwargs)
        # Override base SQL template for Iceberg (uses iceberg_table alias)
        self._base_sql = 'SELECT {fields} FROM iceberg_table {filter} {grouping} {offset} {limit}'

    async def build_query(self, querylimit: int = None, offset: int = None):
        """Build SQL query for Iceberg.

        Iceberg queries use standard SQL syntax. The asyncdb iceberg driver
        handles the DuckDB integration and table resolution via table_id.

        Args:
            querylimit: Maximum number of rows to return.
            offset: Number of rows to skip.

        Returns:
            str: The parsed SQL query string.

        Raises:
            EmptySentence: When no query can be built.
        """
        cdef str sql = self.query_raw

        # Resolve table_id from conditions if not set
        if not self.table_id:
            if self.conditions:
                self.table_id = self.conditions.pop('table_id', None)
            if not self.table_id and self.definition:
                try:
                    self.table_id = self.definition.source
                except AttributeError:
                    pass

        # Resolve namespace
        if not self.namespace:
            if self.conditions:
                self.namespace = self.conditions.pop('namespace', None)

        # Resolve factory
        if self.conditions:
            self._factory = self.conditions.pop('factory', self._factory)

        # Replace table_id and namespace placeholders
        if '{table_id}' in sql:
            sql = sql.format_map(SafeDict(table_id=self.table_id or ''))
        if '{namespace}' in sql:
            sql = sql.format_map(SafeDict(namespace=self.namespace or ''))

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
        self.logger.debug(f": Iceberg SQL :: {sql}")
        if not self.query_parsed:
            raise EmptySentence(
                'QS Iceberg: no SQL query to parse.'
            )
        return self.query_parsed
