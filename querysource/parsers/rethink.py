import iso8601
from rethinkdb.errors import (
    ReqlDriverError,
    ReqlRuntimeError,
    # ReqlNonExistenceError
)
from ..exceptions import (
    ParserError,
    EmptySentence
)
from .abstract import AbstractParser


class RethinkParser(AbstractParser):
    def __init__(
        self,
        *args,
        **kwargs
    ):
        self._distinct: bool = kwargs.pop('distinct', False)
        self._create_indexes: bool = kwargs.pop('create_indexes', False)
        super(RethinkParser, self).__init__(
            *args,
            **kwargs
        )
        self._join_field = None
        self._engine = None
        self._connection = None
        # avoid quoting strings on RethinkDb
        self.string_literal: bool = True

    def set_connection(self, connection):
        self._connection = connection
        self._engine = self._connection.engine()

    async def filtering_conditions(self, query):
        conditions = {}
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
                fields = []
                for field in self.fields:
                    name = ''
                    alias = ''
                    if ' as ' in field:
                        el = field.split(' as ')
                        name = el[0]
                        fields.append(name)
                        alias = el[1].replace('"', '')
                        self._map[alias] = self._engine.row[name]
                    else:
                        fields.append(field)
                self.fields = fields
                self.logger.debug(
                    f"RT FIELDS {self.fields}"
                )
                self._has_fields = self.fields.copy()
                self.logger.debug(
                    f"RT MAP IS {self._map}"
                )
        except Exception as err:  # pylint: disable=W0703
            self.logger.exception(err, stack_info=True)
        try:
            keys = list(conditions.keys())
            self._has_fields = self._has_fields + keys
        except (KeyError, ValueError):
            pass
        self.filter = conditions
        return query

    def get_datefilter(self, query, conditions, field, dtype: str = 'timestamp'):
        try:
            fdate = conditions[field]
            if isinstance(conditions[field], list):
                fdate = conditions[field]
                tz = self._engine.make_timezone('00:00')
                d1 = iso8601.parse_date(fdate[0], default_timezone=tz)
                d2 = iso8601.parse_date(fdate[1], default_timezone=tz)
                if dtype == 'epoch':
                    # The field in the DB is an integer with UNIX epoch seconds,
                    # so do a numeric comparison.
                    dstart = int(d1.strftime('%s'))
                    dend = int(d2.strftime('%s'))
                    query = query.filter(
                        (self._engine.row[field] >= dstart) & (self._engine.row[field] <= dend)
                    )
                elif dtype == 'timestamp':
                    dstart = self._engine.iso8601(
                        d1.strftime('%Y-%m-%dT%H:%M:%S.%f%z')
                    )
                    dend = self._engine.iso8601(
                        d2.strftime('%Y-%m-%dT%H:%M:%S.%f%z')
                    )
                    query = query.filter(
                        self._engine.row[field].during(
                            dstart, dend, left_bound="closed", right_bound="closed"
                        )
                    )
                del self.filter[field]
        except (KeyError, ValueError) as err:
            self.logger.warning(
                f'RethinkDB DateFilter ERROR: field: {field} error: {err}'
            )
        finally:
            return query  # pylint: disable=W0150

    async def between(self, query):
        conditions = self.filter.copy()
        for field in conditions:
            if field in self.cond_definition:
                if self.cond_definition[field] in ('date', 'timestamp', 'datetime'):
                    query = self.get_datefilter(query, conditions, field)
                elif self.cond_definition[field] == 'epoch':
                    query = self.get_datefilter(query, conditions, field, dtype='epoch')
            elif field in ['date', 'filterdate', 'inserted_at']:
                query = self.get_datefilter(query, conditions, field)
            else:
                # assuming are epoch fields integers:
                print('FIELD > ', field)
        return query

    async def orderby(self, query):
        # ordering
        order = None
        if self.ordering:
            if isinstance(self.ordering, list):
                orderby = self.ordering[0].split(' ')
            else:
                orderby = self.ordering.split(' ')
            order = self._engine.desc(orderby[0]) if orderby[1] == 'DESC' else orderby[0]
            # add ordering
            query = query.order_by(order)
        return query

    def distinct(self, query):
        return query.distinct()

    async def query_filter(self, query, conn, indexing: bool = False):
        idx = None
        table = None
        if indexing:
            table = self._engine.table(self.table)
            idx = await table.index_list().run(conn)
        # please, first, check for indexing:
        scalar_fields = {}
        exp = None
        _filter = {}
        for key, value in self.filter.items():
            #  TODO: add field_definition to know escape characters or other conditions
            # check if an index exists, else, create:
            if indexing and key not in idx:
                await table.index_create(key).run(conn)
                table.index_wait(key).run(conn)
            # run first, the array-based queries:
            if isinstance(value, list):
                query = query.filter(
                    (lambda doc: self._engine.expr(value).coerce_to('array').contains(doc[key]))
                )
            elif isinstance(value, dict):
                for k, val in value.items():
                    if k == 'contains':
                        query = query.filter(
                            lambda doc: self._engine.expr(val).coerce_to('array').contains(doc[key])
                        )
                    elif k == 'match':
                        query = query.filter(lambda doc: doc[key].match(val))
            else:
                if key in self.cond_definition:
                    _type = self.cond_definition[key]
                    if _type == 'date':
                        if isinstance(val, str):
                            # I need to convert to date the string
                            tz = self._engine.make_timezone('00:00')
                            dt = iso8601.parse_date(value, default_timezone=tz)
                            dval = self._engine.iso8601(dt.strftime('%Y-%m-%dT%H:%M:%S.%f%z'))
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

    async def has_fields(self, query):
        try:
            # I have the fields that i need:
            if self._has_fields:
                query = query.has_fields(self._has_fields)
        finally:
            return query

    async def field_options(self, query):
        try:
            # pluck fields:
            if self.fields:
                if self._map:
                    query = query.pluck(self.fields).map(self._map)
                else:
                    query = query.pluck(self.fields)
        finally:
            return query

    def inner_join(self, query, join):
        try:
            # return query.inner_join(join, lambda doc1, doc2: doc1[self._join_field] == doc2[self._join_field]).zip()
            query = query.eq_join(self._join_field, join, index=self._join_field)
            if query:
                query = query.zip()
        except Exception as err:
            self.logger.exception(err, stack_info=True)
        finally:
            return query

    async def group_by(self, query):
        try:
            if self.grouping and isinstance(self.grouping, list):
                query = query.group(*self.grouping).distinct()
        except Exception as err:
            self.logger.exception(err, stack_info=True)
        finally:
            return query

    async def columns(self):
        if self.database:
            self._connection.use(self.database)
        conn = self._connection.get_connection()
        self._columns = await self._engine.table(self.table).nth(0).default(None).keys().run(conn)
        return self._columns

    async def build_query(self, connection: callable, run=True, querylimit: int = None):
        '''
        Build a RethinkDB Query.
        '''
        self.logger.debug(
            f"RT FIELDS ARE {self.fields}"
        )
        # set Engine:
        conn = connection.raw_connection
        if self.database:
            await connection.use(self.database)
        # most basic query
        eq_table = None
        try:
            if isinstance(self.table, list):
                # I need to optimize by creating index on pivot field
                # big TODO: need to wait until index will ready to use
                search = self._engine.table(self.table[0])
                eq_table = self._engine.table(self.table[1])
                search = self.inner_join(search, eq_table)
                self.table = self.table[0]
            else:
                search = self._engine.table(self.table)
            if not search:
                raise EmptySentence(
                    "Missing RethinkDB Query"
                )
            # query filter:
            search = await self.filtering_conditions(search)
            # has fields is the first option
            search = await self.has_fields(search)
            # during - between
            search = await self.between(search)
            # filter:
            search = await self.query_filter(search, conn, self._create_indexes)
            # field options
            search = await self.field_options(search)
            # Group By
            search = await self.group_by(search)
            # ordering
            search = await self.orderby(search)
            # adding distinct
            if self._distinct:
                search = self.distinct(search)
            if self._offset:
                search = search.nth(self._offset).default(None)
            if querylimit is not None:
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
            # to add more complex queries to Rethink Engine Search Object
            return search
        try:
            return await self.result_from_cursor(search, conn)
        except ReqlDriverError:
            # connection was closed, we need to reconnect:
            try:
                await conn.reconnect(noreply_wait=False)
                return await self.result_from_cursor(search, conn)
            except Exception as err:
                raise ParserError(
                    'RethinkDB exception: impossible to reach a reconnection: {err}'
                ) from err
        except Exception as err:
            self.logger.exception(err, stack_info=True)
            raise ParserError(
                'RethinkDB exception: impossible to reach a reconnection: {err}'
            ) from err

    async def result_from_cursor(self, search, conn):
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
