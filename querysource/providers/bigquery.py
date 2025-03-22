"""
Google BigQuery Provider.

This module provides a Database provider for Google BigQuery.
"""
from typing import Any, Union
from aiohttp import web
from asyncdb.exceptions import (
    ProviderError,
    NoDataFound,
    DriverError
)
from ..models import QueryModel
from ..parsers.sql import SQLParser
from ..parsers.bigquery import BigQueryParser
from ..types.validators import is_empty
from ..exceptions import (
    DataNotFound,
    ParserError,
    QueryException
)
from .sql import sqlProvider


class bigqueryProvider(sqlProvider):
    """
    bigqueryProvider.

    Provider for Google BigQuery.
    """

    __parser__ = BigQueryParser

    def __init__(
        self,
        slug: str = '',
        query: Any = None,
        qstype: str = '',
        definition: Union[QueryModel, dict] = None,  # Model Object or a dictionary defining a Query.
        conditions: dict = None,
        request: web.Request = None,
        **kwargs
    ):
        self.is_raw = False
        super(bigqueryProvider, self).__init__(
            slug=slug,
            query=query,
            qstype=qstype,
            definition=definition,
            conditions=conditions,
            request=request,
            **kwargs
        )

    async def columns(self):
        """Return the columns (fields) involved on the query (when possible).
        """
        if self._query:
            try:
                async with await self._connection.connection() as conn:
                    stmt, _ = await conn.execute(f"{self._query} LIMIT 0")
                    self._columns = [a.name for a in stmt.schema]
            except AttributeError as ex:
                raise ParserError(
                    f"Invalid Query or Column for query: {self._query}"
                ) from ex
        return self._columns

    def accepts(self) -> str:
        return None

    async def query(self):
        """Run a query on the Data Provider.
        """
        error = None
        try:
            async with await self._connection.connection() as conn:
                result, error = await conn.query(self._query, factory='pandas')
            if error:
                return [result, error]
            if not is_empty(result):
                # check if return a dataframe instead
                self._result = result
                return [self._result, error]
            else:
                raise self.NotFound(
                    'QS: Empty Result'
                )
        except (DataNotFound, NoDataFound) as ex:
            raise self.NotFound(
                f'QS: Empty Result: {ex}'
            ) from ex
        except (ProviderError, DriverError) as ex:
            raise QueryException(
                f"Query Error: {ex}"
            ) from ex
        except Exception as err:
            self._logger.exception(err, stack_info=False)
            raise self.Error(
                "Query: Error",
                exception=err,
                code=406
            )
