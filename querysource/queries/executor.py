from typing import Optional
from rethinkdb.errors import RqlRuntimeError, RqlDriverError
from ..exceptions import QueryError, QueryException
from ..datasources.introspection import get_introspector
from .base import BaseQuery


class Executor(BaseQuery):
    """Executor.

    Description: Arbitrary Query Executor.

    """
    async def close(self):
        pass

    async def introspect(self, table: Optional[dict] = None):
        """Schema introspection for the resolved driver/datasource.

        Resolves the connection exactly like ``query()``/``dry_run()`` (named
        datasource first, otherwise the default driver) and delegates to the
        per-driver strategy in ``datasources.introspection``.

        Returns the list of tables with column counts by default, or the
        columns of a single table when ``table={'schema': ..., 'table': ...}``
        is provided (lazy column loading).
        """
        if datasource := self._query.datasource:
            source, db = await self.datasource(datasource)
            driver = getattr(source, 'driver', None)
        elif drv := self._query.driver:
            _, db = await self.default_driver(drv)
            driver = drv
        else:
            raise QueryError(
                message='QS: a driver or datasource is required for introspection',
                code=410  # bad request
            )
        introspector = get_introspector(driver)
        if not introspector.supported:
            raise QueryError(message=introspector.reason, code=400)
        try:
            async with await db.connection() as conn:
                if table and table.get('table'):
                    return await introspector.columns(
                        conn, table.get('schema'), table.get('table')
                    )
                return await introspector.tables(conn)
        except QueryError:
            raise
        except Exception as ex:
            raise QueryException(
                message=f'QS: Schema introspection error: {ex}',
                code=500
            ) from ex

    def start(self, data):
        try:
            self._query = self.query_model(data)
        except TypeError as ex:
            raise QueryError(
                message=f'QS: Invalid Executor {ex}',
                code=410  # bad request
            ) from ex

    async def dry_run(self):
        """dry_run.
        Description: get a Query Object, check if valid, and only ruturns a false response.
        """
        db = None
        state = None
        started = self.start_timing(self._query.retrieved)
        if datasource := self._query.datasource:
            _, db = await self.datasource(datasource)
            drv_type = 'asyncdb'
        elif driver := self._query.driver:
            ## using a default driver:
            try:
                drv_type, db = await self.default_driver(driver)
                async with await db.connection() as conn:
                    state = f'Connected: {conn.is_connected()}'
            except (RuntimeError, QueryException) as ex:
                raise QueryError(
                    message=str(ex),
                    code=401
                ) from ex
            except Exception as ex:
                print(ex)
        else:
            raise QueryError(
                message=f'QS: Invalid Query Type {self._query!s}',
                code=410  # bad request
            )
        # finish: calculate duration and return result:
        duration = (self.generated_at(started).total_seconds() / 1000)
        try:
            obj = self.get_result(self._query, data=[], duration=duration)
            obj.state = state
            return obj
        except TypeError as ex:
            raise QueryError(
                message=f'QS: Result Error: {ex}',
                code=400  # bad request
            ) from ex
        except Exception as ex:
            raise QueryException(
                message=f'QS: Result Error: {ex}',
                code=400  # bad request
            ) from ex
        finally:
            self._query = None

    async def query(self):
        """query.
        Description: get a Query Object a making a query to Backend.
        """
        db = None
        state = None
        result = []
        started = self.start_timing(self._query.retrieved)
        driver = 'default'
        if datasource := self._query.datasource:
            _, db = await self.datasource(datasource)
            drv_type = 'asyncdb'
        elif driver := self._query.driver:
            ## using a default driver:
            drv_type, db = await self.default_driver(driver)
        else:
            raise QueryError(
                message=f'QS: Invalid Query Type {self._query!s}',
                code=410  # bad request
            )
        if db is None:
            raise QueryError(
                message=f'QS: Invalid Query Type {self._query!s}',
                code=410  # bad request
            )
        try:
            error = None
            if drv_type == 'asyncdb':
                async with await db.connection() as conn:
                    state = f'Connected: {conn.is_connected()}'
                    conn.output_format('iterable')
                    try:
                        kwargs = self._query.parameters or {}
                        # TODO: add support for selecting returning options
                        if driver == 'influx':
                            result, error = await db.query(
                                self._query.query,
                                frmt='recordset',
                                **kwargs
                            )
                        elif driver == 'rethink':
                            # Manual Eval of RethinkDB Query (Risky):
                            # Prepare a safe globals dict exposing only the RethinkDB query builder object
                            safe_globals = {"r": db.engine()}
                            try:
                                # Evaluate the query string to get a RethinkDB query object
                                query = eval(self._query.query, safe_globals)
                                cursor = await query.run(conn.raw_connection)
                                if isinstance(cursor, list):
                                    result = cursor
                                else:
                                    while await cursor.fetch_next():
                                        result.append(await cursor.next())
                            except RqlRuntimeError as e:
                                raise ValueError(f"Error running query: {e}")
                            except Exception as e:
                                raise ValueError(f"Error parsing query: {e}")
                        elif driver in ('mongo', 'documentdb'):
                            database = self._query.query.pop('database', None)
                            if database:
                                await conn.use(database)
                            collection = self._query.query.pop('collection')
                            _filter = self._query.query.pop('filter', {})
                            kwargs.update(self._query.query)
                            result, error = await conn.query(
                                collection_name=collection,
                                query=_filter,
                                **kwargs,
                            )
                        else:
                            result, error = await db.query(
                                self._query.query,
                                **kwargs
                            )
                    except (TypeError, ValueError):
                        result = await db.query(self._query.query)
                    if error:
                        state = f'With Errors: {error}'
            elif drv_type == 'external':
                ## query DB external object.
                pass
        except (RuntimeError, QueryException) as ex:
            raise QueryError(
                message=str(ex),
                code=400
            ) from ex
        # finish: calculate duration and return result:
        duration = (self.generated_at(started).total_seconds() / 1000)
        try:
            return self.get_result(
                self._query,
                data=result,
                duration=duration,
                errors=error,
                state=state
            )
        except TypeError as ex:
            raise QueryError(
                message=f'QS: Result Error: {ex}',
                code=410  # bad request
            ) from ex
        except Exception as ex:
            raise QueryException(
                message=f'QS: Result Error: {ex}',
                code=400  # bad request
            ) from ex
        finally:
            self._query = None
