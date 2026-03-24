# cython: language_level=3, embedsignature=True, boundscheck=False, wraparound=False
# Copyright (C) 2018-present Jesus Lara
#
# file: rethink.pyx
"""RethinkDB Parser for QuerySource."""
import iso8601
from rethinkdb.errors import (
    ReqlDriverError,
    ReqlRuntimeError,
)
from ..exceptions import EmptySentence
from querysource.exceptions import ParserError
from .abstract cimport AbstractParser

# Try to import Rust extension for accelerated parsing
try:
    import qs_parsers as _rs
    HAS_RUST = True
except ImportError:
    HAS_RUST = False


cdef class RethinkParser(AbstractParser):
    """RethinkDB Parser.

    Translates query configuration into RethinkDB query builder calls.
    """
    def __cinit__(self, *args, **kwargs):
        """Initialize critical attributes in __cinit__."""
        self._create_indexes = kwargs.pop('create_indexes', False)
        self._join_field = None
        self._engine = None
        self._connection = None
        self._map = {}
        self._has_fields = []
        # avoid quoting strings on RethinkDb
        self.string_literal = True

    def __init__(self, *args, **kwargs):
        self._distinct = kwargs.pop('distinct', False)
        super(RethinkParser, self).__init__(*args, **kwargs)

    def set_connection(self, connection):
        """Set the RethinkDB connection and engine."""
        self._connection = connection
        self._engine = self._connection.engine()

    # ----- filtering_conditions -----

    async def filtering_conditions(self, query):
        """Process field aliases and has_fields from fields list."""
        cdef dict conditions = {}
        cdef str field
        cdef str name
        cdef str alias
        cdef list fields
        cdef list el

        if self._conditions or self.filter:
            if self._conditions:
                conditions = {**self._conditions}
            if self.filter:
                conditions = {**conditions, **self.filter}
            self.logger.debug(
                f"RT CONDITIONS {conditions}"
            )

        self._map = {}
        self._has_fields = []

        try:
            if self.fields:
                # Try Rust fast-path for field aliasing
                if HAS_RUST and isinstance(self.fields, list) and len(self.fields) > 0:
                    try:
                        clean_fields, alias_map = _rs.rethink_process_fields(
                            list(self.fields)
                        )
                        # Apply alias map to RethinkDB row references
                        for alias, name in alias_map.items():
                            self._map[alias] = self._engine.row[name]
                        self.fields = list(clean_fields)
                    except Exception:
                        # Fall through to Cython path
                        self._process_fields_cy()
                else:
                    self._process_fields_cy()

                self.logger.debug(
                    f"RT FIELDS {self.fields}"
                )
                self._has_fields = list(self.fields)
                self.logger.debug(
                    f"RT MAP IS {self._map}"
                )
        except Exception as err:
            self.logger.exception(err, stack_info=True)

        try:
            keys = list(conditions.keys())
            self._has_fields = self._has_fields + keys
        except (KeyError, ValueError):
            pass

        self.filter = conditions
        return query

    cdef object _process_fields_cy(self):
        """Cython fallback for field processing."""
        cdef list fields = []
        cdef str field
        cdef str name
        cdef str alias
        cdef list el

        for field in self.fields:
            if ' as ' in field:
                el = field.split(' as ')
                name = el[0]
                fields.append(name)
                alias = el[1].replace('"', '')
                self._map[alias] = self._engine.row[name]
            else:
                fields.append(field)
        self.fields = fields

    # ----- get_datefilter -----

    def get_datefilter(self, query, dict conditions, str field, str dtype='timestamp'):
        """Apply date-based filtering to a RethinkDB query."""
        cdef object fdate
        cdef object tz
        cdef object d1
        cdef object d2
        cdef int dstart
        cdef int dend

        try:
            fdate = conditions[field]
            if isinstance(conditions[field], list):
                fdate = conditions[field]
                tz = self._engine.make_timezone('00:00')
                d1 = iso8601.parse_date(fdate[0], default_timezone=tz)
                d2 = iso8601.parse_date(fdate[1], default_timezone=tz)
                if dtype == 'epoch':
                    dstart = int(d1.strftime('%s'))
                    dend = int(d2.strftime('%s'))
                    query = query.filter(
                        (self._engine.row[field] >= dstart)
                        & (self._engine.row[field] <= dend)
                    )
                elif dtype == 'timestamp':
                    dstart_ts = self._engine.iso8601(
                        d1.strftime('%Y-%m-%dT%H:%M:%S.%f%z')
                    )
                    dend_ts = self._engine.iso8601(
                        d2.strftime('%Y-%m-%dT%H:%M:%S.%f%z')
                    )
                    query = query.filter(
                        self._engine.row[field].during(
                            dstart_ts, dend_ts,
                            left_bound="closed",
                            right_bound="closed"
                        )
                    )
                del self.filter[field]
        except (KeyError, ValueError) as err:
            self.logger.warning(
                f'RethinkDB DateFilter ERROR: field: {field} error: {err}'
            )
        finally:
            return query

    # ----- between -----

    async def between(self, query):
        """Apply date-range filters using condition definitions."""
        cdef dict conditions
        cdef str field
        cdef str cond_type

        conditions = self.filter.copy()

        # Use Rust classification if available
        if HAS_RUST and conditions:
            try:
                classifications = _rs.rethink_classify_conditions(
                    conditions,
                    self.cond_definition if self.cond_definition else {}
                )
                for field in conditions:
                    kind = classifications.get(field, 'scalar')
                    if kind == 'date':
                        query = self.get_datefilter(query, conditions, field)
                    elif kind == 'epoch':
                        query = self.get_datefilter(
                            query, conditions, field, dtype='epoch'
                        )
                    elif field in ('date', 'filterdate', 'inserted_at'):
                        query = self.get_datefilter(query, conditions, field)
                return query
            except Exception:
                pass  # fall through to Cython path

        for field in conditions:
            if field in self.cond_definition:
                cond_type = self.cond_definition[field]
                if cond_type in ('date', 'timestamp', 'datetime'):
                    query = self.get_datefilter(query, conditions, field)
                elif cond_type == 'epoch':
                    query = self.get_datefilter(
                        query, conditions, field, dtype='epoch'
                    )
            elif field in ('date', 'filterdate', 'inserted_at'):
                query = self.get_datefilter(query, conditions, field)
        return query

    # ----- orderby -----

    async def orderby(self, query):
        """Apply ordering to a RethinkDB query."""
        if not self.ordering:
            return query

        # Try Rust fast-path for ordering
        if HAS_RUST:
            try:
                ordering_list = self._prepare_ordering_list()
                if ordering_list:
                    parsed = _rs.rethink_process_ordering(ordering_list)
                    if parsed:
                        field_name, direction = parsed[0]
                        if direction == 'DESC':
                            order = self._engine.desc(field_name)
                        else:
                            order = field_name
                        query = query.order_by(order)
                        return query
            except Exception:
                pass  # fall through to Cython path

        return self._process_ordering_cy(query)

    cdef object _prepare_ordering_list(self):
        """Prepare ordering as a list of strings."""
        if isinstance(self.ordering, list):
            return list(self.ordering)
        elif isinstance(self.ordering, str):
            return [self.ordering]
        return []

    cdef object _process_ordering_cy(self, query):
        """Cython fallback for ordering."""
        cdef list orderby
        cdef object order

        if isinstance(self.ordering, list):
            orderby = self.ordering[0].split(' ')
        else:
            orderby = self.ordering.split(' ')

        if len(orderby) >= 2 and orderby[1] == 'DESC':
            order = self._engine.desc(orderby[0])
        else:
            order = orderby[0]
        query = query.order_by(order)
        return query

    # ----- distinct -----

    def distinct(self, query):
        """Apply distinct to a query."""
        return query.distinct()

    # ----- query_filter -----

    async def query_filter(self, query, conn, bint indexing=False):
        """Apply filter conditions to a RethinkDB query."""
        cdef object idx = None
        cdef object table = None
        cdef dict scalar_fields
        cdef object exp = None
        cdef dict _filter = {}
        cdef str key
        cdef str k

        if indexing:
            table = self._engine.table(self.tablename)
            idx = await table.index_list().run(conn)

        for key, value in self.filter.items():
            if indexing and key not in idx:
                await table.index_create(key).run(conn)
                table.index_wait(key).run(conn)

            if isinstance(value, list):
                query = query.filter(
                    (lambda doc: self._engine.expr(value).coerce_to(
                        'array'
                    ).contains(doc[key]))
                )
            elif isinstance(value, dict):
                for k, val in value.items():
                    if k == 'contains':
                        query = query.filter(
                            lambda doc: self._engine.expr(val).coerce_to(
                                'array'
                            ).contains(doc[key])
                        )
                    elif k == 'match':
                        query = query.filter(
                            lambda doc: doc[key].match(val)
                        )
            else:
                if key in self.cond_definition:
                    _type = self.cond_definition[key]
                    if _type == 'date':
                        if isinstance(value, str):
                            tz = self._engine.make_timezone('00:00')
                            dt = iso8601.parse_date(
                                value, default_timezone=tz
                            )
                            dval = self._engine.iso8601(
                                dt.strftime('%Y-%m-%dT%H:%M:%S.%f%z')
                            )
                            _filter[key] = dval
                        else:
                            _filter[key] = self._engine.iso8601(value)
                    elif _type == 'epoch':
                        _filter[key] = self._engine.epoch_time(value)
                    else:
                        _filter[key] = value
                else:
                    _filter[key] = value

        if _filter:
            query = query.filter(_filter)
        return query

    # ----- has_fields -----

    async def has_fields(self, query):
        """Filter documents that have the required fields."""
        try:
            if self._has_fields:
                query = query.has_fields(self._has_fields)
        finally:
            return query

    # ----- field_options -----

    async def field_options(self, query):
        """Apply field plucking and mapping."""
        try:
            if self.fields:
                if self._map:
                    query = query.pluck(self.fields).map(self._map)
                else:
                    query = query.pluck(self.fields)
        finally:
            return query

    # ----- inner_join -----

    def inner_join(self, query, join):
        """Perform an eq_join on two tables."""
        try:
            query = query.eq_join(
                self._join_field, join, index=self._join_field
            )
            if query:
                query = query.zip()
        except Exception as err:
            self.logger.exception(err, stack_info=True)
        finally:
            return query

    # ----- group_by -----

    async def group_by(self, query):
        """Apply grouping to a query."""
        try:
            if self.grouping and isinstance(self.grouping, list):
                query = query.group(*self.grouping).distinct()
        except Exception as err:
            self.logger.exception(err, stack_info=True)
        finally:
            return query

    # ----- columns -----

    async def columns(self):
        """Retrieve column names from the first document."""
        if self.database:
            self._connection.use(self.database)
        conn = self._connection.get_connection()
        self._columns = await self._engine.table(
            self.tablename
        ).nth(0).default(None).keys().run(conn)
        return self._columns

    # ----- build_query -----

    async def build_query(self, connection: callable, bint run=True, int querylimit=0):
        """Build a RethinkDB Query."""
        cdef object eq_table = None
        cdef object search

        self.logger.debug(
            f"RT FIELDS ARE {self.fields}"
        )
        conn = connection.raw_connection
        if self.database:
            await connection.use(self.database)

        try:
            if isinstance(self.tablename, list):
                search = self._engine.table(self.tablename[0])
                eq_table = self._engine.table(self.tablename[1])
                search = self.inner_join(search, eq_table)
                self.tablename = self.tablename[0]
            else:
                search = self._engine.table(self.tablename)

            if not search:
                raise EmptySentence(
                    "Missing RethinkDB Query"
                )

            # query filter pipeline:
            search = await self.filtering_conditions(search)
            search = await self.has_fields(search)
            search = await self.between(search)
            search = await self.query_filter(
                search, conn, self._create_indexes
            )
            search = await self.field_options(search)
            search = await self.group_by(search)
            search = await self.orderby(search)

            if self._distinct:
                search = self.distinct(search)
            if self._offset:
                search = search.nth(self._offset).default(None)
            if querylimit > 0:
                search = search.limit(querylimit)
            elif self._limit:
                search = search.limit(self._limit)
        except Exception as err:
            self.logger.exception(err, stack_info=True)

        try:
            self.logger.debug('SEARCH IS: = ')
            self.logger.debug(search)
        except RuntimeError as err:
            self.logger.exception(err, stack_info=True)

        if run is not True:
            return search

        try:
            return await self.result_from_cursor(search, conn)
        except ReqlDriverError:
            try:
                await conn.reconnect(noreply_wait=False)
                return await self.result_from_cursor(search, conn)
            except Exception as err:
                raise ParserError(
                    f'RethinkDB exception: impossible to reach a reconnection: {err}'
                ) from err
        except Exception as err:
            self.logger.exception(err, stack_info=True)
            raise ParserError(
                f'RethinkDB exception: impossible to reach a reconnection: {err}'
            ) from err

    # ----- result_from_cursor -----

    async def result_from_cursor(self, search, conn):
        """Execute the query and collect results from cursor."""
        cdef list result
        try:
            cursor = await search.run(conn)
            if isinstance(cursor, list):
                return cursor
            result = []
            while (await cursor.fetch_next()):
                row = await cursor.next()
                result.append(row)
            return result
        except ReqlDriverError:
            raise
        except Exception as err:
            raise ParserError(
                f"Error parsing Data using RethinkDB: {err}"
            ) from err
