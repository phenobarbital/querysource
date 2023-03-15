import asyncio
from functools import partial

from querysource.exceptions import EmptySentence
from querysource.types.typedefs import NullDefault, SafeDict

from .sql import SQLParser


class CQLParser(SQLParser):
    _tablename = '{schema}.{table}'

    def set_cql(self, cql: str):
        self.query_raw = cql
        return self

    def where_cond(self, where: dict):
        self.filter = where
        return self

    async def build_query(self, querylimit: int = None, offset: int = None):
        """
        build_query.
         Last Step: Build a SQL Query
        """
        cql = self.query_raw
        self.logger.debug(f":: RAW CQL: {cql}")
        # self.logger.debug(f"FIELDS ARE {self.fields}")
        # self.logger.debug(f'Conditions ARE: {self.filter}')
        cql = await self.process_fields(cql)
        # add query options
        ## TODO: Function FILTERS (called in threads)
        for _, func in self._query_filters.items():
            fn, args = func
            func = partial(fn, args, where=self.filter, program=self.program_slug)
            result, ordering = await asyncio.get_event_loop().run_in_executor(
                self._executor, func
            )
            self.filter = {**self.filter, **result}
            if ordering:
                self.ordering = self.ordering + ordering
        # add filtering conditions
        cql = await self.filtering_options(cql)
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
