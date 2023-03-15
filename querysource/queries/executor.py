from querysource.exceptions import QueryError, QueryException
from .abstract import BaseQuery


class Executor(BaseQuery):
    """Executor.

    Description: Arbitrary Query Executor.

    """
    async def close(self):
        pass

    def start(self, data):
        try:
            self._query = self.query_model(data)
        except TypeError as ex:
            raise QueryError(
                message=f'QS: Invalid Executor {ex}',
                code=410 # bad request
            ) from ex

    async def dry_run(self):
        """dry_run.
        Description: get a Query Object, check if valid, and only ruturns a false response.
        """
        db = None
        state = None
        started = self.start_timing(self._query.retrieved)
        if datasource:= self._query.datasource:
            db = await self.get_datasource(datasource)
        elif driver:= self._query.driver:
            ## using a default driver:
            try:
                db = await self.default_driver(driver)
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
                code=410 # bad request
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
                code=400 # bad request
            ) from ex
        except Exception as ex:
            raise QueryException(
                message=f'QS: Result Error: {ex}',
                code=400 # bad request
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
        if datasource:= self._query.datasource:
            db = await self.get_datasource(datasource)
            driver = 'default'
        elif driver:= self._query.driver:
            ## using a default driver:
            db = await self.default_driver(driver)
        else:
            raise QueryError(
                message=f'QS: Invalid Query Type {self._query!s}',
                code=410  # bad request
            )
        try:
            error = None
            async with await db.connection() as conn:
                state = f'Connected: {conn.is_connected()}'
                conn.output_format('iterable')
                try:
                    kwargs = self._query.parameters
                    if not kwargs:
                        kwargs = {}
                    # TODO: add support for selecting returning options
                    if driver == 'influx':
                        result, error = await db.query(self._query.query, frmt = 'recordset', **kwargs)
                    else:
                        result, error = await db.query(self._query.query, **kwargs)
                except (TypeError, ValueError):
                    result = await db.query(self._query.query)
                if error:
                    state = 'With Errors.'
        except (RuntimeError, QueryException) as ex:
            raise QueryError(
                message=str(ex),
                code=400
            ) from ex
        # finish: calculate duration and return result:
        duration = (self.generated_at(started).total_seconds() / 1000)
        try:
            obj = self.get_result(self._query, data=result, duration=duration, errors=error, state=state)
            return obj
        except TypeError as ex:
            raise QueryError(
                message=f'QS: Result Error: {ex}',
                code=410 # bad request
            ) from ex
        except Exception as ex:
            raise QueryException(
                message=f'QS: Result Error: {ex}',
                code=400 # bad request
            ) from ex
        finally:
            self._query = None
