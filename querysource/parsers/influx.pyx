# cython: language_level=3, embedsignature=True, boundscheck=False, wraparound=False
# Copyright (C) 2018-present Jesus Lara
#
# file: influx.pyx
"""InfluxDB Flux Parser.

Build Flux queries for InfluxDB, validation and parsing.
"""
from datamodel.typedefs import NullDefault, SafeDict
from ..exceptions import EmptySentence
from ..types.validators import Entity
from .parser cimport QueryParser

# Try to import Rust extension for accelerated parsing
try:
    import qs_parsers as _rs
    HAS_RUST = True
except ImportError:
    HAS_RUST = False


cdef class InfluxParser(QueryParser):
    """InfluxDB Flux Parser."""

    def __init__(self, *args, **kwargs):
        self.bucket = None
        self.database = None
        super(InfluxParser, self).__init__(*args, **kwargs)
        if self.definition.source is not None:
            self.measurement = self.definition.source
        else:
            self.measurement = self.database

    async def process_fields(self, str query):
        """Build Flux |> keep(columns: [...]) pipe."""
        if HAS_RUST and isinstance(self.fields, list) and len(self.fields) > 0:
            try:
                return _rs.flux_process_fields(query, list(self.fields))
            except Exception:
                pass  # fall through to Cython
        return await self._process_fields_cy(query)

    async def _process_fields_cy(self, str query):
        """Cython fallback for process_fields."""
        cdef str columns
        cdef str keep
        cdef list fields

        if isinstance(self.fields, list) and len(self.fields) > 0:
            if '_measurement' not in self.fields:
                self.fields.append('_measurement')
            fields = [Entity.dblQuoting(k) for k in self.fields]
            columns = ','.join(fields)
            keep = f"|> keep(columns: [{columns}])"
            query = f"{query} {keep}"
        return query

    async def filter_conditions(self, str query):
        """Build Flux |> filter(...) pipes."""
        if HAS_RUST and self.filter and isinstance(self.filter, dict):
            try:
                return _rs.flux_filter_conditions(query, self.filter)
            except Exception:
                pass  # fall through to Cython
        return await self._filter_conditions_cy(query)

    async def _filter_conditions_cy(self, str query):
        """Cython fallback for filter_conditions."""
        cdef str key
        cdef str val
        cdef str _where
        cdef list where_cond

        if self.filter:
            where_cond = []
            for key, value in self.filter.items():
                if isinstance(value, (str, int)):
                    val = str(value).replace("'", '')
                    where_cond.append(
                        f'|> filter(fn: (r) => r["{key}"] == "{val}")'
                    )
            _where = "".join(where_cond)
            query = f"{query} {_where}"
        return query

    async def build_query(self, querylimit: int = None, offset: int = None):
        """Build Flux Query."""
        cdef str query = self.query_raw
        self.logger.debug(f":: RAW QUERY: {query}")
        self.logger.debug(f"FIELDS ARE {self.fields}")
        self.logger.debug(f"Conditions ARE: {self.conditions}")
        query = await self.process_fields(query)
        # basic filtering:
        query = await self.filter_conditions(query)
        # removing other places:
        if self.conditions and len(self.conditions) > 0:
            query = query.format_map(SafeDict(**self.conditions))
        ## replacing bucket and measurement:
        cdef dict args = {
            "measurement": self.measurement
        }
        query = query.format_map(SafeDict(**args))
        # at the end, default null setters
        query = query.format_map(NullDefault())
        ## adding "Sort" at the end:
        query = f"{query} |> sort()"
        self.query_parsed = query
        self.logger.debug(f": QUERY :: {query}")
        if self.query_parsed == '' or self.query_parsed is None:
            raise EmptySentence(
                'QS Influx: no query to parse.'
            )
        return self.query_parsed
