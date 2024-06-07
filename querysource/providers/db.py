"""
Default PostgreSQL Provider (based on asyncpg).

Used to connect to main PG Database used by QS.
"""
from collections.abc import Callable
from asyncdb.exceptions import (
    DriverError,
    NoDataFound,
    ProviderError
)
from ..exceptions import (
    DataNotFound,
    ParserError,
    QueryException
)
from .pg import pgProvider


class dbProvider(pgProvider):

    async def prepare_connection(self) -> Callable:
        """Signal run before connection is made.
        """
        await super(dbProvider, self).prepare_connection()
        if not self._connection:
            raise QueryException(
                "Connection Object Missing for this Provider."
            )
        ## Parse Query:
        if self.is_raw is False:
            try:
                self._query = await self._parser.build_query()
            except Exception as ex:
                raise ParserError(
                    f"Unable to parse Query: {ex}"
                ) from ex

    async def query(self):
        """Run a query on the Data Provider.
        """
        error = None
        try:
            async with await self._connection.connection() as conn:
                result, error = await conn.query(self._query)
            if error:
                return [result, error]
            if result:
                # check if return a dataframe instead
                self._result = result
                return [self._result, error]
            else:
                raise self.NotFound(
                    'DB: Empty Result'
                )
        except (DataNotFound, NoDataFound) as ex:
            raise self.NotFound(
                f'DB: Empty Result: {ex}'
            ) from ex
        except (ProviderError, DriverError) as ex:
            raise QueryException(
                f"Query Error: {ex}"
            ) from ex
        except Exception as err:
            self._logger.exception(err, stack_info=False)
            raise self.Error(
                "Query: Uncaught Error",
                exception=err,
                code=406
            )

    async def close(self):
        try:
            await self._connection.close()
        except Exception:  # pylint: disable=W0703
            pass
