from collections.abc import Callable
import iso8601
from rethinkdb.errors import (
    ReqlDriverError,
    ReqlRuntimeError,
    # ReqlNonExistenceError
)
from querysource.models import QueryObject
from querysource.providers import BaseProvider
from querysource.exceptions import (
    ParserError,
    EmptySentence
)
from .abstract import QueryParser


class RethinkParser(QueryParser):
    def __init__(
        self,
        query: str = None,
        options: BaseProvider = None,
        connection: Callable = None,
        conditions: QueryObject = None,
        **kwargs
    ):
        super(RethinkParser, self).__init__(
            query=query,
            options=options,
            conditions=conditions,
            connection=connection,
            **kwargs
        )
        self._join_field = None
        self._engine = self._connection.engine()

    async def filtering_conditions(self, query):
        conditions = {}
        if self.conditions or self.filter:
            if self.conditions:
                conditions = {** self.conditions }
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
        except Exception as err: # pylint: disable=W0703
            self.logger.exception(err, stack_info=True)

        try:
            keys = list(conditions.keys())
            self._has_fields = self._has_fields + keys
        except (KeyError, ValueError):
            pass
        self.conditions = conditions
        return query

    def get_datefilter(self, query, conditions, field, dtype: str = 'timestamp'):
        try:
            fdate = conditions[field]
            if isinstance(conditions[field], list):
                fdate = conditions[field]
                tz = self._engine.make_timezone('00:00')
                d1 = iso8601.parse_date(fdate[0], default_timezone=tz)
                d2 = iso8601.parse_date(fdate[1], default_timezone=tz)
                if dtype == 'timestamp':
                    dstart = self._engine.iso8601(
                        d1.strftime('%Y-%m-%dT%H:%M:%S.%f%z')
                    )
                    dend = self._engine.iso8601(
                        d2.strftime('%Y-%m-%dT%H:%M:%S.%f%z')
                    )
                elif dtype == 'epoch':
                    dstart = self._engine.epoch_time(
                        int(d1.strftime('%s'))
                    )
                    dend = self._engine.epoch_time(
                        int(d2.strftime('%s'))
                    )
                query = query.filter(
                    self._engine.row[field].during(dstart, dend, left_bound="closed", right_bound="closed")
                )
                del self.conditions[field]
        except (KeyError, ValueError) as err:
            self.logger.warning(
                f'RethinkDB DateFilter ERROR: field: {field} error: {err}'
            )
        finally:
            return query # pylint: disable=W0150

    async def between(self, query):
        conditions = self.conditions.copy()
        for field in conditions:
            if field in self.cond_definition:
                if self.cond_definition[field] in ('date', 'timestamp', 'datetime'):
                    query = self.get_datefilter(query, conditions, field)
                elif self.cond_definition[field] == 'epoch':
                    query = self.get_datefilter(query, conditions, field, dtype = 'epoch')
            elif field == 'date' or field == 'filterdate' or field == 'inserted_at':
                query = self.get_datefilter(query, conditions, field)
        #self.conditions = conditions
        return query


    async def orderby(self, query):
        # ordering
        order = None
        if self.ordering:
            if isinstance(self.ordering, list):
                orderby = self.ordering[0].split(' ')
            else:
                orderby = self.ordering.split(' ')
            if orderby[1] == 'DESC':
                order = self._engine.desc(orderby[0])
            else:
                order = orderby[0]
            # add ordering
            query = query.order_by(order)
        return query

    def distinct(self, query):
        return query.distinct()

    # async def query_options(self, result):
    #     '''
    #     Query Options
    #     '''
    #     self.logger.debug("PROGRAM FOR QUERY IS {} for option {}".format(self._program, self.qry_options))
    #     if self.qry_options:
    #         #TODO: we need an straightforward manner to get the hierarchy
    #         hierarchy = get_hierarchy(self._program)
    #         if hierarchy:
    #             try:
    #                 get_filter = [ k.replace('!', '') for k in self.conditions if k.replace('!', '') in hierarchy]
    #                 filter_sorted = sorted(get_filter, key=hierarchy.index)
    #             except (TypeError, ValueError, KeyError):
    #                 return result
    #             ## processing different types of query option
    #             try:
    #                 get_index = hierarchy.index(filter_sorted.pop())
    #                 selected = hierarchy[get_index + 1:]
    #             except (KeyError, IndexError):
    #                 selected = []
    #             try:
    #                 if self.qry_options['null_rolldown'] == 'true':
    #                     if selected:
    #                         for n in selected:
    #                             result = result.and_(self._engine.row[n].eq(None))
    #                     else:
    #                         if get_filter:
    #                             last = get_filter.pop(0)
    #                             if last != hierarchy[-1]:
    #                                 result = result.and_(self._engine.row[last].ne(None))
    #                             else:
    #                                 first = hierarchy.pop(0)
    #                                 #_where[first] = 'null'
    #                                 result = result.and_(self._engine.row[first].eq(None))
    #                         else:
    #                             last = hierarchy.pop(0)
    #                             result = result.and_(self._engine.row[last].eq(None))
    #             except (KeyError, ValueError):
    #                 pass
    #             try:
    #                 if self.qry_options['select_child'] == 'true':
    #                     try:
    #                         child = selected.pop(0)
    #                         result = result.and_(self._engine.row[child].ne(None))
    #                         #_where[child] = '!null'
    #                         for n in selected:
    #                             result = result.and_(self._engine.row[n].eq(None))
    #                         return result
    #                     except (ValueError, IndexError):
    #                         if get_filter:
    #                             pass
    #                         else:
    #                             child = hierarchy.pop(0)
    #                             result = result.and_(self._engine.row[child].ne(None))
    #                             #_where[child] = '!null'
    #                             for n in hierarchy:
    #                                 #_where[n] = 'null'
    #                                 result = result.and_(self._engine.row[n].eq(None))
    #             except (KeyError, ValueError):
    #                 pass
    #             try:
    #                 if self.qry_options['select_stores'] == 'true':
    #                     try:
    #                         last = selected.pop()
    #                         result = result.and_(self._engine.row[last].ne(None))
    #                         return result
    #                     except (ValueError, IndexError):
    #                         last = hierarchy.pop()
    #                         result = result.and_(self._engine.row[last].ne(None))
    #             except (KeyError, ValueError):
    #                 pass
    #     return result

    async def query_filter(self, query, indexing: bool = False):
        try:
            #exp = self._engine.expr(True)
            ### build FILTER based on rethink logic
            table = self._engine.table(self.table)
            conn = self._connection.get_connection()
            if indexing is True:
                idx = await table.index_list().run(conn)
            else:
                idx = None
            # please, first, check for indexing:
            scalar_fields = {}
            for key, value in self.conditions.items():
                # check if an index exists, else, create:
                if indexing is True:
                    if key not in idx:
                        await table.index_create(key).run(conn)
                        table.index_wait(key).run(conn)
                # run first, the array-based queries:
                if isinstance(value, list):
                    query = query.filter(
                        (lambda doc: self._engine.expr(value).coerce_to('array').contains(doc[key]))
                    )
                elif isinstance(value, dict):
                    for k, val in value.items():
                        if k == 'match':
                            query = query.filter(lambda doc: doc[key].match(val))
                        elif k == 'contains':
                            query = query.filter(
                                lambda doc: self._engine.expr(val).coerce_to('array').contains(doc[key])
                            )
                else:
                    scalar_fields[key] = value
            # declare first expression:
            exp = None
            _filter = {}
            for key, value in scalar_fields.items():
                #TODO: add field_definition to know escape characters or other conditions
                if key in self.cond_definition:
                    _type = self.cond_definition[key]
                    if _type == 'date':
                        # I need to convert to date the string
                        tz = self._engine.make_timezone('00:00')
                        dt = iso8601.parse_date(value, default_timezone=tz)
                        dval = self._engine.iso8601(dt.strftime('%Y-%m-%dT%H:%M:%S.%f%z'))
                        # row = self._engine.row[key]
                        # exp = exp.and_(row.eq(dval))
                        _filter[key] = dval
                    else:
                        #TODO: cover other conversions of data
                        # row = self._engine.row[key]
                        # exp = exp.and_(row.eq(value))
                        _filter[key] = value
                else:
                    # row = self._engine.row[key]
                    # exp = exp.and_(row.eq(value))
                    _filter[key] = value
                # simplify exact matches
            query = query.filter(_filter)
            #query options
            if self.qry_options:
                exp = self.query_options(self._engine.expr(True))
            # add search criteria
            if exp:
                query = query.filter(exp)
        except Exception as err:
            self.logger.exception(err)
        finally:
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
            #return query.inner_join(join, lambda doc1, doc2: doc1[self._join_field] == doc2[self._join_field]).zip()
            query = query.eq_join(self._join_field, join, index=self._join_field)
            if query:
                query = query.zip()
        except Exception as err:
            self.logger.exception(err, stack_info=True)
        finally:
            return query

    async def group_by(self, query):
        try:
            if self.grouping is not None and isinstance(self.grouping, list):
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

    async def build_query(self, run=True, querylimit: int = None):
        '''
        Build a SQL Query.
        '''
        self.logger.debug(
            f"RT FIELDS ARE {self.fields}"
        )
        conn = await self._connection.connection()
        if self.database:
            await conn.use(self.database)
        try:
            if not conn.is_open():
                await conn.reconnect(noreply_wait=False)
        except (ReqlDriverError, ReqlRuntimeError) as err:
            raise (
                f"Error on RethinkDB connection: {err}"
            ) from err
        if not conn or not conn.is_open():
            raise Exception(
                'RethinkDB error on parsing Query, impossible to restablish a connection'
            )
        # most basic query
        eq_table = None
        try:
            if isinstance(self.table, list):
                # I need to optimize by creating index on pivot field
                #big TODO: need to wait until index will ready to use
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
            search = await self.query_filter(search)
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
        if run is True:
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
        else:
            # to add more complex queries to Rethink Engine Search Object
            return search

    async def result_from_cursor(self, search, conn):
        try:
            cursor = await search.run(conn)
            if isinstance(cursor, list):
                return cursor
            else:
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

    # async def run(self, table):
    #     """run.
    #         Run a filter based on where_cond and conditions
    #     """
    #     conditions = {}
    #     result = []

    #     if self.conditions:
    #         conditions = { **self.conditions }

    #     if self.filter:
    #         conditions.update(self.filter)

    #     conditions.update((x, None)
    #                       for (x, y) in conditions.items() if y == "null")

    #     print("RT CONDITIONS {}".format(conditions))

    #     # mapping fields with new names
    #     map = {}
    #     has_fields = []

    #     try:
    #         if self.fields:
    #             for field in self.fields:
    #                 name = ""
    #                 alias = ""
    #                 if " as " in field:
    #                     el = field.split(" as ")
    #                     print(el)
    #                     name = el[0]
    #                     alias = el[1].replace('"', "")
    #                     map[alias] = self._engine.row[name]
    #                     self.fields.remove(field)
    #                     self.fields.append(name)
    #                 else:
    #                     map[field] = self._engine.row[field]
    #             print("RT FIELDS {}".format(self.fields))
    #             has_fields = self.fields.copy()

    #             print("RT MAP IS {}".format(map))
    #     except Exception as err:
    #         print("FIELD ERROR {}".format(err))

    #     try:
    #         if conditions["filterdate"] == "CURRENT_DATE":
    #             conditions["filterdate"] = today(mask="%Y-%m-%d")
    #     except (KeyError, ValueError):
    #         pass

    #     filters = []
    #     try:
    #         keys = list(conditions.keys())
    #         has_fields = has_fields + keys
    #     except (KeyError, ValueError):
    #         pass

    #     # build the search element
    #     search = self._engine.db(self.database).table(table).has_fields(has_fields)

    #     result = self._engine.expr(True)

    #     ### build FILTER based on rethink logic
    #     for key, value in conditions.items():
    #         print(key, value)
    #         if type(value) is list:
    #             # print(value)
    #             search = search.filter(
    #                 (
    #                     lambda exp: self._engine.expr(value)
    #                     .coerce_to("array")
    #                     .contains(exp[key])
    #                 )
    #             )
    #         else:
    #             if type(value) is str:
    #                 if value.startswith("!"):
    #                     # not null
    #                     result = result.and_(
    #                         self._engine.row[key].ne(value.replace("!", ""))
    #                     )
    #                 elif value.startswith("["):
    #                     # between
    #                     result = result.and_(
    #                         self._engine.row[key].between(10, 20))
    #                 else:
    #                     result = result.and_(self._engine.row[key].eq(value))
    #             else:
    #                 result = result.and_(self._engine.row[key].eq(value))

    #     # query options
    #     if self.qry_options:
    #         result = self.query_options(result, conditions)

    #     print("RESULT IS")
    #     print(result)

    #     # add search criteria
    #     search = search.filter(result)

    #     # fields and mapping
    #     if self.fields:
    #         if map:
    #             search = search.pluck(self.fields).map(map)
    #         else:
    #             search = search.pluck(self.fields)

    #     # ordering
    #     order = None
    #     if self.ordering:
    #         if type(self.ordering) is list:
    #             orderby = self.ordering[0].split(" ")
    #         else:
    #             orderby = self.ordering.split(" ")
    #         if orderby[1] == "DESC":
    #             order = self._engine.desc(orderby[0])
    #         else:
    #             order = orderby[0]
    #         # add ordering
    #         search = search.order_by(order)

    #     # adding distinct
    #     if self.distinct:
    #         search = search.distinct()


    #     data = []
    #     self._result = None
    #     conn = self._connection.get_connection()
    #     try:
    #         try:
    #             cursor = await search.run(conn)
    #         except (ReqlRuntimeError, ReqlRuntimeError) as err:
    #             print("Error on rql query is %s" % err.message)
    #             raise Exception("Error on RQL query is %s" % err.message)
    #             return False
    #         if order or self.distinct:
    #             self._result = cursor
    #             return self._result
    #         else:
    #             while await cursor.fetch_next():
    #                 row = await cursor.next()
    #                 data.append(row)
    #             self._result = data
    #     finally:
    #         return self._result

    # async def get_one(self, table: str, idx: int = 0):
    #     """
    #     Functions for Query API
    #     """
    #     conditions = {}
    #     result = []

    #     if self.conditions:
    #         conditions = { **self.conditions }

    #     if self.filter:
    #         conditions.update(self.filter)

    #     conditions.update((x, None)
    #                       for (x, y) in conditions.items() if y == "null")

    #     try:
    #         if conditions["filterdate"] == "CURRENT_DATE":
    #             conditions["filterdate"] = today(mask="%Y-%m-%d")
    #     except (KeyError, ValueError):
    #         pass

    #     print("RT CONDITIONS {}".format(conditions))

    #     try:
    #         conn = self._connection.get_connection()
    #         result = await self._engine.db(self.database).table(table).filter(conditions).nth(idx).run(conn)
    #     except (ReqlRuntimeError, ReqlNonExistenceError) as err:
    #         raise Exception(
    #             f"RethinkParser: Error on Query One: {err}"
    #         )
    #     except Exception as err:
    #         self.logger.exception(err)
    #         raise
    #     if result:
    #         return result
    #     else:
    #         return []
