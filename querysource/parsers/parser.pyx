# cython: language_level=3, embedsignature=True, boundscheck=False, wraparound=True, initializedcheck=False
# Copyright (C) 2018-present Jesus Lara
#
"""
Query Parser.
"""
from abc import ABC, abstractmethod
import asyncio
from ..exceptions import EmptySentence
from ..models import QueryObject
from querysource.parsers.abstractparser import AbstractQueryParser


cdef class QueryParser(AbstractQueryParser):
    def __init__(self, str query, dict options, dict conditions):
        super().__init__(query, options, conditions)

    cpdef void set_conditions(self, dict conditions):
        self.conditions = QueryObject(conditions)

    cpdef void parse_query(self):
        loop = asyncio.get_event_loop()
        loop.run_until_complete(self._parse_query_async())

    async def _parse_query_async(self):
        # await asyncio.gather(
        #     self._parse_fields(),
        #     self._parse_filter(),
        #     self._parse_grouping(),
        #     self._parse_ordering()
        # )
        pass

    async def _parse_fields(self):
        self.fields = self.conditions.fields

    async def _parse_filter(self):
        self.filter = self.conditions.filter

    async def _parse_grouping(self):
        self.grouping = self.conditions.grouping

    async def _parse_ordering(self):
        self.ordering = self.conditions.ordering

    cpdef str build_query(self):
        self.query_parsed = f"SELECT {', '.join(self.fields)} FROM {self.query_raw} WHERE {self.filter} LIMIT {self.querylimit}"
        return self.query_parsed
