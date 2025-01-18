from typing import (
    Union,
    Any
)
from collections.abc import Callable
import contextlib
import hashlib
from aiohttp import web
from asyncdb.exceptions import ProviderError
from querysource.models import QueryModel
from querysource.exceptions import (
    QueryError,
    QueryException,
    ParserError,
    DriverError,
    DataNotFound
)
from ..libs.json import json_decoder
from querysource.parsers.rethink import RethinkParser
from .abstract import BaseProvider


class rethinkProvider(BaseProvider):
    __parser__ = RethinkParser

    def __init__(
        self,
        slug: str = '',
        query: Any = None,
        qstype: str = '',
        connection: Callable = None,
        definition: Union[QueryModel, dict] = None,  # Model Object or a dictionary defining a Query.
        conditions: dict = None,
        request: web.Request = None,
        **kwargs
    ):
        super(rethinkProvider, self).__init__(
            slug=slug,
            query=query,
            qstype=qstype,
            connection=connection,
            definition=definition,
            conditions=conditions,
            request=request,
            **kwargs
        )
        # getting conditions
        self.is_raw = False
        if qstype == 'slug':
            if self._definition.query_raw:
                try:
                    query_raw = json_decoder(self._definition.query_raw)
                    self._parser.database = query_raw.get('database')
                    self._parser.table = query_raw.get('table')
                except Exception as err:
                    # Unable to use query_raw for database:table info
                    self._logger.error(
                        f"Unable to use query_raw for database:table info: {err}"
                    )
            if not self._parser.database:
                self._parser.database = self._program
            if not self._parser.table:
                self._parser.table = self._definition.source or slug
        print('DB > ', self._parser.database)
        print('TABLE > ', self._parser.table)

    def checksum(self):
        name = f'{self._slug}:{self._conditions!s}'
        return hashlib.sha1(f'{name}'.encode('utf-8')).hexdigest()

    async def prepare_connection(self) -> Callable:
        """Signal run before connection is made.
        """
        await super(rethinkProvider, self).prepare_connection()
        self._parser.set_connection(self._connection)
        if not self._connection:
            raise QueryException(
                "Connection Object Missing for this Provider."
            )

    async def columns(self):
        if not self._connection:
            return False
        try:
            self._columns = await self._parser.columns()
        except Exception as err:  # pylint: disable=W0703
            print(
                f"Empty Result: {err}"
            )
            self._columns = []
        return self._columns

    async def dry_run(self):
        """Running Build Query and return the Query to be executed (without execution).
        """
        try:
            self._query = await self._parser.build_query(run=False)
        except Exception as ex:
            raise ParserError(
                f"Unable to parse Query: {ex}"
            ) from ex
        return (self._query, None)

    async def query(self):
        """
        query
        get data from rethinkdb
        TODO: need to check datatypes
        """
        result = []
        error = None
        try:
            async with await self._connection.connection() as conn:
                result = await self._parser.build_query(
                    conn,
                    run=True
                )
            if not result:
                error = "No data was found"
                return [None, error]
            self._result = result
            return [result, error]
        except DataNotFound:
            raise
        except (ParserError, TypeError) as exc:
            raise QueryError(
                f"Error parsing Query: {exc}"
            ) from exc
        except (RuntimeError, ParserError) as err:
            raise QueryException(
                f"Querysource RT Error: {err}"
            ) from err

    async def close(self):
        with contextlib.suppress(ProviderError, DriverError, RuntimeError):
            await self._connection.close()
